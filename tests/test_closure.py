# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Structural closure must not trust a tampered ledger."""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legalizer_dsl import closure


class SkipEvidenceTamperTests(unittest.TestCase):
    def test_a_negative_cached_verdict_still_fails_in_structural_mode(self):
        saved = (closure.LEDGER, closure.RULES, closure.OUT, closure.CONSTRUCTS, closure.CLOSURE_MD, closure.TESTS)
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            rules = root / "rules"
            rules.mkdir()
            (rules / "x.ldsl").write_text("ruleset G_X { legal; }\n", encoding="utf-8")
            coverage = root / "construct-coverage.json"
            coverage.write_text(
                json.dumps({"counts": {}, "methods": []}), encoding="utf-8"
            )
            pinned = root / "CLOSURE.md"
            pinned.write_text(
                "<!-- closure:pinned -->\n"
                "claims=1\nhand-claims=1\ngenerated-claims=0\n"
                "rules-files=1\nhand-queries=0\nconstructs=0\ntests=0\n"
                "<!-- /closure:pinned -->\n",
                encoding="utf-8",
            )
            tests = root / "tests"
            tests.mkdir()
            closure.LEDGER = root / "claims.json"
            closure.LEDGER.write_text(
                json.dumps(
                    {
                        "llvm_commit": "abc",
                        "boundary": "test",
                        "claims": [
                            {
                                "id": "x",
                                "rules": "x.ldsl",
                                "queries": 0,
                                "ok": False,
                                "divergences": 7,
                                "rules_sha256": closure.sha256(rules / "x.ldsl"),
                                "report": "x.json",
                                "oracle": "oracle/x.exe",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            closure.RULES = rules
            closure.OUT = root / "out"
            closure.CONSTRUCTS = coverage
            closure.CLOSURE_MD = pinned
            closure.TESTS = tests
            try:
                with contextlib.redirect_stdout(io.StringIO()) as captured:
                    rc = closure.audit(False, True)
            finally:
                (
                    closure.LEDGER,
                    closure.RULES,
                    closure.OUT,
                    closure.CONSTRUCTS,
                    closure.CLOSURE_MD,
                    closure.TESTS,
                ) = saved
        self.assertEqual(rc, 1)
        self.assertIn("not marked ok", captured.getvalue())
        self.assertIn("divergence", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
