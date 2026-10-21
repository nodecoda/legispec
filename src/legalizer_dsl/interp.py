# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Fixpoint interpreter for the Legalizer rule DSL.

One ``step`` mirrors ``LegalizeRuleSet::apply``.  ``legalize`` repeatedly
applies that step until a terminal action is reached, a state repeats, or the
budget is exhausted.  The interpreter deliberately does not model the MIR
rewrites performed later by LegalizerHelper.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Optional

from .checker.analysis import iter_queries
from .diagnostics import Diagnostic, Severity
from .model import Action, LegalizerInfo, Query, Step

TERMINAL_ACTIONS = {
    Action.LEGAL,
    Action.CUSTOM,
    Action.UNSUPPORTED,
    Action.LOWER,
    Action.LIBCALL,
}


@dataclass(frozen=True)
class TraceStep:
    query: Query
    step: Step


@dataclass
class Trace:
    start: Query
    steps: list[TraceStep] = field(default_factory=list)
    final: Optional[Query] = None
    status: str = "Budget"

    @property
    def terminal(self) -> bool:
        return self.status in {action.value for action in TERMINAL_ACTIONS}

    def __str__(self) -> str:
        lines = [f"Query: {self.start}"]
        for index, entry in enumerate(self.steps):
            lines.append(f"  [{index}] {entry.step}")
        lines.append(f"Status: {self.status} ({len(self.steps)} step(s))")
        return "\n".join(lines)


@dataclass
class FixpointReport:
    total: int = 0
    terminated: int = 0
    cycles: list[Trace] = field(default_factory=list)
    budget: list[Trace] = field(default_factory=list)
    stuck: list[Trace] = field(default_factory=list)
    # Traces whose successor left D.  These are not failures: the policy
    # claim only covers D, and a mutation may project the observed slot into
    # a shape the opcode contract excludes (G_CONCAT_VECTORS bitcast does
    # exactly this).  They are kept for reporting, not counted as stuck.
    out_of_domain: list[Trace] = field(default_factory=list)
    max_steps_seen: int = 0

    @property
    def ok(self) -> bool:
        return not self.cycles and not self.budget and not self.stuck


def legalize(info: LegalizerInfo, query: Query, max_steps: int = 64) -> Trace:
    if max_steps <= 0:
        raise ValueError("max_steps must be positive")
    start = query
    seen: set[tuple[str, tuple, tuple, tuple]] = set()
    steps: list[TraceStep] = []
    current = query
    for _ in range(max_steps):
        if info.domain is not None and not info.domain.accepts(current):
            return Trace(start, steps, current, "OutOfDomain")
        key = (current.opcode, current.types, current.mems, current.imms)
        if key in seen:
            return Trace(start, steps, current, "Cycle")
        seen.add(key)
        step, _ = info.action(current)
        steps.append(TraceStep(current, step))
        if step.action in TERMINAL_ACTIONS:
            return Trace(start, steps, current, step.action.value)
        if step.new_type is None:
            return Trace(start, steps, current, "Stuck")
        types = list(current.types)
        if step.type_idx >= len(types):
            return Trace(start, steps, current, "Stuck")
        types[step.type_idx] = step.new_type
        current = Query(current.opcode, tuple(types), current.mems, current.imms)
    return Trace(start, steps, current, "Budget")


def iter_domain(info: LegalizerInfo) -> Iterator[Query]:
    if info.domain is None:
        return
    for ruleset in info.rulesets:
        yield from iter_queries(info, ruleset)


def verify_domain(info: LegalizerInfo, max_steps: int = 64) -> FixpointReport:
    report = FixpointReport()
    for query in iter_domain(info):
        report.total += 1
        trace = legalize(info, query, max_steps=max_steps)
        report.max_steps_seen = max(report.max_steps_seen, len(trace.steps))
        if trace.terminal:
            report.terminated += 1
        elif trace.status == "OutOfDomain":
            report.out_of_domain.append(trace)
        elif trace.status == "Cycle":
            report.cycles.append(trace)
        elif trace.status == "Budget":
            report.budget.append(trace)
        else:
            report.stuck.append(trace)
    return report


def fixpoint_diagnostics(info: LegalizerInfo, max_steps: int = 64) -> list[Diagnostic]:
    report = verify_domain(info, max_steps=max_steps)
    diagnostics: list[Diagnostic] = []
    for trace in report.cycles[:20]:
        diagnostics.append(
            Diagnostic("T002", Severity.ERROR, f"fixpoint cycle from {trace.start}", None)
        )
    for trace in report.budget[:20]:
        diagnostics.append(
            Diagnostic(
                "T004",
                Severity.UNKNOWN,
                f"fixpoint budget {max_steps} exhausted from {trace.start}",
                None,
            )
        )
    for trace in report.stuck[:20]:
        diagnostics.append(
            Diagnostic("T003", Severity.UNKNOWN, f"fixpoint stuck from {trace.start}", None)
        )
    if report.out_of_domain:
        diagnostics.append(
            Diagnostic(
                "T005",
                Severity.INFO,
                f"fixpoint boundary: {len(report.out_of_domain)} trace(s) left D; "
                "the successor shape is outside the declared opcode contract",
                None,
            )
        )
    return diagnostics
