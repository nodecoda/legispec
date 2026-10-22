# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Semantics of the G_ICMP result/operand DSL primitives.

Expected values are read from LLVM:

  LegalityPredicates::isPointerVector
  LegalityPredicates::scalarOrEltSizeNotPow2
  LegalityPredicates::scalarOrEltNarrowerThan
  LegalizeRuleSet::minScalarOrElt            (LegalizerInfo.h:1034)
  LegalizeRuleSet::minScalarOrEltIf          (LegalizerInfo.h:1043)
  LegalizeRuleSet::widenScalarOrEltToNextPow2(LegalizerInfo.h:994)

The G_ICMP slice is the first one where Types[0] is the result mask and
Types[1] is the operand, so the predicates below deliberately read both slots.
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legispec import Action, LLT, Query, codegen, parse, parse_llt

POINTER_VECTOR = """
ruleset G_Q {
    legal when is_pointer_vector(t1);
    custom;
}
"""

ELEM_TYPES = """
ruleset G_Q {
    legal when elem_types_eq(t0, t1);
    custom;
}
"""

MIN_ELT = """
ruleset G_Q {
    min_scalar_or_elt 1, s32;
}
"""

NEXT_POW2 = """
ruleset G_Q {
    widen_scalar_or_elt_to_next_pow2 0;
}
"""

PREDICATE_FORM = """
ruleset G_Q {
    min_scalar_or_elt_if lanes(t1) <= 2, 1, s32;
}
"""

LANES_FORM = """
ruleset G_Q {
    min_scalar_or_elt_if 1, lanes <= 2, s32;
}
"""


def step(info, *types: str):
    query = Query("G_Q", tuple(parse_llt(t) for t in types))
    return info.action(query, trace=False)[0]


class ICMPPredicateTests(unittest.TestCase):
    def test_pointer_vector_is_true_for_pointer_lanes(self):
        info = parse(POINTER_VECTOR)
        self.assertEqual(step(info, "v2s64", "v2p0:64").action, Action.LEGAL)

    def test_pointer_vector_is_false_for_scalar_pointer(self):
        info = parse(POINTER_VECTOR)
        self.assertEqual(step(info, "v2s64", "p0:64").action, Action.CUSTOM)

    def test_pointer_vector_is_false_for_integer_vector(self):
        info = parse(POINTER_VECTOR)
        self.assertEqual(step(info, "v2s64", "v2s64").action, Action.CUSTOM)

    def test_elem_types_eq_compares_types_not_widths(self):
        info = parse(ELEM_TYPES)
        # Same element width (64), different element type.
        self.assertEqual(step(info, "v2p0:64", "v2s64").action, Action.CUSTOM)
        self.assertEqual(step(info, "v2s64", "v2s64").action, Action.LEGAL)


class ICMPMacroTests(unittest.TestCase):
    def test_min_scalar_or_elt_widens_vector_element(self):
        info = parse(MIN_ELT)
        result = step(info, "v4s16", "v4s16")
        self.assertEqual(result.action, Action.WIDEN)
        self.assertEqual(result.type_idx, 1)
        self.assertEqual(result.new_type, LLT.vector(4, LLT.scalar(32)))

    def test_min_scalar_or_elt_ignores_wide_element(self):
        info = parse(MIN_ELT)
        # No rule matches; the rule set falls back to Unsupported.
        self.assertEqual(step(info, "v4s32", "v4s32").action, Action.UNSUPPORTED)

    def test_next_pow2_preserves_lane_count(self):
        info = parse(NEXT_POW2)
        result = step(info, "v3s24", "v3s24")
        self.assertEqual(result.action, Action.WIDEN)
        self.assertEqual(result.type_idx, 0)
        self.assertEqual(result.new_type, LLT.vector(3, LLT.scalar(32)))

    def test_next_pow2_widens_scalar(self):
        info = parse(NEXT_POW2)
        result = step(info, "s24", "s24")
        self.assertEqual(result.new_type, LLT.scalar(32))

    def test_next_pow2_declines_on_power_of_two(self):
        info = parse(NEXT_POW2)
        self.assertEqual(step(info, "v3s32", "v3s32").action, Action.UNSUPPORTED)

    def test_predicate_form_matches_legacy_lanes_form(self):
        pred = parse(PREDICATE_FORM)
        lanes = parse(LANES_FORM)
        for types in (("v2s8", "v2s8"), ("v4s8", "v4s8"), ("s8", "s8")):
            a = step(pred, *types)
            b = step(lanes, *types)
            self.assertEqual(
                (a.action, a.type_idx, a.new_type, a.rule_index),
                (b.action, b.type_idx, b.new_type, b.rule_index),
                types,
            )


class ICMPCodegenTests(unittest.TestCase):
    def test_new_primitives_have_cpp_templates(self):
        info = parse(
            """
            ruleset G_Q {
                widen_scalar_or_elt_to_next_pow2 0;
                min_scalar_or_elt 1, s32;
                min_scalar_or_elt_if is_pointer_vector(t1), 0, s64;
                custom when vector(t0) and not elem_types_eq(t0, t1);
            }
            """
        )
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            codegen.generate(info, out_dir=out, rules_rel="inline", rules_sha="0" * 64)
            cpp = (out / codegen.GENERATED_SOURCE).read_text(encoding="utf-8")

        self.assertIn("widenScalarOrEltToNextPow2(0, 0)", cpp)
        self.assertIn("changeElementSizeTo(1, s32)", cpp)
        self.assertIn("isPointerVector(1)", cpp)
        self.assertIn("getElementType() ==", cpp)


if __name__ == "__main__":
    unittest.main()
