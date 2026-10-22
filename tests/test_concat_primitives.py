# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Semantics of the G_CONCAT_VECTORS DSL primitives.

Expected values are read from LLVM:

  LLT::isFixedVector                      (LowLevelType.h:369)
  LLT::getNumElements / getElementType     (LowLevelType.h:353-374)
  LLT::changeElementSize                   (LowLevelType.h:428)
  LLT::changeElementCount                  (LowLevelType.h:454)
  ElementCount::divideCoefficientBy        (TypeSize.h:252)
  isPowerOf2_64                            (MathExtras.h)
  LegalizeMutations::changeElementSizeTo   (LegalizeMutations.cpp)

Four properties are the point of this slice:

1. LLT equality is not spelling equality: ConcatShape / ConcatShapeAny compare
   element types with LLT::operator==, so a scalar constant matches a
   same-size float.
2. `is_pow2(elem_bits(tN))` and `is_pow2(bits(tN))` differ exactly when the
   lane count is not a power of two (v3s8: element 8 is, total 24 is not).
   AArch64's G_CONCAT_VECTORS rule tests the *element* size.
3. `concat_bitcast(t1)` is not `same_size_as(t1)`: it uses the source's total
   size and then divides the result lane count by the source lane count.
   The domain uses the n-ary opcode contract (`concat_shape_any`), not the
   two-source special case: MachineVerifier requires `lanes(dst) == k *
   lanes(src)` with `k >= 2`, and the legality query recovers k from the two
   type slots.
4. When the lane count does not divide, the C++ lambda would compute a
   zero-lane LLT.  The DSL refuses that with T017; the interpreter treats the
   resulting state as a domain boundary instead of feeding it back into the
   rule set (see interp.legalize).
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legalizer_dsl import Action, Query, codegen, parse, parse_llt
from legalizer_dsl.conformance import (
    _closure_outside_d,
    closure_queries,
    iter_d_queries,
)
from legalizer_dsl.interp import verify_domain
from legalizer_dsl.parser import parse_file

HERE = pathlib.Path(__file__).resolve().parents[1]
CONCAT_RULES = HERE / "rules" / "aarch64-concat-vectors.lspec"

FIXED_VECTOR = """
ruleset G_Q {
    legal when fixed_vector(t0);
    custom;
}
"""

CONCAT_SHAPE = """
ruleset G_Q {
    legal when concat_shape(t0, t1);
    custom;
}
"""

CONCAT_SHAPE_ANY = """
ruleset G_Q {
    legal when concat_shape_any(t0, t1);
    custom;
}
"""

ELEM_POW2 = """
ruleset G_Q {
    legal when is_pow2(elem_bits(t0));
    custom;
}
"""

TOTAL_POW2 = """
ruleset G_Q {
    legal when is_pow2(bits(t0));
    custom;
}
"""

CONCAT_BITCAST = """
ruleset G_Q {
    bitcast t0 to concat_bitcast(t1) when concat_shape(t0, t1);
}
"""

SAME_SIZE_AS = """
ruleset G_Q {
    bitcast t0 to same_size_as(t1) when concat_shape(t0, t1);
}
"""


def step(info, *types: str, opcode: str = "G_Q"):
    query = Query(opcode, tuple(parse_llt(t) for t in types))
    return info.action(query, trace=False)[0]


class FixedVectorTests(unittest.TestCase):
    def test_fixed_vector_accepts_vectors_only(self):
        info = parse(FIXED_VECTOR)
        self.assertEqual(step(info, "v4s32").action, Action.LEGAL)
        self.assertEqual(step(info, "v4f32").action, Action.LEGAL)
        self.assertEqual(step(info, "v3s8").action, Action.LEGAL)
        self.assertEqual(step(info, "s32").action, Action.CUSTOM)
        self.assertEqual(step(info, "p0").action, Action.CUSTOM)


class ConcatShapeTests(unittest.TestCase):
    def test_two_source_relation(self):
        info = parse(CONCAT_SHAPE)
        self.assertEqual(step(info, "v16s8", "v8s8").action, Action.LEGAL)
        self.assertEqual(step(info, "v8s16", "v4s16").action, Action.LEGAL)
        # lanes(t0) must be exactly 2 * lanes(t1)
        self.assertEqual(step(info, "v8s8", "v8s8").action, Action.CUSTOM)
        self.assertEqual(step(info, "v4s8", "v8s8").action, Action.CUSTOM)

    def test_element_types_use_llvm_equality(self):
        info = parse(CONCAT_SHAPE)
        # LLT::scalar(16) == LLT::float16()
        self.assertEqual(step(info, "v8s16", "v4f16").action, Action.LEGAL)
        # different element widths are different element types
        self.assertEqual(step(info, "v16s8", "v8s16").action, Action.CUSTOM)

    def test_scalars_are_not_concat_shapes(self):
        info = parse(CONCAT_SHAPE)
        self.assertEqual(step(info, "s64", "s32").action, Action.CUSTOM)


class ConcatShapeAnyTests(unittest.TestCase):
    def test_any_source_count_follows_the_verifier_contract(self):
        info = parse(CONCAT_SHAPE_ANY)
        # k = 2, 4, 8 sources
        self.assertEqual(step(info, "v8s8", "v4s8").action, Action.LEGAL)
        self.assertEqual(step(info, "v16s8", "v4s8").action, Action.LEGAL)
        self.assertEqual(step(info, "v16s8", "v2s8").action, Action.LEGAL)
        # k = 1 is not a concat (the verifier requires at least two sources)
        self.assertEqual(step(info, "v4s8", "v4s8").action, Action.CUSTOM)
        # non-integral ratio has no k at all
        self.assertEqual(step(info, "v2s32", "v4s8").action, Action.CUSTOM)
        # element types must still be LLT-equal
        self.assertEqual(step(info, "v16s8", "v4s16").action, Action.CUSTOM)
        # scalars are not vectors
        self.assertEqual(step(info, "s64", "s32").action, Action.CUSTOM)

    def test_two_source_is_the_k_equals_two_special_case(self):
        two = parse(CONCAT_SHAPE)
        any_ = parse(CONCAT_SHAPE_ANY)
        for dst, src in (("v16s8", "v8s8"), ("v8s16", "v4s16"), ("v4s32", "v2s32")):
            self.assertEqual(step(two, dst, src).action, Action.LEGAL)
            self.assertEqual(step(any_, dst, src).action, Action.LEGAL)
        # k = 4 is outside the two-source predicate but inside the opcode
        self.assertEqual(step(two, "v16s8", "v4s8").action, Action.CUSTOM)
        self.assertEqual(step(any_, "v16s8", "v4s8").action, Action.LEGAL)


class Pow2Tests(unittest.TestCase):
    def test_element_and_total_size_differ_for_odd_lane_counts(self):
        elem = parse(ELEM_POW2)
        total = parse(TOTAL_POW2)
        # v3s8: element 8 is a power of two, total 24 is not.
        self.assertEqual(step(elem, "v3s8").action, Action.LEGAL)
        self.assertEqual(step(total, "v3s8").action, Action.CUSTOM)
        # v4s8: both are.
        self.assertEqual(step(elem, "v4s8").action, Action.LEGAL)
        self.assertEqual(step(total, "v4s8").action, Action.LEGAL)
        # v3s24: neither is.
        self.assertEqual(step(elem, "v3s24").action, Action.CUSTOM)
        self.assertEqual(step(total, "v3s24").action, Action.CUSTOM)


class ConcatBitcastTests(unittest.TestCase):
    def test_result_uses_source_total_size_and_lane_division(self):
        info = parse(CONCAT_BITCAST)
        cases = {
            ("v8s8", "v4s8"): "v2s32",
            ("v4s16", "v2s16"): "v2s32",
            ("v4s8", "v2s8"): "v2s16",
            ("v16s8", "v8s8"): "v2s64",
        }
        for (dst, src), expected in cases.items():
            result = step(info, dst, src)
            self.assertEqual(result.action, Action.BITCAST)
            self.assertEqual(result.type_idx, 0)
            self.assertEqual(str(result.new_type), expected)

    def test_bitcast_preserves_total_size(self):
        info = parse(CONCAT_BITCAST)
        for dst, src in (("v8s8", "v4s8"), ("v4s16", "v2s16")):
            result = step(info, dst, src)
            before = parse_llt(dst).get_size_in_bits()
            after = parse_llt(src).get_size_in_bits() * (
                parse_llt(dst).lanes // parse_llt(src).lanes
            )
            self.assertEqual(before, after)

    def test_concat_bitcast_is_not_same_size_as(self):
        info = parse(SAME_SIZE_AS)
        result = step(info, "v8s8", "v4s8")
        # same_size_as takes the source *scalar* size and keeps the shape.
        self.assertEqual(str(result.new_type), "v8s8")

    def test_non_divisible_lane_count_is_rejected(self):
        # A predicate that only checks vector-ness lets a non-divisible shape
        # reach the mutation.  C++ would compute 2 / 4 == 0 lanes; the DSL
        # makes that undefined instead of inventing a zero-lane LLT.
        info = parse(
            """
            ruleset G_Q {
                bitcast t0 to concat_bitcast(t1)
                    when fixed_vector(t0) and fixed_vector(t1);
            }
            """
        )
        query = Query(
            "G_Q", (parse_llt("v2s32"), parse_llt("v4s8"))
        )
        with self.assertRaises(Exception) as caught:
            info.action(query)
        self.assertIn("T017", str(caught.exception))


class ConcatCodegenTests(unittest.TestCase):
    def test_primitives_have_cpp_templates(self):
        info = parse(
            """
            domain {
                types { v8s8, v4s8, v4s16, v2s16 };
                filter concat_shape(t0, t1);
                q0 { (G_Q, [v8s8, v4s8]); }
            }
            ruleset G_Q {
                bitcast t0 to concat_bitcast(t1)
                    when concat_shape(t0, t1)
                         and fixed_vector(t0) and is_pow2(elem_bits(t0));
            }
            """
        )
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            codegen.generate(
                info, out_dir=out, rules_rel="inline", rules_sha="0" * 64
            )
            cpp = (out / codegen.GENERATED_SOURCE).read_text(encoding="utf-8")

        self.assertIn("Q.Types[0].isFixedVector()", cpp)
        self.assertIn("Dst.getNumElements() == 2 * Src.getNumElements()", cpp)
        self.assertIn("getScalarSizeInBits()", cpp)
        self.assertIn("divideCoefficientBy", cpp)
        self.assertIn("getElementCount()", cpp)


class ConcatRulesFileTests(unittest.TestCase):
    def test_declared_domain_is_the_n_ary_concat_contract(self):
        info = parse_file(str(CONCAT_RULES))
        queries = list(iter_d_queries(info))
        self.assertEqual(len(queries), 28)
        for query in queries:
            self.assertGreaterEqual(len(query.types), 2)
            self.assertTrue(query.types[0].is_vector)
            self.assertTrue(query.types[1].is_vector)
            self.assertEqual(
                query.types[0].lanes % query.types[1].lanes, 0
            )
            self.assertGreaterEqual(
                query.types[0].lanes // query.types[1].lanes, 2
            )
            self.assertTrue(
                query.types[0].elem.eq_llvm(query.types[1].elem)
            )

    def test_rule_actions(self):
        info = parse_file(str(CONCAT_RULES))
        expected = {
            ("v16s8", "v8s8"): ("Legal", None),
            ("v8s16", "v4s16"): ("Legal", None),
            ("v4s32", "v2s32"): ("Legal", None),
            ("v16s1", "v8s1"): ("Custom", None),
            ("v8s4", "v4s4"): ("Custom", None),
            ("v8s32", "v4s32"): ("Unsupported", None),
            ("v4s8", "v2s8"): ("Bitcast", "v2s16"),
            ("v8s8", "v4s8"): ("Bitcast", "v2s32"),
            ("v4s16", "v2s16"): ("Bitcast", "v2s32"),
            # k = 4 and k = 8: the same rules, a different source count
            ("v16s8", "v4s8"): ("Bitcast", "v4s32"),
            ("v16s8", "v2s8"): ("Bitcast", "v8s16"),
            ("v8s8", "v2s8"): ("Bitcast", "v4s16"),
            ("v16s16", "v8s16"): ("Unsupported", None),
        }
        for (dst, src), (action, new_type) in expected.items():
            result = step(info, dst, src, opcode="G_CONCAT_VECTORS")
            self.assertEqual(result.action.value, action)
            self.assertEqual(
                str(result.new_type) if result.new_type is not None else None,
                new_type,
            )

    def test_bitcast_successor_leaves_the_domain(self):
        info = parse_file(str(CONCAT_RULES))
        boundary = []
        closure = closure_queries(info, boundary=boundary)
        self.assertFalse(_closure_outside_d(info, closure))
        self.assertEqual(len(boundary), 2)
        for query in boundary:
            self.assertNotEqual(query.types[0].lanes, 2 * query.types[1].lanes)
            self.assertFalse(info.domain.accepts(query))

    def test_domain_terminates_with_boundary_states(self):
        info = parse_file(str(CONCAT_RULES))
        report = verify_domain(info)
        self.assertEqual(report.total, 28)
        self.assertEqual(report.terminated, 21)
        self.assertEqual(len(report.out_of_domain), 7)
        self.assertTrue(report.ok)


if __name__ == "__main__":
    unittest.main()
