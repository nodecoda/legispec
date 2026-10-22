# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Single-step termination checks (T)."""

from __future__ import annotations

from ..diagnostics import Diagnostic, Severity
from ..model import LegalizerInfo
from .analysis import RuleSetAnalysis


def _edge_label(source, target) -> str:
    for idx, (old, new) in enumerate(zip(source.types, target.types)):
        if old != new:
            return f"t{idx}:{old}"
    return "t?:" + str(source.types)


def _find_cycle(transitions: dict[object, set[object]]):
    colour: dict[object, int] = {}
    stack: list[object] = []

    def visit(node):
        colour[node] = 1
        stack.append(node)
        for nxt in transitions.get(node, ()):
            child = nxt
            state = colour.get(child, 0)
            if state == 0:
                found = visit(child)
                if found:
                    return found
            elif state == 1:
                start = stack.index(child)
                return stack[start:] + [child]
        stack.pop()
        colour[node] = 2
        return None

    for node in list(transitions):
        if colour.get(node, 0) == 0:
            found = visit(node)
            if found:
                return found
    return None


def run(info: LegalizerInfo, analyses: dict[str, RuleSetAnalysis]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for ruleset in info.rulesets:
        analysis = analyses[ruleset.opcodes[0]]
        for rule_index, old in sorted(analysis.self_loops.items()):
            if rule_index < 0:
                continue
            rule = ruleset.rules[rule_index]
            diagnostics.append(
                Diagnostic(
                    "T001",
                    Severity.ERROR,
                    f"{', '.join(ruleset.opcodes)} rule {rule_index}: self-loop on {old}",
                    rule.span,
                )
            )
        cycle = _find_cycle(analysis.transitions)
        if cycle is not None:
            path = " -> ".join(
                _edge_label(cycle[i], cycle[i + 1]) for i in range(len(cycle) - 1)
            )
            diagnostics.append(
                Diagnostic(
                    "T002",
                    Severity.ERROR,
                    f"{', '.join(ruleset.opcodes)}: type cycle in D: {path}",
                    ruleset.span,
                )
            )
    return diagnostics
