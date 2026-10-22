# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0.
# See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0
#
# ===----------------------------------------------------------------------===##

"""Coverage (C) checks over the declared finite domain.

Coverage is intentionally shallow here: it answers *whether a type or
immediate slot is mentioned by any rule predicate*.  It does not claim that
every value of the slot is handled; that is reachability/conflict work for
Phase 3.

Arity comes from Q0.  A ruleset with no Q0 query has no declared arity and is
reported as UNKNOWN instead of silently borrowing an arity from C++.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .diagnostics import Diagnostic, Severity
from .model import (
    Always,
    And,
    BitsCmp,
    ConcatShape,
    ConcatShapeAny,
    ElemBitsCmp,
    ElemTypesEq,
    EltSizeNotPow2,
    FloatIs,
    FixedVector,
    FloatValue,
    FixedVectorElemCountCmp,
    ImmCmp,
    IsMultipleBits,
    IsPow2Bits,
    IsPow2ElemBits,
    LanesCmp,
    LegalizerInfo,
    MemAlignCmp,
    MemCompatInSet,
    MemTypeIn,
    Not,
    NotPow2OrSmaller,
    NumElementsNotPow2,
    Or,
    Pointer,
    PointerVector,
    Pred,
    RuleSet,
    SameShape,
    SameSize,
    Scalar,
    ScalarBitsCmp,
    ScalarSizeVsSizeCmp,
    TypeEq,
    TypeIn,
    TypePairInSet,
    Vector,
)


class UnhandledPredicate(Exception):
    """Coverage has no read-set rule for this predicate node."""

    def __init__(self, pred: object) -> None:
        self.predicate_name = type(pred).__name__
        super().__init__(f"unhandled predicate type {self.predicate_name}")


@dataclass(frozen=True)
class CoverageEntry:
    opcodes: tuple[str, ...]
    arity: Optional[int]
    q0_queries: int
    covered_types: tuple[int, ...]
    uncovered_types: tuple[int, ...]
    imm_arity: Optional[int]
    covered_imms: tuple[int, ...]
    uncovered_imms: tuple[int, ...]
    mem_arity: int = 0
    covered_mems: tuple[int, ...] = ()
    uncovered_mems: tuple[int, ...] = ()


@dataclass
class CoverageReport:
    entries: list[CoverageEntry] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity is Severity.WARN]


def type_read_set(pred: Pred) -> set[int]:
    if isinstance(pred, Always):
        return set()
    if isinstance(
        pred,
        (TypeIn, TypeEq, Scalar, Vector, FixedVector, Pointer, PointerVector,
         FloatIs, FloatValue),
    ):
        return {pred.idx}
    if isinstance(pred, BitsCmp):
        return {pred.idx}
    if isinstance(pred, ElemBitsCmp):
        return {pred.idx}
    if isinstance(pred, LanesCmp):
        return {pred.idx}
    if isinstance(pred, FixedVectorElemCountCmp):
        return {pred.idx}
    if isinstance(pred, NumElementsNotPow2):
        return {pred.idx}
    if isinstance(
        pred,
        (SameSize, ElemTypesEq, ConcatShape, ConcatShapeAny, SameShape),
    ):
        return {pred.i, pred.j}
    if isinstance(pred, ScalarBitsCmp):
        return {pred.lhs, pred.rhs}
    if isinstance(pred, ScalarSizeVsSizeCmp):
        return {pred.scalar_idx, pred.size_idx}
    if isinstance(pred, (IsPow2Bits, IsPow2ElemBits, NotPow2OrSmaller, EltSizeNotPow2)):
        return {pred.idx}
    if isinstance(pred, IsMultipleBits):
        return {pred.idx}
    if isinstance(pred, TypePairInSet):
        return {pred.i, pred.j}
    if isinstance(pred, MemCompatInSet):
        return {pred.i, pred.j}
    if isinstance(pred, (ImmCmp, MemTypeIn, MemAlignCmp)):
        # Immediate and memory descriptors do not occupy a type slot.
        return set()
    if isinstance(pred, Not):
        return type_read_set(pred.inner)
    if isinstance(pred, (And, Or)):
        return type_read_set(pred.lhs) | type_read_set(pred.rhs)
    raise UnhandledPredicate(pred)


def imm_read_set(pred: Pred) -> set[int]:
    if isinstance(pred, ImmCmp):
        return {pred.idx}
    if isinstance(pred, Not):
        return imm_read_set(pred.inner)
    if isinstance(pred, (And, Or)):
        return imm_read_set(pred.lhs) | imm_read_set(pred.rhs)
    return set()


def mem_read_set(pred: Pred) -> set[int]:
    if isinstance(pred, (MemTypeIn, MemAlignCmp, MemCompatInSet)):
        return {pred.mem_idx}
    if isinstance(pred, Not):
        return mem_read_set(pred.inner)
    if isinstance(pred, (And, Or)):
        return mem_read_set(pred.lhs) | mem_read_set(pred.rhs)
    # A node coverage does not know must be reported, not treated as
    # "reads no memory".  type_read_set owns the list of known nodes.
    type_read_set(pred)
    return set()


def _entry(info: LegalizerInfo, ruleset: RuleSet) -> CoverageEntry:
    domain = info.domain
    assert domain is not None
    queries = domain.q0_for(ruleset.opcodes)
    arity = max((len(q.types) for q in queries), default=None)
    imm_arity = max((len(q.imms) for q in queries), default=None)
    mem_arity = max((len(q.mems) for q in queries), default=0)
    covered_types = sorted({i for rule in ruleset.rules for i in type_read_set(rule.pred)})
    covered_imms = sorted({i for rule in ruleset.rules for i in imm_read_set(rule.pred)})
    covered_mems = sorted({i for rule in ruleset.rules for i in mem_read_set(rule.pred)})
    uncovered_types = tuple(i for i in range(arity or 0) if i not in covered_types)
    uncovered_imms = tuple(i for i in range(imm_arity or 0) if i not in covered_imms)
    uncovered_mems = tuple(i for i in range(mem_arity) if i not in covered_mems)
    return CoverageEntry(
        opcodes=ruleset.opcodes,
        arity=arity,
        q0_queries=len(queries),
        covered_types=tuple(covered_types),
        uncovered_types=uncovered_types,
        imm_arity=imm_arity,
        covered_imms=tuple(covered_imms),
        uncovered_imms=uncovered_imms,
        mem_arity=mem_arity,
        covered_mems=tuple(covered_mems),
        uncovered_mems=uncovered_mems,
    )


def run_coverage(info: LegalizerInfo) -> CoverageReport:
    report = CoverageReport()
    if info.domain is None:
        return report
    for ruleset in info.rulesets:
        try:
            entry = _entry(info, ruleset)
        except UnhandledPredicate as exc:
            report.diagnostics.append(
                Diagnostic(
                    "C007",
                    Severity.ERROR,
                    f"{', '.join(ruleset.opcodes)}: unhandled predicate "
                    f"{exc.predicate_name} in coverage analysis",
                    ruleset.span,
                )
            )
            continue
        report.entries.append(entry)
        span = ruleset.span
        if entry.arity is None:
            report.diagnostics.append(
                Diagnostic(
                    "C000",
                    Severity.UNKNOWN,
                    f"{', '.join(entry.opcodes)}: no Q0 query, so arity and coverage are undefined",
                    span,
                )
            )
            continue
        for idx in entry.uncovered_types:
            report.diagnostics.append(
                Diagnostic(
                    "C001",
                    Severity.WARN,
                    f"{', '.join(entry.opcodes)}: type index t{idx} is never mentioned by a rule predicate",
                    span,
                )
            )
        for idx in entry.uncovered_imms:
            report.diagnostics.append(
                Diagnostic(
                    "C002",
                    Severity.WARN,
                    f"{', '.join(entry.opcodes)}: immediate index {idx} is never mentioned by a rule predicate",
                    span,
                )
            )
        for idx in entry.uncovered_mems:
            report.diagnostics.append(
                Diagnostic(
                    "C006",
                    Severity.WARN,
                    f"{', '.join(entry.opcodes)}: memory index {idx} is never mentioned by a rule predicate",
                    span,
                )
            )
    for query in info.domain.q0:
        if query.opcode not in info.by_opcode:
            report.diagnostics.append(
                Diagnostic(
                    "C003",
                    Severity.ERROR,
                    f"Q0 query uses opcode {query.opcode}, but no ruleset defines it",
                    info.domain.span,
                )
            )
    return report
