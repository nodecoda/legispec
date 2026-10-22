# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0.
# See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0
#
# ===----------------------------------------------------------------------===##

"""Parser and runner for the executable witness corpus.

The witness file is the falsification surface for the rule DSL.  It is
deliberately independent from the C++ implementation: each witness records
what the *specification* intends, then the Python evaluator is asked whether
that intent still holds.

Witness format (see ``witness/toycpu.witness`` and ``docs/legispec-v1.0.md``)::

    witness name {
        query       = (G_ADD, [s16])
        step        = WidenScalar t0 -> s32 rule 1
        # or: not_rule = 0
        # or: not_action = Unsupported
        # or: unreachable = 3
        reason      = "..."
    }
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .diagnostics import DslError, Span
from .model import Action, LLT, LegalizerInfo, MemDesc, Query, parse_llt

_ACTION_VALUES = {action.value: action for action in Action}
_ACTION_RE = re.compile(
    r"^(?P<action>" + "|".join(sorted(_ACTION_VALUES, key=len, reverse=True)) + r")"
    r"(?:\s+t(?P<idx>\d+)\s*->\s*(?P<ty>\S+))?"
    r"(?:\s+rule\s+(?P<rule>\d+))?$"
)
_QUERY_RE = re.compile(
    r"^\(\s*(?P<opcode>[A-Za-z_][A-Za-z0-9_]*)\s*,\s*\[(?P<types>[^\]]*)\]\s*"
    r"(?:,\s*mem\s*=\s*\[(?P<mems>[^\]]*)\])?\s*"
    r"(?:,\s*imm\s*=\s*\[(?P<imms>[^\]]*)\])?\s*\)$"
)


@dataclass(frozen=True)
class ExpectedStep:
    action: Action
    type_idx: Optional[int] = None
    new_type: Optional[LLT] = None
    rule_index: Optional[int] = None


@dataclass(frozen=True)
class Witness:
    name: str
    query: Query
    line: int
    expected: Optional[ExpectedStep] = None
    not_rule: Optional[int] = None
    not_action: Optional[Action] = None
    unreachable: Optional[int] = None
    shadowed: Optional[int] = None
    reason: Optional[str] = None

    def constraint_count(self) -> int:
        return sum(
            value is not None
            for value in (
                self.expected,
                self.not_rule,
                self.not_action,
                self.unreachable,
                self.shadowed,
            )
        )


@dataclass
class WitnessReport:
    total: int = 0
    positives: int = 0
    negatives: int = 0
    unreachable: int = 0
    shadowed: int = 0
    failures: list[str] = field(default_factory=list)
    rulesets_without_positive: list[str] = field(default_factory=list)
    rulesets_without_negative: list[str] = field(default_factory=list)
    uncovered_rules: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures and not self.uncovered_rules

    @property
    def complete(self) -> bool:
        return (
            self.ok
            and not self.rulesets_without_positive
            and not self.rulesets_without_negative
        )

    def summary(self) -> str:
        return (
            f"witnesses={self.total} positives={self.positives} "
            f"negatives={self.negatives} unreachable={self.unreachable} "
            f"shadowed={self.shadowed} "
            f"failures={len(self.failures)}"
        )


def _split_items(text: Optional[str]) -> list[str]:
    if text is None or not text.strip():
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


def _parse_query(text: str, *, line: int) -> Query:
    match = _QUERY_RE.match(text.strip())
    if match is None:
        raise DslError("malformed query; expected (OPCODE, [types] ...)", code="X001",
                       span=Span.point(line, 1))
    try:
        types = tuple(parse_llt(item) for item in _split_items(match.group("types")))
        mems = tuple(
            _parse_mem(item, line=line) for item in _split_items(match.group("mems"))
        )
        imms = tuple(int(item) for item in _split_items(match.group("imms")))
    except ValueError as exc:
        raise DslError(f"bad witness integer: {exc}", code="X002",
                       span=Span.point(line, 1)) from None
    return Query(match.group("opcode"), types, mems, imms)


def _parse_mem(text: str, *, line: int) -> MemDesc:
    if ":" not in text:
        raise DslError(f"bad memory descriptor {text!r}; expected <llt>:<align>",
                       code="X003", span=Span.point(line, 1))
    ty_text, align_text = text.split(":", 1)
    try:
        align = int(align_text)
    except ValueError:
        raise DslError(f"bad memory alignment {align_text!r}", code="X004",
                       span=Span.point(line, 1)) from None
    if align <= 0:
        raise DslError(f"memory alignment must be positive, got {align}", code="X005",
                       span=Span.point(line, 1))
    return MemDesc(parse_llt(ty_text), align)


def _parse_step(text: str, *, line: int) -> ExpectedStep:
    match = _ACTION_RE.match(text.strip())
    if match is None or match.group("action") not in _ACTION_VALUES:
        raise DslError(f"malformed step {text!r}", code="X006", span=Span.point(line, 1))
    action = _ACTION_VALUES[match.group("action")]
    type_idx = int(match.group("idx")) if match.group("idx") is not None else None
    new_type = parse_llt(match.group("ty")) if match.group("ty") is not None else None
    rule_index = int(match.group("rule")) if match.group("rule") is not None else None
    if (type_idx is None) != (new_type is None):
        raise DslError(f"step mutation needs both type index and type: {text!r}",
                       code="X007", span=Span.point(line, 1))
    return ExpectedStep(action, type_idx, new_type, rule_index)


def parse_witness_file(path: str | Path) -> list[Witness]:
    return parse_witness_text(Path(path).read_text(encoding="utf-8"))


def parse_witness_text(text: str) -> list[Witness]:
    witnesses: list[Witness] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        raw = lines[index]
        line = index + 1
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue
        match = re.match(r"^witness\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{\s*$", stripped)
        if match is None:
            raise DslError(f"expected witness block, got {stripped!r}", code="X008",
                           span=Span.point(line, 1))
        name = match.group(1)
        start = line
        body: dict[str, tuple[str, int]] = {}
        index += 1
        while index < len(lines):
            body_line = index + 1
            body_raw = lines[index]
            body_stripped = body_raw.strip()
            index += 1
            if body_stripped == "}":
                break
            if not body_stripped or body_stripped.startswith("#"):
                continue
            if "=" not in body_stripped:
                raise DslError(f"malformed witness property {body_stripped!r}", code="X009",
                               span=Span.point(body_line, 1))
            key, value = body_stripped.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key in body:
                raise DslError(f"duplicate witness property {key!r}", code="X010",
                               span=Span.point(body_line, 1))
            if key not in {
                "query",
                "step",
                "not_rule",
                "not_action",
                "unreachable",
                "shadowed",
                "reason",
            }:
                raise DslError(f"unknown witness property {key!r}", code="X011",
                               span=Span.point(body_line, 1))
            body[key] = (value, body_line)
        else:
            raise DslError(f"unterminated witness {name!r}", code="X012",
                           span=Span.point(start, 1))

        if "query" not in body:
            raise DslError(f"witness {name!r} has no query", code="X013",
                           span=Span.point(start, 1))
        query = _parse_query(body["query"][0], line=body["query"][1])
        expected = _parse_step(body["step"][0], line=body["step"][1]) if "step" in body else None
        not_rule = int(body["not_rule"][0]) if "not_rule" in body else None
        not_action = None
        if "not_action" in body:
            value = body["not_action"][0]
            if value not in _ACTION_VALUES:
                raise DslError(f"unknown action {value!r}", code="X014",
                               span=Span.point(body["not_action"][1], 1))
            not_action = _ACTION_VALUES[value]
        unreachable = int(body["unreachable"][0]) if "unreachable" in body else None
        shadowed = int(body["shadowed"][0]) if "shadowed" in body else None
        reason = None
        if "reason" in body:
            reason = body["reason"][0]
            if len(reason) >= 2 and reason[0] == '"' and reason[-1] == '"':
                reason = reason[1:-1]

        witness = Witness(
            name=name,
            query=query,
            line=start,
            expected=expected,
            not_rule=not_rule,
            not_action=not_action,
            unreachable=unreachable,
            shadowed=shadowed,
            reason=reason,
        )
        if witness.constraint_count() != 1:
            raise DslError(
                f"witness {name!r} must have exactly one of "
                "step/not_rule/not_action/unreachable/shadowed",
                code="X015",
                span=Span.point(start, 1),
            )
        witnesses.append(witness)
    return witnesses


def run_witnesses(info: LegalizerInfo, witnesses: list[Witness]) -> WitnessReport:
    report = WitnessReport(total=len(witnesses))
    positive_rulesets: set[int] = set()
    negative_rulesets: set[int] = set()
    covered: set[tuple[int, int]] = set()

    for witness in witnesses:
        ruleset = info.get(witness.query.opcode)
        ruleset_id = id(ruleset)
        actual, _ = ruleset.apply(witness.query)
        label = f"line {witness.line}: {witness.name}"

        if witness.expected is not None:
            report.positives += 1
            positive_rulesets.add(ruleset_id)
            expected = witness.expected
            if actual.action is not expected.action:
                report.failures.append(
                    f"{label}: expected action {expected.action.value}, got {actual.action.value}"
                )
            if expected.new_type is not None and actual.new_type != expected.new_type:
                report.failures.append(
                    f"{label}: expected new type {expected.new_type}, got {actual.new_type}"
                )
            if expected.type_idx is not None and actual.type_idx != expected.type_idx:
                report.failures.append(
                    f"{label}: expected type index t{expected.type_idx}, got t{actual.type_idx}"
                )
            if expected.rule_index is not None:
                if actual.rule_index != expected.rule_index:
                    report.failures.append(
                        f"{label}: expected rule {expected.rule_index}, got "
                        f"{actual.rule_index if actual.rule_index is not None else 'fallback'}"
                    )
                else:
                    covered.add((ruleset_id, expected.rule_index))
        elif witness.not_rule is not None:
            report.negatives += 1
            negative_rulesets.add(ruleset_id)
            if actual.rule_index == witness.not_rule:
                report.failures.append(
                    f"{label}: rule {witness.not_rule} matched but is forbidden"
                )
        elif witness.not_action is not None:
            report.negatives += 1
            negative_rulesets.add(ruleset_id)
            if actual.action is witness.not_action:
                report.failures.append(
                    f"{label}: action {witness.not_action.value} is forbidden"
                )
        elif witness.shadowed is not None:
            report.shadowed += 1
            if witness.shadowed < 0 or witness.shadowed >= len(ruleset.rules):
                report.failures.append(
                    f"{label}: shadowed rule index {witness.shadowed} is out of range"
                )
            else:
                covered.add((ruleset_id, witness.shadowed))
                if actual.rule_index == witness.shadowed:
                    report.failures.append(
                        f"{label}: declared shadowed but rule {witness.shadowed} won"
                    )
        else:
            assert witness.unreachable is not None
            report.unreachable += 1
            if witness.unreachable < 0 or witness.unreachable >= len(ruleset.rules):
                report.failures.append(
                    f"{label}: unreachable rule index {witness.unreachable} is out of range"
                )
            else:
                covered.add((ruleset_id, witness.unreachable))
                if actual.rule_index == witness.unreachable:
                    report.failures.append(
                        f"{label}: declared unreachable but rule {witness.unreachable} matched"
                    )

    for ruleset in info.rulesets:
        ruleset_id = id(ruleset)
        ops = ", ".join(ruleset.opcodes)
        if ruleset_id not in positive_rulesets:
            report.rulesets_without_positive.append(ops)
        if ruleset_id not in negative_rulesets:
            report.rulesets_without_negative.append(ops)
        for index in range(len(ruleset.rules)):
            if (ruleset_id, index) not in covered:
                report.uncovered_rules.append(f"{ops} rule {index}")

    return report
