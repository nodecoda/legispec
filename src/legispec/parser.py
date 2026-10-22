# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0.
# See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0
#
# ===----------------------------------------------------------------------===##

"""Lexer and recursive-descent parser for the Legalizer rule DSL.

The grammar is intentionally small.  It has two layers:

1. Core rules, one predicate and one action per line:
       legal when t0 == s32 and t1 == s32;
       custom when t0 in {v4s1} and t1 in {v4s32};
       narrow t0 to s32 when scalar(t0) and bits(t0) > 32;
       unsupported;

2. Macros that expand to one or more core rules, mirroring LLVM's
   LegalizeRuleSet builder methods:
       legal_for {s32, v4s32};
       legal_for_pairs {(s32, s32), (p0, p0)};
       custom_for {s64};
       custom_for_pairs {(v4s1, v4s32)};
       libcall_for {s32, s64};
       clamp_scalar 0, s32, s32;
       widen_to_next_pow2 0, 32;
       widen_to_next_multiple 0, 32;
       legal_for_mem {(s32, p0, s32, 1), ...};
       always_legal;

Macros accept an optional compile-time guard:
       legal_for {s32} when not is64bit;
       custom_for {s64} when is64bit;
A guard is evaluated before rule insertion; a false guard emits no rule.
It is not a query predicate and never appears in the rule table.

The parser is free-form: newlines are whitespace.  Statements end with ';',
blocks use braces, and comments start with '#' or '//'.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .diagnostics import Diagnostic, DslError, Severity, Span
from .model import (
    Action,
    Always,
    And,
    BitsCmp,
    ConcatBitcast,
    ConcatShape,
    ConcatShapeAny,
    CopyType,
    Domain,
    ElemBitsCmp,
    ElemTypesEq,
    EltSizeNotPow2,
    FloatIs,
    FloatValue,
    ElementOf,
    FixedVector,
    FixedVectorElemCountCmp,
    ImmCmp,
    IsMultipleBits,
    IsPow2Bits,
    IsPow2ElemBits,
    LanesCmp,
    LegalizerInfo,
    LLT,
    MemAlignCmp,
    MemCompatInSet,
    MemDesc,
    MemTypeIn,
    NextMultiple,
    NextPow2Lanes,
    NotPow2OrSmaller,
    NumElementsNotPow2,
    NextPow2,
    Not,
    Or,
    Pointer,
    PointerVector,
    Pred,
    Query,
    Rule,
    RuleExpansion,
    RuleSet,
    SameShape,
    SameSize,
    ScalarBitsCmp,
    ScalarSizeVsSizeCmp,
    Scalar,
    SetElementCount,
    SameSizeAs,
    SetElementSize,
    SetElementType,
    TypeEq,
    TypeIn,
    TypePairInSet,
    Vector,
    parse_llt,
)


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    line: int
    col: int

    @property
    def span(self) -> Span:
        width = max(len(self.value) - 1, 0)
        return Span(self.line, self.col, self.line, self.col + width)

    def __str__(self) -> str:
        return f"{self.value!r} at {self.line}:{self.col}"


def _err(code: str, token: Optional[Token], message: str) -> DslError:
    return DslError(message, code=code, span=token.span if token else None)


def _config_cmp(op: str, lhs: int, rhs: int) -> bool:
    if op == "==":
        return lhs == rhs
    if op == "!=":
        return lhs != rhs
    if op == "<":
        return lhs < rhs
    if op == "<=":
        return lhs <= rhs
    if op == ">":
        return lhs > rhs
    if op == ">=":
        return lhs >= rhs
    raise AssertionError(f"unhandled comparison {op!r}")


_TOKEN_RE = re.compile(
    r"""
      (?P<WS>[ \t\r\n]+)
    | (?P<COMMENT>\#[^\n]*|//[^\n]*)
    | (?P<INT>-?[0-9]+)
    | (?P<LLT>v[0-9]+(?:s[0-9]+|p[0-9]+(?::[0-9]+)?)|p[0-9]+(?::[0-9]+)?|s[0-9]+)(?![A-Za-z0-9_:])
    | (?P<IDENT>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<OP><=|>=|==|!=|[{}()\[\],;=.:<>])
    """,
    re.VERBOSE,
)


def lex(text: str) -> list[Token]:
    # A UTF-8 BOM is invisible in editors and must not shift columns.
    if text.startswith("\ufeff"):
        text = text[1:]
    tokens: list[Token] = []
    pos = 0
    line = 1
    col = 1
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise DslError(f"unexpected character {text[pos]!r}", code="L001", span=Span.point(line, col))
        kind = m.lastgroup
        value = m.group()
        if kind not in ("WS", "COMMENT"):
            tokens.append(Token(kind, value, line, col))
        newlines = value.count("\n")
        if newlines:
            line += newlines
            col = len(value.rsplit("\n", 1)[1]) + 1
        else:
            col += len(value)
        pos = m.end()
    tokens.append(Token("EOF", "", line, col))
    return tokens


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


CORE_ACTIONS: dict[str, tuple[Action, bool, bool]] = {
    # name: (action, needs type mutation, mutation is element count)
    "legal": (Action.LEGAL, False, False),
    "custom": (Action.CUSTOM, False, False),
    "unsupported": (Action.UNSUPPORTED, False, False),
    "libcall": (Action.LIBCALL, False, False),
    "lower": (Action.LOWER, False, False),
    "narrow": (Action.NARROW, True, False),
    "widen": (Action.WIDEN, True, False),
    "bitcast": (Action.BITCAST, True, False),
    "more": (Action.MORE, True, True),
    "fewer": (Action.FEWER, True, True),
}

MACROS = {
    "legal_for",
    "legal_for_pairs",
    "libcall_for_pairs",
    "custom_for",
    "custom_for_pairs",
    "libcall_for",
    "lower_for",
    "unsupported_for",
    "clamp_scalar",
    "clamp_num_elements",
    "clamp_min_num_elements",
    "clamp_max_num_elements",
    "min_scalar_or_elt",
    "min_scalar_or_elt_if",
    "widen_scalar_or_elt_to_next_pow2",
    "min_scalar_same_as",
    "min_scalar_elt_same_as_if",
    "max_scalar_elt_same_as_if",
    "more_elements_to_next_pow2",
    "widen_to_next_pow2",
    "widen_to_next_pow2_or_min",
    "widen_to_next_multiple",
    "legal_for_mem",
    "always_legal",
}

CMP_OPS = {"==", "!=", "<", "<=", ">", ">="}


class Parser:
    def __init__(
        self,
        tokens: list[Token],
        config: Optional[dict[str, object]] = None,
    ):
        self.tokens = tokens
        self.pos = 0
        self.config = dict(config or {})
        self.last_macro_guard: Optional[bool] = None
        # LLT-valued configuration entries participate in type parsing.
        self.aliases: dict[str, LLT] = {
            name: value
            for name, value in self.config.items()
            if isinstance(value, LLT)
        }

    # -- token helpers ------------------------------------------------------

    def peek(self, offset: int = 0) -> Token:
        index = min(self.pos + offset, len(self.tokens) - 1)
        return self.tokens[index]

    def advance(self) -> Token:
        token = self.peek()
        if token.kind != "EOF":
            self.pos += 1
        return token

    def accept(self, value: str) -> bool:
        if self.peek().value == value:
            self.advance()
            return True
        return False

    def accept_ident(self, value: str) -> bool:
        token = self.peek()
        if token.kind == "IDENT" and token.value == value:
            self.advance()
            return True
        return False

    def expect(self, value: str) -> Token:
        token = self.peek()
        if token.value != value:
            raise _err("P010", token, f"expected {value!r}, got {token.value!r}")
        return self.advance()

    def expect_ident(self, value: Optional[str] = None) -> str:
        token = self.peek()
        if token.kind != "IDENT":
            raise _err("P011", token, f"expected identifier, got {token.value!r}")
        if value is not None and token.value != value:
            raise _err("P010", token, f"expected {value!r}, got {token.value!r}")
        self.advance()
        return token.value

    def expect_int(self) -> int:
        token = self.peek()
        if token.kind != "INT":
            raise _err("P012", token, f"expected integer, got {token.value!r}")
        self.advance()
        return int(token.value)

    def expect_index(self) -> int:
        """Read a slot index and reject negative values at parse time."""
        token = self.peek()
        value = self.expect_int()
        if value < 0:
            raise _err(
                "P037", token, f"slot index must be non-negative, got {value}"
            )
        return value

    def parse_bit_count(self) -> int:
        """Bit-count operand: an integer literal or a type-valued config alias.

        `bits(t0) < sXLen` is the DSL spelling of `minScalar(0, sXLen)`.
        sXLen is a construction-time constant, so the comparison folds to an
        integer here; this does not add query-time configuration.
        """
        token = self.peek()
        if token.kind == "INT":
            self.advance()
            return int(token.value)
        if token.kind == "IDENT" and token.value in self.aliases:
            return self.parse_type().get_scalar_size_in_bits()
        return self.expect_int()

    # -- program ------------------------------------------------------------

    def parse_program(self) -> LegalizerInfo:
        info = LegalizerInfo()
        while self.peek().kind != "EOF":
            if self.accept_ident("type"):
                self.parse_alias()
            elif self.peek().kind == "IDENT" and self.peek().value == "domain":
                start = self.advance()
                if info.domain is not None:
                    raise _err("P020", start, "duplicate 'domain' block")
                info.domain = self.parse_domain(start)
            elif self.peek().kind == "IDENT" and self.peek().value == "ruleset":
                start = self.advance()
                info.add(self.parse_ruleset(start))
            else:
                token = self.peek()
                raise _err(
                    "P001",
                    token,
                    f"expected 'type', 'domain', or 'ruleset', got {token.value!r}",
                )
        return info

    def parse_alias(self) -> None:
        token = self.peek()
        if token.kind not in ("IDENT", "LLT"):
            raise _err("P011", token, f"expected identifier, got {token.value!r}")
        name = self.advance().value
        shadows_llt = token.kind == "LLT" or re.fullmatch(r"[spv]\d+.*", name)
        if not shadows_llt:
            try:
                parse_llt(name)
            except DslError:
                pass
            else:
                # Names such as ``f32`` and ``bf16`` are IDENT tokens but are
                # still built-in LLT spellings, so an alias would be ambiguous.
                shadows_llt = True
        if shadows_llt:
            raise _err(
                "W010", token, f"type alias {name!r} shadows a built-in LLT spelling"
            )
        self.expect("=")
        ty = self.parse_type()
        self.expect(";")
        if name in self.aliases:
            raise _err("W011", token, f"duplicate type alias {name!r}")
        self.aliases[name] = ty

    def parse_domain(self, start: Token) -> Domain:
        self.expect("{")
        seen: set[str] = set()
        types: tuple[LLT, ...] = ()
        imms: tuple[int, ...] = ()
        mems: tuple[MemDesc, ...] = ()
        q0: tuple[Query, ...] = ()
        domain_filter: Optional[Pred] = None
        while not self.accept("}"):
            token = self.peek()
            if token.kind != "IDENT" or token.value not in {
                "types",
                "imms",
                "mems",
                "q0",
                "filter",
            }:
                raise _err(
                    "P019",
                    token,
                    "expected domain item 'types', 'imms', 'mems', 'q0', "
                    f"or 'filter', got {token.value!r}",
                )
            item = token.value
            self.advance()
            if item in seen:
                raise _err("P021", token, f"duplicate domain item {item!r}")
            seen.add(item)
            if item == "types":
                types = self.parse_domain_types()
            elif item == "imms":
                imms = self.parse_domain_imms()
            elif item == "mems":
                mems = self.parse_domain_mems()
            elif item == "filter":
                domain_filter = self.parse_predicate()
                self.expect(";")
            else:
                q0 = self.parse_domain_q0()
        close = self.tokens[self.pos - 1]
        return Domain(
            types=types,
            imms=imms,
            mems=mems,
            q0=q0,
            filter=domain_filter,
            span=Span(start.line, start.col, close.line, close.col),
        )

    def parse_domain_types(self) -> tuple[LLT, ...]:
        self.expect("{")
        types: list[LLT] = []
        if not self.accept("}"):
            types.append(self.parse_type())
            while self.accept(","):
                types.append(self.parse_type())
            self.expect("}")
        self.expect(";")
        return tuple(types)

    def parse_domain_imms(self) -> tuple[int, ...]:
        self.expect("{")
        imms: list[int] = []
        if not self.accept("}"):
            imms.append(self.expect_int())
            while self.accept(","):
                imms.append(self.expect_int())
            self.expect("}")
        self.expect(";")
        return tuple(imms)

    def parse_domain_mems(self) -> tuple[MemDesc, ...]:
        self.expect("{")
        mems: list[MemDesc] = []
        if not self.accept("}"):
            mems.append(self.parse_mem_desc())
            while self.accept(","):
                mems.append(self.parse_mem_desc())
            self.expect("}")
        self.expect(";")
        return tuple(mems)

    def parse_mem_desc(self) -> MemDesc:
        ty = self.parse_type()
        self.expect(":")
        align = self.expect_int()
        if align <= 0:
            raise _err("P023", self.tokens[self.pos - 1], "memory alignment must be positive")
        return MemDesc(ty, align)

    def parse_domain_q0(self) -> tuple[Query, ...]:
        self.expect("{")
        queries: list[Query] = []
        while not self.accept("}"):
            if self.peek().kind == "EOF":
                raise _err("P024", self.peek(), "unterminated q0 block; expected '}'")
            queries.append(self.parse_query_decl())
        return tuple(queries)

    def parse_query_decl(self) -> Query:
        self.expect("(")
        opcode = self.expect_ident()
        self.expect(",")
        self.expect("[")
        types: list[LLT] = []
        if not self.accept("]"):
            types.append(self.parse_type())
            while self.accept(","):
                types.append(self.parse_type())
            self.expect("]")
        mems: list[MemDesc] = []
        imms: list[int] = []
        while self.accept(","):
            field = self.expect_ident()
            if field == "mem":
                self.expect("=")
                self.expect("[")
                if not self.accept("]"):
                    mems.append(self.parse_mem_desc())
                    while self.accept(","):
                        mems.append(self.parse_mem_desc())
                    self.expect("]")
            elif field == "imm":
                self.expect("=")
                self.expect("[")
                if not self.accept("]"):
                    imms.append(self.expect_int())
                    while self.accept(","):
                        imms.append(self.expect_int())
                    self.expect("]")
            else:
                raise _err(
                    "P022",
                    self.tokens[self.pos - 1],
                    f"expected 'mem' or 'imm' in q0 query, got {field!r}",
                )
        self.expect(")")
        self.expect(";")
        return Query(opcode, tuple(types), tuple(mems), tuple(imms))

    def parse_ruleset(self, start: Token) -> RuleSet:
        opcodes = [self.expect_ident()]
        while self.accept(","):
            opcodes.append(self.expect_ident())
        self.expect("{")
        ruleset = RuleSet(opcodes)
        while not self.accept("}"):
            if self.peek().kind == "EOF":
                raise _err("P002", self.peek(), "unterminated ruleset; expected '}'")
            self.parse_statement(ruleset)
        close = self.tokens[self.pos - 1]
        ruleset.span = Span(start.line, start.col, close.line, close.col)
        return ruleset

    # -- statements ---------------------------------------------------------

    def parse_statement(self, ruleset: RuleSet) -> None:
        token = self.peek()
        if token.kind != "IDENT":
            raise _err(
                "P003", token, f"expected rule or macro, got {token.value!r}"
            )
        name = token.value
        before = len(ruleset.rules)
        guard: Optional[bool] = None
        if name in CORE_ACTIONS:
            ruleset.add(self.parse_core_rule())
        elif name in MACROS:
            self.last_macro_guard = None
            self.parse_macro(ruleset)
            guard = self.last_macro_guard
        else:
            raise _err("P004", token, f"unknown rule or macro {name!r}")
        end = self.tokens[self.pos - 1]
        span = Span(
            token.line, token.col, end.line, end.col + max(len(end.value) - 1, 0)
        )
        for rule in ruleset.rules[before:]:
            rule.span = span
        ruleset.expansions.append(
            RuleExpansion(
                name=name,
                guard=guard,
                first_rule=before,
                rule_count=len(ruleset.rules) - before,
                span=span,
            )
        )

    def parse_core_rule(self) -> Rule:
        name = self.expect_ident()
        action, needs_mutation, count_mutation = CORE_ACTIONS[name]
        type_idx = 0
        new_type = None
        if needs_mutation:
            type_idx = self.parse_type_idx()
            self.expect("to")
            if count_mutation:
                new_type = self.parse_count_mutation(type_idx)
            else:
                new_type = self.parse_mutation(type_idx)
        pred: Pred = Always()
        if self.accept_ident("when"):
            pred = self.parse_predicate()
        self.expect(";")
        return Rule(action, pred, type_idx, new_type, origin="core")

    # -- macros -------------------------------------------------------------

    def parse_macro(self, ruleset: RuleSet) -> None:
        name = self.expect_ident()

        def add(rules: list[Rule]) -> None:
            guard = self.parse_guard_if_present()
            self.last_macro_guard = guard
            self.expect(";")
            if not guard:
                return
            for rule in rules:
                ruleset.add(rule)

        if name in (
            "legal_for",
            "libcall_for",
            "custom_for",
            "lower_for",
            "unsupported_for",
        ):
            types = self.parse_type_set()
            action = {
                "legal_for": Action.LEGAL,
                "libcall_for": Action.LIBCALL,
                "custom_for": Action.CUSTOM,
                "lower_for": Action.LOWER,
                "unsupported_for": Action.UNSUPPORTED,
            }[name]
            add(
                [
                    Rule(
                        action,
                        TypeIn(0, types),
                        origin=f"{name} {set_text(types)}",
                    )
                ]
            )
            return

        if name in ("legal_for_pairs", "custom_for_pairs", "libcall_for_pairs"):
            pairs = self.parse_pair_set()
            action = {
                "legal_for_pairs": Action.LEGAL,
                "custom_for_pairs": Action.CUSTOM,
                "libcall_for_pairs": Action.LIBCALL,
            }[name]
            add(
                [
                    Rule(
                        action,
                        TypePairInSet(0, 1, pairs),
                        origin=f"{name} {pair_set_text(pairs)}",
                    )
                ]
            )
            return

        if name == "clamp_scalar":
            idx = self.parse_type_idx()
            self.expect(",")
            min_ty = self.parse_type()
            self.expect(",")
            max_ty = self.parse_type()
            min_bits = min_ty.get_scalar_size_in_bits()
            max_bits = max_ty.get_scalar_size_in_bits()
            # LLVM: clampScalar = minScalar(...).maxScalar(...)
            add(
                [
                    Rule(
                        Action.WIDEN,
                        And(Scalar(idx), BitsCmp(idx, "<", min_bits)),
                        idx,
                        SetElementSize(idx, min_bits, str(min_ty)),
                        origin=f"clamp_scalar({idx}, {min_ty}, {max_ty}) step 1/2 (min)",
                    ),
                    Rule(
                        Action.NARROW,
                        And(Scalar(idx), BitsCmp(idx, ">", max_bits)),
                        idx,
                        SetElementSize(idx, max_bits, str(max_ty)),
                        origin=f"clamp_scalar({idx}, {min_ty}, {max_ty}) step 2/2 (max)",
                    ),
                ]
            )
            return

        if name in (
            "clamp_min_num_elements",
            "clamp_max_num_elements",
            "clamp_num_elements",
        ):
            idx = self.parse_type_idx()
            self.expect(",")

            if name == "clamp_num_elements":
                min_ty = self.parse_type()
                self.expect(",")
                max_ty = self.parse_type()
                if not min_ty.is_vector or not max_ty.is_vector:
                    raise _err(
                        "P029",
                        self.tokens[self.pos - 1],
                        "clamp_num_elements bounds must be vector types",
                    )
                if min_ty.elem != max_ty.elem:
                    raise _err(
                        "P030",
                        self.tokens[self.pos - 1],
                        "clamp_num_elements bounds must have the same element type",
                    )
                if min_ty.lanes < 2 or max_ty.lanes < 2:
                    raise _err(
                        "P031",
                        self.tokens[self.pos - 1],
                        "clamp_num_elements bounds must have at least 2 lanes",
                    )
                elt = min_ty.elem
                add(
                    [
                        Rule(
                            Action.MORE,
                            FixedVectorElemCountCmp(
                                idx, elt, "<", min_ty.lanes
                            ),
                            idx,
                            SetElementCount(idx, min_ty.lanes, str(min_ty)),
                            origin=(
                                f"clamp_num_elements({idx}, {min_ty}, "
                                f"{max_ty}) step 1/2 (min)"
                            ),
                        ),
                        Rule(
                            Action.FEWER,
                            FixedVectorElemCountCmp(
                                idx, elt, ">", max_ty.lanes
                            ),
                            idx,
                            SetElementCount(idx, max_ty.lanes, str(max_ty)),
                            origin=(
                                f"clamp_num_elements({idx}, {min_ty}, "
                                f"{max_ty}) step 2/2 (max)"
                            ),
                        ),
                    ]
                )
                return

            elt = self.parse_type()
            self.expect(",")
            count = self.expect_int()
            if elt.is_vector:
                raise _err(
                    "P032",
                    self.tokens[self.pos - 1],
                    f"{name} takes a scalar or pointer element type",
                )
            if count < 2:
                raise _err(
                    "P033",
                    self.tokens[self.pos - 1],
                    f"{name} count must be at least 2 (LLVM v1 is a scalar)",
                )
            is_min = name == "clamp_min_num_elements"
            action = Action.MORE if is_min else Action.FEWER
            op = "<" if is_min else ">"
            add(
                [
                    Rule(
                        action,
                        FixedVectorElemCountCmp(idx, elt, op, count),
                        idx,
                        SetElementCount(idx, count, f"v{count}{elt}"),
                        origin=(
                            f"clamp{'Min' if is_min else 'Max'}NumElements"
                            f"({idx}, {elt}, {count})"
                        ),
                    )
                ]
            )
            return

        if name == "min_scalar_same_as":
            idx = self.parse_type_idx()
            self.expect(",")
            large = self.parse_type_idx()
            if large == idx:
                raise _err(
                    "P034",
                    self.tokens[self.pos - 1],
                    "min_scalar_same_as needs two different type indices",
                )
            add(
                [
                    Rule(
                        Action.WIDEN,
                        ScalarSizeVsSizeCmp(large, idx, ">"),
                        idx,
                        SameSizeAs(idx, large),
                        origin=f"minScalarSameAs({idx}, {large})",
                    )
                ]
            )
            return

        if name in ("min_scalar_elt_same_as_if", "max_scalar_elt_same_as_if"):
            idx = self.parse_type_idx()
            self.expect(",")
            other = self.parse_type_idx()
            if other == idx:
                raise _err(
                    "P034",
                    self.tokens[self.pos - 1],
                    f"{name} needs two different type indices",
                )
            self.expect_ident("when")
            guard = self.parse_predicate()
            self.expect(";")
            if name == "min_scalar_elt_same_as_if":
                rule = Rule(
                    Action.WIDEN,
                    And(ScalarBitsCmp(other, idx, ">"), guard),
                    idx,
                    SameSizeAs(idx, other),
                    origin=f"minScalarEltSameAsIf({idx}, {other})",
                )
            else:
                rule = Rule(
                    Action.NARROW,
                    And(ScalarBitsCmp(other, idx, "<"), guard),
                    idx,
                    CopyType(idx, other),
                    origin=f"maxScalarEltSameAsIf({idx}, {other})",
                )
            # This macro expands unconditionally; record that explicitly so
            # the structure manifest cannot mistake it for an unguarded core
            # rule (guard=None means "no macro expansion metadata").
            self.last_macro_guard = True
            ruleset.add(rule)
            return

        if name == "min_scalar_or_elt_if":
            # Two source forms:
            #   minScalarOrEltIf(Predicate, TypeIdx, Ty)   -> predicate first
            #   minScalarOrEltIf(TypeIdx, Ty)              (always) is the
            #                                                minScalarOrElt macro
            # The legacy lanes form is kept for the G_MUL/G_ADD slices:
            #   min_scalar_or_elt_if N, lanes <cmp> K, elt_ty;
            if self._looks_like_lanes_form():
                idx = self.parse_type_idx()
                self.expect(",")
                self.expect_ident("lanes")
                op = self.parse_cmp_op()
                count = self.expect_int()
                self.expect(",")
                ty = self.parse_type()
                guard: Pred = LanesCmp(idx, op, count)
                origin = f"minScalarOrEltIf(lanes({idx}) {op} {count}, {idx}, {ty})"
            else:
                guard = self.parse_predicate()
                self.expect(",")
                idx = self.parse_type_idx()
                self.expect(",")
                ty = self.parse_type()
                origin = f"minScalarOrEltIf({guard}, {idx}, {ty})"
            bits = ty.get_scalar_size_in_bits()
            add(
                [
                    Rule(
                        Action.WIDEN,
                        And(guard, ElemBitsCmp(idx, "<", bits)),
                        idx,
                        SetElementSize(idx, bits, str(ty)),
                        origin=origin,
                    )
                ]
            )
            return

        if name == "min_scalar_or_elt":
            idx = self.parse_type_idx()
            self.expect(",")
            ty = self.parse_type()
            bits = ty.get_scalar_size_in_bits()
            add(
                [
                    Rule(
                        Action.WIDEN,
                        ElemBitsCmp(idx, "<", bits),
                        idx,
                        SetElementSize(idx, bits, str(ty)),
                        origin=f"minScalarOrElt({idx}, {ty})",
                    )
                ]
            )
            return

        if name == "widen_scalar_or_elt_to_next_pow2":
            idx = self.parse_type_idx()
            min_bits = 0
            if self.accept(","):
                min_bits = self.expect_int()
            add(
                [
                    Rule(
                        Action.WIDEN,
                        EltSizeNotPow2(idx),
                        idx,
                        NextPow2(idx, min_bits),
                        origin=f"widenScalarOrEltToNextPow2({idx}, {min_bits})",
                    )
                ]
            )
            return

        if name == "more_elements_to_next_pow2":
            idx = self.parse_type_idx()
            add(
                [
                    Rule(
                        Action.MORE,
                        NumElementsNotPow2(idx),
                        idx,
                        NextPow2Lanes(idx, 0),
                        origin=f"moreElementsToNextPow2({idx})",
                    )
                ]
            )
            return

        if name == "widen_to_next_pow2":
            idx = self.parse_type_idx()
            self.expect(",")
            min_bits = self.expect_int()
            add(
                [
                    Rule(
                        Action.WIDEN,
                        And(Scalar(idx), Not(IsPow2Bits(idx))),
                        idx,
                        NextPow2(idx, min_bits),
                        origin=f"widen_to_next_pow2({idx}, {min_bits})",
                    )
                ]
            )
            return

        if name == "widen_to_next_pow2_or_min":
            idx = self.parse_type_idx()
            self.expect(",")
            min_bits = self.expect_int()
            add(
                [
                    Rule(
                        Action.WIDEN,
                        NotPow2OrSmaller(idx, min_bits),
                        idx,
                        NextPow2(idx, min_bits),
                        origin=f"widen_to_next_pow2_or_min({idx}, {min_bits})",
                    )
                ]
            )
            return

        if name == "widen_to_next_multiple":
            idx = self.parse_type_idx()
            self.expect(",")
            size_token = self.peek()
            size = self.expect_int()
            if size <= 0:
                raise _err(
                    "P040",
                    size_token,
                    f"widen_to_next_multiple size must be positive, got {size}",
                )
            add(
                [
                    Rule(
                        Action.WIDEN,
                        And(Scalar(idx), Not(IsMultipleBits(idx, size))),
                        idx,
                        NextMultiple(idx, size),
                        origin=f"widen_to_next_multiple({idx}, {size})",
                    )
                ]
            )
            return

        if name == "legal_for_mem":
            entries = self.parse_mem_set()
            add(
                [
                    Rule(
                        Action.LEGAL,
                        MemCompatInSet(0, 1, 0, entries),
                        origin=f"legal_for_mem {mem_set_text(entries)}",
                    )
                ]
            )
            return

        if name == "always_legal":
            add([Rule(Action.LEGAL, Always(), origin="always_legal")])
            return

        raise _err("P013", self.tokens[self.pos - 1], f"unhandled macro {name!r}")

    # -- compile-time configuration guards --------------------------------

    def parse_guard_if_present(self) -> bool:
        if not self.accept_ident("when"):
            return True
        return self.parse_config_or()

    def parse_config_or(self) -> bool:
        value = self.parse_config_and()
        while self.accept_ident("or"):
            rhs = self.parse_config_and()
            value = value or rhs
        return value

    def parse_config_and(self) -> bool:
        value = self.parse_config_not()
        while self.accept_ident("and"):
            rhs = self.parse_config_not()
            value = value and rhs
        return value

    def parse_config_not(self) -> bool:
        if self.accept_ident("not"):
            return not self.parse_config_not()
        if self.accept("("):
            value = self.parse_config_or()
            self.expect(")")
            return value
        return self.parse_config_atom()

    def parse_config_atom(self) -> bool:
        token = self.peek()
        if token.kind != "IDENT":
            raise _err(
                "P027",
                token,
                f"expected configuration boolean, got {token.value!r}",
            )
        name = token.value
        if name in ("true", "false"):
            self.advance()
            return name == "true"
        if name not in self.config:
            raise _err(
                "P025",
                token,
                f"unknown configuration variable {name!r}",
            )
        self.advance()
        value = self.config[name]

        if self.peek().value in CMP_OPS:
            op = self.parse_cmp_op()
            rhs_token = self.peek()
            if rhs_token.kind == "INT":
                rhs: object = int(rhs_token.value)
                self.advance()
            elif rhs_token.value in ("true", "false"):
                rhs = rhs_token.value == "true"
                self.advance()
            else:
                raise _err(
                    "P026",
                    rhs_token,
                    f"expected integer or boolean after {op!r}, "
                    f"got {rhs_token.value!r}",
                )
            if type(value) is not type(rhs):
                raise _err(
                    "P026",
                    token,
                    f"configuration variable {name!r} and comparison "
                    f"value have different types",
                )
            if not isinstance(value, (int, bool)):
                raise _err(
                    "P026",
                    token,
                    f"configuration variable {name!r} is not ordered",
                )
            if isinstance(value, bool) and op not in ("==", "!="):
                raise _err(
                    "P026",
                    token,
                    "boolean configuration values only support == and !=",
                )
            return _config_cmp(op, value, rhs)

        if not isinstance(value, bool):
            raise _err(
                "P026",
                token,
                f"configuration variable {name!r} is not a boolean",
            )
        return value

    # -- type syntax --------------------------------------------------------

    def parse_type_idx(self) -> int:
        token = self.peek()
        if token.kind == "INT":
            return self.expect_index()
        if token.kind == "IDENT" and re.fullmatch(r"t\d+", token.value):
            self.advance()
            return int(token.value[1:])
        raise _err(
            "P005", token, f"expected type index (t0 or 0), got {token.value!r}"
        )

    def parse_type(self) -> LLT:
        token = self.peek()
        if token.kind not in ("IDENT", "LLT"):
            raise _err("P006", token, f"expected type, got {token.value!r}")
        self.advance()
        if token.kind == "IDENT" and token.value in self.aliases:
            return self.aliases[token.value]
        try:
            return parse_llt(token.value)
        except DslError as exc:
            # T011 is a semantic representability error, not a spelling error.
            code = "T011" if exc.code == "T011" else "P006"
            raise DslError(exc.message, code=code, span=token.span) from None

    def parse_type_expr_text(self) -> str:
        # Used only for error messages / origin text.
        token = self.peek()
        return token.value

    def parse_type_set(self) -> tuple[LLT, ...]:
        self.expect("{")
        types: list[LLT] = []
        if self.accept("}"):
            raise _err(
                "P042",
                self.tokens[self.pos - 1],
                "type set must not be empty",
            )
        types.append(self.parse_type())
        while self.accept(","):
            types.append(self.parse_type())
        self.expect("}")
        return tuple(types)

    def parse_pair_set(self) -> tuple[tuple[LLT, LLT], ...]:
        self.expect("{")
        pairs: list[tuple[LLT, LLT]] = []
        if self.accept("}"):
            raise _err(
                "P043",
                self.tokens[self.pos - 1],
                "type pair set must not be empty",
            )
        while True:
            self.expect("(")
            a = self.parse_type()
            self.expect(",")
            b = self.parse_type()
            self.expect(")")
            pairs.append((a, b))
            if not self.accept(","):
                break
        self.expect("}")
        return tuple(pairs)

    def parse_mem_set(self) -> tuple[tuple[LLT, LLT, LLT, int], ...]:
        self.expect("{")
        entries: list[tuple[LLT, LLT, LLT, int]] = []
        if not self.accept("}"):
            while True:
                self.expect("(")
                a = self.parse_type()
                self.expect(",")
                b = self.parse_type()
                self.expect(",")
                mem_ty = self.parse_type()
                self.expect(",")
                align = self.expect_int()
                self.expect(")")
                entries.append((a, b, mem_ty, align))
                if not self.accept(","):
                    break
            self.expect("}")
        return tuple(entries)

    # -- mutations ----------------------------------------------------------

    def parse_mutation(self, idx: int) -> object:
        if self.accept_ident("next_pow2"):
            self.expect("(")
            self.expect_ident("bits")
            self.expect("(")
            inner = self.parse_type_idx()
            self.expect(")")
            self.expect(",")
            min_bits = self.expect_int()
            self.expect(")")
            if inner != idx:
                raise _err(
                    "P014",
                    self.tokens[self.pos - 1],
                    f"next_pow2 must use the same type index as the mutation "
                    f"(t{idx}), got t{inner}",
                )
            return NextPow2(idx, min_bits)

        if self.accept_ident("next_multiple"):
            self.expect("(")
            self.expect_ident("bits")
            self.expect("(")
            inner = self.parse_type_idx()
            self.expect(")")
            self.expect(",")
            size_token = self.peek()
            size = self.expect_int()
            self.expect(")")
            if size <= 0:
                raise _err(
                    "P039",
                    size_token,
                    f"next_multiple size must be positive, got {size}",
                )
            if inner != idx:
                raise _err(
                    "P014",
                    self.tokens[self.pos - 1],
                    f"next_multiple must use the same type index as the mutation "
                    f"(t{idx}), got t{inner}",
                )
            return NextMultiple(idx, size)

        if self.accept_ident("element_of"):
            self.expect("(")
            inner = self.parse_type_idx()
            self.expect(")")
            if inner != idx:
                raise _err(
                    "P014",
                    self.tokens[self.pos - 1],
                    f"element_of must use the same type index as the mutation "
                    f"(t{idx}), got t{inner}",
                )
            return ElementOf(idx)

        if self.accept_ident("type_of"):
            self.expect("(")
            src = self.parse_type_idx()
            self.expect(")")
            if src == idx:
                raise _err(
                    "P014",
                    self.tokens[self.pos - 1],
                    f"type_of needs a different type index than the mutation "
                    f"(t{idx})",
                )
            return CopyType(idx, src)

        if self.accept_ident("same_size_as"):
            self.expect("(")
            src = self.parse_type_idx()
            self.expect(")")
            if src == idx:
                raise _err(
                    "P014",
                    self.tokens[self.pos - 1],
                    f"same_size_as needs a different type index than the "
                    f"mutation (t{idx})",
                )
            return SameSizeAs(idx, src)

        if self.accept_ident("change_element_to"):
            self.expect("(")
            ty = self.parse_type()
            self.expect(")")
            return SetElementType(idx, ty)

        if self.accept_ident("concat_bitcast"):
            self.expect("(")
            src = self.parse_type_idx()
            self.expect(")")
            if src == idx:
                raise _err(
                    "P014",
                    self.tokens[self.pos - 1],
                    f"concat_bitcast needs a different type index than the "
                    f"mutation (t{idx})",
                )
            return ConcatBitcast(idx, src)

        ty = self.parse_type()
        return SetElementSize(idx, ty.get_scalar_size_in_bits(), str(ty))

    def parse_count_mutation(self, idx: int) -> object:
        if self.accept_ident("element_of"):
            self.expect("(")
            inner = self.parse_type_idx()
            self.expect(")")
            if inner != idx:
                raise _err(
                    "P014",
                    self.tokens[self.pos - 1],
                    f"element_of must use the same type index as the mutation "
                    f"(t{idx}), got t{inner}",
                )
            return ElementOf(idx)
        ty = self.parse_type()
        if not ty.is_vector:
            raise _err(
                "P015",
                self.tokens[self.pos - 1],
                f"more/fewer needs a vector target type, got {ty}",
            )
        if ty.lanes <= 0:
            raise _err(
                "P041",
                self.tokens[self.pos - 1],
                f"more/fewer element count must be positive, got {ty.lanes}",
            )
        return SetElementCount(idx, ty.lanes, str(ty))

    # -- predicates ---------------------------------------------------------

    def parse_predicate(self) -> Pred:
        return self.parse_or()

    def parse_or(self) -> Pred:
        lhs = self.parse_and()
        while self.accept_ident("or"):
            lhs = Or(lhs, self.parse_and())
        return lhs

    def parse_and(self) -> Pred:
        lhs = self.parse_not()
        while self.accept_ident("and"):
            lhs = And(lhs, self.parse_not())
        return lhs

    def parse_not(self) -> Pred:
        if self.accept_ident("not"):
            return Not(self.parse_not())
        if self.accept("("):
            pred = self.parse_or()
            self.expect(")")
            return pred
        return self.parse_atom()

    def parse_atom(self) -> Pred:
        token = self.peek()
        if token.kind != "IDENT":
            raise _err("P007", token, f"expected predicate, got {token.value!r}")
        name = token.value

        if name in ("always", "true"):
            self.advance()
            return Always()

        if name in ("scalar", "vector", "fixed_vector", "pointer"):
            self.advance()
            self.expect("(")
            idx = self.parse_type_idx()
            self.expect(")")
            return {
                "scalar": Scalar,
                "vector": Vector,
                "fixed_vector": FixedVector,
                "pointer": Pointer,
            }[name](idx)

        if name == "elem_types_eq":
            self.advance()
            self.expect("(")
            i = self.parse_type_idx()
            self.expect(",")
            j = self.parse_type_idx()
            self.expect(")")
            return ElemTypesEq(i, j)

        if name == "scalar_is_float":
            self.advance()
            self.expect("(")
            idx = self.parse_type_idx()
            self.expect(",")
            token = self.peek()
            if token.kind != "IDENT":
                raise _err("P007", token, "expected a float type such as f16 or bf16")
            self.advance()
            ty = parse_llt(token.value)
            if not ty.is_float:
                raise _err("P019", token, f"expected a float type, got {ty}")
            self.expect(")")
            return FloatIs(idx, ty.fp)

        if name == "is_float":
            self.advance()
            self.expect("(")
            idx = self.parse_type_idx()
            self.expect(")")
            return FloatValue(idx)

        if name == "is_pointer_vector":
            self.advance()
            self.expect("(")
            idx = self.parse_type_idx()
            self.expect(")")
            return PointerVector(idx)

        if name == "same_size":
            self.advance()
            self.expect("(")
            i = self.parse_type_idx()
            self.expect(",")
            j = self.parse_type_idx()
            self.expect(")")
            return SameSize(i, j)

        if name in ("is_pow2", "is_multiple"):
            self.advance()
            self.expect("(")
            operand = self.expect_ident()
            if operand not in ("bits", "elem_bits"):
                raise _err(
                    "P035",
                    self.tokens[self.pos - 1],
                    f"expected 'bits' or 'elem_bits', got {operand!r}",
                )
            self.expect("(")
            idx = self.parse_type_idx()
            self.expect(")")
            if name == "is_pow2":
                self.expect(")")
                return IsPow2Bits(idx) if operand == "bits" else IsPow2ElemBits(idx)
            if operand != "bits":
                raise _err(
                    "P036",
                    self.tokens[self.pos - 1],
                    "is_multiple only supports bits(tN)",
                )
            self.expect(",")
            divisor_token = self.peek()
            divisor = self.expect_int()
            self.expect(")")
            if divisor <= 0:
                raise _err(
                    "P038",
                    divisor_token,
                    f"is_multiple divisor must be positive, got {divisor}",
                )
            return IsMultipleBits(idx, divisor)

        if name in ("same_shape", "concat_shape", "concat_shape_any"):
            self.advance()
            self.expect("(")
            i = self.parse_type_idx()
            self.expect(",")
            j = self.parse_type_idx()
            self.expect(")")
            constructor = {
                "same_shape": SameShape,
                "concat_shape": ConcatShape,
                "concat_shape_any": ConcatShapeAny,
            }[name]
            return constructor(i, j)

        if name == "scalar_bits":
            self.advance()
            self.expect("(")
            lhs = self.parse_type_idx()
            self.expect(")")
            op = self.parse_cmp_op()
            if self.accept_ident("bits"):
                self.expect("(")
                rhs = self.parse_type_idx()
                self.expect(")")
                return ScalarSizeVsSizeCmp(lhs, rhs, op)
            self.expect_ident("scalar_bits")
            self.expect("(")
            rhs = self.parse_type_idx()
            self.expect(")")
            return ScalarBitsCmp(lhs, rhs, op)

        if name in ("bits", "elem_bits", "size"):
            self.advance()
            self.expect("(")
            idx = self.parse_type_idx()
            self.expect(")")
            op = self.parse_cmp_op()
            value = self.parse_bit_count()
            if name == "elem_bits":
                return ElemBitsCmp(idx, op, value)
            # LLVM has no separate size() in this context; both mean total size.
            return BitsCmp(idx, op, value)

        if name == "lanes":
            self.advance()
            self.expect("(")
            idx = self.parse_type_idx()
            self.expect(")")
            op = self.parse_cmp_op()
            value = self.expect_int()
            return LanesCmp(idx, op, value)

        if name == "mem":
            self.advance()
            mem_idx = self.expect_index()
            self.expect(".")
            field = self.expect_ident()
            if field == "type":
                if self.accept_ident("in"):
                    types = self.parse_type_set()
                    return MemTypeIn(mem_idx, types)
                op = self.parse_cmp_op()
                ty = self.parse_type()
                if op == "==":
                    return MemTypeIn(mem_idx, (ty,))
                if op == "!=":
                    return Not(MemTypeIn(mem_idx, (ty,)))
                raise _err("P016", self.tokens[self.pos - 1], "mem.type only supports ==, !=, or 'in'")
            if field == "align":
                op = self.parse_cmp_op()
                value = self.expect_int()
                return MemAlignCmp(mem_idx, op, value)
            raise _err("P017", token, f"unknown memory field {field!r}; expected type or align")

        if name == "imm":
            self.advance()
            idx = self.expect_index()
            op = self.parse_cmp_op()
            value = self.expect_int()
            return ImmCmp(idx, op, value)

        if name == "pair":
            self.advance()
            self.expect("(")
            i = self.parse_type_idx()
            self.expect(",")
            j = self.parse_type_idx()
            self.expect(")")
            self.expect("in")
            pairs = self.parse_pair_set()
            return TypePairInSet(i, j, pairs)

        if re.fullmatch(r"t\d+", name):
            idx = self.parse_type_idx()
            if self.accept_ident("in"):
                return TypeIn(idx, self.parse_type_set())
            op = self.parse_cmp_op()
            ty = self.parse_type()
            if op == "==":
                return TypeEq(idx, ty)
            if op == "!=":
                return Not(TypeEq(idx, ty))
            raise _err("P018", self.tokens[self.pos - 1], "type comparisons only support == and !=")

        raise _err("P008", token, f"unknown predicate {name!r}")

    def _looks_like_lanes_form(self) -> bool:
        """Disambiguate the legacy ``N, lanes <cmp> K`` macro form.

        The general form starts with a predicate, which may itself start with
        a type index (``t1 == v2s16``).  A type index followed by ',' and the
        literal ``lanes`` is unambiguous.
        """
        if self.pos + 2 >= len(self.tokens):
            return False
        return (
            re.fullmatch(r"t?\d+", self.tokens[self.pos].value) is not None
            and self.tokens[self.pos + 1].value == ","
            and self.tokens[self.pos + 2].value == "lanes"
        )

    def parse_cmp_op(self) -> str:
        token = self.peek()
        if token.value not in CMP_OPS:
            raise _err(
                "P009",
                token,
                f"expected comparison operator, got {token.value!r}",
            )
        self.advance()
        return token.value


# ---------------------------------------------------------------------------
# Helpers and public API
# ---------------------------------------------------------------------------


def set_text(types: tuple[LLT, ...]) -> str:
    return "{" + ", ".join(map(str, types)) + "}"


def pair_set_text(pairs: tuple[tuple[LLT, LLT], ...]) -> str:
    return "{" + ", ".join(f"({a}, {b})" for a, b in pairs) + "}"


def mem_set_text(entries: tuple[tuple[LLT, LLT, LLT, int], ...]) -> str:
    return "{" + ", ".join(f"({a}, {b}, {m}, {al})" for a, b, m, al in entries) + "}"


def parse_config_value(text: str) -> object:
    if text == "true":
        return True
    if text == "false":
        return False
    if re.fullmatch(r"-?[0-9]+", text):
        return int(text)
    try:
        return parse_llt(text)
    except DslError:
        return text


def parse_defines(items: list[str]) -> dict[str, object]:
    config: dict[str, object] = {}
    for item in items:
        if "=" not in item:
            raise DslError(
                f"expected NAME=VALUE, got {item!r}", code="P028"
            )
        name, value_text = item.split("=", 1)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise DslError(
                f"invalid configuration name {name!r}", code="P028"
            )
        config[name] = parse_config_value(value_text)
    return config


def parse(
    text: str, config: Optional[dict[str, object]] = None
) -> LegalizerInfo:
    return Parser(lex(text), config=config).parse_program()


def parse_file(
    path: str | Path, config: Optional[dict[str, object]] = None
) -> LegalizerInfo:
    return parse(
        Path(path).read_text(encoding="utf-8"), config=config
    )
