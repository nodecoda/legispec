# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Phase 1: every diagnostic carries a code and a source span."""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legalizer_dsl.diagnostics import Diagnostic, DslError, Severity, Span
from legalizer_dsl.parser import parse, parse_file

RULES = pathlib.Path(__file__).resolve().parents[1] / "rules" / "toycpu.ldsl"

CASES = [
    ("lexer", "ruleset G_X { @ }", "L001"),
    ("top-level", "foo", "P001"),
    ("unterminated-ruleset", "ruleset G_X { legal_for {s32};", "P002"),
    ("statement", "ruleset G_X { 123; }", "P003"),
    ("unknown-macro", "ruleset G_X { make_legal; }", "P004"),
    ("missing-semicolon", "ruleset G_X { legal_for {s32} }", "P010"),
    ("type-index", "ruleset G_X { narrow to s32; }", "P005"),
    ("type-token", "ruleset G_X { legal_for {33}; }", "P006"),
    ("type-spelling", "ruleset G_X { legal_for {s33x}; }", "P006"),
    ("v1-lane", "ruleset G_X { legal_for {v1s32}; }", "T011"),
    ("predicate-token", "ruleset G_X { legal when 123; }", "P007"),
    ("unknown-predicate", "ruleset G_X { legal when magic(t0); }", "P008"),
    ("cmp-op", "ruleset G_X { legal when bits(t0) s32; }", "P009"),
    ("next-pow2-index", "ruleset G_X { narrow t0 to next_pow2(bits(t1), 32); }", "P014"),
    ("next-multiple-index", "ruleset G_X { narrow t0 to next_multiple(bits(t1), 32); }", "P014"),
    ("element-of-index", "ruleset G_X { narrow t0 to element_of(t1); }", "P014"),
    ("more-needs-vector", "ruleset G_X { more t0 to s32; }", "P015"),
    ("mem-type-op", "ruleset G_X { legal when mem 0.type > s32; }", "P016"),
    ("mem-field", "ruleset G_X { legal when mem 0.foo == 1; }", "P017"),
    ("type-cmp", "ruleset G_X { legal when t0 > s32; }", "P018"),
    ("duplicate-opcode", "ruleset G_X { legal; } ruleset G_X { custom; }", "W003"),
    ("alias-shadow", "type s32 = s64;", "W010"),
    ("integer-token", "ruleset G_X { legal when mem 0.align == ; }", "P012"),
    ("trailing-junk", "ruleset G_X { legal_for {s32}; } junk", "P001"),
]


class DiagnosticContractTests(unittest.TestCase):
    def test_every_error_has_code_and_span(self):
        for name, source, expected in CASES:
            with self.subTest(name=name):
                with self.assertRaises(DslError) as ctx:
                    parse(source)
                exc = ctx.exception
                self.assertEqual(exc.code, expected, msg=f"{name}: {exc}")
                self.assertTrue(exc.code)
                self.assertIsNotNone(exc.span, msg=f"{name}: no span")
                self.assertTrue(exc.span.is_known(), msg=f"{name}: {exc.span}")
                self.assertIn(f"ERROR: {expected}:", str(exc))

    def test_span_points_at_the_bad_token(self):
        with self.assertRaises(DslError) as ctx:
            parse("ruleset G_X { legal_for {s33x}; }")
        span = ctx.exception.span
        self.assertEqual((span.start_line, span.start_col), (1, 26))
        self.assertEqual((span.end_line, span.end_col), (1, 29))

    def test_bom_is_ignored(self):
        info = parse("﻿ruleset G_X { legal_for {s32}; }")
        self.assertEqual(info.check()["rules"], 1)

    def test_rules_and_rulesets_carry_spans(self):
        info = parse_file(str(RULES))
        self.assertEqual(len(info.rulesets), 47)
        for ruleset in info.rulesets:
            self.assertIsNotNone(ruleset.span, msg=str(ruleset.opcodes))
            self.assertTrue(ruleset.span.is_known())
            for rule in ruleset.rules:
                self.assertIsNotNone(rule.span, msg=str(rule))
                self.assertTrue(rule.span.is_known(), msg=str(rule))
        first = info.rulesets[0]
        self.assertEqual(first.rules[0].span.start_line, 92)
        self.assertEqual(first.rules[0].span.start_col, 5)

    def test_span_rendering(self):
        self.assertEqual(str(Span.point(3, 7)), "3:7-7")
        self.assertEqual(str(Span(3, 2, 5, 9)), "3:2-5:9")
        self.assertEqual(str(Span.unknown()), "<unknown>")

    def test_diagnostic_rendering(self):
        diag = Diagnostic("W003", Severity.WARN, "duplicate opcode", Span.point(2, 4))
        self.assertEqual(diag.format("f.ldsl"), "f.ldsl:2:4-4: WARN: W003: duplicate opcode")


if __name__ == "__main__":
    unittest.main()
