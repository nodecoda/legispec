# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0.
# See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0
#
# ===----------------------------------------------------------------------===##

"""Rule reachability checks (R)."""

from __future__ import annotations

from typing import Iterable, Optional

from ..diagnostics import Diagnostic, Severity
from ..model import LegalizerInfo
from ..witness import Witness
from .analysis import RuleSetAnalysis


def _registered(
    info: LegalizerInfo,
    witnesses: Optional[Iterable[Witness]],
    attr: str,
) -> set[tuple[str, int]]:
    registered: set[tuple[str, int]] = set()
    if witnesses is None:
        return registered
    for witness in witnesses:
        index = getattr(witness, attr)
        if index is None:
            continue
        ruleset = info.by_opcode.get(witness.query.opcode)
        if ruleset is not None:
            registered.add((ruleset.opcodes[0], index))
    return registered


def run(
    info: LegalizerInfo,
    analyses: dict[str, RuleSetAnalysis],
    witnesses: Optional[Iterable[Witness]] = None,
    strict: bool = False,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    registered = _registered(info, witnesses, "unreachable")
    shadowed = _registered(info, witnesses, "shadowed")
    for ruleset in info.rulesets:
        analysis = analyses[ruleset.opcodes[0]]
        if analysis.arity is None:
            continue
        for index, rule in enumerate(ruleset.rules):
            key = (ruleset.opcodes[0], index)
            matched = analysis.matched[index]
            first = analysis.first[index]
            if matched == 0:
                if key in shadowed:
                    diagnostics.append(
                        Diagnostic(
                            "R003",
                            Severity.ERROR if strict else Severity.WARN,
                            f"{', '.join(ruleset.opcodes)} rule {index}: declared "
                            "shadowed but no query in D matches its predicate",
                            rule.span,
                        )
                    )
                elif key in registered:
                    diagnostics.append(
                        Diagnostic(
                            "R001",
                            Severity.INFO,
                            f"{', '.join(ruleset.opcodes)} rule {index}: declared unreachable in witness",
                            rule.span,
                        )
                    )
                else:
                    diagnostics.append(
                        Diagnostic(
                            "R001",
                            Severity.ERROR if strict else Severity.WARN,
                            f"{', '.join(ruleset.opcodes)} rule {index}: no query in D matches this rule",
                            rule.span,
                        )
                    )
            elif key in registered:
                diagnostics.append(
                    Diagnostic(
                        "R001",
                        Severity.ERROR,
                        f"{', '.join(ruleset.opcodes)} rule {index}: declared unreachable but "
                        f"matched {matched} query/queries in D",
                        rule.span,
                    )
                )
            elif key in shadowed:
                if first != 0:
                    diagnostics.append(
                        Diagnostic(
                            "R004",
                            Severity.ERROR,
                            f"{', '.join(ruleset.opcodes)} rule {index}: declared shadowed "
                            f"but won for {first} query/queries in D",
                            rule.span,
                        )
                    )
                else:
                    diagnostics.append(
                        Diagnostic(
                            "R005",
                            Severity.INFO,
                            f"{', '.join(ruleset.opcodes)} rule {index}: declared shadowed; "
                            "every match is preceded by an earlier rule",
                            rule.span,
                        )
                    )
            elif first == 0:
                diagnostics.append(
                    Diagnostic(
                        "R002",
                        Severity.WARN,
                        f"{', '.join(ruleset.opcodes)} rule {index}: every match is shadowed by an earlier rule",
                        rule.span,
                    )
                )
    return diagnostics
