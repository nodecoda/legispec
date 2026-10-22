# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Construct coverage must fail when upstream adds a builder method."""

from __future__ import annotations

import contextlib
import io
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legispec import constructs


class ConstructCoverageTests(unittest.TestCase):
    @unittest.skipUnless(
        constructs.HEADER.exists(), "LLVM source tree not available"
    )
    def test_every_builder_method_is_classified(self):
        methods = constructs.extract_methods()
        self.assertGreaterEqual(len(methods), 60)
        entries, unclassified = constructs.classify(methods)
        self.assertEqual(unclassified, [])
        self.assertEqual(len(entries), len(methods))

    def test_known_constructs_are_mapped(self):
        expected = {
            "minScalar": "core",
            "maxScalar": "core",
            "clampScalar": "macro",
            "widenScalarToNextPow2": "macro",
            "legalFor": "guard",
            "customFor": "guard",
            "legalForTypesWithMemDesc": "macro",
            "legalForCartesianProduct": "partial",
            "minScalarOrEltIf": "macro",
            "moreElementsToNextPow2": "macro",
            "scalarize": "core",
            "scalarizeIf": "core",
            "widenScalarOrEltToNextPow2": "macro",
            "minScalarOrElt": "macro",
            "minScalarSameAs": "macro",
            "minScalarEltSameAsIf": "macro",
            "maxScalarEltSameAsIf": "macro",
        }
        for name, status in expected.items():
            with self.subTest(method=name):
                self.assertIn(name, constructs.SUPPORT)
                self.assertEqual(constructs.SUPPORT[name].status, status)

    def test_snapshot_commit_mismatch_helper(self):
        meta = {"llvm_commit": "d855d9e48067026317998cbff734004f328f833d"}
        self.assertIsNone(constructs.snapshot_commit_mismatch(meta, meta["llvm_commit"]))
        message = constructs.snapshot_commit_mismatch(meta, "other")
        self.assertIsNotNone(message)
        self.assertIn(meta["llvm_commit"], message)
        self.assertIn("other", message)
        self.assertIsNone(constructs.snapshot_commit_mismatch(meta, None))
        self.assertIsNone(
            constructs.snapshot_commit_mismatch({"llvm_commit": "unknown"}, "abc")
        )

    def test_unrepresented_constructs_are_declared_not_silent(self):
        none = [k for k, v in constructs.SUPPORT.items() if v.status == "none"]
        self.assertGreaterEqual(len(none), 11)
        for name in none:
            self.assertTrue(constructs.SUPPORT[name].note, name)



class SnapshotGuardTests(unittest.TestCase):
    def _saved(self):
        return (
            constructs.HEADER,
            constructs.SNAPSHOT,
            constructs.LEDGER,
            constructs.OUT_JSON,
        )

    def _restore(self, saved):
        (
            constructs.HEADER,
            constructs.SNAPSHOT,
            constructs.LEDGER,
            constructs.OUT_JSON,
        ) = saved

    def test_empty_snapshot_fails_instead_of_reporting_zero_methods(self):
        saved = self._saved()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            constructs.HEADER = root / "missing.h"
            constructs.SNAPSHOT = root / "methods.txt"
            constructs.SNAPSHOT.write_text("# empty\n", encoding="utf-8")
            constructs.LEDGER = root / "missing-claims.json"
            constructs.OUT_JSON = root / "missing-coverage.json"
            try:
                with contextlib.redirect_stdout(
                    io.StringIO()
                ), contextlib.redirect_stderr(io.StringIO()):
                    rc = constructs.main(["check"])
            finally:
                self._restore(saved)
        self.assertEqual(rc, 1)

    def test_write_snapshot_refuses_an_empty_extraction(self):
        saved = self._saved()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            constructs.HEADER = root / "LegalizerInfo.h"
            constructs.HEADER.write_text("// no builders here\n", encoding="utf-8")
            constructs.SNAPSHOT = root / "methods.txt"
            constructs.SNAPSHOT.write_text("keep-me\n", encoding="utf-8")
            constructs.LEDGER = root / "missing-claims.json"
            constructs.OUT_JSON = root / "missing-coverage.json"
            try:
                with contextlib.redirect_stdout(
                    io.StringIO()
                ), contextlib.redirect_stderr(io.StringIO()):
                    rc = constructs.main(["check", "--write-snapshot"])
                kept = constructs.SNAPSHOT.read_text(encoding="utf-8")
            finally:
                self._restore(saved)
        self.assertEqual(rc, 1)
        self.assertEqual(kept, "keep-me\n")

    def test_corrupt_ledger_disables_no_gate(self):
        saved = self._saved()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            constructs.HEADER = root / "missing.h"
            constructs.SNAPSHOT = root / "methods.txt"
            constructs.SNAPSHOT.write_text("legalFor\n", encoding="utf-8")
            constructs.LEDGER = root / "claims.json"
            constructs.LEDGER.write_text("{not json", encoding="utf-8")
            constructs.OUT_JSON = root / "missing-coverage.json"
            try:
                with contextlib.redirect_stdout(
                    io.StringIO()
                ), contextlib.redirect_stderr(io.StringIO()) as captured:
                    rc = constructs.main(["check"])
            finally:
                self._restore(saved)
        self.assertEqual(rc, 1)
        self.assertIn("cannot read claim registry", captured.getvalue())


if __name__ == "__main__":
    unittest.main()