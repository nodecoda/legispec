# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Phase 6: translate the LegiSpec AST into C++ builder calls.

The generated file is a *translation artifact*.  Its only contract is:

    generated_rules(query) == interpreter(query)      for every query in D

It is deliberately independent of the hand-written target source.  Each AST
node has exactly one C++ template, default parameters are expanded explicitly,
and rule order is preserved.

Usage::

    python codegen.py --rules rules/toycpu.lspec --out-dir out/codegen

The command writes ``toycpu_rules_gen.h``, ``toycpu_rules_gen.cpp`` and
``codegen-manifest.json``.  The manifest records the rule hash, rule counts and
the Custom/Libcall/Lower capabilities required by the policy.
"""

from __future__ import annotations

# Allow both ``python -m legispec.codegen`` and direct script execution.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _pathlib

    _sys.path.insert(0, str(_pathlib(__file__).resolve().parents[1]))
    __package__ = "legispec"

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

from . import __version__, model, paths
from .checker.capability import CapabilityManifestError
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

# Bump this when the surface syntax or its elaboration contract changes.  It is
# independent of the packaging version and is pinned into every manifest.
DSL_SPEC_VERSION = "1.0"
STRUCTURE_CONTRACT_VERSION = 1
DEFAULT_LLVM_COMMIT = "unknown"

_CPP_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# C++20 keywords plus the alternative tokens.  ``str.isidentifier()`` alone
# accepts Python keywords and misses C++ ones such as ``int`` or ``template``.
_CPP_KEYWORDS = frozenset(
    """
    alignas alignof and and_eq asm atomic_cancel atomic_commit atomic_noexcept
    auto bitand bitor bool break case catch char char8_t char16_t char32_t
    class compl concept const consteval constexpr constinit const_cast continue
    co_await co_return co_yield decltype default delete do double dynamic_cast
    else enum explicit export extern false float for friend goto if inline int
    long mutable namespace new noexcept not not_eq nullptr operator or or_eq
    private protected public reflexpr register reinterpret_cast requires return
    short signed sizeof static static_assert static_cast struct switch
    synchronized template this thread_local throw true try typedef typeid
    typename union unsigned using virtual void volatile wchar_t while xor xor_eq
    """.split()
)


def _is_valid_namespace(namespace: str) -> bool:
    return (
        _CPP_IDENTIFIER_RE.fullmatch(namespace) is not None
        and namespace not in _CPP_KEYWORDS
    )


def _require_valid_namespace(namespace: str) -> None:
    if not _is_valid_namespace(namespace):
        raise DslError(
            f"not a valid C++ namespace identifier: {namespace!r}",
            code="G002",
        )


_STEM_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _require_valid_output_stem(stem: str) -> None:
    if _STEM_RE.fullmatch(stem) is None:
        raise DslError(
            "output stem must be a single C++ identifier without path "
            f"separators: {stem!r}",
            code="G003",
        )


def _rules_label(rules_path: Path) -> str:
    """Return a stable, non-absolute label for the rules source.

    Generated artifacts must be byte-identical regardless of the directory the
    generator was invoked from.  Repository rules are recorded relative to the
    data root; anything else is labelled ``external/<name>`` rather than
    leaking an absolute machine path into the manifest and generated headers.
    """

    resolved = rules_path.resolve()
    for root in (paths.DATA_ROOT, paths.REPO_ROOT):
        try:
            return resolved.relative_to(Path(root).resolve()).as_posix()
        except ValueError:
            continue
    return f"external/{resolved.name}"


def _derive_output_stem(rules_path: Path) -> str:
    """Derive a filename-safe C++ identifier from the rules file stem."""
    stem = re.sub(r"[^A-Za-z0-9_]", "_", rules_path.stem)
    if not stem or stem[0].isdigit():
        stem = f"_{stem}"
    return f"{stem}_rules_gen"


_NAMESPACE_FAMILIES = {
    "toycpu": "ToyCPU",
    "aarch64": "AArch64",
    "riscv": "RISCV",
    "riscv32": "RISCV32",
    "riscv64": "RISCV64",
    "x86": "X86",
}


def _namespace_from_rules_stem(stem: str) -> str:
    """Derive a stable C++ namespace from a known target-family file stem.

    Unknown families are rejected rather than silently falling back to a
    shared namespace: two unrelated rule slices must never collide merely
    because the generator could not classify them.
    """

    parts = [
        part
        for part in re.split(r"[^A-Za-z0-9]+", stem.lower())
        if part
    ]
    if not parts or parts[0] not in _NAMESPACE_FAMILIES:
        raise DslError(
            "cannot derive a namespace from rules file stem "
            f"{stem!r}; pass --namespace explicitly",
            code="G004",
        )
    family = _NAMESPACE_FAMILIES[parts[0]]
    suffix = "".join(part[:1].upper() + part[1:] for part in parts[1:])
    return family + suffix


def _header_guard(namespace: str, output_stem: str) -> str:
    # ``output_stem`` already ends in ``_rules_gen`` (see
    # ``_derive_output_stem``), so do not append it a second time.  The stem is
    # what makes two slices in the same namespace coexist in one TU.
    return re.sub(
        r"[^A-Za-z0-9]", "_", f"{namespace}_{output_stem}_h"
    ).upper()


def _wiring_guard(output_stem: str) -> str:
    return re.sub(
        r"[^A-Za-z0-9]", "_", f"LEGISPEC_WIRING_{output_stem}_INC"
    ).upper()


def _resolve_namespace(
    namespace: Optional[str], rules_stem: str
) -> tuple[str, str]:
    if namespace is None:
        return _namespace_from_rules_stem(rules_stem), "derived"
    return namespace, "explicit"


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


_SLOT_FIELDS = (
    "idx",
    "i",
    "j",
    "lhs",
    "rhs",
    "mem_idx",
    "src_idx",
    "scalar_idx",
    "size_idx",
)


def _reject_negative_slots(node, *, kind: str) -> None:
    """Never emit a negative slot index into generated C++.

    Parsing already rejects negative indices, but predicates and mutations can
    also be built programmatically.  A negative index would become
    ``Q.Types[-1]`` (or ``-1u``) in C++, so reject it at the emission boundary.
    """
    for name in _SLOT_FIELDS:
        value = getattr(node, name, None)
        if isinstance(value, int) and not isinstance(value, bool) and value < 0:
            raise DslError(
                f"{kind} {type(node).__name__} uses negative slot index "
                f"{name}={value}",
                code="G006",
            )


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
    bounds check, mirroring the interpreter's non-negative slot guard.
    """

    _reject_negative_slots(pred, kind="predicate")

    if isinstance(pred, Always):
        return "LegalityPredicate([](const LegalityQuery &) { return true; })"
    if isinstance(pred, TypeIn):
        if not pred.types:
            # Defensive: the parser rejects empty sets, but a hand-built model
            # must still generate a valid predicate (never `typeInSet(idx, {})`).
            return "LegalityPredicate([](const LegalityQuery &) { return false; })"
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
        if not pred.types:
            # Defensive: an empty alternative list would emit `return ;`.
            return "LegalityPredicate([](const LegalityQuery &) { return false; })"
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
    _reject_negative_slots(mut, kind="mutation")
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


def _generated_header(
    namespace: str,
    rules_rel: str,
    rules_sha: str,
    header_name: str = GENERATED_HEADER,
    header_guard: Optional[str] = None,
) -> str:
    guard = header_guard or _header_guard(namespace, Path(header_name).stem)
    return f"""//===-- {header_name} - Generated Legalizer rules -*- C++ -*-===//
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

/// Populate \\p LI with the rules translated from the LegiSpec source.
void applyGeneratedLegalizerRules(LegalizerInfo &LI);

}} // namespace {namespace}
}} // namespace llvm

#endif // {guard}
"""


def _generated_source(
    info: model.LegalizerInfo,
    namespace: str,
    rules_rel: str,
    rules_sha: str,
    header_name: str = GENERATED_HEADER,
    source_name: str = GENERATED_SOURCE,
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
        f"//===-- {source_name} - Generated Legalizer rules -*- C++ -*-===//"
    )
    lines.append("//")
    lines.append("// GENERATED FILE. DO NOT EDIT.")
    lines.append(f"// Source: {rules_rel}")
    lines.append(f"// SHA-256: {rules_sha}")
    lines.append("//")
    lines.append("//===----------------------------------------------------------------------===//")
    lines.append("")
    lines.append(f'#include "{header_name}"')
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


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _config_record(config: Optional[dict[str, object]]) -> dict[str, object]:
    if not config:
        return {}
    return {
        name: value if isinstance(value, (bool, int)) else str(value)
        for name, value in sorted(config.items())
    }


def _render_artifacts(
    info: model.LegalizerInfo,
    *,
    rules_rel: str,
    rules_sha: str,
    config: Optional[dict[str, object]],
    namespace: str,
    namespace_source: str,
    output_stem: str,
    llvm_commit: str,
    dsl_spec_version: str,
) -> tuple[str, str, dict]:
    _require_valid_namespace(namespace)
    _require_valid_output_stem(output_stem)
    if namespace_source not in ("derived", "explicit"):
        raise DslError(
            f"invalid namespace_source: {namespace_source!r}",
            code="G005",
        )
    if not llvm_commit:
        raise DslError("llvm_commit must not be empty", code="G006")
    if not dsl_spec_version:
        raise DslError("dsl_spec_version must not be empty", code="G007")

    header_name = f"{output_stem}.h"
    source_name = f"{output_stem}.cpp"
    header_guard = _header_guard(namespace, output_stem)
    header_text = _generated_header(
        namespace,
        rules_rel,
        rules_sha,
        header_name,
        header_guard=header_guard,
    )
    source_text = _generated_source(
        info, namespace, rules_rel, rules_sha, header_name, source_name
    )
    header_sha = _sha256_text(header_text)
    source_sha = _sha256_text(source_text)

    manifest = {
        "version": 1,
        "generator_version": __version__,
        "dsl_spec_version": dsl_spec_version,
        "llvm_commit": llvm_commit,
        "source": rules_rel,
        "rules_sha256": rules_sha,
        "config": _config_record(config),
        "namespace": namespace,
        "namespace_source": namespace_source,
        "output_stem": output_stem,
        "header_guard": header_guard,
        "header_sha256": header_sha,
        "source_sha256": source_sha,
        "rulesets": len(info.rulesets),
        "opcodes": len(info.by_opcode),
        "rules": sum(len(rs.rules) for rs in info.rulesets),
        "generated": {
            "header": header_name,
            "header_sha256": header_sha,
            "source": source_name,
            "source_sha256": source_sha,
        },
        "capabilities_required": _required_capabilities(info),
        "structure": _structure_manifest(info),
    }
    return header_text, source_text, manifest


def _manifest_text(manifest: dict) -> str:
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def _wiring_relative_dir(out_dir: Path, wiring_dir: Path) -> str:
    try:
        relative = os.path.relpath(out_dir, wiring_dir)
    except ValueError as exc:
        raise DslError(
            "cannot emit wiring across filesystem drives; place --emit-wiring "
            "on the same drive as --out-dir",
            code="G008",
        ) from exc
    return Path(relative).as_posix()


def _render_wiring(
    info: model.LegalizerInfo,
    *,
    out_dir: Path,
    wiring_dir: Path,
    namespace: str,
    output_stem: str,
    llvm_commit: str,
) -> dict[str, str]:
    header_name = f"{output_stem}.h"
    source_name = f"{output_stem}.cpp"
    generated_dir = _wiring_relative_dir(out_dir, wiring_dir)
    target_name = f"legispec_{output_stem}"
    wiring_guard = _wiring_guard(output_stem)
    wiring_function = f"applyGeneratedLegalizerRulesForConsumer_{output_stem}"
    lookup_function = f"lookupGeneratedOpcode_{output_stem}"

    # An explicit if-chain is intentional.  ``StringSwitch`` with an
    # assignment expression as its value argument is legal C++ but makes the
    # side effect look like a value and is easy to misread.  The chain is also
    # self-contained: it only names opcodes that the generated rules actually
    # use, all of which are TargetOpcode enumerators.
    opcode_cases = []
    for opcode in sorted(info.by_opcode):
        opcode_cases.append(
            f'  if (Name == "{opcode}") {{\n'
            f"    Opcode = llvm::TargetOpcode::{opcode};\n"
            f"    return true;\n"
            f"  }}"
        )
    opcode_cases_text = "\n".join(opcode_cases)

    cmake_text = f"""#===-- legalizer-rules.cmake - generated wiring -*- CMake -*-===//
#
# GENERATED FILE. DO NOT EDIT.
# generator: legispec {__version__}
# llvm_commit: {llvm_commit}
#
#===----------------------------------------------------------------------===//

set(LEGISPEC_GENERATED_DIR "${{CMAKE_CURRENT_LIST_DIR}}/{generated_dir}")
set(LEGISPEC_GENERATED_HEADER "{header_name}")
set(LEGISPEC_GENERATED_SOURCE "{source_name}")
set(LEGISPEC_NAMESPACE "{namespace}")
set(LEGISPEC_OUTPUT_STEM "{output_stem}")
set(LEGISPEC_TARGET "{target_name}")
set(LEGISPEC_HEADER_GUARD "{_header_guard(namespace, output_stem)}")

if(NOT TARGET {target_name})
  add_library({target_name} STATIC
    "${{LEGISPEC_GENERATED_DIR}}/${{LEGISPEC_GENERATED_SOURCE}}")
  target_include_directories({target_name} PUBLIC
    "${{LEGISPEC_GENERATED_DIR}}"
    "${{CMAKE_CURRENT_LIST_DIR}}")
  target_link_libraries({target_name} PUBLIC
    LLVMCodeGen LLVMGlobalISel LLVMCore LLVMSupport)
endif()
"""

    wiring_text = f"""//===-- wiring.cpp.inc - generated consumer wiring -*- C++ -*-===//
//
// GENERATED FILE. DO NOT EDIT.
// generator: legispec {__version__}
// llvm_commit: {llvm_commit}
//
//===----------------------------------------------------------------------===//

#ifndef {wiring_guard}
#define {wiring_guard}

#include "{header_name}"
#include "llvm/ADT/StringRef.h"
#include "llvm/CodeGen/GlobalISel/LegalizerInfo.h"
#include "llvm/CodeGen/TargetOpcodes.h"

[[maybe_unused]] static bool {lookup_function}(llvm::StringRef Name,
                                               unsigned &Opcode) {{
{opcode_cases_text}
  return false;
}}

[[maybe_unused]] static void {wiring_function}(llvm::LegalizerInfo &LI) {{
  llvm::{namespace}::applyGeneratedLegalizerRules(LI);
}}

#define LEGISPEC_APPLY_RULES {wiring_function}

#endif // {wiring_guard}
"""

    markdown_text = f"""# Generated legalizer wiring

- source: `{header_name}` / `{source_name}`
- namespace: `llvm::{namespace}`
- entry point: `llvm::{namespace}::applyGeneratedLegalizerRules(LegalizerInfo &)`
- llvm commit pin: `{llvm_commit}`

## CMake

The consumer must provide the LLVM package exports first:

```cmake
find_package(LLVM CONFIG REQUIRED)
include("path/to/{wiring_dir.name}/legalizer-rules.cmake")
target_link_libraries(your_consumer PRIVATE {target_name})
```

`legalizer-rules.cmake` resolves the generated directory relative to its own
location, so the include path may be absolute or relative to the consumer's
CMake source directory.  The target already exposes the generated directory
and this wiring directory on its public include path.

## C++

`wiring.cpp.inc` includes the generated header itself; include the wiring once
in the translation unit that constructs the `LegalizerInfo`:

```cpp
#include "wiring.cpp.inc"

llvm::LegalizerInfo LI;
LEGISPEC_APPLY_RULES(LI);
```

Do not copy the generated source into the target's hand-written
`LegalizerInfo` translation unit. Keep it as a separate static library so a
regeneration cannot silently overwrite upstream files.
"""
    return {
        "legalizer-rules.cmake": cmake_text,
        "wiring.cpp.inc": wiring_text,
        "wiring.md": markdown_text,
    }


def emit_wiring(
    info: model.LegalizerInfo,
    *,
    out_dir: Path,
    wiring_dir: Path,
    namespace: str,
    output_stem: str,
    llvm_commit: str = DEFAULT_LLVM_COMMIT,
) -> list[Path]:
    texts = _render_wiring(
        info,
        out_dir=out_dir,
        wiring_dir=wiring_dir,
        namespace=namespace,
        output_stem=output_stem,
        llvm_commit=llvm_commit,
    )
    wiring_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in texts.items():
        path = wiring_dir / name
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
    return written


def check_wiring(
    info: model.LegalizerInfo,
    *,
    out_dir: Path,
    wiring_dir: Path,
    namespace: str,
    output_stem: str,
    llvm_commit: str = DEFAULT_LLVM_COMMIT,
) -> list[str]:
    texts = _render_wiring(
        info,
        out_dir=out_dir,
        wiring_dir=wiring_dir,
        namespace=namespace,
        output_stem=output_stem,
        llvm_commit=llvm_commit,
    )
    problems = []
    for name, text in texts.items():
        path = wiring_dir / name
        try:
            actual = path.read_bytes()
        except FileNotFoundError:
            problems.append(f"missing {path}")
            continue
        except OSError as exc:
            problems.append(f"cannot read {path}: {exc}")
            continue
        if actual != text.encode("utf-8"):
            problems.append(f"content differs: {path}")
    return problems


def _slots_used(node) -> list[int]:
    slots: set[int] = set()
    for name in _SLOT_FIELDS:
        value = getattr(node, name, None)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            slots.add(value)
    return sorted(slots)


def _mutation_record(expr) -> Optional[dict]:
    if expr is None:
        return None
    record = {
        "kind": type(expr).__name__,
        "text": str(expr),
        "slots": _slots_used(expr),
    }
    for name, value in vars(expr).items():
        if isinstance(value, int) and not isinstance(value, bool):
            record[name] = value
    return record


def _rule_structure(rule: model.Rule) -> dict:
    return {
        "action": rule.action.value,
        "type_idx": rule.type_idx,
        "predicate": {
            "kind": type(rule.pred).__name__,
            "text": str(rule.pred),
            "slots": _slots_used(rule.pred),
        },
        "mutation": _mutation_record(rule.new_type),
        "origin": rule.origin,
    }


def _structure_manifest(info: model.LegalizerInfo) -> dict:
    rulesets: list[dict] = []
    action_counts: dict[str, int] = {}
    macro_counts: dict[str, int] = {}
    macro_invocations = 0
    guarded_off = 0
    for index, ruleset in enumerate(info.rulesets):
        rules = [_rule_structure(rule) for rule in ruleset.rules]
        expansions = []
        for expansion in getattr(ruleset, "expansions", ()):
            expansions.append(
                {
                    "name": expansion.name,
                    "guard": expansion.guard,
                    "first_rule": expansion.first_rule,
                    "rule_count": expansion.rule_count,
                    "rule_indices": list(
                        range(
                            expansion.first_rule,
                            expansion.first_rule + expansion.rule_count,
                        )
                    ),
                }
            )
            macro_invocations += 1
            macro_counts[expansion.name] = macro_counts.get(expansion.name, 0) + 1
            if expansion.guard is False:
                guarded_off += 1
        for rule in ruleset.rules:
            name = rule.action.value
            action_counts[name] = action_counts.get(name, 0) + 1
        rulesets.append(
            {
                "index": index,
                "opcodes": list(ruleset.opcodes),
                "rule_count": len(ruleset.rules),
                "actions": [rule.action.value for rule in ruleset.rules],
                "type_slots": [rule.type_idx for rule in ruleset.rules],
                "mutation_kinds": [
                    type(rule.new_type).__name__
                    if rule.new_type is not None
                    else "none"
                    for rule in ruleset.rules
                ],
                "rules": rules,
                "expansions": expansions,
            }
        )
    return {
        "contract_version": STRUCTURE_CONTRACT_VERSION,
        "rulesets": rulesets,
        "counts": {
            "rulesets": len(info.rulesets),
            "opcodes": len(info.by_opcode),
            "rules": sum(len(rs.rules) for rs in info.rulesets),
            "actions": dict(sorted(action_counts.items())),
            "macro_invocations": macro_invocations,
            "macro_counts": dict(sorted(macro_counts.items())),
            "guarded_off": guarded_off,
        },
    }


def generate(
    info: model.LegalizerInfo,
    *,
    out_dir: Path,
    rules_rel: str,
    rules_sha: str,
    config: Optional[dict[str, object]] = None,
    namespace: str = "ToyCPU",
    namespace_source: str = "explicit",
    output_stem: str = "toycpu_rules_gen",
    llvm_commit: str = DEFAULT_LLVM_COMMIT,
    dsl_spec_version: str = DSL_SPEC_VERSION,
) -> dict:
    """Write the generated header/source/manifest and return the manifest."""

    header_text, source_text, manifest = _render_artifacts(
        info,
        rules_rel=rules_rel,
        rules_sha=rules_sha,
        config=config,
        namespace=namespace,
        namespace_source=namespace_source,
        output_stem=output_stem,
        llvm_commit=llvm_commit,
        dsl_spec_version=dsl_spec_version,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    header_path = out_dir / manifest["generated"]["header"]
    source_path = out_dir / manifest["generated"]["source"]
    manifest_path = out_dir / GENERATED_MANIFEST

    header_path.write_text(header_text, encoding="utf-8", newline="\n")
    source_path.write_text(source_text, encoding="utf-8", newline="\n")
    manifest_path.write_text(
        _manifest_text(manifest),
        encoding="utf-8",
        newline="\n",
    )
    return manifest


def check_generated(
    info: model.LegalizerInfo,
    *,
    out_dir: Path,
    rules_rel: str,
    rules_sha: str,
    config: Optional[dict[str, object]] = None,
    namespace: str = "ToyCPU",
    namespace_source: str = "explicit",
    output_stem: str = "toycpu_rules_gen",
    llvm_commit: str = DEFAULT_LLVM_COMMIT,
    dsl_spec_version: str = DSL_SPEC_VERSION,
) -> list[str]:
    """Return differences between ``out_dir`` and freshly rendered artifacts.

    This never creates or writes ``out_dir``; callers decide whether a stale or
    missing artifact set is a check failure.
    """

    header_text, source_text, manifest = _render_artifacts(
        info,
        rules_rel=rules_rel,
        rules_sha=rules_sha,
        config=config,
        namespace=namespace,
        namespace_source=namespace_source,
        output_stem=output_stem,
        llvm_commit=llvm_commit,
        dsl_spec_version=dsl_spec_version,
    )
    expected = {
        manifest["generated"]["header"]: header_text,
        manifest["generated"]["source"]: source_text,
        GENERATED_MANIFEST: _manifest_text(manifest),
    }
    problems: list[str] = []
    for name, text in expected.items():
        path = out_dir / name
        try:
            actual = path.read_bytes()
        except FileNotFoundError:
            problems.append(f"missing {path}")
            continue
        except OSError as exc:
            problems.append(f"cannot read {path}: {exc}")
            continue
        if actual != text.encode("utf-8"):
            problems.append(f"content differs: {path}")
    return problems


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _default_paths() -> tuple[Path, Path]:
    return paths.RULES / "toycpu.lspec", paths.OUT / "codegen"


def main(argv: Optional[list[str]] = None) -> int:
    default_rules, default_out = _default_paths()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules", default=str(default_rules), help="LegiSpec rules file")
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
        default=None,
        help="C++ namespace for the generated entry point (default: derive from rules stem)",
    )
    parser.add_argument(
        "--output-stem",
        default=None,
        metavar="STEM",
        help="generated file stem (default: <rules-stem>_rules_gen)",
    )
    parser.add_argument(
        "--llvm-commit",
        default=DEFAULT_LLVM_COMMIT,
        help="LLVM commit/version pinned in the manifest",
    )
    parser.add_argument(
        "--dsl-spec-version",
        default=DSL_SPEC_VERSION,
        help="LegiSpec specification version pinned in the manifest",
    )
    parser.add_argument(
        "--emit-wiring",
        default=None,
        metavar="DIR",
        help="emit CMake/C++ wiring for the generated rules",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify that out-dir matches freshly generated output; never write",
    )
    args = parser.parse_args(argv)

    rules_path = Path(args.rules)
    if not rules_path.is_file():
        print(f"error: rules file not found: {rules_path}", file=sys.stderr)
        return 2
    try:
        namespace, namespace_source = _resolve_namespace(
            args.namespace, rules_path.stem
        )
    except DslError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not _is_valid_namespace(namespace):
        print(f"error: not a C++ identifier: {namespace!r}", file=sys.stderr)
        return 2

    data = rules_path.read_bytes()
    rules_sha = hashlib.sha256(data).hexdigest()
    rel = _rules_label(rules_path)
    output_stem = (
        args.output_stem
        if args.output_stem is not None
        else _derive_output_stem(rules_path)
    )

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
            diagnostics = run_capability(
                info,
                manifest,
                custom_source=source_text,
                custom_source_name=args.custom_source,
            )
            fatal = [d for d in diagnostics if d.severity.value == "ERROR"]
            for diagnostic in diagnostics:
                print(diagnostic.format(args.capabilities), file=sys.stderr)
            if fatal:
                print(
                    f"codegen: {len(fatal)} capability error(s); refusing to generate",
                    file=sys.stderr,
                )
                return 1

        render_kwargs = dict(
            rules_rel=rel,
            rules_sha=rules_sha,
            config=config,
            namespace=namespace,
            namespace_source=namespace_source,
            output_stem=output_stem,
            llvm_commit=args.llvm_commit,
            dsl_spec_version=args.dsl_spec_version,
        )
        if args.check:
            problems = check_generated(
                info,
                out_dir=Path(args.out_dir),
                **render_kwargs,
            )
            if args.emit_wiring is not None:
                problems.extend(
                    check_wiring(
                        info,
                        out_dir=Path(args.out_dir),
                        wiring_dir=Path(args.emit_wiring),
                        namespace=namespace,
                        output_stem=output_stem,
                        llvm_commit=args.llvm_commit,
                    )
                )
            if problems:
                for problem in problems:
                    print(f"codegen-check: {problem}", file=sys.stderr)
                print(
                    "codegen-check: generated output is stale; rerun without --check",
                    file=sys.stderr,
                )
                return 1
            print(f"codegen-check: {Path(args.out_dir)} is up to date")
            return 0

        manifest = generate(
            info,
            out_dir=Path(args.out_dir),
            **render_kwargs,
        )
        if args.emit_wiring is not None:
            emit_wiring(
                info,
                out_dir=Path(args.out_dir),
                wiring_dir=Path(args.emit_wiring),
                namespace=namespace,
                output_stem=output_stem,
                llvm_commit=args.llvm_commit,
            )
    except DslError as exc:
        print(f"{rules_path}:{exc}", file=sys.stderr)
        return 2
    except CapabilityManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: cannot write generated files: {exc}", file=sys.stderr)
        return 2

    print(
        f"codegen: rulesets={manifest['rulesets']} opcodes={manifest['opcodes']} "
        f"rules={manifest['rules']} out={Path(args.out_dir)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())