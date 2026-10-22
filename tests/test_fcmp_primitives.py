# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Semantics of the G_FCMP float-slice DSL primitives.

Expected values are read from LLVM:

  LLT::scalar / LLT::float16 / LLT::bfloat16        (LowLevelType.h)
  LLT::operator==                                   (LowLevelType.h:540)
  LLT::changeElementSize                            (LowLevelType.h:427)
  LLT::changeElementType                            (LowLevelType.h:421)
  LegalizeMutations::changeElementTo                (LegalizeMutations.cpp:29)
  LegalizeRuleSet::widenScalarIf                    (LegalizerInfo.h)
  LegalizeRuleSet::scalarizeIf                      (LegalizerInfo.h:1026)
  LegalizeRuleSet::clampMaxNumElements              (LegalizerInfo.h:1275)
  LegalizeRuleSet::libcallFor

Two properties are the point of this slice:

1. LLT::scalar(N) is Kind::ANY_SCALAR, so it compares equal to any scalar of
   the same size, including float32.  AArch64 spells its constants with
   LLT::scalar, which is why an s32-keyed lane clamp matches a v6f32 operand.
2. changeElementSize preserves IEEE float semantics but drops them for bf16,
   because bf16 is not in isFloatIEEE().
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legispec import Action, LLT, Query, codegen, parse, parse_llt

SCALAR_IS_FLOAT = """
ruleset G_Q {
    legal when scalar_is_float(t1, f16);
    custom;
}
"""

CHANGE_ELEMENT = """
ruleset G_Q {
    widen t1 to change_element_to(f32)
        when scalar_is_float(t1, f16) or scalar_is_float(t1, bf16);
}
"""

LIBCALL_PAIR = """
ruleset G_Q {
    libcall_for_pairs {(s32, s128)};
}
"""

IS_FLOAT = """
ruleset G_Q {
    legal when not is_float(t0);
    custom;
}
"""

ELEMENT_EQ = """
ruleset G_Q {
    legal when elem_types_eq(t0, t1);
    custom;
}
"""

LANE_CLAMP = """
ruleset G_Q {
    clamp_num_elements 1, v2s32, v4s32;
}
"""


def step(info, *types: str):
    query = Query("G_Q", tuple(parse_llt(t) for t in types))
    return info.action(query, trace=False)[0]


class FloatLLTTests(unittest.TestCase):
    def test_float_spellings_round_trip(self):
        for text in ("f16", "f32", "f64", "f128", "bf16", "v4f32", "v8f16"):
            self.assertEqual(str(parse_llt(text)), text)

    def test_bf16_and_f16_are_different_types(self):
        self.assertNotEqual(parse_llt("bf16"), parse_llt("f16"))
        self.assertFalse(parse_llt("bf16").eq_llvm(parse_llt("f16")))

    def test_any_scalar_equals_same_size_float(self):
        # LLT::scalar(32) == LLT::float32()
        self.assertTrue(parse_llt("s32").eq_llvm(parse_llt("f32")))
        self.assertTrue(parse_llt("s16").eq_llvm(parse_llt("bf16")))
        self.assertFalse(parse_llt("s16").eq_llvm(parse_llt("f32")))
        # Plain dataclass equality stays spelling-exact for dedup/domain use.
        self.assertNotEqual(parse_llt("s32"), parse_llt("f32"))

    def test_vector_equality_follows_element_equality(self):
        self.assertTrue(parse_llt("v4s16").eq_llvm(parse_llt("v4f16")))
        self.assertFalse(parse_llt("v4s16").eq_llvm(parse_llt("v8f16")))

    def test_change_element_size_preserves_ieee_float(self):
        self.assertEqual(
            parse_llt("f16").change_element_size(32), parse_llt("f32")
        )
        self.assertEqual(
            parse_llt("v4f16").change_element_size(32), parse_llt("v4f32")
        )

    def test_change_element_size_drops_bfloat_semantics(self):
        # bf16 is not isFloatIEEE(), so LLVM falls through to LLT::scalar.
        result = parse_llt("bf16").change_element_size(32)
        self.assertEqual(result, LLT.scalar(32))
        self.assertFalse(result.is_float)

    def test_is_scalar_includes_float(self):
        self.assertTrue(parse_llt("f32").is_scalar)
        self.assertTrue(parse_llt("bf16").is_scalar)


class FCMPPredicateTests(unittest.TestCase):
    def test_scalar_is_float_matches_scalar_and_element(self):
        info = parse(SCALAR_IS_FLOAT)
        self.assertEqual(step(info, "s32", "f16").action, Action.LEGAL)
        self.assertEqual(step(info, "v4s1", "v4f16").action, Action.LEGAL)

    def test_scalar_is_float_uses_semantics_not_width(self):
        info = parse(SCALAR_IS_FLOAT)
        self.assertEqual(step(info, "s32", "bf16").action, Action.CUSTOM)
        self.assertEqual(step(info, "s32", "s16").action, Action.CUSTOM)

    def test_is_float_covers_scalars_and_vectors(self):
        info = parse(IS_FLOAT)
        self.assertEqual(step(info, "s32", "f32").action, Action.LEGAL)
        self.assertEqual(step(info, "v4s32", "v4f32").action, Action.LEGAL)
        self.assertEqual(step(info, "f32", "f32").action, Action.CUSTOM)
        self.assertEqual(step(info, "v4f32", "v4f32").action, Action.CUSTOM)

    def test_elem_types_eq_uses_any_scalar_equality(self):
        info = parse(ELEMENT_EQ)
        # s32 and f32 elements compare equal under LLT::operator==.
        self.assertEqual(step(info, "v4s32", "v4f32").action, Action.LEGAL)
        # 16 vs 32 bits still differ.
        self.assertEqual(step(info, "v4s16", "v4f32").action, Action.CUSTOM)


class FCMPMacroTests(unittest.TestCase):
    def test_change_element_to_widens_f16(self):
        info = parse(CHANGE_ELEMENT)
        result = step(info, "s32", "f16")
        self.assertEqual(result.action, Action.WIDEN)
        self.assertEqual(result.type_idx, 1)
        self.assertEqual(result.new_type, parse_llt("f32"))

    def test_change_element_to_widens_bfloat(self):
        info = parse(CHANGE_ELEMENT)
        result = step(info, "s32", "bf16")
        self.assertEqual(result.new_type, parse_llt("f32"))

    def test_change_element_to_preserves_lanes(self):
        info = parse(CHANGE_ELEMENT)
        result = step(info, "v4s1", "v4f16")
        self.assertEqual(result.new_type, parse_llt("v4f32"))

    def test_libcall_for_pairs_matches_any_128_bit_scalar(self):
        info = parse(LIBCALL_PAIR)
        self.assertEqual(step(info, "s32", "s128").action, Action.LIBCALL)
        # The pair constant s128 is LLT::scalar(128), i.e. ANY_SCALAR, so it
        # also matches a 128-bit float operand.
        self.assertEqual(step(info, "s32", "f128").action, Action.LIBCALL)

    def test_libcall_for_pairs_still_checks_the_result_slot(self):
        info = parse(LIBCALL_PAIR)
        self.assertEqual(step(info, "s64", "s128").action, Action.UNSUPPORTED)

    def test_lane_clamp_matches_float_by_size(self):
        info = parse(LANE_CLAMP)
        result = step(info, "v6s32", "v6f32")
        self.assertEqual(result.action, Action.FEWER)
        self.assertEqual(result.type_idx, 1)
        self.assertEqual(result.new_type, parse_llt("v4f32"))


class FCMPCodegenTests(unittest.TestCase):
    def test_float_primitives_have_cpp_templates(self):
        info = parse(
            """
            ruleset G_Q {
                legal_for_pairs {(s32, f32)};
                widen t1 to change_element_to(f32)
                    when scalar_is_float(t1, f16) or scalar_is_float(t1, bf16);
                libcall_for_pairs {(s32, s128)};
                custom when is_float(t0);
            }
            """
        )
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            codegen.generate(
                info, out_dir=out, rules_rel="inline", rules_sha="0" * 64
            )
            cpp = (out / codegen.GENERATED_SOURCE).read_text(encoding="utf-8")

        self.assertIn("LLT::float32()", cpp)
        self.assertIn("changeElementTo(1, f32)", cpp)
        self.assertIn("isFloat16()", cpp)
        self.assertIn("isBFloat16()", cpp)
        self.assertIn("isFloatOrFloatVector()", cpp)
        self.assertIn("libcallFor({{s32, s128}})", cpp)


if __name__ == "__main__":
    unittest.main()
