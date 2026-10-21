# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Core model for the Legalizer rule DSL.

Scope: this models the *policy* layer of LLVM GlobalISel, i.e. the path

    LegalizerInfo::getAction(MI, MRI)
        -> LegalityQuery
        -> LegalizeRuleSet::apply
        -> LegalizeActionStep

It deliberately does not model the MIR rewriting helpers
(``narrowScalarAddSub``, ``widenScalar``, ``libcall``, ``legalizeCustom``).
Those are operational semantics and live in LegalizerHelper.cpp.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional, Sequence

from .diagnostics import Diagnostic, DslError, Severity, Span


# ---------------------------------------------------------------------------
# Low-level types (a small subset of llvm::LLT)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LLT:
    """A scalar, pointer, or fixed vector type.

    ``s32``  -> LLT(kind="scalar", bits=32)
    ``p0``   -> LLT(kind="pointer", addrspace=0, bits=32)
    ``v4s32``-> LLT(kind="vector", lanes=4, elem=LLT.scalar(32))
    """

    kind: str
    bits: int = 0
    addrspace: int = 0
    lanes: int = 0
    elem: Optional["LLT"] = None
    fp: str = ""

    @staticmethod
    def scalar(bits: int) -> "LLT":
        if bits <= 0:
            raise DslError(f"scalar size must be positive, got {bits}", code="T001")
        return LLT("scalar", bits=bits)

    FLOAT_KINDS = {
        "half": 16,
        "bfloat": 16,
        "single": 32,
        "double": 64,
        "quad": 128,
    }

    @staticmethod
    def float_type(semantics: str, bits: int = 0) -> "LLT":
        """A floating-point LLT: ``f16``/``f32``/``f64``/``f128``/``bf16``.

        LLVM keeps float semantics in the LLT (``FpSemantics``), so ``f16`` and
        ``bf16`` are different types even though both are 16 bits wide.
        """
        expected = LLT.FLOAT_KINDS.get(semantics)
        if expected is None:
            raise DslError(f"unknown float semantics {semantics!r}", code="T013")
        if bits == 0:
            bits = expected
        if bits != expected:
            raise DslError(
                f"float semantics {semantics!r} requires {expected} bits, got {bits}",
                code="T014",
            )
        return LLT("float", bits=bits, fp=semantics)

    @staticmethod
    def pointer(addrspace: int = 0, bits: int = 32) -> "LLT":
        if addrspace < 0:
            raise DslError(f"address space must be >= 0, got {addrspace}", code="T002")
        if bits <= 0:
            raise DslError(f"pointer size must be positive, got {bits}", code="T003")
        return LLT("pointer", bits=bits, addrspace=addrspace)

    @staticmethod
    def vector(lanes: int, elem: "LLT") -> "LLT":
        if lanes <= 0:
            raise DslError(f"vector lane count must be positive, got {lanes}", code="T004")
        if lanes == 1:
            # LLVM represents v1 as a scalar: ElementCount::getFixed(1) is
            # isScalar(), and LLT::vector asserts !EC.isScalar().  v1sN is
            # therefore not a representable LLT and must not enter the domain.
            raise DslError(
                "vector lane count must be at least 2: LLVM cannot represent "
                "v1 (ElementCount::getFixed(1) is scalar)",
                code="T011",
            )
        if elem.kind == "vector":
            raise DslError("nested vectors are not an LLT", code="T005")
        return LLT("vector", lanes=lanes, elem=elem)

    @property
    def is_scalar(self) -> bool:
        # LLVM's LLT::isScalar() includes FLOAT.
        return self.kind in ("scalar", "float")

    @property
    def is_float(self) -> bool:
        return self.kind == "float"

    @property
    def is_pointer(self) -> bool:
        return self.kind == "pointer"

    @property
    def is_vector(self) -> bool:
        return self.kind == "vector"

    @property
    def num_elements(self) -> int:
        return self.lanes if self.is_vector else 1

    def get_scalar_size_in_bits(self) -> int:
        if self.is_vector:
            assert self.elem is not None
            return self.elem.get_scalar_size_in_bits()
        return self.bits

    def get_size_in_bits(self) -> int:
        if self.is_vector:
            assert self.elem is not None
            return self.lanes * self.elem.get_size_in_bits()
        return self.bits

    def element_type(self) -> "LLT":
        if self.is_vector:
            assert self.elem is not None
            return self.elem
        return self

    def eq_llvm(self, other: "LLT") -> bool:
        """Mirror ``LLT::operator==``.

        AArch64's type constants are ``LLT::scalar(N)``, i.e. Kind::ANY_SCALAR.
        LLVM compares an ANY_SCALAR against any other scalar by size alone:

            LLT::scalar(32) == LLT::float32()   // true

        Exact dataclass equality (used for dedup and domain membership) must
        stay spelling-exact, so every predicate that mirrors a C++ ``==`` on
        LLT has to call this method instead of ``==``.
        """
        if (
            self.kind == other.kind
            and self.bits == other.bits
            and self.addrspace == other.addrspace
            and self.lanes == other.lanes
            and self.fp == other.fp
            and self.elem == other.elem
        ):
            return True
        if self.kind == "scalar" or other.kind == "scalar":
            return self.is_scalar and other.is_scalar and self.bits == other.bits
        if self.is_vector and other.is_vector:
            assert self.elem is not None and other.elem is not None
            return self.elem.eq_llvm(other.elem) and self.lanes == other.lanes
        return False

    def change_element_size(self, bits: int) -> "LLT":
        """Mirror LLT::changeElementSize.

        The shape (scalar vs vector) is preserved; only the element size
        changes.  A pointer has no element size, so it is returned unchanged.
        """
        if bits <= 0:
            raise DslError(f"element size must be positive, got {bits}", code="T006")
        if self.is_float:
            if self.fp == "bfloat":
                # LLVM's changeElementSize falls through to LLT::scalar for
                # non-IEEE floats (bf16 is not IEEE): semantics are dropped.
                return LLT.scalar(bits)
            for semantics, value in LLT.FLOAT_KINDS.items():
                if value == bits and semantics != "bfloat":
                    return LLT.float_type(semantics)
            raise DslError(
                f"no IEEE float type with {bits} bits", code="T015"
            )
        if self.is_scalar:
            return LLT.scalar(bits)
        if self.is_vector:
            assert self.elem is not None
            return LLT.vector(self.lanes, self.elem.change_element_size(bits))
        return self

    def change_element_type(self, new_elem: "LLT") -> "LLT":
        """Mirror LLT::changeElementType: vectors keep lanes, scalars replace."""
        if self.is_vector:
            assert self.elem is not None
            return LLT.vector(self.lanes, new_elem)
        return new_elem

    def change_element_count(self, lanes: int) -> "LLT":
        """Mirror LLT::changeElementCount."""
        if not self.is_vector:
            raise DslError(f"cannot change element count of non-vector {self}", code="T007")
        assert self.elem is not None
        return LLT.vector(lanes, self.elem)

    def __str__(self) -> str:
        if self.is_float:
            if self.fp == "bfloat":
                return "bf16"
            return f"f{self.bits}"
        if self.is_scalar:
            return f"s{self.bits}"
        if self.is_pointer:
            base = f"p{self.addrspace}"
            return base if self.bits == 32 else f"{base}:{self.bits}"
        return f"v{self.lanes}{self.elem}"


def parse_llt(text: str) -> LLT:
    """Parse the LLT spellings used by the DSL."""
    text = text.strip()
    if text == "bf16":
        return LLT.float_type("bfloat")
    m = re.fullmatch(r"f(\d+)", text)
    if m:
        bits = int(m.group(1))
        for semantics, value in LLT.FLOAT_KINDS.items():
            if value == bits and semantics != "bfloat":
                return LLT.float_type(semantics)
        raise DslError(f"bad float spelling: {text!r}", code="T008")
    m = re.fullmatch(r"s(\d+)", text)
    if m:
        return LLT.scalar(int(m.group(1)))
    m = re.fullmatch(r"p(\d+)(?::(\d+))?", text)
    if m:
        bits = int(m.group(2)) if m.group(2) else 32
        return LLT.pointer(int(m.group(1)), bits)
    m = re.fullmatch(r"v(\d+)(.+)", text)
    if m:
        lanes = int(m.group(1))
        return LLT.vector(lanes, parse_llt(m.group(2)))
    raise DslError(f"bad LLT spelling: {text!r}", code="T008")


# ---------------------------------------------------------------------------
# Query and result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemDesc:
    """A subset of LegalityQuery::MemDesc."""

    memory_ty: LLT
    align_bits: int
    ordering: str = "not_atomic"


@dataclass(frozen=True)
class Query:
    opcode: str
    types: tuple[LLT, ...] = ()
    mems: tuple[MemDesc, ...] = ()
    imms: tuple[int, ...] = ()

    def __str__(self) -> str:
        parts = [f"types=[{', '.join(map(str, self.types))}]"]
        if self.mems:
            parts.append(
                "mems=["
                + ", ".join(f"{m.memory_ty}/{m.align_bits}b" for m in self.mems)
                + "]"
            )
        if self.imms:
            parts.append(f"imms=[{', '.join(map(str, self.imms))}]")
        return f"{self.opcode} " + " ".join(parts)


@dataclass(frozen=True)
class Domain:
    """The finite abstract domain D and the initial query set Q0.

    Arity is deliberately *not* a separate declaration: a Q0 query for an
    opcode (or for any alias in the same ruleset) fixes the observed number of
    type slots.  The checker treats a ruleset without any Q0 query as
    ``UNKNOWN`` rather than inventing an arity from the C++ implementation.
    """

    types: tuple[LLT, ...] = ()
    imms: tuple[int, ...] = ()
    mems: tuple[MemDesc, ...] = ()
    q0: tuple[Query, ...] = ()
    # Optional restriction on the Cartesian product.  It is not an LLVM
    # predicate: it says which *shapes* the declared domain contains (for
    # example, a two-slot opcode whose slots must both be scalar or both be
    # vectors).  D is enumerated as {types^arity : filter holds}.
    filter: Optional[Pred] = None
    span: Optional[Span] = None

    def q0_for(self, opcodes: Sequence[str]) -> tuple[Query, ...]:
        wanted = set(opcodes)
        return tuple(query for query in self.q0 if query.opcode in wanted)

    def accepts(self, query: Query) -> bool:
        """True when *query* is inside the declared domain D.

        A predicate that cannot even be evaluated (for example a shape that
        makes a mutation helper undefined) is outside D, not an error: D is
        the set the claim talks about, and states outside it are reported as
        a boundary instead of being silently coerced into the claim.
        """
        if self.filter is None:
            return True
        try:
            return bool(self.filter.eval(query))
        except DslError:
            return False


class Action(Enum):
    LEGAL = "Legal"
    NARROW = "NarrowScalar"
    WIDEN = "WidenScalar"
    LOWER = "Lower"
    LIBCALL = "Libcall"
    CUSTOM = "Custom"
    UNSUPPORTED = "Unsupported"
    BITCAST = "Bitcast"
    MORE = "MoreElements"
    FEWER = "FewerElements"


ACTION_VERB = {
    Action.LEGAL: "legal",
    Action.NARROW: "narrow",
    Action.WIDEN: "widen",
    Action.LOWER: "lower",
    Action.LIBCALL: "libcall",
    Action.CUSTOM: "custom",
    Action.UNSUPPORTED: "unsupported",
    Action.BITCAST: "bitcast",
    Action.MORE: "more",
    Action.FEWER: "fewer",
}

# Actions that carry a type mutation in this DSL.
MUTATING_ACTIONS = {
    Action.NARROW,
    Action.WIDEN,
    Action.BITCAST,
    Action.MORE,
    Action.FEWER,
}


@dataclass
class Step:
    action: Action
    type_idx: int = 0
    new_type: Optional[LLT] = None
    rule_index: Optional[int] = None

    def __str__(self) -> str:
        name = self.action.value
        if self.new_type is not None:
            return f"{name} t{self.type_idx} -> {self.new_type}"
        return name


# ---------------------------------------------------------------------------
# Predicates
# ---------------------------------------------------------------------------


def _cmp(op: str, lhs: int, rhs: int) -> bool:
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
    raise DslError(f"unknown comparison operator {op!r}", code="T009")


class Pred:
    def eval(self, query: Query) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def __str__(self) -> str:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass(frozen=True)
class Always(Pred):
    def eval(self, query: Query) -> bool:
        return True

    def __str__(self) -> str:
        return "always"


@dataclass(frozen=True)
class TypeIn(Pred):
    idx: int
    types: tuple[LLT, ...]

    def eval(self, query: Query) -> bool:
        if self.idx >= len(query.types):
            return False
        return any(query.types[self.idx].eq_llvm(ty) for ty in self.types)

    def __str__(self) -> str:
        return f"t{self.idx} in {{{', '.join(map(str, self.types))}}}"


@dataclass(frozen=True)
class TypeEq(Pred):
    idx: int
    ty: LLT

    def eval(self, query: Query) -> bool:
        if self.idx >= len(query.types):
            return False
        return query.types[self.idx].eq_llvm(self.ty)

    def __str__(self) -> str:
        return f"t{self.idx} == {self.ty}"


@dataclass(frozen=True)
class FixedVectorElemCountCmp(Pred):
    """Mirror clampMin/MaxNumElements' predicate exactly.

    LLVM tests ``isFixedVector()`` and an exact element-type match before it
    calls ``getNumElements()``.  Keeping all three tests in one node avoids
    both the scalar ``getNumElements()`` assertion and an approximation of the
    element match by bit width.
    """

    idx: int
    elem: LLT
    op: str
    count: int

    def eval(self, query: Query) -> bool:
        if self.idx >= len(query.types):
            return False
        ty = query.types[self.idx]
        return (
            ty.is_vector
            and ty.elem is not None
            and ty.elem.eq_llvm(self.elem)
            and _cmp(self.op, ty.lanes, self.count)
        )

    def __str__(self) -> str:
        return (
            f"fixed_vector(t{self.idx}, {self.elem}) and "
            f"lanes(t{self.idx}) {self.op} {self.count}"
        )


@dataclass(frozen=True)
class TypePairInSet(Pred):
    i: int
    j: int
    pairs: tuple[tuple[LLT, LLT], ...]

    def eval(self, query: Query) -> bool:
        if self.i >= len(query.types) or self.j >= len(query.types):
            return False
        lhs = query.types[self.i]
        rhs = query.types[self.j]
        return any(
            lhs.eq_llvm(a) and rhs.eq_llvm(b) for a, b in self.pairs
        )

    def __str__(self) -> str:
        pairs = ", ".join(f"({a}, {b})" for a, b in self.pairs)
        return f"pair(t{self.i}, t{self.j}) in {{{pairs}}}"


@dataclass(frozen=True)
class Scalar(Pred):
    idx: int

    def eval(self, query: Query) -> bool:
        return self.idx < len(query.types) and query.types[self.idx].is_scalar

    def __str__(self) -> str:
        return f"scalar(t{self.idx})"


@dataclass(frozen=True)
class Vector(Pred):
    idx: int

    def eval(self, query: Query) -> bool:
        return self.idx < len(query.types) and query.types[self.idx].is_vector

    def __str__(self) -> str:
        return f"vector(t{self.idx})"


@dataclass(frozen=True)
class FixedVector(Pred):
    """Mirror ``LLT::isFixedVector()``.

    The current LLT model has no scalable-vector kind, so every vector in a
    domain is fixed.  Keeping the predicate explicit is still worth it: it
    mirrors the upstream test, and it forces a future scalable-vector model
    to revisit this method instead of silently reading ``isVector()``.
    """

    idx: int

    def eval(self, query: Query) -> bool:
        return self.idx < len(query.types) and query.types[self.idx].is_vector

    def __str__(self) -> str:
        return f"fixed_vector(t{self.idx})"


@dataclass(frozen=True)
class SameShape(Pred):
    """Domain-level restriction: both slots are scalar, or both are vectors
    with the same number of lanes.

    LLVM's two-slot shift rules assume the opcode's shape contract.  Mixed
    vector/scalar queries and vector pairs with different lane counts are not
    meaningful inputs: the oracle either cycles or trips mutationIsSane.
    Element sizes may differ inside a vector pair -- that is exactly what the
    min/maxScalarEltSameAsIf rules are for.  This predicate belongs to the
    domain block, not to a legality rule.
    """

    i: int
    j: int

    def eval(self, query: Query) -> bool:
        if self.i >= len(query.types) or self.j >= len(query.types):
            return False
        a = query.types[self.i]
        b = query.types[self.j]
        if a.is_vector != b.is_vector:
            return False
        if a.is_vector and a.lanes != b.lanes:
            return False
        return True

    def __str__(self) -> str:
        return f"same_shape(t{self.i}, t{self.j})"


@dataclass(frozen=True)
class ConcatShape(Pred):
    """Domain contract for ``G_CONCAT_VECTORS``.

    The opcode concatenates two or more vectors of the same element type.  A
    legality query projects that contract to two slots:

        t0 = result vector
        t1 = one source vector

    For the two-source form the result has twice the source's lane count.
    The policy rules read only these two slots.  Keeping the relation in the
    domain prevents probe queries that are not G_CONCAT_VECTORS shapes from
    being treated as evidence.
    """

    i: int
    j: int

    def eval(self, query: Query) -> bool:
        if self.i >= len(query.types) or self.j >= len(query.types):
            return False
        dst = query.types[self.i]
        src = query.types[self.j]
        if not dst.is_vector or not src.is_vector:
            return False
        if dst.lanes != 2 * src.lanes:
            return False
        assert dst.elem is not None and src.elem is not None
        return dst.elem.eq_llvm(src.elem)

    def __str__(self) -> str:
        return f"concat_shape(t{self.i}, t{self.j})"


@dataclass(frozen=True)
class ConcatShapeAny(Pred):
    """The n-ary ``G_CONCAT_VECTORS`` contract.

    Mirrors ``MachineVerifier.cpp`` for G_CONCAT_VECTORS: both slots are
    vectors, all sources are homogeneous, and

        lanes(dst) == k * lanes(src),   k = number of sources >= 2

    The legality query does not carry k.  ``GenericOpcodes.td`` gives the
    opcode exactly two generic type slots (``type0`` = dst, ``type1`` = src0)
    plus ``variable_ops``, so ``LegalizerInfo::getAction`` projects the
    variable operand list onto (dst, src0).  k is recovered from the two
    slots as ``lanes(dst) / lanes(src)``; integrality and ``k >= 2`` are the
    verifier's conditions on that projection.

    ``ConcatShape`` is the two-source special case; this node is the full
    opcode contract.
    """

    i: int
    j: int

    def eval(self, query: Query) -> bool:
        if self.i >= len(query.types) or self.j >= len(query.types):
            return False
        dst = query.types[self.i]
        src = query.types[self.j]
        if not dst.is_vector or not src.is_vector:
            return False
        if src.lanes <= 0 or dst.lanes % src.lanes != 0:
            return False
        if dst.lanes // src.lanes < 2:
            return False
        assert dst.elem is not None and src.elem is not None
        return dst.elem.eq_llvm(src.elem)

    def __str__(self) -> str:
        return f"concat_shape_any(t{self.i}, t{self.j})"


@dataclass(frozen=True)
class Pointer(Pred):
    idx: int

    def eval(self, query: Query) -> bool:
        return self.idx < len(query.types) and query.types[self.idx].is_pointer

    def __str__(self) -> str:
        return f"pointer(t{self.idx})"


@dataclass(frozen=True)
class PointerVector(Pred):
    """Mirror ``LLT::isPointerVector()``: a vector whose element is a pointer."""

    idx: int

    def eval(self, query: Query) -> bool:
        if self.idx >= len(query.types):
            return False
        ty = query.types[self.idx]
        return ty.is_vector and ty.elem is not None and ty.elem.is_pointer

    def __str__(self) -> str:
        return f"is_pointer_vector(t{self.idx})"


@dataclass(frozen=True)
class ElemTypesEq(Pred):
    """Mirror ``Types[i].getElementType() == Types[j].getElementType()``.

    This is type equality, not size equality: ``v2p0:64`` and ``v2s64`` have
    elements of the same bit width but are different LLTs, and the upstream
    G_ICMP/G_FCMP predicate distinguishes them.
    """

    i: int
    j: int

    def eval(self, query: Query) -> bool:
        if self.i >= len(query.types) or self.j >= len(query.types):
            return False
        return query.types[self.i].element_type().eq_llvm(
            query.types[self.j].element_type()
        )

    def __str__(self) -> str:
        return f"elem_types_eq(t{self.i}, t{self.j})"


@dataclass(frozen=True)
class EltSizeNotPow2(Pred):
    """Mirror ``scalarOrEltSizeNotPow2``.

    Note the contrast with ``IsPow2Bits``: this uses the *scalar/element* size,
    not the total size.  For ``v4s24`` the total is 96 (not a power of two)
    while the element is 24 (also not a power of two); for ``v3s32`` the total
    is 96 but the element is 32, so only this predicate is false.
    """

    idx: int

    def eval(self, query: Query) -> bool:
        if self.idx >= len(query.types):
            return False
        bits = query.types[self.idx].get_scalar_size_in_bits()
        return bits > 0 and (bits & (bits - 1)) != 0

    def __str__(self) -> str:
        return f"is_pow2(elem_bits(t{self.idx})) == false"


@dataclass(frozen=True)
class FloatIs(Pred):
    """Mirror ``Q.Types[idx].getScalarType().isFloat16()/isBFloat16()``."""

    idx: int
    semantics: str

    def eval(self, query: Query) -> bool:
        if self.idx >= len(query.types):
            return False
        scalar = query.types[self.idx].element_type()
        return scalar.is_float and scalar.fp == self.semantics

    def __str__(self) -> str:
        name = "bf16" if self.semantics == "bfloat" else f"f{LLT.FLOAT_KINDS[self.semantics]}"
        return f"scalar_is_float(t{self.idx}, {name})"


@dataclass(frozen=True)
class FloatValue(Pred):
    """Mirror ``LLT::isFloatOrFloatVector()``.

    Unlike ``scalar_is_float`` this does not name a semantics; it answers
    "is this slot floating-point at all", which is what a domain filter needs
    when one slot has an opcode-level contract (a G_FCMP result is an integer
    mask, never a float).
    """

    idx: int

    def eval(self, query: Query) -> bool:
        if self.idx >= len(query.types):
            return False
        ty = query.types[self.idx]
        if ty.is_float:
            return True
        return ty.is_vector and ty.elem is not None and ty.elem.is_float

    def __str__(self) -> str:
        return f"is_float(t{self.idx})"


@dataclass(frozen=True)
class BitsCmp(Pred):
    idx: int
    op: str
    value: int

    def eval(self, query: Query) -> bool:
        if self.idx >= len(query.types):
            return False
        return _cmp(self.op, query.types[self.idx].get_size_in_bits(), self.value)

    def __str__(self) -> str:
        return f"bits(t{self.idx}) {self.op} {self.value}"


@dataclass(frozen=True)
class ScalarBitsCmp(Pred):
    """Compare getScalarSizeInBits() of two type slots.

    LLVM expresses scalarSameSizeAs with a lambda over two type slots; this
    predicate is the structural form of that lambda.
    """

    lhs: int
    rhs: int
    op: str

    def eval(self, query: Query) -> bool:
        if self.lhs >= len(query.types) or self.rhs >= len(query.types):
            return False
        return _cmp(
            self.op,
            query.types[self.lhs].get_scalar_size_in_bits(),
            query.types[self.rhs].get_scalar_size_in_bits(),
        )

    def __str__(self) -> str:
        return f"scalar_bits(t{self.lhs}) {self.op} scalar_bits(t{self.rhs})"


@dataclass(frozen=True)
class ScalarSizeVsSizeCmp(Pred):
    """Mirror minScalarSameAs' asymmetric size comparison.

    LLVM compares ``Types[LargeTypeIdx].getScalarSizeInBits()`` against
    ``Types[TypeIdx].getSizeInBits()`` -- scalar size versus *total* size.
    That asymmetry is observable when the narrower slot is a vector; the
    predicate is modelled literally rather than "fixed" to scalar-vs-scalar.
    """

    scalar_idx: int
    size_idx: int
    op: str

    def eval(self, query: Query) -> bool:
        if self.scalar_idx >= len(query.types) or self.size_idx >= len(query.types):
            return False
        return _cmp(
            self.op,
            query.types[self.scalar_idx].get_scalar_size_in_bits(),
            query.types[self.size_idx].get_size_in_bits(),
        )

    def __str__(self) -> str:
        return f"scalar_bits(t{self.scalar_idx}) {self.op} bits(t{self.size_idx})"


@dataclass(frozen=True)
class ElemBitsCmp(Pred):
    idx: int
    op: str
    value: int

    def eval(self, query: Query) -> bool:
        if self.idx >= len(query.types):
            return False
        return _cmp(
            self.op, query.types[self.idx].get_scalar_size_in_bits(), self.value
        )

    def __str__(self) -> str:
        return f"elem_bits(t{self.idx}) {self.op} {self.value}"


@dataclass(frozen=True)
class LanesCmp(Pred):
    idx: int
    op: str
    value: int

    def eval(self, query: Query) -> bool:
        if self.idx >= len(query.types):
            return False
        return _cmp(self.op, query.types[self.idx].num_elements, self.value)

    def __str__(self) -> str:
        return f"lanes(t{self.idx}) {self.op} {self.value}"


@dataclass(frozen=True)
class NumElementsNotPow2(Pred):
    """Mirror LegalityPredicates::numElementsNotPow2.

    LLVM tests ``isFixedVector()`` before calling ``getNumElements()``, then
    requires the lane count not to be a power of two.
    """

    idx: int

    def eval(self, query: Query) -> bool:
        if self.idx >= len(query.types):
            return False
        ty = query.types[self.idx]
        if not ty.is_vector:
            return False
        lanes = ty.lanes
        return lanes > 0 and (lanes & (lanes - 1)) != 0

    def __str__(self) -> str:
        return f"fixed_vector(t{self.idx}) and lanes_not_pow2(t{self.idx})"


@dataclass(frozen=True)
class SameSize(Pred):
    i: int
    j: int

    def eval(self, query: Query) -> bool:
        if self.i >= len(query.types) or self.j >= len(query.types):
            return False
        return (
            query.types[self.i].get_size_in_bits()
            == query.types[self.j].get_size_in_bits()
        )

    def __str__(self) -> str:
        return f"same_size(t{self.i}, t{self.j})"


@dataclass(frozen=True)
class NotPow2OrSmaller(Pred):
    """Mirror sizeNotPow2OrSmallerThan: scalar size < Min or not a power of 2."""

    idx: int
    min_bits: int

    def eval(self, query: Query) -> bool:
        if self.idx >= len(query.types):
            return False
        bits = query.types[self.idx].get_scalar_size_in_bits()
        return bits < self.min_bits or (bits & (bits - 1)) != 0

    def __str__(self) -> str:
        return (
            f"scalar_bits(t{self.idx}) < {self.min_bits} or "
            f"not is_pow2(scalar_bits(t{self.idx}))"
        )


@dataclass(frozen=True)
class IsPow2Bits(Pred):
    idx: int

    def eval(self, query: Query) -> bool:
        if self.idx >= len(query.types):
            return False
        n = query.types[self.idx].get_size_in_bits()
        return n > 0 and (n & (n - 1)) == 0

    def __str__(self) -> str:
        return f"is_pow2(bits(t{self.idx}))"


@dataclass(frozen=True)
class IsPow2ElemBits(Pred):
    """Mirror ``isPowerOf2_64(Types[idx].getScalarSizeInBits())``.

    ``IsPow2Bits`` deliberately uses the total type size.  This predicate is
    the element-size variant, needed when a rule tests the element width of a
    vector rather than the vector's total width.
    """

    idx: int

    def eval(self, query: Query) -> bool:
        if self.idx >= len(query.types):
            return False
        n = query.types[self.idx].get_scalar_size_in_bits()
        return n > 0 and (n & (n - 1)) == 0

    def __str__(self) -> str:
        return f"is_pow2(elem_bits(t{self.idx}))"


@dataclass(frozen=True)
class IsMultipleBits(Pred):
    idx: int
    divisor: int

    def eval(self, query: Query) -> bool:
        if self.idx >= len(query.types):
            return False
        return query.types[self.idx].get_size_in_bits() % self.divisor == 0

    def __str__(self) -> str:
        return f"is_multiple(bits(t{self.idx}), {self.divisor})"


@dataclass(frozen=True)
class MemTypeIn(Pred):
    mem_idx: int
    types: tuple[LLT, ...]

    def eval(self, query: Query) -> bool:
        if self.mem_idx >= len(query.mems):
            return False
        mem_ty = query.mems[self.mem_idx].memory_ty
        return any(mem_ty.eq_llvm(ty) for ty in self.types)

    def __str__(self) -> str:
        return f"mem{self.mem_idx}.type in {{{', '.join(map(str, self.types))}}}"


@dataclass(frozen=True)
class MemAlignCmp(Pred):
    mem_idx: int
    op: str
    value: int

    def eval(self, query: Query) -> bool:
        if self.mem_idx >= len(query.mems):
            return False
        return _cmp(self.op, query.mems[self.mem_idx].align_bits, self.value)

    def __str__(self) -> str:
        return f"mem{self.mem_idx}.align {self.op} {self.value}"


@dataclass(frozen=True)
class MemCompatInSet(Pred):
    """Mirror LegalityPredicates::typePairAndMemDescInSet.

    Each entry is (type0, type1, memory_ty, min_align_bits).  A query matches
    when type0/type1 are exactly equal, the memory type has the same total
    size, and the actual alignment is at least the required alignment.
    """

    i: int
    j: int
    mem_idx: int
    entries: tuple[tuple[LLT, LLT, LLT, int], ...]

    def eval(self, query: Query) -> bool:
        if self.i >= len(query.types) or self.j >= len(query.types):
            return False
        if self.mem_idx >= len(query.mems):
            return False
        mem = query.mems[self.mem_idx]
        for type0, type1, mem_ty, align in self.entries:
            if (
                query.types[self.i].eq_llvm(type0)
                and query.types[self.j].eq_llvm(type1)
                and mem.memory_ty.get_size_in_bits() == mem_ty.get_size_in_bits()
                and mem.align_bits >= align
            ):
                return True
        return False

    def __str__(self) -> str:
        entries = ", ".join(
            f"({a}, {b}, {m}, {al})" for a, b, m, al in self.entries
        )
        return f"mem_compat(t{self.i}, t{self.j}, mem{self.mem_idx}) in {{{entries}}}"


@dataclass(frozen=True)
class ImmCmp(Pred):
    idx: int
    op: str
    value: int

    def eval(self, query: Query) -> bool:
        if self.idx >= len(query.imms):
            return False
        return _cmp(self.op, query.imms[self.idx], self.value)

    def __str__(self) -> str:
        return f"imm{self.idx} {self.op} {self.value}"


@dataclass(frozen=True)
class And(Pred):
    lhs: Pred
    rhs: Pred

    def eval(self, query: Query) -> bool:
        return self.lhs.eval(query) and self.rhs.eval(query)

    def __str__(self) -> str:
        return f"({self.lhs}) and ({self.rhs})"


@dataclass(frozen=True)
class Or(Pred):
    lhs: Pred
    rhs: Pred

    def eval(self, query: Query) -> bool:
        return self.lhs.eval(query) or self.rhs.eval(query)

    def __str__(self) -> str:
        return f"({self.lhs}) or ({self.rhs})"


@dataclass(frozen=True)
class Not(Pred):
    inner: Pred

    def eval(self, query: Query) -> bool:
        return not self.inner.eval(query)

    def __str__(self) -> str:
        if isinstance(self.inner, (And, Or)):
            return f"not ({self.inner})"
        return f"not {self.inner}"


# ---------------------------------------------------------------------------
# Type mutations
# ---------------------------------------------------------------------------


class TypeExpr:
    def eval(self, query: Query) -> LLT:  # pragma: no cover - interface
        raise NotImplementedError

    def __str__(self) -> str:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass(frozen=True)
class SetElementSize(TypeExpr):
    idx: int
    bits: int
    text: str

    def eval(self, query: Query) -> LLT:
        if self.idx >= len(query.types):
            raise DslError(f"type index t{self.idx} is out of range", code="T010")
        return query.types[self.idx].change_element_size(self.bits)

    def __str__(self) -> str:
        return self.text


@dataclass(frozen=True)
class SetElementCount(TypeExpr):
    idx: int
    lanes: int
    text: str

    def eval(self, query: Query) -> LLT:
        if self.idx >= len(query.types):
            raise DslError(f"type index t{self.idx} is out of range", code="T010")
        return query.types[self.idx].change_element_count(self.lanes)

    def __str__(self) -> str:
        return self.text


@dataclass(frozen=True)
class ElementOf(TypeExpr):
    idx: int

    def eval(self, query: Query) -> LLT:
        if self.idx >= len(query.types):
            raise DslError(f"type index t{self.idx} is out of range", code="T010")
        return query.types[self.idx].element_type()

    def __str__(self) -> str:
        return f"element_of(t{self.idx})"


@dataclass(frozen=True)
class SameSizeAs(TypeExpr):
    """Mirror LegalizeMutations::changeElementSizeTo(TypeIdx, FromTypeIdx).

    Both operands are type slots.  The result keeps the shape (scalar or
    vector lane count) of t{idx} and takes the scalar size of t{src_idx}.
    """

    idx: int
    src_idx: int

    def eval(self, query: Query) -> LLT:
        if self.idx >= len(query.types):
            raise DslError(f"type index t{self.idx} is out of range", code="T010")
        if self.src_idx >= len(query.types):
            raise DslError(f"type index t{self.src_idx} is out of range", code="T010")
        return query.types[self.idx].change_element_size(
            query.types[self.src_idx].get_scalar_size_in_bits()
        )

    def __str__(self) -> str:
        return f"same_size_as(t{self.src_idx})"


@dataclass(frozen=True)
class ConcatBitcast(TypeExpr):
    """The bitcast mutation used by AArch64's G_CONCAT_VECTORS rule.

    C++ computes, from the result type ``DstTy`` and one source type
    ``SrcTy``:

        DstTy.changeElementSize(SrcTy.getSizeInBits())
             .changeElementCount(
                 DstTy.getElementCount().divideCoefficientBy(
                     SrcTy.getNumElements()))

    This is not ``same_size_as``: that helper uses the source's *scalar*
    size, while G_CONCAT_VECTORS uses the source's *total* size and then
    divides the result lane count by the source lane count.
    """

    idx: int
    src_idx: int

    def eval(self, query: Query) -> LLT:
        if self.idx >= len(query.types):
            raise DslError(f"type index t{self.idx} is out of range", code="T010")
        if self.src_idx >= len(query.types):
            raise DslError(f"type index t{self.src_idx} is out of range", code="T010")
        dst = query.types[self.idx]
        src = query.types[self.src_idx]
        if not dst.is_vector or not src.is_vector:
            raise DslError(
                f"concat_bitcast requires vectors, got {dst} and {src}",
                code="T016",
            )
        if src.lanes == 0 or dst.lanes % src.lanes != 0:
            raise DslError(
                f"concat_bitcast lane count {dst.lanes} is not divisible by {src.lanes}",
                code="T017",
            )
        return dst.change_element_size(src.get_size_in_bits()).change_element_count(
            dst.lanes // src.lanes
        )

    def __str__(self) -> str:
        return f"concat_bitcast(t{self.src_idx})"


@dataclass(frozen=True)
class SetElementType(TypeExpr):
    """Mirror LegalizeMutations::changeElementTo(TypeIdx, LLT)."""

    idx: int
    new_elem: LLT

    def eval(self, query: Query) -> LLT:
        if self.idx >= len(query.types):
            raise DslError(f"type index t{self.idx} is out of range", code="T010")
        return query.types[self.idx].change_element_type(self.new_elem)

    def __str__(self) -> str:
        return f"change_element_to({self.new_elem})"


@dataclass(frozen=True)
class CopyType(TypeExpr):
    """Replace a slot with the whole type of another slot.

    This is the mutation used by ``maxScalarEltSameAsIf``: LLVM assigns
    ``Types[SmallTypeIdx]`` to ``TypeIdx`` directly, so a scalar amount can
    become a vector when the source is a vector.  It is intentionally not
    ``SameSizeAs``, which would preserve the target slot's shape.
    """

    idx: int
    src_idx: int

    def eval(self, query: Query) -> LLT:
        if self.src_idx >= len(query.types):
            raise DslError(f"type index t{self.src_idx} is out of range", code="T010")
        return query.types[self.src_idx]

    def __str__(self) -> str:
        return f"type_of(t{self.src_idx})"


@dataclass(frozen=True)
class NextPow2(TypeExpr):
    """Mirror LegalizeMutations::widenScalarOrEltToNextPow2."""

    idx: int
    min_bits: int

    def eval(self, query: Query) -> LLT:
        if self.idx >= len(query.types):
            raise DslError(f"type index t{self.idx} is out of range", code="T010")
        ty = query.types[self.idx]
        cur = ty.get_scalar_size_in_bits()
        n = 1
        while n < cur:
            n <<= 1
        return ty.change_element_size(max(n, self.min_bits))

    def __str__(self) -> str:
        return f"next_pow2(bits(t{self.idx}), {self.min_bits})"


@dataclass(frozen=True)
class NextPow2Lanes(TypeExpr):
    """Mirror LegalizeMutations::moreElementsToNextPow2."""

    idx: int
    min_lanes: int = 0

    def eval(self, query: Query) -> LLT:
        if self.idx >= len(query.types):
            raise DslError(f"type index t{self.idx} is out of range", code="T010")
        ty = query.types[self.idx]
        if not ty.is_vector:
            raise DslError(
                f"more_elements_to_next_pow2 requires a vector, got {ty}",
                code="T012",
            )
        lanes = ty.lanes
        new_lanes = 1
        while new_lanes < lanes:
            new_lanes <<= 1
        return ty.change_element_count(max(new_lanes, self.min_lanes))

    def __str__(self) -> str:
        return f"next_pow2(lanes(t{self.idx}), {self.min_lanes})"


@dataclass(frozen=True)
class NextMultiple(TypeExpr):
    """Mirror LegalizeMutations::widenScalarOrEltToNextMultipleOf."""

    idx: int
    size: int

    def eval(self, query: Query) -> LLT:
        if self.idx >= len(query.types):
            raise DslError(f"type index t{self.idx} is out of range", code="T010")
        ty = query.types[self.idx]
        cur = ty.get_scalar_size_in_bits()
        new_bits = ((cur + self.size - 1) // self.size) * self.size
        return ty.change_element_size(new_bits)

    def __str__(self) -> str:
        return f"next_multiple(bits(t{self.idx}), {self.size})"


# ---------------------------------------------------------------------------
# Rules and rule sets
# ---------------------------------------------------------------------------


@dataclass
class Rule:
    action: Action
    pred: Pred
    type_idx: int = 0
    new_type: Optional[TypeExpr] = None
    origin: str = ""
    span: Optional[Span] = None

    def __str__(self) -> str:
        text = ACTION_VERB[self.action]
        if self.new_type is not None:
            text += f" t{self.type_idx} to {self.new_type}"
        if not isinstance(self.pred, Always):
            text += f" when {self.pred}"
        return text


@dataclass
class TraceEntry:
    index: int
    rule: Rule
    matched: bool


class RuleSet:
    def __init__(self, opcodes: Sequence[str], span: Optional[Span] = None):
        if not opcodes:
            raise DslError("a rule set needs at least one opcode", code="W005")
        self.opcodes = tuple(opcodes)
        self.rules: list[Rule] = []
        self.span = span

    def add(self, rule: Rule) -> None:
        self.rules.append(rule)

    def apply(
        self, query: Query, trace: bool = False
    ) -> tuple[Step, list[TraceEntry]]:
        entries: list[TraceEntry] = []
        for index, rule in enumerate(self.rules):
            matched = rule.pred.eval(query)
            if trace:
                entries.append(TraceEntry(index, rule, matched))
            if matched:
                new_type = (
                    rule.new_type.eval(query) if rule.new_type is not None else None
                )
                return (
                    Step(rule.action, rule.type_idx, new_type, index),
                    entries,
                )
        return Step(Action.UNSUPPORTED, 0, None, None), entries


class LegalizerInfo:
    def __init__(self) -> None:
        self.rulesets: list[RuleSet] = []
        self.by_opcode: dict[str, RuleSet] = {}
        self.domain: Optional[Domain] = None

    def add(self, ruleset: RuleSet) -> None:
        # Mirror the alias rule: opcodes listed in one builder share a rule set,
        # and an aliased opcode cannot later receive its own rules.
        for opcode in ruleset.opcodes:
            if opcode in self.by_opcode:
                raise DslError(
                    f"opcode {opcode} already has a rule set; "
                    "opcodes aliased in one ruleset cannot be redefined",
                    code="W003",
                    span=ruleset.span,
                )
            self.by_opcode[opcode] = ruleset
        self.rulesets.append(ruleset)

    def get(self, opcode: str) -> RuleSet:
        try:
            return self.by_opcode[opcode]
        except KeyError:
            raise DslError(f"no rule set for opcode {opcode}", code="W009") from None

    def action(
        self, query: Query, trace: bool = False
    ) -> tuple[Step, list[TraceEntry]]:
        return self.get(query.opcode).apply(query, trace=trace)

    def check(self) -> dict[str, int]:
        return {
            "rulesets": len(self.rulesets),
            "opcodes": len(self.by_opcode),
            "rules": sum(len(rs.rules) for rs in self.rulesets),
        }