# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""The frozen witness corpus must replay cleanly against the interpreter."""

from __future__ import annotations

import contextlib
import io
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legalizer_dsl import cli
from legalizer_dsl.parser import parse, parse_file
from legalizer_dsl.witness import parse_witness_file, parse_witness_text, run_witnesses

HERE = pathlib.Path(__file__).resolve().parents[1]
RULES = HERE / "rules" / "toycpu.ldsl"
WITNESS = HERE / "witness" / "toycpu.witness"


class WitnessCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.info = parse_file(str(RULES))
        cls.witnesses = parse_witness_file(WITNESS)
        cls.report = run_witnesses(cls.info, cls.witnesses)

    def test_counts_are_frozen(self):
        self.assertEqual(len(self.witnesses), 205)
        self.assertEqual(self.report.positives, 114)
        self.assertEqual(self.report.negatives, 89)
        self.assertEqual(self.report.unreachable, 2)

    def test_no_witness_fails(self):
        self.assertEqual(self.report.failures, [])

    def test_every_rule_is_covered_or_declared_unreachable(self):
        self.assertEqual(self.report.uncovered_rules, [])

    def test_every_ruleset_has_positive_and_negative_coverage(self):
        self.assertEqual(self.report.rulesets_without_positive, [])
        self.assertEqual(self.report.rulesets_without_negative, [])

    def test_report_is_ok(self):
        self.assertTrue(self.report.ok)


class WitnessStrictTests(unittest.TestCase):
    RULES = "ruleset G_X { legal; }"
    WITNESS = """
    witness positive_only {
        query = (G_X, [])
        step = Legal rule 0
    }
    """

    def test_complete_requires_positive_and_negative_coverage(self):
        info = parse(self.RULES)
        report = run_witnesses(info, parse_witness_text(self.WITNESS))
        self.assertTrue(report.ok)
        self.assertFalse(report.complete)

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "probe.witness"
            path.write_text(self.WITNESS, encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(cli._cmd_witness(info, str(path)), 0)
                self.assertEqual(cli._cmd_witness(info, str(path), strict=True), 1)


if __name__ == "__main__":
    unittest.main()
