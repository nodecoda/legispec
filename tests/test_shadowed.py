# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""The `shadowed` witness category.

`unreachable` means the predicate is unsatisfiable in D.  `shadowed` means the
predicate is satisfiable, but an earlier rule always wins.  The two are
different facts about the source and must not be conflated:

  * R003: declared shadowed, but no query matches the predicate
  * R004: declared shadowed, but the rule wins for some query
  * R005: declared shadowed, and every match is preceded
"""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legalizer_dsl.checker import analysis, reachability
from legalizer_dsl.parser import parse
from legalizer_dsl.witness import parse_witness_text, run_witnesses

RULES = """
domain {
    types { s8, s16, s32 };
    imms {};
    mems {};
    q0 { (G_T, [s8]); }
}

ruleset G_T {
    widen t0 to s32 when bits(t0) < 32;
    widen t0 to s16 when bits(t0) < 16;
    custom when bits(t0) == 128;
}
"""

INFO = parse(RULES)


def _witness(extra: str) -> str:
    return f"""
witness probe {{
    query = (G_T, [s8])
    {extra}
}}
"""


class ShadowedWitnessTests(unittest.TestCase):
    def test_shadowed_witness_is_accepted_and_covers_the_rule(self):
        witnesses = parse_witness_text(_witness("shadowed = 1"))
        report = run_witnesses(INFO, witnesses)
        self.assertEqual(report.shadowed, 1)
        self.assertEqual(report.failures, [])
        self.assertNotIn("G_T rule 1", report.uncovered_rules)

    def test_shadowed_witness_fails_when_the_rule_wins(self):
        witnesses = parse_witness_text(_witness("shadowed = 0"))
        report = run_witnesses(INFO, witnesses)
        self.assertTrue(
            any("declared shadowed but rule 0 won" in f for f in report.failures),
            report.failures,
        )

    def test_shadowed_witness_rejects_out_of_range_index(self):
        witnesses = parse_witness_text(_witness("shadowed = 99"))
        report = run_witnesses(INFO, witnesses)
        self.assertTrue(
            any("out of range" in f for f in report.failures), report.failures
        )

    def test_unreachable_and_shadowed_are_mutually_exclusive(self):
        from legalizer_dsl.diagnostics import DslError

        with self.assertRaises(DslError):
            parse_witness_text(
                """
                witness probe {
                    query = (G_T, [s8])
                    shadowed = 1
                    unreachable = 2
                }
                """
            )


class ShadowedReachabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analyses = analysis.analyze_all(INFO)

    def _codes(self, extra: str, strict: bool = True):
        witnesses = parse_witness_text(_witness(extra))
        diagnostics = reachability.run(
            INFO, self.analyses, witnesses=witnesses, strict=strict
        )
        return {d.code for d in diagnostics}

    def test_correct_shadowed_declaration_is_info_r005(self):
        self.assertIn("R005", self._codes("shadowed = 1"))

    def test_shadowed_without_predicate_matches_is_r003(self):
        self.assertIn("R003", self._codes("shadowed = 2"))

    def test_shadowed_but_winning_is_r004(self):
        self.assertIn("R004", self._codes("shadowed = 0"))


if __name__ == "__main__":
    unittest.main()
