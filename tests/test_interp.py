# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Phase 4: fixpoint interpreter and domain termination evidence."""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legispec.interp import legalize, verify_domain
from legispec.model import Query, parse_llt
from legispec.parser import parse, parse_file

HERE = pathlib.Path(__file__).resolve().parents[1]
RULES = HERE / "rules" / "toycpu.lspec"


class InterpTests(unittest.TestCase):
    def test_add_s64_narrows_then_legal(self):
        info = parse_file(str(RULES))
        trace = legalize(info, Query("G_ADD", (parse_llt("s64"),)))
        self.assertEqual(trace.status, "Legal")
        self.assertEqual(len(trace.steps), 2)
        self.assertEqual(trace.steps[0].step.action.value, "NarrowScalar")
        self.assertEqual(str(trace.steps[0].step.new_type), "s32")

    def test_cycle_is_detected(self):
        info = parse(
            """
            domain { types { s32, s64 }; q0 { (G_X, [s32]); } }
            ruleset G_X {
                widen t0 to s64 when t0 == s32;
                narrow t0 to s32 when t0 == s64;
            }
            """
        )
        trace = legalize(info, Query("G_X", (parse_llt("s32"),)))
        self.assertEqual(trace.status, "Cycle")
        self.assertEqual(len(trace.steps), 2)

    def test_budget_is_reported(self):
        info = parse(
            """
            domain { types { s32, s64 }; q0 { (G_X, [s32]); } }
            ruleset G_X {
                widen t0 to s64 when t0 == s32;
                narrow t0 to s32 when t0 == s64;
            }
            """
        )
        trace = legalize(info, Query("G_X", (parse_llt("s32"),)), max_steps=1)
        self.assertEqual(trace.status, "Budget")

    def test_successor_outside_d_is_a_boundary_not_a_failure(self):
        # The policy claim covers D.  A mutation may project the observed slot
        # outside the domain filter; that state must not be fed back into the
        # rule set as if it were a real query.
        info = parse(
            """
            domain {
                types { s32, s64 };
                filter bits(t0) == 32;
                q0 { (G_X, [s32]); }
            }
            ruleset G_X {
                widen t0 to s64 when t0 == s32;
            }
            """
        )
        report = verify_domain(info)
        self.assertEqual(report.total, 1)
        self.assertEqual(report.terminated, 0)
        self.assertEqual(len(report.out_of_domain), 1)
        self.assertEqual(str(report.out_of_domain[0].final.types[0]), "s64")
        self.assertTrue(report.ok)

    def test_entire_toycpu_domain_terminates(self):
        info = parse_file(str(RULES))
        report = verify_domain(info, max_steps=16)
        self.assertEqual(report.total, 146974)
        self.assertTrue(report.ok)
        self.assertEqual(report.terminated, report.total)
        self.assertEqual(report.max_steps_seen, 4)


if __name__ == "__main__":
    unittest.main()
