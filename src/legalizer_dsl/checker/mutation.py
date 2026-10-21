# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Mutation soundness checks (M)."""

from __future__ import annotations

from ..diagnostics import Diagnostic, Severity
from ..model import LegalizerInfo
from .analysis import RuleSetAnalysis


def run(info: LegalizerInfo, analyses: dict[str, RuleSetAnalysis]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for ruleset in info.rulesets:
        analysis = analyses[ruleset.opcodes[0]]
        if analysis.apply_error is not None:
            diagnostics.append(
                Diagnostic(
                    "M006",
                    Severity.ERROR,
                    f"{', '.join(ruleset.opcodes)}: rule evaluation failed: "
                    f"{analysis.apply_error}",
                    ruleset.span,
                )
            )
        for (rule_index, code), issue in sorted(analysis.mutation_issues.items()):
            rule = ruleset.rules[rule_index]
            diagnostics.append(
                Diagnostic(
                    code,
                    Severity.ERROR,
                    f"{', '.join(ruleset.opcodes)} rule {rule_index}: {issue.message} "
                    f"(sample: {issue.query})",
                    rule.span,
                )
            )
    return diagnostics
