# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Construct coverage must fail when upstream adds a builder method."""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legalizer_dsl import constructs


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

    def test_unrepresented_constructs_are_declared_not_silent(self):
        none = [k for k, v in constructs.SUPPORT.items() if v.status == "none"]
        self.assertGreaterEqual(len(none), 11)
        for name in none:
            self.assertTrue(constructs.SUPPORT[name].note, name)


if __name__ == "__main__":
    unittest.main()