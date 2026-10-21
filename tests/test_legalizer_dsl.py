# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Equivalence tests for the Legalizer rule DSL.

The expected values come from the LLVM source, not from this implementation:

  llvm/lib/Target/ToyCPU/GISel/ToyCPULegalizerInfo.cpp
  llvm/lib/CodeGen/GlobalISel/LegalizerInfo.cpp
  llvm/lib/CodeGen/GlobalISel/LegalityPredicates.cpp
  llvm/lib/CodeGen/GlobalISel/LegalizeMutations.cpp

The tests check one-step policy results (LegalizeActionStep).  They do not
model the iterative Legalizer pass or the helper MIR rewrites.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legalizer_dsl import (
    Action,
    DslError,
    LLT,
    MemDesc,
    Query,
    parse,
    parse_file,
    parse_llt,
)

RULES = pathlib.Path(__file__).resolve().parents[1] / "rules" / "toycpu.ldsl"
INFO = parse_file(RULES)


def run(opcode: str, *types: str, mems=(), imms=()):
    query = Query(
        opcode,
        tuple(parse_llt(t) for t in types),
        tuple(mems),
        tuple(imms),
    )
    step, _ = INFO.action(query, trace=False)
    return step


def mem(ty: str, align: int) -> MemDesc:
    return MemDesc(parse_llt(ty), align)


class ToyCpuRuleTests(unittest.TestCase):
    # -- integer ALU --------------------------------------------------------

    def test_add_s32_is_legal(self):
        step = run("G_ADD", "s32")
        self.assertEqual(step.action, Action.LEGAL)

    def test_add_v4s32_is_legal(self):
        step = run("G_ADD", "v4s32")
        self.assertEqual(step.action, Action.LEGAL)

    def test_add_s16_widens_to_s32(self):
        step = run("G_ADD", "s16")
        self.assertEqual(step.action, Action.WIDEN)
        self.assertEqual(step.type_idx, 0)
        self.assertEqual(step.new_type, LLT.scalar(32))

    def test_add_s64_narrows_to_s32(self):
        step = run("G_ADD", "s64")
        self.assertEqual(step.action, Action.NARROW)
        self.assertEqual(step.type_idx, 0)
        self.assertEqual(step.new_type, LLT.scalar(32))

    def test_add_s48_narrows_to_s32_by_clamp(self):
        step = run("G_ADD", "s48")
        self.assertEqual(step.action, Action.NARROW)
        self.assertEqual(step.new_type, LLT.scalar(32))

    def test_shift_clamps_amount_before_result(self):
        # clamp_scalar(1) is expanded before clamp_scalar(0).
        step = run("G_SHL", "s64", "s64")
        self.assertEqual(step.action, Action.NARROW)
        self.assertEqual(step.type_idx, 1)
        self.assertEqual(step.new_type, LLT.scalar(32))

    # -- compare / phi / select --------------------------------------------

    def test_icmp_v4s1_v4s32_is_custom(self):
        step = run("G_ICMP", "v4s1", "v4s32")
        self.assertEqual(step.action, Action.CUSTOM)

    def test_icmp_s32_s32_is_legal(self):
        step = run("G_ICMP", "s32", "s32")
        self.assertEqual(step.action, Action.LEGAL)

    def test_icmp_s1_s32_widens_result(self):
        # widen_to_next_pow2 does NOT match s1 (s1 is already a power of two);
        # clampScalar's min rule is what widens it.
        step = run("G_ICMP", "s1", "s32")
        self.assertEqual(step.action, Action.WIDEN)
        self.assertEqual(step.type_idx, 0)
        self.assertEqual(step.new_type, LLT.scalar(32))

    def test_icmp_s48_s32_widens_to_64_in_one_step(self):
        # Rule order: widen_to_next_pow2 comes before clampScalar's max rule.
        # One step widens s48 -> s64; the next legalizer iteration would then
        # narrow s64 -> s32.  This proves the DSL models a single step.
        step = run("G_ICMP", "s48", "s32")
        self.assertEqual(step.action, Action.WIDEN)
        self.assertEqual(step.new_type, LLT.scalar(64))

    def test_phi_s1_widens_to_s32(self):
        step = run("G_PHI", "s1")
        self.assertEqual(step.action, Action.WIDEN)
        self.assertEqual(step.type_idx, 0)
        self.assertEqual(step.new_type, LLT.scalar(32))

    def test_phi_v2s32_is_legal(self):
        step = run("G_PHI", "v2s32")
        self.assertEqual(step.action, Action.LEGAL)

    def test_select_v4s1_condition_is_legal(self):
        step = run("G_SELECT", "v4s32", "v4s1")
        self.assertEqual(step.action, Action.LEGAL)

    # -- libcall ------------------------------------------------------------

    def test_sdiv_s64_is_libcall(self):
        step = run("G_SDIV", "s64")
        self.assertEqual(step.action, Action.LIBCALL)

    def test_sdiv_s32_is_libcall(self):
        step = run("G_SDIV", "s32")
        self.assertEqual(step.action, Action.LIBCALL)

    def test_sdiv_s16_widens_before_libcall(self):
        step = run("G_SDIV", "s16")
        self.assertEqual(step.action, Action.WIDEN)
        self.assertEqual(step.new_type, LLT.scalar(32))

    # -- vector custom / unsupported ---------------------------------------

    def test_shuffle_v4s32_is_custom(self):
        step = run("G_SHUFFLE_VECTOR", "v4s32", "v4s32")
        self.assertEqual(step.action, Action.CUSTOM)

    def test_shuffle_v2s32_is_custom(self):
        step = run("G_SHUFFLE_VECTOR", "v2s32", "v2s32")
        self.assertEqual(step.action, Action.CUSTOM)

    def test_shuffle_scalar_is_unsupported(self):
        step = run("G_SHUFFLE_VECTOR", "s32", "s32")
        self.assertEqual(step.action, Action.UNSUPPORTED)

    def test_concat_v4s32_is_custom(self):
        step = run("G_CONCAT_VECTORS", "v4s32", "v2s32")
        self.assertEqual(step.action, Action.CUSTOM)

    # -- memory -------------------------------------------------------------

    def test_load_s32_from_s8_is_legal(self):
        step = run("G_LOAD", "s32", "p0", mems=(mem("s8", 8),))
        self.assertEqual(step.action, Action.LEGAL)

    def test_load_s8_value_widens_to_s32(self):
        # The value type is widened; the MMO keeps the 1-byte access width.
        step = run("G_LOAD", "s8", "p0", mems=(mem("s8", 8),))
        self.assertEqual(step.action, Action.WIDEN)
        self.assertEqual(step.new_type, LLT.scalar(32))

    def test_load_v4s32_at_128_bits_is_legal(self):
        step = run("G_LOAD", "v4s32", "p0", mems=(mem("v4s32", 128),))
        self.assertEqual(step.action, Action.LEGAL)

    def test_load_v4s32_below_required_alignment_is_unsupported(self):
        # 127 bits is still not 128 bits; no scalar clamp applies to a vector.
        step = run("G_LOAD", "v4s32", "p0", mems=(mem("v4s32", 127),))
        self.assertEqual(step.action, Action.UNSUPPORTED)

    def test_load_without_mmo_is_not_legal(self):
        step = run("G_LOAD", "s32", "p0")
        self.assertNotEqual(step.action, Action.LEGAL)

    # -- conversion rules ---------------------------------------------------

    def test_zext_s32_s8_is_custom(self):
        step = run("G_ZEXT", "s32", "s8")
        self.assertEqual(step.action, Action.CUSTOM)

    def test_zext_s32_s32_is_legal(self):
        step = run("G_ZEXT", "s32", "s32")
        self.assertEqual(step.action, Action.LEGAL)

    def test_sext_v4s32_v4s1_is_legal(self):
        step = run("G_SEXT", "v4s32", "v4s1")
        self.assertEqual(step.action, Action.LEGAL)

    def test_trunc_s1_s64_is_custom(self):
        step = run("G_TRUNC", "s1", "s64")
        self.assertEqual(step.action, Action.CUSTOM)

    def test_brcond_s1_widens(self):
        step = run("G_BRCOND", "s1")
        self.assertEqual(step.action, Action.WIDEN)
        self.assertEqual(step.new_type, LLT.scalar(32))

    # -- alias semantics ----------------------------------------------------

    def test_opcodes_in_one_ruleset_share_the_same_object(self):
        info = parse("ruleset G_A, G_B { always_legal; }")
        self.assertIs(info.get("G_A"), info.get("G_B"))

    def test_redefining_an_aliased_opcode_is_an_error(self):
        with self.assertRaises(DslError):
            parse(
                "ruleset G_A, G_B { always_legal; } "
                "ruleset G_B { always_legal; }"
            )

    def test_duplicate_opcode_across_rulesets_is_an_error(self):
        with self.assertRaises(DslError):
            parse("ruleset G_A { always_legal; } ruleset G_A { always_legal; }")

    def test_zext_and_sext_do_not_share_a_ruleset(self):
        self.assertIsNot(INFO.get("G_ZEXT"), INFO.get("G_SEXT"))


class MacroExpansionTests(unittest.TestCase):
    def test_clamp_num_elements_expands_min_then_max(self):
        info = parse("ruleset G_X { clamp_num_elements 0, v8s8, v16s8; }")
        rules = info.get("G_X").rules
        self.assertEqual(
            [rule.action for rule in rules], [Action.MORE, Action.FEWER]
        )
        self.assertIn("step 1/2 (min)", rules[0].origin)
        self.assertIn("step 2/2 (max)", rules[1].origin)

        low, _ = info.action(
            Query("G_X", (LLT.vector(4, LLT.scalar(8)),))
        )
        high, _ = info.action(
            Query("G_X", (LLT.vector(32, LLT.scalar(8)),))
        )
        self.assertEqual(low.new_type, LLT.vector(8, LLT.scalar(8)))
        self.assertEqual(high.new_type, LLT.vector(16, LLT.scalar(8)))

    def test_clamp_num_elements_requires_matching_element_types(self):
        with self.assertRaises(DslError):
            parse("ruleset G_X { clamp_num_elements 0, v8s8, v16s16; }")

    def test_clamp_num_elements_ignores_other_element_types(self):
        info = parse(
            "ruleset G_X { clamp_num_elements 0, v8s8, v16s8; lower; }"
        )
        step, _ = info.action(
            Query("G_X", (LLT.vector(32, LLT.scalar(16)),))
        )
        self.assertEqual(step.action, Action.LOWER)

    def test_clamp_min_and_max_are_independent(self):
        info = parse(
            "ruleset G_X {"
            " clamp_min_num_elements 0, s8, 4;"
            " clamp_max_num_elements 0, s8, 8;"
            " }"
        )
        low, _ = info.action(
            Query("G_X", (LLT.vector(2, LLT.scalar(8)),))
        )
        high, _ = info.action(
            Query("G_X", (LLT.vector(16, LLT.scalar(8)),))
        )
        self.assertEqual(low.action, Action.MORE)
        self.assertEqual(low.new_type, LLT.vector(4, LLT.scalar(8)))
        self.assertEqual(high.action, Action.FEWER)
        self.assertEqual(high.new_type, LLT.vector(8, LLT.scalar(8)))

    def test_clamp_scalar_expands_to_min_then_max(self):
        info = parse("ruleset G_X { clamp_scalar 0, s32, s32; }")
        rules = info.get("G_X").rules
        self.assertEqual(len(rules), 2)
        self.assertIn("step 1/2 (min)", rules[0].origin)
        self.assertIn("step 2/2 (max)", rules[1].origin)

    def test_widen_to_next_pow2_does_not_match_power_of_two(self):
        info = parse(
            "ruleset G_X { widen_to_next_pow2 0, 32; }"
        )
        step, _ = info.action(Query("G_X", (LLT.scalar(1),)))
        self.assertEqual(step.action, Action.UNSUPPORTED)
        step, _ = info.action(Query("G_X", (LLT.scalar(24),)))
        self.assertEqual(step.action, Action.WIDEN)
        self.assertEqual(step.new_type, LLT.scalar(32))

    def test_widen_to_next_multiple_aligns_to_multiple(self):
        info = parse("ruleset G_X { widen_to_next_multiple 0, 32; }")
        step, _ = info.action(Query("G_X", (LLT.scalar(8),)))
        self.assertEqual(step.action, Action.WIDEN)
        self.assertEqual(step.new_type, LLT.scalar(32))

    def test_element_of_scalarizes(self):
        info = parse("ruleset G_X { fewer t0 to element_of(t0); }")
        step, _ = info.action(Query("G_X", (LLT.vector(4, LLT.scalar(32)),)))
        self.assertEqual(step.action, Action.FEWER)
        self.assertEqual(step.new_type, LLT.scalar(32))

    def test_more_changes_lane_count(self):
        info = parse("ruleset G_X { more t0 to v4s32; }")
        step, _ = info.action(Query("G_X", (LLT.vector(2, LLT.scalar(32)),)))
        self.assertEqual(step.action, Action.MORE)
        self.assertEqual(step.new_type, LLT.vector(4, LLT.scalar(32)))

    def test_mem_compat_requires_exact_type_pair_and_size(self):
        info = parse(
            "ruleset G_X { legal_for_mem {(s32, p0, s8, 8)}; }"
        )
        ok, _ = info.action(
            Query("G_X", (LLT.scalar(32), LLT.pointer(0, 32)), (mem("s8", 8),))
        )
        self.assertEqual(ok.action, Action.LEGAL)
        bad, _ = info.action(
            Query("G_X", (LLT.scalar(32), LLT.pointer(0, 32)), (mem("s8", 7),))
        )
        self.assertEqual(bad.action, Action.UNSUPPORTED)

    def test_aliased_type_names_work(self):
        info = parse(
            "type V4 = v4s32;"
            "ruleset G_X { legal_for {V4}; }"
        )
        step, _ = info.action(Query("G_X", (LLT.vector(4, LLT.scalar(32)),)))
        self.assertEqual(step.action, Action.LEGAL)


class SameSizeAsTests(unittest.TestCase):
    """Mirror LegalizeMutations::changeElementSizeTo(TypeIdx, FromTypeIdx)."""

    def test_widen_matches_source_width(self):
        info = parse(
            "ruleset G_X {"
            "  widen t1 to same_size_as(t0) when scalar_bits(t1) < scalar_bits(t0);"
            "}"
        )
        step, _ = info.action(
            Query("G_X", (LLT.scalar(64), LLT.scalar(32)))
        )
        self.assertEqual(step.action, Action.WIDEN)
        self.assertEqual(step.type_idx, 1)
        self.assertEqual(step.new_type, LLT.scalar(64))

    def test_narrow_matches_source_width(self):
        info = parse(
            "ruleset G_X {"
            "  narrow t1 to same_size_as(t0) when scalar_bits(t1) > scalar_bits(t0);"
            "}"
        )
        step, _ = info.action(
            Query("G_X", (LLT.scalar(64), LLT.scalar(128)))
        )
        self.assertEqual(step.action, Action.NARROW)
        self.assertEqual(step.type_idx, 1)
        self.assertEqual(step.new_type, LLT.scalar(64))

    def test_self_reference_is_rejected(self):
        with self.assertRaises(DslError) as ctx:
            parse("ruleset G_X { widen t0 to same_size_as(t0); }")
        self.assertEqual(ctx.exception.code, "P014")


class LLTRepresentabilityTests(unittest.TestCase):
    def test_v1_is_rejected(self):
        with self.assertRaises(DslError) as ctx:
            parse_llt("v1s32")
        self.assertEqual(ctx.exception.code, "T011")
        self.assertIn("at least 2", ctx.exception.message)

    def test_v1_in_rules_carries_a_source_span(self):
        with self.assertRaises(DslError) as ctx:
            parse("ruleset G_X { legal_for {v1s32}; }")
        self.assertEqual(ctx.exception.code, "T011")
        self.assertTrue(ctx.exception.span.is_known())

    def test_v2_remains_representable(self):
        ty = parse_llt("v2s32")
        self.assertTrue(ty.is_vector)
        self.assertEqual(ty.lanes, 2)


class CliMainGuardTests(unittest.TestCase):
    def test_cli_module_has_main_guard(self):
        import legalizer_dsl.cli as cli

        source = pathlib.Path(cli.__file__).read_text(encoding="utf-8")
        self.assertIn('if __name__ == "__main__":', source)
        self.assertIn("raise SystemExit(main())", source)


class ParseErrorTests(unittest.TestCase):
    def test_bad_type_is_rejected(self):
        with self.assertRaises(DslError):
            parse("ruleset G_X { legal_for {s33x}; }")

    def test_missing_semicolon_is_rejected(self):
        with self.assertRaises(DslError):
            parse("ruleset G_X { legal_for {s32} }")

    def test_unknown_macro_is_rejected(self):
        with self.assertRaises(DslError):
            parse("ruleset G_X { make_everything_legal; }")

    def test_unknown_predicate_is_rejected(self):
        with self.assertRaises(DslError):
            parse("ruleset G_X { legal when magic(t0); }")


if __name__ == "__main__":
    unittest.main()