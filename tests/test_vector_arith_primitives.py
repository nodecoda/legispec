# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0.
# See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0
#
# ===----------------------------------------------------------------------===##

"""Semantics of the vector-arith DSL primitives.

Expected values are read from LLVM:

  LegalityPredicates::numElementsNotPow2
  LegalizeMutations::moreElementsToNextPow2
  LegalizeRuleSet::minScalarOrEltIf
  LegalizeRuleSet::scalarizeIf

These tests cover the primitives in isolation; the AArch64 G_ADD family
conformance report covers the composed ruleset.
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legispec import Action, LLT, Query, codegen, parse, parse_llt

RULES = """
ruleset G_X {
    legal_for {v8s8};
    min_scalar_or_elt_if 0, lanes <= 2, s32;
    fewer t0 to element_of(t0) when vector(t0) and elem_bits(t0) > 64;
    more_elements_to_next_pow2 0;
}
"""

INFO = parse(RULES)
MORE_ONLY = parse("ruleset G_Y { more_elements_to_next_pow2 0; }")


def run(*types: str, info=INFO, opcode="G_X"):
    query = Query(opcode, tuple(parse_llt(t) for t in types))
    step, _ = info.action(query, trace=False)
    return step


class VectorArithPrimitiveTests(unittest.TestCase):
    def test_legal_for_wins_first(self):
        self.assertEqual(run("v8s8").action, Action.LEGAL)

    def test_min_scalar_or_elt_if_widens_short_vectors(self):
        step = run("v2s8")
        self.assertEqual(step.action, Action.WIDEN)
        self.assertEqual(step.type_idx, 0)
        self.assertEqual(step.new_type, LLT.vector(2, LLT.scalar(32)))

    def test_scalarize_if_returns_the_element_type(self):
        step = run("v2s128")
        self.assertEqual(step.action, Action.FEWER)
        self.assertEqual(step.type_idx, 0)
        self.assertEqual(step.new_type, LLT.scalar(128))

    def test_more_elements_to_next_pow2_rounds_up_lanes(self):
        step = run("v3s16")
        self.assertEqual(step.action, Action.MORE)
        self.assertEqual(step.type_idx, 0)
        self.assertEqual(step.new_type, LLT.vector(4, LLT.scalar(16)))

    def test_num_elements_not_pow2_ignores_power_of_two_vectors(self):
        self.assertEqual(
            run("v4s16", info=MORE_ONLY, opcode="G_Y").action,
            Action.UNSUPPORTED,
        )

    def test_num_elements_not_pow2_ignores_scalars(self):
        self.assertEqual(
            run("s24", info=MORE_ONLY, opcode="G_Y").action,
            Action.UNSUPPORTED,
        )

    def test_codegen_emits_the_lane_rounding_lambda(self):
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
        self.assertIn(".moreElementsIf(", source)
        self.assertIn("isFixedVector()", source)
        self.assertIn("changeElementCount(N)", source)
        self.assertIn(".widenScalarIf(", source)
        self.assertIn(".fewerElementsIf(", source)


if __name__ == "__main__":
    unittest.main()
