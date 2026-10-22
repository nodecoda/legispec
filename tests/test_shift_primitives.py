# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0.
# See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0
#
# ===----------------------------------------------------------------------===##

"""Semantics of the shift same-as DSL primitives.

Expected values are read from LLVM's inline builder methods:

  LegalizeRuleSet::minScalarSameAs            (LegalizerInfo.h:1162)
  LegalizeRuleSet::minScalarEltSameAsIf       (LegalizerInfo.h:1193)
  LegalizeRuleSet::maxScalarEltSameAsIf       (LegalizerInfo.h:1210)

Two asymmetries matter and are pinned here:

  * minScalarSameAs compares a scalar size against the *total* size of the
    other slot;
  * maxScalarEltSameAsIf assigns the whole source type, so a scalar amount
    can become a vector.
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legispec import Action, LLT, Query, codegen, parse, parse_llt

RULES = """
ruleset G_SH {
    custom when not vector(t0) and bits(t0) == 32 and bits(t1) == 32;
    legal_for_pairs {(s64, s64)};
    min_scalar_same_as 1, 0;
    min_scalar_elt_same_as_if 1, 0 when vector(t0);
    max_scalar_elt_same_as_if 1, 0 when vector(t0);
}
"""

INFO = parse(RULES)


def step_for(*types: str):
    query = Query("G_SH", tuple(parse_llt(t) for t in types))
    step, _ = INFO.action(query, trace=False)
    return step


class ShiftSameAsTests(unittest.TestCase):
    def test_custom_if_precedes_legal_for(self):
        step = step_for("s32", "s32")
        self.assertEqual(step.action, Action.CUSTOM)
        self.assertEqual(step.rule_index, 0)

    def test_legal_pair(self):
        step = step_for("s64", "s64")
        self.assertEqual(step.action, Action.LEGAL)
        self.assertEqual(step.rule_index, 1)

    def test_min_scalar_same_as_widens_scalar_amount(self):
        step = step_for("s64", "s32")
        self.assertEqual(step.action, Action.WIDEN)
        self.assertEqual(step.type_idx, 1)
        self.assertEqual(step.new_type, LLT.scalar(64))
        self.assertEqual(step.rule_index, 2)

    def test_min_scalar_same_as_compares_against_total_vector_size(self):
        # t0 scalar size is 64; t1 total size is 2 * 32 == 64, so the
        # minScalarSameAs rule must NOT fire.  The next rule does.
        step = step_for("v2s64", "v2s32")
        self.assertEqual(step.action, Action.WIDEN)
        self.assertEqual(step.type_idx, 1)
        self.assertEqual(step.new_type, LLT.vector(2, LLT.scalar(64)))
        self.assertEqual(step.rule_index, 3)

    def test_min_scalar_elt_same_as_if_widens_to_source_element_size(self):
        # t1 has 8 lanes of s8 (total 64), so minScalarSameAs' 64 > 64 is
        # false and the element-wise rule is the one that fires.
        step = step_for("v4s64", "v8s8")
        self.assertEqual(step.action, Action.WIDEN)
        self.assertEqual(step.type_idx, 1)
        self.assertEqual(step.new_type, LLT.vector(8, LLT.scalar(64)))
        self.assertEqual(step.rule_index, 3)

    def test_max_scalar_elt_same_as_if_copies_the_whole_source_type(self):
        # t0 is v2s32, t1 is scalar s64: LLVM assigns Types[0] to slot 1,
        # so the result is a vector, not a scalar s32.
        step = step_for("v2s32", "s64")
        self.assertEqual(step.action, Action.NARROW)
        self.assertEqual(step.type_idx, 1)
        self.assertEqual(step.new_type, LLT.vector(2, LLT.scalar(32)))
        self.assertEqual(step.rule_index, 4)

    def test_elt_rules_are_guarded_by_vector_source(self):
        # t0 is scalar, so neither minScalarEltSameAsIf nor
        # maxScalarEltSameAsIf may fire.  minScalarSameAs sees 64 > 64,
        # which is false because t1's total size is 64.
        step = step_for("s64", "v2s32")
        self.assertEqual(step.action, Action.UNSUPPORTED)
        self.assertIsNone(step.rule_index)

    def test_codegen_emits_both_mutation_shapes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            codegen.generate(
                INFO,
                out_dir=out,
                rules_rel="probe.lspec",
                rules_sha="0" * 64,
            )
            source = (out / codegen.GENERATED_SOURCE).read_text(
                encoding="utf-8"
            )
        self.assertIn("getScalarSizeInBits() > Q.Types[1].getSizeInBits()", source)
        self.assertIn("changeElementSizeTo(1, 0)", source)
        self.assertIn("std::make_pair(1, Q.Types[0])", source)
        self.assertIn(".widenScalarIf(", source)
        self.assertIn(".narrowScalarIf(", source)


if __name__ == "__main__":
    unittest.main()
