# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Diagnostics: source spans, severities, and the DSL error type.

Contract (spec v0.1 §3.1):

    file:line:col[-end]: SEVERITY: CODE: message

Every parser error carries a code and a span.  Value-level errors (for example
``LLT.scalar(0)``) may carry a code without a span; callers that know a token
must re-raise them with ``raise DslError(exc.message, code=..., span=...)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Severity(Enum):
    ERROR = "ERROR"
    WARN = "WARN"
    INFO = "INFO"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, order=True)
class Span:
    """Inclusive source range.  A zero line means "unknown"."""

    start_line: int
    start_col: int
    end_line: int
    end_col: int

    @staticmethod
    def point(line: int, col: int) -> "Span":
        return Span(line, col, line, col)

    @staticmethod
    def unknown() -> "Span":
        return Span(0, 0, 0, 0)

    def is_known(self) -> bool:
        return self.start_line > 0

    def __str__(self) -> str:
        if not self.is_known():
            return "<unknown>"
        if self.start_line == self.end_line:
            return f"{self.start_line}:{self.start_col}-{self.end_col}"
        return f"{self.start_line}:{self.start_col}-{self.end_line}:{self.end_col}"


class DslError(Exception):
    """A DSL error with a stable code and an optional source span."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "E000",
        span: Optional[Span] = None,
    ) -> None:
        self.message = message
        self.code = code
        self.span = span
        super().__init__(self.format())

    def format(self) -> str:
        loc = f"{self.span}: " if self.span and self.span.is_known() else ""
        return f"{loc}ERROR: {self.code}: {self.message}"


@dataclass(frozen=True)
class Diagnostic:
    """A non-fatal finding produced by the static checker."""

    code: str
    severity: Severity
    message: str
    span: Optional[Span] = None

    def format(self, path: Optional[str] = None) -> str:
        loc = f"{path}:" if path else ""
        if self.span and self.span.is_known():
            loc += f"{self.span}: "
        return f"{loc}{self.severity.value}: {self.code}: {self.message}"