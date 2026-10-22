# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Phase 3: static checker fixtures (one positive/negative per rule family)."""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legispec.checker import run_checks
from legispec.diagnostics import DslError, Severity
from legispec.parser import parse, parse_file
from legispec.witness import parse_witness_file

HERE = pathlib.Path(__file__).resolve().parents[1]
RULES = HERE / "rules" / "toycpu.lspec"
WITNESS = HERE / "witness" / "toycpu.witness"


def _codes(diagnostics):
    return {d.code for d in diagnostics}


def _by_code(diagnostics, code):
    return [d for d in diagnostics if d.code == code]


class CheckerFixtureTests(unittest.TestCase):
    def test_dead_rule_is_error_under_strict(self):
        info = parse(
            """
            domain { types { s32 }; q0 { (G_X, [s32]); } }
            ruleset G_X { legal when bits(t0) < 1; }
            """
        )
        diagnostics = run_checks(info, strict=True)
        self.assertIn("R001", _codes(diagnostics))
        self.assertTrue(any(d.severity is Severity.ERROR for d in _by_code(diagnostics, "R001")))

    def test_dead_rule_is_warn_without_strict(self):
        info = parse(
            """
            domain { types { s32 }; q0 { (G_X, [s32]); } }
            ruleset G_X { legal when bits(t0) < 1; }
            """
        )
        diagnostics = run_checks(info, strict=False)
        self.assertTrue(all(d.severity is not Severity.ERROR for d in _by_code(diagnostics, "R001")))

    def test_shadowed_rule_is_reported(self):
        info = parse(
            """
            domain { types { s32 }; q0 { (G_X, [s32]); } }
            ruleset G_X {
                legal when t0 == s32;
                custom when t0 == s32;
            }
            """
        )
        diagnostics = run_checks(info, strict=True)
        self.assertIn("R002", _codes(diagnostics))

    def test_order_sensitive_overlap_is_reported(self):
        info = parse(
            """
            domain { types { s32 }; q0 { (G_X, [s32]); } }
            ruleset G_X {
                legal when scalar(t0);
                custom when always;
            }
            """
        )
        diagnostics = run_checks(info, strict=True)
        self.assertIn("K001", _codes(diagnostics))
        self.assertIn("K002", _codes(diagnostics))

    def test_bad_narrow_direction_is_m001(self):
        info = parse(
            """
            domain { types { s32, s64 }; q0 { (G_X, [s32]); } }
            ruleset G_X { narrow t0 to s64 when t0 == s32; }
            """
        )
        diagnostics = run_checks(info, strict=True)
        self.assertIn("M001", _codes(diagnostics))

    def test_self_loop_is_t001(self):
        info = parse(
            """
            domain { types { s32 }; q0 { (G_X, [s32]); } }
            ruleset G_X { narrow t0 to s32 when t0 == s32; }
            """
        )
        diagnostics = run_checks(info, strict=True)
        self.assertIn("T001", _codes(diagnostics))

    def test_type_cycle_is_t002(self):
        info = parse(
            """
            domain { types { s32, s64 }; q0 { (G_X, [s32]); } }
            ruleset G_X {
                widen t0 to s64 when t0 == s32;
                narrow t0 to s32 when t0 == s64;
            }
            """
        )
        diagnostics = run_checks(info, strict=True)
        self.assertIn("T002", _codes(diagnostics))

    def test_type_index_outside_arity_is_w001(self):
        info = parse(
            """
            domain { types { s32 }; q0 { (G_X, [s32]); } }
            ruleset G_X { legal when t1 == s32; }
            """
        )
        diagnostics = run_checks(info, strict=True)
        self.assertIn("W001", _codes(diagnostics))

    def test_imm_index_outside_arity_is_w002(self):
        info = parse(
            """
            domain { types { s32 }; q0 { (G_X, [s32]); } }
            ruleset G_X { legal when imm 0 == 0; }
            """
        )
        diagnostics = run_checks(info, strict=True)
        self.assertIn("W002", _codes(diagnostics))

    def test_zero_is_multiple_divisor_is_rejected_by_parse(self):
        with self.assertRaises(DslError):
            parse("ruleset G_X { legal when is_multiple(bits(t0), 0); }")

    def test_zero_next_multiple_size_is_rejected_by_parse(self):
        with self.assertRaises(DslError):
            parse("ruleset G_X { widen 0 to next_multiple(bits(t0), 0); }")

    def test_zero_widen_to_next_multiple_size_is_rejected_by_parse(self):
        with self.assertRaises(DslError):
            parse("ruleset G_X { widen_to_next_multiple 0, 0; }")

    def test_empty_type_set_is_rejected_by_parse(self):
        with self.assertRaises(DslError):
            parse("ruleset G_X { legal when t0 in {}; }")

    def test_empty_pair_set_is_rejected_by_parse(self):
        with self.assertRaises(DslError):
            parse("ruleset G_X { legal_for_pairs {}; }")

    def test_more_scalar_to_vector_reports_m006_without_exception(self):
        info = parse(
            """
            domain { types { s32 }; q0 { (G_X, [s32]); } }
            ruleset G_X { more 0 to v4s32; }
            """
        )
        diagnostics = run_checks(info, strict=True)
        m006 = _by_code(diagnostics, "M006")
        self.assertTrue(m006, [d.format("check") for d in diagnostics])
        self.assertTrue(all(d.severity is Severity.ERROR for d in m006))

    def test_missing_q0_yields_unknown(self):
        info = parse(
            """
            domain { types { s32 }; }
            ruleset G_X { legal_for {s32}; }
            """
        )
        diagnostics = run_checks(info, strict=True)
        self.assertTrue(any(d.code == "C000" and d.severity is Severity.UNKNOWN for d in diagnostics))


class ToyCpuCheckerTests(unittest.TestCase):
    def test_frozen_toycpu_has_no_errors(self):
        info = parse_file(str(RULES))
        witnesses = parse_witness_file(WITNESS)
        diagnostics = run_checks(info, strict=True, witnesses=witnesses)
        errors = [d for d in diagnostics if d.severity is Severity.ERROR]
        self.assertEqual(errors, [])
        self.assertEqual([d.code for d in diagnostics if d.code == "C001"], ["C001"] * 6)
        self.assertEqual([d.code for d in diagnostics if d.code == "R001"], ["R001"] * 2)
        self.assertTrue(all(d.severity is Severity.INFO for d in diagnostics if d.code == "R001"))


if __name__ == "__main__":
    unittest.main()
