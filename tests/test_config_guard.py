# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0.
# See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0
#
# ===----------------------------------------------------------------------===##

"""Compile-time configuration guards for LegiSpec.

These tests pin the distinction between a query predicate and a
construction-time boolean such as LLVM's legalFor(bool, ...):
a false guard erases the rule, it does not emit a dead query predicate.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legispec import (
    Action,
    DslError,
    LLT,
    Query,
    parse,
    parse_defines,
    parse_llt,
)

SOURCE = """
domain {
    types { s1, s2, s3, s4, s8, s16, s32, s64, s128 };
    imms {};
    mems {};
    q0 {
        (G_CONSTANT, [s32]);
        (G_CONSTANT, [s64]);
        (G_CONSTANT, [s128]);
    }
}
ruleset G_CONSTANT {
    legal_for {s32} when not is64bit;
    custom_for {s64} when is64bit;
    widen_to_next_pow2 0, 0;
    clamp_scalar 0, sXLen, sXLen;
}
"""


def step(info, ty: str):
    result, _ = info.action(Query("G_CONSTANT", (parse_llt(ty),)))
    return result


class ConfigGuardTests(unittest.TestCase):
    def test_rv32_guard_emits_legal_s32_and_no_custom(self):
        info = parse(SOURCE, config={"is64bit": False, "sXLen": LLT.scalar(32)})
        ruleset = info.get("G_CONSTANT")
        self.assertEqual(len(ruleset.rules), 4)
        self.assertEqual(step(info, "s32").action, Action.LEGAL)
        result = step(info, "s64")
        self.assertEqual(result.action, Action.NARROW)
        self.assertEqual(result.new_type, LLT.scalar(32))

    def test_rv64_guard_emits_custom_s64_and_no_legal_s32(self):
        info = parse(SOURCE, config={"is64bit": True, "sXLen": LLT.scalar(64)})
        ruleset = info.get("G_CONSTANT")
        self.assertEqual(len(ruleset.rules), 4)
        self.assertEqual(step(info, "s64").action, Action.CUSTOM)
        result = step(info, "s32")
        self.assertEqual(result.action, Action.WIDEN)
        self.assertEqual(result.new_type, LLT.scalar(64))

    def test_false_guard_erases_rule_instead_of_emitting_dead_predicate(self):
        info = parse(
            "ruleset G_X {"
            "  custom_for {s32} when is64bit;"
            "  lower_for {s64} when not is64bit;"
            "}",
            config={"is64bit": False},
        )
        ruleset = info.get("G_X")
        self.assertEqual(len(ruleset.rules), 1)
        self.assertEqual(ruleset.rules[0].action, Action.LOWER)
        result, _ = info.action(Query("G_X", (parse_llt("s32"),)))
        self.assertEqual(result.action, Action.UNSUPPORTED)

    def test_guard_supports_and_or_not_and_integer_comparison(self):
        info = parse(
            "ruleset G_X {"
            "  legal_for {s32} when is64bit and xlen >= 64;"
            "  custom_for {s64} when not is64bit or xlen < 64;"
            "}",
            config={"is64bit": True, "xlen": 64},
        )
        ruleset = info.get("G_X")
        self.assertEqual(len(ruleset.rules), 1)
        self.assertEqual(ruleset.rules[0].action, Action.LEGAL)

    def test_unknown_configuration_variable_is_p025(self):
        with self.assertRaises(DslError) as ctx:
            parse(
                "ruleset G_X { legal_for {s32} when missing_feature; }",
                config={"is64bit": True},
            )
        self.assertEqual(ctx.exception.code, "P025")

    def test_non_boolean_configuration_value_is_p026(self):
        with self.assertRaises(DslError) as ctx:
            parse(
                "ruleset G_X { legal_for {s32} when xlen; }",
                config={"xlen": 64},
            )
        self.assertEqual(ctx.exception.code, "P026")

    def test_every_macro_honours_a_false_guard(self):
        macros = [
            "legal_for {s32}",
            "legal_for_pairs {(s32, s32)}",
            "custom_for {s32}",
            "custom_for_pairs {(s32, s32)}",
            "lower_for {s32}",
            "unsupported_for {s32}",
            "libcall_for {s32}",
            "clamp_scalar 0, s32, s32",
            "widen_to_next_pow2 0, 0",
            "widen_to_next_pow2_or_min 0, 0",
            "widen_to_next_multiple 0, 32",
            "legal_for_mem {(s32, s32, s32, 32)}",
            "always_legal",
        ]
        for macro in macros:
            with self.subTest(macro=macro):
                erased = parse(
                    "ruleset G_X {" + macro + " when false;}",
                    config={"is64bit": False},
                )
                self.assertEqual(erased.get("G_X").rules, [])
                kept = parse(
                    "ruleset G_X {" + macro + " when true;}",
                    config={"is64bit": False},
                )
                self.assertGreaterEqual(len(kept.get("G_X").rules), 1)

    def test_type_valued_configuration_alias_is_usable(self):
        info = parse(
            "ruleset G_X { clamp_scalar 0, sXLen, sXLen; }",
            config={"sXLen": LLT.scalar(32)},
        )
        result, _ = info.action(Query("G_X", (LLT.scalar(64),)))
        self.assertEqual(result.action, Action.NARROW)
        self.assertEqual(result.new_type, LLT.scalar(32))

    def test_parse_defines_values(self):
        config = parse_defines(
            ["is64bit=false", "xlen=32", "sXLen=s32", "name=generic"]
        )
        self.assertIs(config["is64bit"], False)
        self.assertEqual(config["xlen"], 32)
        self.assertEqual(config["sXLen"], LLT.scalar(32))
        self.assertEqual(config["name"], "generic")

    def test_parse_defines_rejects_bad_entry(self):
        with self.assertRaises(DslError) as ctx:
            parse_defines(["is64bit"])
        self.assertEqual(ctx.exception.code, "P028")


if __name__ == "__main__":
    unittest.main()
