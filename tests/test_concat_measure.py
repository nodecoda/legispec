# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Unit tests for the real-MIR shape measurement parser."""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legispec.measure_concat_shapes import (  # noqa: E402
    check_contract,
    iter_events,
    parse_shape_type,
)


class ParseShapeTypeTests(unittest.TestCase):
    def test_vector_spellings(self):
        self.assertEqual(parse_shape_type("<16 x i32>")["canonical"], "v16s32")
        self.assertEqual(parse_shape_type("<4 x f32>")["canonical"], "v4f32")
        self.assertEqual(parse_shape_type("<8 x i1>")["canonical"], "v8s1")
        self.assertEqual(parse_shape_type("<2 x p0>")["canonical"], "v2p0")

    def test_scalar_spellings(self):
        self.assertEqual(parse_shape_type("i32")["canonical"], "s32")
        self.assertEqual(parse_shape_type("f32")["canonical"], "f32")
        self.assertEqual(parse_shape_type("p0")["canonical"], "p0")
        self.assertIsNone(parse_shape_type("i32")["lanes"])

    def test_unknown_spelling_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_shape_type("<4 x half>")


class ContractTests(unittest.TestCase):
    def shape(self, text):
        return parse_shape_type(text)

    def test_k4_is_legal(self):
        k, problems = check_contract(self.shape("<16 x i32>"), [self.shape("<4 x i32>")] * 4)
        self.assertEqual(k, 4)
        self.assertEqual(problems, [])

    def test_k8_is_legal(self):
        k, problems = check_contract(self.shape("<32 x i32>"), [self.shape("<4 x i32>")] * 8)
        self.assertEqual(k, 8)
        self.assertEqual(problems, [])

    def test_two_sources_is_the_k2_special_case(self):
        k, problems = check_contract(self.shape("<16 x i8>"), [self.shape("<8 x i8>")] * 2)
        self.assertEqual(k, 2)
        self.assertEqual(problems, [])

    def test_k1_is_not_a_concat(self):
        k, problems = check_contract(self.shape("<8 x i8>"), [self.shape("<8 x i8>")] * 2)
        self.assertEqual(k, 1)
        self.assertTrue(any("k=1" in p for p in problems))

    def test_non_divisible_lanes_are_rejected(self):
        k, problems = check_contract(self.shape("<6 x i8>"), [self.shape("<4 x i8>")] * 2)
        self.assertIsNone(k)
        self.assertTrue(any("not divisible" in p for p in problems))

    def test_mixed_elements_are_rejected(self):
        k, problems = check_contract(self.shape("<8 x i8>"), [self.shape("<4 x i16>")] * 2)
        self.assertIsNone(k)
        self.assertTrue(any("element" in p for p in problems))


class EventParserTests(unittest.TestCase):
    DEBUG = """
Legalize Machine IR for: concat_k4
Legalizing: %5:_(<16 x i32>) = G_CONCAT_VECTORS %1:_(<4 x i32>), %2:_(<4 x i32>), %3:_(<4 x i32>), %4:_(<4 x i32>)
.. Unable to legalize
.. Not legalized, moving to artifacts retry
Legalizing: %9:_(<2 x i64>) = G_ADD %1, %2
.. Already legal
"""

    def test_extracts_dst_and_all_sources(self):
        events = list(iter_events(self.DEBUG))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["function"], "concat_k4")
        self.assertEqual(events[0]["types"][0], "<16 x i32>")
        self.assertEqual(len(events[0]["types"]), 5)
        self.assertEqual(events[0]["llvm_action"], "UNSUPPORTED")

    def test_custom_action_line(self):
        text = """
Legalize Machine IR for: cat
Legalizing: %3:_(<16 x i1>) = G_CONCAT_VECTORS %0:_(<8 x i1>), %1:_(<8 x i1>)
.. Custom legalization
"""
        events = list(iter_events(text))
        self.assertEqual(events[0]["llvm_action"], "CUSTOM")


if __name__ == "__main__":
    unittest.main()