# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Overlap/conflict checks (K)."""

from __future__ import annotations

from ..diagnostics import Diagnostic, Severity
from ..model import LegalizerInfo
from .analysis import RuleSetAnalysis


def run(info: LegalizerInfo, analyses: dict[str, RuleSetAnalysis]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for ruleset in info.rulesets:
        analysis = analyses[ruleset.opcodes[0]]
        for (i, j), count in sorted(analysis.overlaps.items()):
            left = ruleset.rules[i]
            right = ruleset.rules[j]
            diagnostics.append(
                Diagnostic(
                    "K001",
                    Severity.WARN,
                    f"{', '.join(ruleset.opcodes)} rules {i}/{j}: both match {count} query/queries in D",
                    ruleset.span,
                )
            )
            if (left.action, str(left.new_type)) != (right.action, str(right.new_type)):
                diagnostics.append(
                    Diagnostic(
                        "K002",
                        Severity.WARN,
                        f"{', '.join(ruleset.opcodes)} rules {i}/{j}: overlap is order-sensitive "
                        f"({left.action.value} vs {right.action.value})",
                        ruleset.span,
                    )
                )
    return diagnostics
