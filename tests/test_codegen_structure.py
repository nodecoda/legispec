# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Structural-faithfulness tests for the generated C++ manifest.

The behavior oracle proves that the interpreter agrees with LLVM.  These tests
prove a different link in the chain: every rule, macro expansion, ordering
decision, slot index and mutation recorded in the manifest is exactly what the
parser put into the AST.  A green behavioral slice cannot detect a generator
that silently reorders, drops or duplicates a rule outside the sampled domain.
"""

from __future__ import annotations

import hashlib
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legalizer_dsl import codegen, parse, parse_defines, parse_file

HERE = pathlib.Path(__file__).resolve().parents[1]
RULES_DIR = HERE / "rules"

SLOT_FIELDS = (
    "idx",
    "i",
    "j",
    "lhs",
    "rhs",
    "mem_idx",
    "src_idx",
    "scalar_idx",
    "size_idx",
)

CONFIG_DEFINES = {
    "riscv-conditional-gconstant.lspec": [
        "is64bit=true",
        "sXLen=s64",
        "sDoubleXLen=s128",
    ],
    "riscv-scalar-conditional.lspec": [
        "is64bit=true",
        "sXLen=s64",
        "sDoubleXLen=s128",
    ],
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _slots(node) -> list[int]:
    values = {
        getattr(node, name)
        for name in SLOT_FIELDS
        if isinstance(getattr(node, name, None), int)
        and not isinstance(getattr(node, name), bool)
        and getattr(node, name) >= 0
    }
    return sorted(values)


def _expected_mutation(expr):
    if expr is None:
        return None
    record = {
        "kind": type(expr).__name__,
        "text": str(expr),
        "slots": _slots(expr),
    }
    for name, value in vars(expr).items():
        if isinstance(value, int) and not isinstance(value, bool):
            record[name] = value
    return record


def _expected_rule(rule) -> dict:
    return {
        "action": rule.action.value,
        "type_idx": rule.type_idx,
        "predicate": {
            "kind": type(rule.pred).__name__,
            "text": str(rule.pred),
            "slots": _slots(rule.pred),
        },
        "mutation": _expected_mutation(rule.new_type),
        "origin": rule.origin,
    }


def _expected_structure(info) -> dict:
    rulesets = []
    action_counts: dict[str, int] = {}
    macro_counts: dict[str, int] = {}
    macro_invocations = 0
    guarded_off = 0
    for index, ruleset in enumerate(info.rulesets):
        expansions = [
            {
                "name": expansion.name,
                "guard": expansion.guard,
                "first_rule": expansion.first_rule,
                "rule_count": expansion.rule_count,
                "rule_indices": list(
                    range(
                        expansion.first_rule,
                        expansion.first_rule + expansion.rule_count,
                    )
                ),
            }
            for expansion in ruleset.expansions
        ]
        macro_invocations += len(expansions)
        for expansion in ruleset.expansions:
            macro_counts[expansion.name] = (
                macro_counts.get(expansion.name, 0) + 1
            )
            if expansion.guard is False:
                guarded_off += 1
        for rule in ruleset.rules:
            action_counts[rule.action.value] = (
                action_counts.get(rule.action.value, 0) + 1
            )
        rulesets.append(
            {
                "index": index,
                "opcodes": list(ruleset.opcodes),
                "rule_count": len(ruleset.rules),
                "actions": [rule.action.value for rule in ruleset.rules],
                "type_slots": [rule.type_idx for rule in ruleset.rules],
                "mutation_kinds": [
                    type(rule.new_type).__name__
                    if rule.new_type is not None
                    else "none"
                    for rule in ruleset.rules
                ],
                "rules": [_expected_rule(rule) for rule in ruleset.rules],
                "expansions": expansions,
            }
        )
    return {
        "contract_version": codegen.STRUCTURE_CONTRACT_VERSION,
        "rulesets": rulesets,
        "counts": {
            "rulesets": len(info.rulesets),
            "opcodes": len(info.by_opcode),
            "rules": sum(len(rs.rules) for rs in info.rulesets),
            "actions": dict(sorted(action_counts.items())),
            "macro_invocations": macro_invocations,
            "macro_counts": dict(sorted(macro_counts.items())),
            "guarded_off": guarded_off,
        },
    }


def _generate(source: str, *, config=None):
    info = parse(source, config=config)
    with tempfile.TemporaryDirectory() as tmp:
        manifest = codegen.generate(
            info,
            out_dir=pathlib.Path(tmp),
            rules_rel="probe.lspec",
            rules_sha="0" * 64,
            namespace="Probe",
            output_stem="probe_rules_gen",
        )
    return manifest["structure"]


class AllRulesStructureTests(unittest.TestCase):
    def test_all_committed_rules_match_ast_structure(self):
        paths = sorted(RULES_DIR.glob("*.lspec"))
        self.assertEqual(len(paths), 22)
        for path in paths:
            with self.subTest(rule=path.name):
                config = parse_defines(
                    CONFIG_DEFINES.get(path.name, [])
                )
                info = parse_file(path, config=config)
                with tempfile.TemporaryDirectory() as tmp:
                    manifest = codegen.generate(
                        info,
                        out_dir=pathlib.Path(tmp),
                        rules_rel=f"rules/{path.name}",
                        rules_sha=_sha(path.read_bytes()),
                        config=config,
                        namespace="Probe",
                        output_stem=codegen._derive_output_stem(path),
                    )
                self.assertEqual(
                    manifest["structure"], _expected_structure(info)
                )
                self.assertEqual(
                    manifest["structure"]["counts"]["rulesets"],
                    manifest["rulesets"],
                )
                self.assertEqual(
                    manifest["structure"]["counts"]["rules"],
                    manifest["rules"],
                )
                self.assertEqual(
                    manifest["structure"]["counts"]["opcodes"],
                    manifest["opcodes"],
                )


class MacroStructureTests(unittest.TestCase):
    def test_legal_for_and_pairs_record_predicate_and_action(self):
        structure = _generate(
            "ruleset G_A, G_B { legal_for {s32}; "
            "legal_for_pairs {(s32, s32)}; }"
        )
        ruleset = structure["rulesets"][0]
        self.assertEqual(ruleset["opcodes"], ["G_A", "G_B"])
        self.assertEqual(ruleset["actions"], ["Legal", "Legal"])
        self.assertEqual(
            [rule["predicate"]["kind"] for rule in ruleset["rules"]],
            ["TypeIn", "TypePairInSet"],
        )
        self.assertEqual(
            [exp["name"] for exp in ruleset["expansions"]],
            ["legal_for", "legal_for_pairs"],
        )
        self.assertEqual(
            [exp["rule_count"] for exp in ruleset["expansions"]], [1, 1]
        )
        self.assertIn("(s32, s32)", ruleset["rules"][1]["predicate"]["text"])

    def test_clamp_scalar_records_min_then_max(self):
        structure = _generate(
            "ruleset G_X { clamp_scalar 0, s32, s64; }"
        )
        ruleset = structure["rulesets"][0]
        self.assertEqual(ruleset["actions"], ["WidenScalar", "NarrowScalar"])
        self.assertEqual(
            ruleset["mutation_kinds"], ["SetElementSize", "SetElementSize"]
        )
        self.assertEqual(
            [rule["type_idx"] for rule in ruleset["rules"]], [0, 0]
        )
        self.assertLess(
            ruleset["rules"][0]["mutation"]["bits"],
            ruleset["rules"][1]["mutation"]["bits"],
        )
        self.assertIn("step 1/2 (min)", ruleset["rules"][0]["origin"])
        self.assertIn("step 2/2 (max)", ruleset["rules"][1]["origin"])
        self.assertEqual(ruleset["expansions"][0]["rule_count"], 2)

    def test_clamp_num_elements_records_more_then_fewer(self):
        structure = _generate(
            "ruleset G_X { clamp_num_elements 0, v2s32, v8s32; }"
        )
        ruleset = structure["rulesets"][0]
        self.assertEqual(ruleset["actions"], ["MoreElements", "FewerElements"])
        self.assertEqual(
            ruleset["mutation_kinds"],
            ["SetElementCount", "SetElementCount"],
        )
        self.assertEqual(
            [rule["mutation"]["lanes"] for rule in ruleset["rules"]],
            [2, 8],
        )

    def test_widen_and_more_macros_record_exact_mutation_kinds(self):
        structure = _generate(
            "ruleset G_X { widen_to_next_pow2 0, 32; "
            "more_elements_to_next_pow2 0; }"
        )
        ruleset = structure["rulesets"][0]
        self.assertEqual(ruleset["actions"], ["WidenScalar", "MoreElements"])
        self.assertEqual(
            ruleset["mutation_kinds"], ["NextPow2", "NextPow2Lanes"]
        )

    def test_core_more_fewer_custom_and_bitcast_are_visible(self):
        structure = _generate(
            "ruleset G_X { more 0 to v8s32; fewer 0 to v2s32; "
            "custom when t0 == s32; bitcast 0 to concat_bitcast(1); }"
        )
        ruleset = structure["rulesets"][0]
        self.assertEqual(
            ruleset["actions"],
            ["MoreElements", "FewerElements", "Custom", "Bitcast"],
        )
        self.assertEqual(
            ruleset["mutation_kinds"],
            ["SetElementCount", "SetElementCount", "none", "ConcatBitcast"],
        )
        self.assertEqual(
            [exp["guard"] for exp in ruleset["expansions"]],
            [None, None, None, None],
        )

    def test_empty_ruleset_is_recorded(self):
        structure = _generate("ruleset G_X { }")
        ruleset = structure["rulesets"][0]
        self.assertEqual(ruleset["rule_count"], 0)
        self.assertEqual(ruleset["rules"], [])
        self.assertEqual(ruleset["expansions"], [])
        self.assertEqual(structure["counts"]["rules"], 0)

    def test_false_config_guard_records_zero_rule_expansion(self):
        structure = _generate(
            "ruleset G_X { legal_for {s32} when false; "
            "legal_for {s64} when true; }"
        )
        ruleset = structure["rulesets"][0]
        self.assertEqual(ruleset["rule_count"], 1)
        self.assertEqual(
            [exp["guard"] for exp in ruleset["expansions"]],
            [False, True],
        )
        self.assertEqual(
            [exp["rule_count"] for exp in ruleset["expansions"]], [0, 1]
        )
        self.assertEqual(
            [exp["rule_indices"] for exp in ruleset["expansions"]],
            [[], [0]],
        )
        self.assertEqual(structure["counts"]["guarded_off"], 1)

    def test_alias_group_is_one_ruleset_not_two(self):
        structure = _generate(
            "ruleset G_A, G_B, G_C { legal; }"
        )
        self.assertEqual(len(structure["rulesets"]), 1)
        self.assertEqual(
            structure["rulesets"][0]["opcodes"], ["G_A", "G_B", "G_C"]
        )
        self.assertEqual(structure["counts"]["rulesets"], 1)
        self.assertEqual(structure["counts"]["opcodes"], 3)


class MacroGuardMetadataTests(unittest.TestCase):
    def test_unconditional_scalar_elt_macros_record_guard_true(self):
        source = """
ruleset G_X {
  min_scalar_elt_same_as_if 1, 0 when vector(t0);
  max_scalar_elt_same_as_if 1, 0 when vector(t0);
}
"""
        info = parse(source)
        guards = [expansion.guard for expansion in info.rulesets[0].expansions]
        self.assertEqual(guards, [True, True])
        structure = _expected_structure(info)
        recorded = [
            expansion["guard"]
            for expansion in structure["rulesets"][0]["expansions"]
        ]
        self.assertEqual(recorded, [True, True])


if __name__ == "__main__":
    unittest.main()
