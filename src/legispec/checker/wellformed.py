# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Well-formedness checks (W)."""

from __future__ import annotations

from ..coverage import imm_read_set, type_read_set
from ..diagnostics import Diagnostic, Severity
from ..model import Action, LegalizerInfo, MUTATING_ACTIONS
from .analysis import RuleSetAnalysis, shape


def _mutation_indices(rule) -> set[int]:
    indices = set()
    if rule.new_type is not None:
        idx = getattr(rule.new_type, "idx", None)
        if idx is not None:
            indices.add(idx)
        src_idx = getattr(rule.new_type, "src_idx", None)
        if src_idx is not None:
            indices.add(src_idx)
    return indices


def run(info: LegalizerInfo, analyses: dict[str, RuleSetAnalysis]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    assert info.domain is not None
    for ruleset in info.rulesets:
        arity, _mem_arity, imm_arity = shape(info, ruleset)
        if arity is None:
            continue  # coverage reports C000 UNKNOWN
        if not ruleset.rules:
            diagnostics.append(
                Diagnostic("W005", Severity.WARN, f"{', '.join(ruleset.opcodes)} has no rules", ruleset.span)
            )
            continue
        for index, rule in enumerate(ruleset.rules):
            type_indices = type_read_set(rule.pred) | _mutation_indices(rule)
            if rule.action in MUTATING_ACTIONS:
                type_indices.add(rule.type_idx)
            for type_idx in sorted(type_indices):
                if type_idx >= arity:
                    diagnostics.append(
                        Diagnostic(
                            "W001",
                            Severity.ERROR,
                            f"{', '.join(ruleset.opcodes)} rule {index}: type index t{type_idx} "
                            f"is outside Q0 arity {arity}",
                            rule.span,
                        )
                    )
            for imm_idx in sorted(imm_read_set(rule.pred)):
                if imm_idx >= imm_arity:
                    diagnostics.append(
                        Diagnostic(
                            "W002",
                            Severity.ERROR,
                            f"{', '.join(ruleset.opcodes)} rule {index}: immediate index {imm_idx} "
                            f"is outside Q0 arity {imm_arity}",
                            rule.span,
                        )
                    )
            if rule.action in MUTATING_ACTIONS and rule.new_type is None:
                diagnostics.append(
                    Diagnostic(
                        "W006",
                        Severity.ERROR,
                        f"{', '.join(ruleset.opcodes)} rule {index}: {rule.action.value} needs a mutation",
                        rule.span,
                    )
                )
            if rule.action not in MUTATING_ACTIONS and rule.new_type is not None:
                diagnostics.append(
                    Diagnostic(
                        "W007",
                        Severity.ERROR,
                        f"{', '.join(ruleset.opcodes)} rule {index}: {rule.action.value} must not carry a mutation",
                        rule.span,
                    )
                )
            mutation_idx = getattr(rule.new_type, "idx", None)
            if mutation_idx is not None and mutation_idx != rule.type_idx:
                diagnostics.append(
                    Diagnostic(
                        "W008",
                        Severity.ERROR,
                        f"{', '.join(ruleset.opcodes)} rule {index}: rule type index t{rule.type_idx} "
                        f"does not match mutation t{mutation_idx}",
                        rule.span,
                    )
                )
    return diagnostics
