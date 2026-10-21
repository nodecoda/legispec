# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Phase 6: translate the Legalizer DSL AST into C++ builder calls.

The generated file is a *translation artifact*.  Its only contract is:

    generated_rules(query) == interpreter(query)      for every query in D

It is deliberately independent of the hand-written target source.  Each AST
node has exactly one C++ template, default parameters are expanded explicitly,
and rule order is preserved.

Usage::

    python codegen.py --rules rules/toycpu.ldsl --out-dir out/codegen

The command writes ``toycpu_rules_gen.h``, ``toycpu_rules_gen.cpp`` and
``codegen-manifest.json``.  The manifest records the rule hash, rule counts and
the Custom/Libcall/Lower capabilities required by the policy.
"""

from __future__ import annotations

# Allow both ``python -m legalizer_dsl.codegen`` and direct script execution.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _pathlib

    _sys.path.insert(0, str(_pathlib(__file__).resolve().parents[1]))
    __package__ = "legalizer_dsl"

import argparse
import hashlib
import json
import os
import keyword
import sys
from pathlib import Path
from typing import Optional

from . import model
from .diagnostics import DslError
from .model import (
    Action,
    Always,
    And,
    BitsCmp,
    ConcatBitcast,
    ConcatShape,
    ConcatShapeAny,
    CopyType,
    ElementOf,
    FixedVectorElemCountCmp,
    ElemBitsCmp,
    ElemTypesEq,
    EltSizeNotPow2,
    FloatIs,
    FixedVector,
    FloatValue,
    ImmCmp,
    IsMultipleBits,
    IsPow2Bits,
    IsPow2ElemBits,
    LanesCmp,
    LLT,
    MemAlignCmp,
    MemCompatInSet,
    MemTypeIn,
    NextMultiple,
    NextPow2,
    NextPow2Lanes,
    NotPow2OrSmaller,
    NumElementsNotPow2,
    SameSizeAs,
    SetElementType,
    Not,
    Or,
    Pointer,
    PointerVector,
    SameSize,
    Scalar,
    ScalarBitsCmp,
    ScalarSizeVsSizeCmp,
    SetElementCount,
    SetElementSize,
    TypeEq,
    TypeIn,
    TypePairInSet,
    Vector,
)
from .parser import parse_defines, parse_file

GENERATED_HEADER = "toycpu_rules_gen.h"
GENERATED_SOURCE = "toycpu_rules_gen.cpp"
GENERATED_MANIFEST = "codegen-manifest.json"

# Public builder helpers for "type set / type pair" predicates.  The generic
# ``actionFor`` is private in LegalizeRuleSet, so codegen must use the public
# per-action spelling (legalFor/customFor/...).
ACTION_FOR = {
    Action.LEGAL: "legalFor",
    Action.CUSTOM: "customFor",
    Action.LIBCALL: "libcallFor",
    Action.LOWER: "lowerFor",
    Action.UNSUPPORTED: "unsupportedFor",
}

ACTION_IF = {
    Action.LEGAL: "legalIf",
    Action.CUSTOM: "customIf",
    Action.LIBCALL: "libcallIf",
    Action.LOWER: "lowerIf",
    Action.UNSUPPORTED: "unsupportedIf",
}

# Public predicate+mutation builders.  The generic ``actionIf`` overload is
# private in LegalizeRuleSet.
ACTION_MUTATE_IF = {
    Action.WIDEN: "widenScalarIf",
    Action.NARROW: "narrowScalarIf",
    Action.BITCAST: "bitcastIf",
    Action.MORE: "moreElementsIf",
    Action.FEWER: "fewerElementsIf",
}

ACTION_CPP = {
    Action.LEGAL: "Legal",
    Action.NARROW: "NarrowScalar",
    Action.WIDEN: "WidenScalar",
    Action.LOWER: "Lower",
    Action.LIBCALL: "Libcall",
    Action.CUSTOM: "Custom",
    Action.UNSUPPORTED: "Unsupported",
    Action.BITCAST: "Bitcast",
    Action.MORE: "MoreElements",
    Action.FEWER: "FewerElements",
}


class CodegenError(DslError):
    """A construct with no faithful C++ template."""

    def __init__(self, message: str, *, code: str = "G001", span=None) -> None:
        super().__init__(message, code=code, span=span)


# ---------------------------------------------------------------------------
# LLT naming
# ---------------------------------------------------------------------------


def _llt_ident(ty: LLT) -> str:
    """Stable C++ identifier for an LLT.

    ``p0:64`` becomes ``p0_64``; every other DSL spelling is already a valid
    C++ identifier.
    """
    return str(ty).replace(":", "_")


def _llt_ctor(ty: LLT) -> str:
    if ty.is_float:
        if ty.fp == "bfloat":
            return "LLT::bfloat16()"
        return {
            16: "LLT::float16()",
            32: "LLT::float32()",
            64: "LLT::float64()",
            128: "LLT::float128()",
        }[ty.bits]
    if ty.is_scalar:
        return f"LLT::scalar({ty.bits})"
    if ty.is_pointer:
        return f"LLT::pointer({ty.addrspace}, {ty.bits})"
    assert ty.elem is not None
    return f"LLT::fixed_vector({ty.lanes}, {_llt_ctor(ty.elem)})"


class LLTTable:
    """Collects every LLT that must be declared in the generated function.

    Types are emitted in first-use order so the output is deterministic.  We do
    not recursively declare vector element types: a vector constructor inlines
    ``LLT::scalar(...)``, and an unused constant would trigger ``-Wunused``.
    """

    def __init__(self) -> None:
        self._names: dict[LLT, str] = {}

    def add(self, ty: LLT) -> str:
        if ty not in self._names:
            self._names[ty] = _llt_ident(ty)
        return self._names[ty]

    def declarations(self) -> list[tuple[str, str]]:
        return [(name, _llt_ctor(ty)) for ty, name in self._names.items()]


def _llt_set(types, table: LLTTable) -> str:
    return "{" + ", ".join(table.add(ty) for ty in types) + "}"


# ---------------------------------------------------------------------------
# Predicates
# ---------------------------------------------------------------------------


def _pred(pred, table: LLTTable) -> str:
    """Emit one C++ predicate expression.

    Primitive helpers are used whenever LLVM has one with exactly the DSL
    semantics.  The remaining predicates are inline lambdas with an explicit
    bounds check, mirroring the interpreter's ``idx < len`` guard.
    """

    if isinstance(pred, Always):
        return "LegalityPredicate([](const LegalityQuery &) { return true; })"
    if isinstance(pred, TypeIn):
        return f"typeInSet({pred.idx}, {_llt_set(pred.types, table)})"
    if isinstance(pred, TypeEq):
        return f"typeIs({pred.idx}, {table.add(pred.ty)})"
    if isinstance(pred, FixedVectorElemCountCmp):
        return (
            "LegalityPredicate([=](const LegalityQuery &Q) { return "
            f"Q.Types.size() > {pred.idx} && "
            f"Q.Types[{pred.idx}].isFixedVector() && "
            f"Q.Types[{pred.idx}].getElementType() == "
            f"{table.add(pred.elem)} && "
            f"Q.Types[{pred.idx}].getNumElements() {pred.op} {pred.count}; }})"
        )
    if isinstance(pred, TypePairInSet):
        pairs = ", ".join(
            f"{{{table.add(a)}, {table.add(b)}}}" for a, b in pred.pairs
        )
        return f"typePairInSet({pred.i}, {pred.j}, {{{pairs}}})"
    if isinstance(pred, Scalar):
        return f"isScalar({pred.idx})"
    if isinstance(pred, Vector):
        return f"isVector({pred.idx})"
    if isinstance(pred, FixedVector):
        # LegalityPredicates has isVector() but no isFixedVector() helper, so
        # mirror the LLT call directly instead of inventing a helper name.
        return (
            "LegalityPredicate([](const LegalityQuery &Q) { "
            f"return Q.Types.size() > {pred.idx} && "
            f"Q.Types[{pred.idx}].isFixedVector(); }})"
        )
    if isinstance(pred, Pointer):
        return f"isPointer({pred.idx})"
    if isinstance(pred, ElemTypesEq):
        return (
            "LegalityPredicate([](const LegalityQuery &Q) { return "
            f"Q.Types.size() > {pred.i} && Q.Types.size() > {pred.j} && "
            f"Q.Types[{pred.i}].getElementType() == "
            f"Q.Types[{pred.j}].getElementType(); }})"
        )
    if isinstance(pred, FloatIs):
        method = "isBFloat16" if pred.semantics == "bfloat" else {
            "half": "isFloat16",
            "single": "isFloat32",
            "double": "isFloat64",
            "quad": "isFloat128",
        }[pred.semantics]
        return (
            "LegalityPredicate([](const LegalityQuery &Q) { return "
            f"Q.Types.size() > {pred.idx} && "
            f"Q.Types[{pred.idx}].getScalarType().{method}(); }})"
        )
    if isinstance(pred, FloatValue):
        return (
            "LegalityPredicate([](const LegalityQuery &Q) { return "
            f"Q.Types.size() > {pred.idx} && "
            f"Q.Types[{pred.idx}].isFloatOrFloatVector(); }})"
        )
    if isinstance(pred, PointerVector):
        return f"isPointerVector({pred.idx})"
    if isinstance(pred, EltSizeNotPow2):
        return (
            "LegalityPredicate([](const LegalityQuery &Q) { return "
            f"Q.Types.size() > {pred.idx} && "
            f"!isPowerOf2_64(Q.Types[{pred.idx}].getScalarSizeInBits()); }})"
        )
    if isinstance(pred, BitsCmp):
        return (
            "LegalityPredicate([](const LegalityQuery &Q) { return "
            f"Q.Types.size() > {pred.idx} && "
            f"Q.Types[{pred.idx}].getSizeInBits() {pred.op} {pred.value}; }})"
        )
    if isinstance(pred, ScalarBitsCmp):
        return (
            "LegalityPredicate([](const LegalityQuery &Q) { return "
            f"Q.Types.size() > {pred.lhs} && Q.Types.size() > {pred.rhs} && "
            f"Q.Types[{pred.lhs}].getScalarSizeInBits() {pred.op} "
            f"Q.Types[{pred.rhs}].getScalarSizeInBits(); }})"
        )
    if isinstance(pred, ScalarSizeVsSizeCmp):
        return (
            "LegalityPredicate([](const LegalityQuery &Q) { return "
            f"Q.Types.size() > {pred.scalar_idx} && "
            f"Q.Types.size() > {pred.size_idx} && "
            f"Q.Types[{pred.scalar_idx}].getScalarSizeInBits() {pred.op} "
            f"Q.Types[{pred.size_idx}].getSizeInBits(); }})"
        )
    if isinstance(pred, ElemBitsCmp):
        return (
            "LegalityPredicate([](const LegalityQuery &Q) { return "
            f"Q.Types.size() > {pred.idx} && "
            f"Q.Types[{pred.idx}].getScalarSizeInBits() {pred.op} {pred.value}; }})"
        )
    if isinstance(pred, LanesCmp):
        lanes = (
            f"(Q.Types[{pred.idx}].isVector() ? "
            f"Q.Types[{pred.idx}].getNumElements() : 1u)"
        )
        return (
            "LegalityPredicate([](const LegalityQuery &Q) { return "
            f"Q.Types.size() > {pred.idx} && {lanes} {pred.op} {pred.value}; }})"
        )
    if isinstance(pred, NumElementsNotPow2):
        return (
            "LegalityPredicate([](const LegalityQuery &Q) { "
            f"if (Q.Types.size() <= {pred.idx}) return false; "
            f"const LLT T = Q.Types[{pred.idx}]; "
            "if (!T.isFixedVector()) return false; "
            "const unsigned N = T.getNumElements(); "
            "return N != 0 && (N & (N - 1)) != 0; })"
        )
    if isinstance(pred, SameSize):
        return f"sameSize({pred.i}, {pred.j})"
    if isinstance(pred, ConcatShape):
        return (
            "LegalityPredicate([](const LegalityQuery &Q) { "
            f"if (Q.Types.size() <= {pred.i} || Q.Types.size() <= {pred.j}) "
            "return false; "
            f"const LLT Dst = Q.Types[{pred.i}]; "
            f"const LLT Src = Q.Types[{pred.j}]; "
            "return Dst.isFixedVector() && Src.isFixedVector() && "
            "Dst.getNumElements() == 2 * Src.getNumElements() && "
            "Dst.getElementType() == Src.getElementType(); })"
        )
    if isinstance(pred, ConcatShapeAny):
        return (
            "LegalityPredicate([](const LegalityQuery &Q) { "
            f"if (Q.Types.size() <= {pred.i} || Q.Types.size() <= {pred.j}) "
            "return false; "
            f"const LLT Dst = Q.Types[{pred.i}]; "
            f"const LLT Src = Q.Types[{pred.j}]; "
            "if (!Dst.isFixedVector() || !Src.isFixedVector()) return false; "
            "const unsigned S = Src.getNumElements(); "
            "const unsigned D = Dst.getNumElements(); "
            "return S != 0 && D % S == 0 && D / S >= 2 && "
            "Dst.getElementType() == Src.getElementType(); })"
        )
    if isinstance(pred, NotPow2OrSmaller):
        return (
            "LegalityPredicate([](const LegalityQuery &Q) { "
            f"if (Q.Types.size() <= {pred.idx}) return false; "
            f"const unsigned B = Q.Types[{pred.idx}].getScalarSizeInBits(); "
            f"return B < {pred.min_bits} || (B & (B - 1)) != 0; }})"
        )
    if isinstance(pred, IsPow2Bits):
        return (
            "LegalityPredicate([](const LegalityQuery &Q) { "
            f"if (Q.Types.size() <= {pred.idx}) return false; "
            f"const uint32_t N = Q.Types[{pred.idx}].getSizeInBits(); "
            "return N != 0 && (N & (N - 1)) == 0; })"
        )
    if isinstance(pred, IsPow2ElemBits):
        return (
            "LegalityPredicate([](const LegalityQuery &Q) { "
            f"if (Q.Types.size() <= {pred.idx}) return false; "
            f"const uint32_t N = Q.Types[{pred.idx}].getScalarSizeInBits(); "
            "return N != 0 && (N & (N - 1)) == 0; })"
        )
    if isinstance(pred, IsMultipleBits):
        return (
            "LegalityPredicate([](const LegalityQuery &Q) { return "
            f"Q.Types.size() > {pred.idx} && "
            f"Q.Types[{pred.idx}].getSizeInBits() % {pred.divisor} == 0; }})"
        )
    if isinstance(pred, MemTypeIn):
        alts = " || ".join(f"T == {table.add(t)}" for t in pred.types)
        return (
            "LegalityPredicate([](const LegalityQuery &Q) { "
            f"if (Q.MMODescrs.size() <= {pred.mem_idx}) return false; "
            f"const LLT T = Q.MMODescrs[{pred.mem_idx}].MemoryTy; "
            f"return {alts}; }})"
        )
    if isinstance(pred, MemAlignCmp):
        return (
            "LegalityPredicate([](const LegalityQuery &Q) { return "
            f"Q.MMODescrs.size() > {pred.mem_idx} && "
            f"Q.MMODescrs[{pred.mem_idx}].AlignInBits {pred.op} {pred.value}; }})"
        )
    if isinstance(pred, MemCompatInSet):
        entries = ", ".join(
            f"{{{table.add(t0)}, {table.add(t1)}, {table.add(mem)}, {align}}}"
            for t0, t1, mem, align in pred.entries
        )
        return (
            f"typePairAndMemDescInSet({pred.i}, {pred.j}, {pred.mem_idx}, "
            f"{{{entries}}})"
        )
    if isinstance(pred, ImmCmp):
        return (
            "LegalityPredicate([](const LegalityQuery &Q) { return "
            f"Q.Immediates.size() > {pred.idx} && "
            f"Q.Immediates[{pred.idx}] {pred.op} {pred.value}; }})"
        )
    if isinstance(pred, And):
        return f"all({_pred(pred.lhs, table)}, {_pred(pred.rhs, table)})"
    if isinstance(pred, Or):
        return f"any({_pred(pred.lhs, table)}, {_pred(pred.rhs, table)})"
    if isinstance(pred, Not):
        return f"predNot({_pred(pred.inner, table)})"
    raise CodegenError(
        f"no C++ template for predicate {type(pred).__name__}", code="G001"
    )


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


def _mutation(mut, table: LLTTable) -> str:
    if isinstance(mut, SetElementSize):
        return f"changeElementSizeTo({mut.idx}, {table.add(LLT.scalar(mut.bits))})"
    if isinstance(mut, SameSizeAs):
        return f"changeElementSizeTo({mut.idx}, {mut.src_idx})"
    if isinstance(mut, ConcatBitcast):
        return (
            "LegalizeMutation([](const LegalityQuery &Q) { "
            f"const LLT DstTy = Q.Types[{mut.idx}]; "
            f"const LLT SrcTy = Q.Types[{mut.src_idx}]; "
            f"return std::make_pair({mut.idx}, "
            "DstTy.changeElementSize(SrcTy.getSizeInBits())"
            ".changeElementCount("
            "DstTy.getElementCount().divideCoefficientBy("
            "SrcTy.getNumElements()))); })"
        )
    if isinstance(mut, CopyType):
        return (
            "LegalizeMutation([](const LegalityQuery &Q) { "
            f"return std::make_pair({mut.idx}, Q.Types[{mut.src_idx}]); }})"
        )
    if isinstance(mut, SetElementType):
        return f"changeElementTo({mut.idx}, {table.add(mut.new_elem)})"
    if isinstance(mut, NextPow2):
        return f"widenScalarOrEltToNextPow2({mut.idx}, {mut.min_bits})"
    if isinstance(mut, NextPow2Lanes):
        clamp = ""
        if mut.min_lanes:
            clamp = f"if (N < {mut.min_lanes}) N = {mut.min_lanes}; "
        return (
            "LegalizeMutation([](const LegalityQuery &Q) { "
            f"const LLT T = Q.Types[{mut.idx}]; "
            "unsigned N = 1; "
            "while (N < T.getNumElements()) N <<= 1; "
            f"{clamp}"
            f"return std::make_pair({mut.idx}, T.changeElementCount(N)); }})"
        )
    if isinstance(mut, NextMultiple):
        return (
            f"widenScalarOrEltToNextMultipleOf({mut.idx}, {mut.size})"
        )
    if isinstance(mut, SetElementCount):
        return (
            "LegalizeMutation([](const LegalityQuery &Q) { "
            f"const LLT T = Q.Types[{mut.idx}]; "
            f"return std::make_pair({mut.idx}, "
            f"T.changeElementCount({mut.lanes})); }})"
        )
    if isinstance(mut, ElementOf):
        return (
            "LegalizeMutation([](const LegalityQuery &Q) { "
            f"const LLT T = Q.Types[{mut.idx}]; "
            f"return std::make_pair({mut.idx}, T.getElementType()); }})"
        )
    raise CodegenError(
        f"no C++ template for mutation {type(mut).__name__}", code="G002"
    )


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def _opcodes_arg(opcodes: tuple[str, ...]) -> str:
    if len(opcodes) == 1:
        return opcodes[0]
    return "{" + ", ".join(opcodes) + "}"


def _rule_expr(rule: model.Rule, table: LLTTable) -> str:
    action = ACTION_CPP.get(rule.action)
    if action is None:
        raise CodegenError(
            f"no C++ action for {rule.action}", code="G003", span=rule.span
        )
    cxx_action = f"LegalizeAction::{action}"
    pred = rule.pred

    if isinstance(pred, Always):
        # These builder methods also update LLVM's debug-only coverage bits.
        if rule.action is Action.LEGAL:
            return ".alwaysLegal()"
        if rule.action is Action.UNSUPPORTED:
            return ".unsupported()"
        if rule.action is Action.LOWER:
            return ".lower()"
        if rule.action is Action.LIBCALL:
            return ".libcall()"
        if rule.action is Action.CUSTOM:
            return ".custom()"

    if rule.new_type is None:
        # Per-action builders mark type indices as covered, matching the
        # hand-written builder path.  ``actionFor`` itself is private.
        helper = ACTION_FOR.get(rule.action)
        if helper is not None and isinstance(pred, TypeIn) and pred.idx == 0:
            return f".{helper}({_llt_set(pred.types, table)})"
        if (
            helper is not None
            and isinstance(pred, TypePairInSet)
            and pred.i == 0
            and pred.j == 1
        ):
            pairs = ", ".join(
                f"{{{table.add(a)}, {table.add(b)}}}" for a, b in pred.pairs
            )
            return f".{helper}({{{pairs}}})"
        if (
            isinstance(pred, MemCompatInSet)
            and rule.action is Action.LEGAL
            and pred.i == 0
            and pred.j == 1
        ):
            entries = ", ".join(
                f"{{{table.add(t0)}, {table.add(t1)}, {table.add(mem)}, {align}}}"
                for t0, t1, mem, align in pred.entries
            )
            return f".legalForTypesWithMemDesc({{{entries}}})"
        if helper is not None:
            # customIf/lowerIf/libcallIf/legalIf/unsupportedIf are the
            # predicate forms the hand-written target uses; they also mark
            # all type indices as covered in debug builds.
            return f".{ACTION_IF[rule.action]}({_pred(pred, table)})"
        raise CodegenError(
            f"action {rule.action.value} requires a mutation but has none",
            code="G004",
            span=rule.span,
        )

    helper = ACTION_MUTATE_IF.get(rule.action)
    if helper is None:
        raise CodegenError(
            f"no public predicate+mutation builder for {rule.action.value}",
            code="G005",
            span=rule.span,
        )
    return (
        f".{helper}({_pred(pred, table)}, "
        f"{_mutation(rule.new_type, table)})"
    )


# ---------------------------------------------------------------------------
# Whole-file generation
# ---------------------------------------------------------------------------


def _required_capabilities(info: model.LegalizerInfo) -> dict[str, list[str]]:
    required: dict[str, set[str]] = {
        "custom": set(),
        "libcall": set(),
        "lower": set(),
    }
    for ruleset in info.rulesets:
        for rule in ruleset.rules:
            if rule.action is Action.CUSTOM:
                required["custom"].update(ruleset.opcodes)
            elif rule.action is Action.LIBCALL:
                required["libcall"].update(ruleset.opcodes)
            elif rule.action is Action.LOWER:
                required["lower"].update(ruleset.opcodes)
    return {key: sorted(value) for key, value in required.items()}


def _generated_header(namespace: str, rules_rel: str, rules_sha: str) -> str:
    guard = f"{namespace.upper()}_RULES_GEN_H"
    return f"""//===-- {GENERATED_HEADER} - Generated Legalizer rules -*- C++ -*-===//
//
// GENERATED FILE. DO NOT EDIT.
// Source: {rules_rel}
// SHA-256: {rules_sha}
//
//===----------------------------------------------------------------------===//

#ifndef {guard}
#define {guard}

namespace llvm {{
class LegalizerInfo;

namespace {namespace} {{

/// Populate \\p LI with the rules translated from the LDSL source.
void applyGeneratedLegalizerRules(LegalizerInfo &LI);

}} // namespace {namespace}
}} // namespace llvm

#endif // {guard}
"""


def _generated_source(
    info: model.LegalizerInfo, namespace: str, rules_rel: str, rules_sha: str
) -> str:
    table = LLTTable()

    # Build every rule expression first.  ``_rule_expr`` is the only place that
    # adds LLTs to ``table``, so declarations are complete before we emit them.
    rule_blocks: list[tuple[tuple[str, ...], list[str]]] = []
    for ruleset in info.rulesets:
        exprs: list[str] = []
        for rule in ruleset.rules:
            expr = _rule_expr(rule, table)
            if rule.origin:
                exprs.append(f"      // {rule.origin}")
            exprs.append(f"      {expr}")
        rule_blocks.append((ruleset.opcodes, exprs))

    lines: list[str] = []
    lines.append(
        f"//===-- {GENERATED_SOURCE} - Generated Legalizer rules -*- C++ -*-===//"
    )
    lines.append("//")
    lines.append("// GENERATED FILE. DO NOT EDIT.")
    lines.append(f"// Source: {rules_rel}")
    lines.append(f"// SHA-256: {rules_sha}")
    lines.append("//")
    lines.append("//===----------------------------------------------------------------------===//")
    lines.append("")
    lines.append(f'#include "{GENERATED_HEADER}"')
    lines.append('#include "llvm/CodeGen/GlobalISel/LegalizerInfo.h"')
    lines.append('#include "llvm/CodeGen/TargetOpcodes.h"')
    lines.append('#include "llvm/CodeGenTypes/LowLevelType.h"')
    lines.append("")
    lines.append("#include <cstdint>")
    lines.append("#include <utility>")
    lines.append("")
    lines.append("using namespace llvm;")
    lines.append("")
    lines.append("namespace llvm {")
    lines.append(f"namespace {namespace} {{")
    lines.append("")
    lines.append("void applyGeneratedLegalizerRules(LegalizerInfo &LI) {")
    lines.append("  using namespace TargetOpcode;")
    # These must be function-scope directives.  At global scope, entering
    # namespace llvm makes unqualified lookup find llvm::any (BitmaskEnum)
    # before the global using-directive, so a nested ``or`` would emit
    # any(any(a, b), c) that resolves to the wrong overload set.
    lines.append("  using namespace LegalityPredicates;")
    lines.append("  using namespace LegalizeMutations;")
    lines.append("")
    declarations = table.declarations()
    if declarations:
        lines.append("  // Low-level types, in first-use order.")
        for name, ctor in declarations:
            lines.append(f"  const LLT {name} = {ctor};")
        lines.append("")

    for opcodes, exprs in rule_blocks:
        lines.append(f"  // {', '.join(opcodes)}")
        lines.append(f"  LI.getActionDefinitionsBuilder({_opcodes_arg(opcodes)})")
        if exprs:
            lines.extend(exprs)
            lines[-1] += ";"
        else:
            lines[-1] += "; // empty ruleset"
        lines.append("")

    lines.append("}")
    lines.append("")
    lines.append(f"}} // namespace {namespace}")
    lines.append("} // namespace llvm")
    lines.append("")
    return "\n".join(lines)


def _collect_rule_types(rule: model.Rule, table: LLTTable) -> None:
    """Populate ``table`` with every LLT referenced by ``rule``."""

    def visit_pred(pred) -> None:
        if isinstance(pred, Always):
            return
        if isinstance(pred, (TypeIn,)):
            for ty in pred.types:
                table.add(ty)
            return
        if isinstance(pred, TypeEq):
            table.add(pred.ty)
            return
        if isinstance(pred, FixedVectorElemCountCmp):
            table.add(pred.elem)
            return
        if isinstance(pred, TypePairInSet):
            for a, b in pred.pairs:
                table.add(a)
                table.add(b)
            return
        if isinstance(pred, MemTypeIn):
            for ty in pred.types:
                table.add(ty)
            return
        if isinstance(pred, MemCompatInSet):
            for t0, t1, mem, _align in pred.entries:
                table.add(t0)
                table.add(t1)
                table.add(mem)
            return
        if isinstance(pred, (And, Or)):
            visit_pred(pred.lhs)
            visit_pred(pred.rhs)
            return
        if isinstance(pred, Not):
            visit_pred(pred.inner)
            return
        # Scalar/Vector/Pointer/BitsCmp/... carry no LLT payload.

    def visit_mut(mut) -> None:
        if isinstance(mut, SetElementSize):
            table.add(LLT.scalar(mut.bits))
            return
        if isinstance(mut, ElementOf):
            return
        # SameSizeAs/NextPow2/NextMultiple/SetElementCount carry no LLT payload.

    visit_pred(rule.pred)
    if rule.new_type is not None:
        visit_mut(rule.new_type)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _config_record(config: Optional[dict[str, object]]) -> dict[str, object]:
    if not config:
        return {}
    return {
        name: value if isinstance(value, (bool, int)) else str(value)
        for name, value in sorted(config.items())
    }


def generate(
    info: model.LegalizerInfo,
    *,
    out_dir: Path,
    rules_rel: str,
    rules_sha: str,
    config: Optional[dict[str, object]] = None,
    namespace: str = "ToyCPU",
) -> dict:
    """Write the generated header/source/manifest and return the manifest."""

    out_dir.mkdir(parents=True, exist_ok=True)
    header_text = _generated_header(namespace, rules_rel, rules_sha)
    source_text = _generated_source(info, namespace, rules_rel, rules_sha)

    header_path = out_dir / GENERATED_HEADER
    source_path = out_dir / GENERATED_SOURCE
    manifest_path = out_dir / GENERATED_MANIFEST

    header_path.write_text(header_text, encoding="utf-8", newline="\n")
    source_path.write_text(source_text, encoding="utf-8", newline="\n")

    manifest = {
        "version": 1,
        "source": rules_rel,
        "rules_sha256": rules_sha,
        "config": _config_record(config),
        "namespace": namespace,
        "rulesets": len(info.rulesets),
        "opcodes": len(info.by_opcode),
        "rules": sum(len(rs.rules) for rs in info.rulesets),
        "generated": {
            "header": GENERATED_HEADER,
            "header_sha256": _sha256_text(header_text),
            "source": GENERATED_SOURCE,
            "source_sha256": _sha256_text(source_text),
        },
        "capabilities_required": _required_capabilities(info),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _default_paths() -> tuple[Path, Path]:
    here = Path(__file__).resolve()
    repo = here.parents[1]
    out_dir = Path(os.environ.get("LEGALIZER_DSL_OUT", repo / "out"))
    return repo / "rules" / "toycpu.ldsl", out_dir / "codegen"


def main(argv: Optional[list[str]] = None) -> int:
    default_rules, default_out = _default_paths()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules", default=str(default_rules), help="LDSL rules file")
    parser.add_argument(
        "--define",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="compile-time configuration value (repeatable)",
    )
    parser.add_argument("--out-dir", default=str(default_out), help="generated output directory")
    parser.add_argument(
        "--capabilities",
        default=None,
        help="capability manifest to check before generating",
    )
    parser.add_argument(
        "--custom-source",
        default=None,
        help="optional ToyCPULegalizerInfo.cpp used to verify custom cases",
    )
    parser.add_argument(
        "--namespace",
        default="ToyCPU",
        help="C++ namespace for the generated entry point (default: ToyCPU)",
    )
    args = parser.parse_args(argv)
    if not args.namespace.isidentifier() or keyword.iskeyword(args.namespace):
        print(f"error: not a C++ identifier: {args.namespace!r}", file=sys.stderr)
        return 2

    rules_path = Path(args.rules)
    if not rules_path.is_file():
        print(f"error: rules file not found: {rules_path}", file=sys.stderr)
        return 2
    data = rules_path.read_bytes()
    rules_sha = hashlib.sha256(data).hexdigest()
    package_dir = Path(__file__).resolve().parent
    try:
        rel = "tools/legalizer_dsl/" + rules_path.resolve().relative_to(
            package_dir
        ).as_posix()
    except ValueError:
        rel = rules_path.name

    try:
        config = parse_defines(args.define)
        info = parse_file(rules_path, config=config)
        if args.capabilities:
            from .checker.capability import load_manifest, run as run_capability

            manifest = load_manifest(Path(args.capabilities))
            source_text = (
                Path(args.custom_source).read_text(encoding="utf-8")
                if args.custom_source
                else None
            )
            diagnostics = run_capability(info, manifest, custom_source=source_text)
            fatal = [d for d in diagnostics if d.severity.value == "ERROR"]
            for diagnostic in diagnostics:
                print(diagnostic.format(args.capabilities), file=sys.stderr)
            if fatal:
                print(
                    f"codegen: {len(fatal)} capability error(s); refusing to generate",
                    file=sys.stderr,
                )
                return 1
        manifest = generate(
            info,
            out_dir=Path(args.out_dir),
            rules_rel=rel,
            rules_sha=rules_sha,
            config=config,
            namespace=args.namespace,
        )
    except DslError as exc:
        print(f"{rules_path}:{exc}", file=sys.stderr)
        return 2

    print(
        f"codegen: rulesets={manifest['rulesets']} opcodes={manifest['opcodes']} "
        f"rules={manifest['rules']} out={Path(args.out_dir)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())