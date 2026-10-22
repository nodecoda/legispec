# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0.
# See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0
#
# ===----------------------------------------------------------------------===##

"""Phase 2: domain declarations, strict mode, and coverage."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legispec.coverage import run_coverage
from legispec.diagnostics import DslError, Severity
from legispec.parser import parse, parse_file

HERE = pathlib.Path(__file__).resolve().parents[1]
RULES = HERE / "rules" / "toycpu.lspec"
CLI = [sys.executable, "-m", "legispec"]
CLI_ENV = {**os.environ, "PYTHONPATH": str(HERE / "src")}


class DomainParseTests(unittest.TestCase):
    def test_domain_items_are_parsed(self):
        info = parse(
            """
            domain {
                types { s32, p0:64, v4s32 };
                imms { -1, 0, 1 };
                mems { s32:32, v4s32:128 };
                q0 { (G_X, [s32, p0], mem = [s32:32], imm = [-1]); }
            }
            ruleset G_X { legal_for {s32}; }
            """
        )
        self.assertIsNotNone(info.domain)
        domain = info.domain
        self.assertEqual([str(t) for t in domain.types], ["s32", "p0:64", "v4s32"])
        self.assertEqual(domain.imms, (-1, 0, 1))
        self.assertEqual(len(domain.mems), 2)
        self.assertEqual(len(domain.q0), 1)
        self.assertEqual(domain.q0[0].opcode, "G_X")
        self.assertEqual(str(domain.q0[0].types[1]), "p0")
        self.assertEqual(domain.q0[0].imms, (-1,))

    def test_duplicate_domain_is_rejected(self):
        with self.assertRaises(DslError) as ctx:
            parse("domain {} domain {}")
        self.assertEqual(ctx.exception.code, "P020")

    def test_duplicate_domain_item_is_rejected(self):
        with self.assertRaises(DslError) as ctx:
            parse("domain { types {s32}; types {s64}; }")
        self.assertEqual(ctx.exception.code, "P021")

    def test_bad_domain_item_is_rejected(self):
        with self.assertRaises(DslError) as ctx:
            parse("domain { arity { G_X = 1; } }")
        self.assertEqual(ctx.exception.code, "P019")

    def test_toycpu_domain_is_complete(self):
        info = parse_file(str(RULES))
        self.assertIsNotNone(info.domain)
        self.assertEqual(len(info.domain.types), 28)
        self.assertEqual(len(info.domain.imms), 12)
        self.assertEqual(len(info.domain.mems), 11)
        self.assertEqual(len(info.domain.q0), 47)


class CoverageTests(unittest.TestCase):
    def test_unmentioned_type_slot_is_reported(self):
        info = parse(
            """
            domain {
                types { s32 };
                q0 { (G_X, [s32, s32]); }
            }
            ruleset G_X { legal_for {s32}; }
            """
        )
        report = run_coverage(info)
        self.assertEqual(report.errors, [])
        codes = [(d.code, d.severity) for d in report.diagnostics]
        self.assertIn(("C001", Severity.WARN), codes)
        self.assertEqual(report.entries[0].uncovered_types, (1,))

    def test_unmentioned_imm_slot_is_reported(self):
        info = parse(
            """
            domain {
                types { s32 };
                imms { 0 };
                q0 { (G_X, [s32], imm = [0]); }
            }
            ruleset G_X { legal_for {s32}; }
            """
        )
        report = run_coverage(info)
        self.assertEqual(report.entries[0].uncovered_imms, (0,))
        self.assertTrue(any(d.code == "C002" for d in report.diagnostics))

    def test_q0_opcode_without_ruleset_is_an_error(self):
        info = parse(
            """
            domain {
                types { s32 };
                q0 { (G_MISSING, [s32]); }
            }
            ruleset G_X { legal_for {s32}; }
            """
        )
        report = run_coverage(info)
        self.assertTrue(any(d.code == "C003" and d.severity is Severity.ERROR for d in report.diagnostics))


class StrictModeTests(unittest.TestCase):
    def test_check_strict_requires_domain(self):
        source = "ruleset G_X { legal_for {s32}; }\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "no-domain.lspec"
            path.write_text(source, encoding="utf-8", newline="")
            result = subprocess.run(
                CLI + ["--rules", str(path), "check", "--strict"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=CLI_ENV,
            )
        self.assertEqual(result.returncode, 1)
        self.assertIn("W014", result.stderr)

    def test_check_strict_accepts_toycpu_domain(self):
        result = subprocess.run(
            CLI + ["--rules", str(RULES), "check", "--strict"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=CLI_ENV,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("rulesets=47", result.stdout)
        self.assertIn("C001", result.stderr)


if __name__ == "__main__":
    unittest.main()
