# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Static checker pipeline for the Legalizer DSL."""

from __future__ import annotations

from typing import Iterable, Optional

from ..coverage import run_coverage
from ..diagnostics import Diagnostic, Severity
from ..model import LegalizerInfo
from ..witness import Witness
from .analysis import analyze_all
from . import conflict, mutation, reachability, termination, wellformed

__all__ = ["run_checks", "errors", "warnings", "unknowns"]


def errors(diagnostics: Iterable[Diagnostic]) -> list[Diagnostic]:
    return [d for d in diagnostics if d.severity is Severity.ERROR]


def warnings(diagnostics: Iterable[Diagnostic]) -> list[Diagnostic]:
    return [d for d in diagnostics if d.severity is Severity.WARN]


def unknowns(diagnostics: Iterable[Diagnostic]) -> list[Diagnostic]:
    return [d for d in diagnostics if d.severity is Severity.UNKNOWN]


def run_checks(
    info: LegalizerInfo,
    strict: bool = False,
    witnesses: Optional[Iterable[Witness]] = None,
) -> list[Diagnostic]:
    if info.domain is None:
        return [
            Diagnostic(
                "W014",
                Severity.ERROR,
                "no 'domain' declaration; coverage and reachability are undefined",
                None,
            )
        ]
    analyses = analyze_all(info)
    diagnostics: list[Diagnostic] = []
    diagnostics.extend(run_coverage(info).diagnostics)
    diagnostics.extend(wellformed.run(info, analyses))
    diagnostics.extend(mutation.run(info, analyses))
    diagnostics.extend(reachability.run(info, analyses, witnesses, strict))
    diagnostics.extend(conflict.run(info, analyses))
    diagnostics.extend(termination.run(info, analyses))
    return diagnostics
