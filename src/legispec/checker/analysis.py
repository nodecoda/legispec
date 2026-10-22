# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Shared finite-domain enumeration for all checker modules.

The domain is the one declared in LegiSpec: Q0 fixes each ruleset's observed type
and immediate arity.  Enumeration is deterministic (domain order is source
order), so every diagnostic can be reproduced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from typing import Optional

from ..diagnostics import DslError
from ..model import Action, LLT, LegalizerInfo, MUTATING_ACTIONS, Query, Rule, RuleSet


@dataclass(frozen=True)
class MutationIssue:
    rule_index: int
    code: str
    message: str
    query: Query


@dataclass
class RuleSetAnalysis:
    opcodes: tuple[str, ...]
    arity: Optional[int]
    mem_arity: int
    imm_arity: int
    queries: int = 0
    matched: list[int] = field(default_factory=list)
    first: list[int] = field(default_factory=list)
    overlaps: dict[tuple[int, int], int] = field(default_factory=dict)
    mutation_issues: dict[tuple[int, str], MutationIssue] = field(default_factory=dict)
    # Exact one-step state graph.  The key is the whole query, not a
    # (slot, type) projection: a mutation may read another type slot
    # (changeElementSizeTo(TypeIdx, FromTypeIdx) / scalarSameSizeAs), so a
    # per-slot projection can report cycles that cannot occur.
    transitions: dict[Query, set[Query]] = field(default_factory=dict)
    self_loops: dict[int, LLT] = field(default_factory=dict)
    samples: dict[int, Query] = field(default_factory=dict)
    # First ``ruleset.apply`` failure seen in D.  ``None`` means every query
    # could be evaluated; the message includes a sample query so mutation.run
    # can report a reproducible M006 at the ruleset span.
    apply_error: Optional[str] = None


def shape(info: LegalizerInfo, ruleset: RuleSet) -> tuple[Optional[int], int, int]:
    assert info.domain is not None
    queries = info.domain.q0_for(ruleset.opcodes)
    arity = max((len(q.types) for q in queries), default=None)
    mem_arity = max((len(q.mems) for q in queries), default=0)
    imm_arity = max((len(q.imms) for q in queries), default=0)
    return arity, mem_arity, imm_arity


def iter_queries(info: LegalizerInfo, ruleset: RuleSet):
    assert info.domain is not None
    domain = info.domain
    arity, mem_arity, imm_arity = shape(info, ruleset)
    if arity is None:
        return
    for types in product(domain.types, repeat=arity):
        for mems in product(domain.mems, repeat=mem_arity):
            for imms in product(domain.imms, repeat=imm_arity):
                query = Query(ruleset.opcodes[0], types, mems, imms)
                if not domain.accepts(query):
                    continue
                yield query


def _mutation_issue(rule: Rule, query: Query) -> Optional[tuple[str, str]]:
    if rule.action in MUTATING_ACTIONS:
        if rule.new_type is None:
            return ("M006", "mutating action has no mutation")
        try:
            new = rule.new_type.eval(query)
        except (DslError, ArithmeticError) as exc:
            return ("M006", f"mutation failed: {exc}")
        if not (0 <= rule.type_idx < len(query.types)):
            return ("M006", f"mutation type index t{rule.type_idx} is out of range")
        old = query.types[rule.type_idx]
        if new == old:
            return ("M006", f"mutation is a no-op: {old} -> {new}")
        if rule.action is Action.NARROW:
            if not new.get_scalar_size_in_bits() < old.get_scalar_size_in_bits():
                return ("M001", f"narrow did not shrink element size: {old} -> {new}")
        elif rule.action is Action.WIDEN:
            if not new.get_scalar_size_in_bits() > old.get_scalar_size_in_bits():
                return ("M002", f"widen did not grow element size: {old} -> {new}")
        elif rule.action is Action.BITCAST:
            if new.get_size_in_bits() != old.get_size_in_bits() or new == old:
                return ("M003", f"bitcast changed total size or type: {old} -> {new}")
        elif rule.action is Action.MORE:
            if new.num_elements <= old.num_elements or new.element_type() != old.element_type():
                return ("M004", f"more did not increase lanes: {old} -> {new}")
        elif rule.action is Action.FEWER:
            if new.num_elements >= old.num_elements or new.element_type() != old.element_type():
                return ("M005", f"fewer did not decrease lanes: {old} -> {new}")
    elif rule.new_type is not None:
        return ("M007", "non-mutating action carries a mutation")
    return None


def analyze_ruleset(info: LegalizerInfo, ruleset: RuleSet) -> RuleSetAnalysis:
    arity, mem_arity, imm_arity = shape(info, ruleset)
    result = RuleSetAnalysis(
        opcodes=ruleset.opcodes,
        arity=arity,
        mem_arity=mem_arity,
        imm_arity=imm_arity,
        matched=[0] * len(ruleset.rules),
        first=[0] * len(ruleset.rules),
    )
    if arity is None:
        return result

    for query in iter_queries(info, ruleset):
        result.queries += 1
        # ``apply`` short-circuits at the first match.  Reachability and
        # overlap need every rule's predicate evaluated independently.
        try:
            step, _ = ruleset.apply(query)
        except (DslError, ArithmeticError) as exc:
            if result.apply_error is None:
                result.apply_error = f"{exc} (sample query: {query})"
            continue
        matched_indices: list[int] = []
        for index, rule in enumerate(ruleset.rules):
            if not rule.pred.eval(query):
                continue
            matched_indices.append(index)
            result.matched[index] += 1
            result.samples.setdefault(index, query)
            if index == step.rule_index:
                result.first[index] += 1
            issue = _mutation_issue(rule, query)
            if issue is not None:
                code, message = issue
                result.mutation_issues.setdefault(
                    (index, code),
                    MutationIssue(index, code, message, query),
                )
        for a_pos, i in enumerate(matched_indices):
            for j in matched_indices[a_pos + 1:]:
                result.overlaps[(i, j)] = result.overlaps.get((i, j), 0) + 1
        if step.new_type is not None and 0 <= step.type_idx < len(query.types):
            old = query.types[step.type_idx]
            if step.new_type == old:
                result.self_loops.setdefault(step.rule_index if step.rule_index is not None else -1, old)
            types = list(query.types)
            types[step.type_idx] = step.new_type
            successor = Query(query.opcode, tuple(types), query.mems, query.imms)
            result.transitions.setdefault(query, set()).add(successor)
    return result


def analyze_all(info: LegalizerInfo) -> dict[str, RuleSetAnalysis]:
    return {ruleset.opcodes[0]: analyze_ruleset(info, ruleset) for ruleset in info.rulesets}
