# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##
#
# Negative controls for the correctness gates.  A green conformance run is
# only evidence if the harness can turn red when a rule, a config guard, a
# manifest hash, or a header guard is changed.
#
# The oracle comparisons use one witness query per mutation so the tests stay
# fast; the full domain is covered by the conformance runner and by
# scripts/integration_smoke.py.

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from legalizer_dsl import codegen  # noqa: E402
from legalizer_dsl.conformance import (  # noqa: E402
    ComparisonReport,
    _write_input,
    compare,
    python_observation,
)
from legalizer_dsl.model import Query, parse_llt  # noqa: E402
from legalizer_dsl.parser import parse, parse_defines, parse_file  # noqa: E402

TOY_RULES = ROOT / "rules" / "toycpu.ldsl"
TOY_ORACLE = ROOT / "out" / "oracle" / "legalizer-oracle.exe"
RISCV_RULES = ROOT / "rules" / "riscv-conditional-gconstant.ldsl"
RISCV_ORACLE = ROOT / "out" / "oracle-riscv" / "legalizer-oracle-riscv.exe"


def _observation(info, query):
    return python_observation(info, query)[0]


def _oracle_divergences(info, query):
    with tempfile.TemporaryDirectory() as tmp:
        query_path = pathlib.Path(tmp) / "queries.jsonl"
        _write_input(query_path, [query])
        report = ComparisonReport()
        compare(info, TOY_ORACLE, query_path, [query], report)
    return report


class ToyCPUObservationMutations(unittest.TestCase):
    def setUp(self):
        self.source = TOY_RULES.read_text(encoding="utf-8")
        self.baseline = parse_file(TOY_RULES)

    def _mutated(self, old, new):
        mutated = self.source.replace(old, new, 1)
        self.assertNotEqual(mutated, self.source, f"mutation anchor not found: {old!r}")
        return parse(mutated)

    def test_rule_order_swap_changes_observation(self):
        mutated = self._mutated(
            "    clamp_scalar 1, s32, s32;\n"
            "    clamp_scalar 0, s32, s32;",
            "    clamp_scalar 0, s32, s32;\n"
            "    clamp_scalar 1, s32, s32;",
        )
        query = Query("G_SHL", (parse_llt("s1"), parse_llt("s1")))
        self.assertTrue(self.baseline.domain.accepts(query))
        self.assertNotEqual(
            _observation(self.baseline, query),
            _observation(mutated, query),
        )

    def test_removed_clamp_max_changes_observation(self):
        mutated = self._mutated("    clamp_scalar 0, s1, s32;\n", "")
        query = Query("G_CONSTANT", (parse_llt("s64"),))
        self.assertTrue(self.baseline.domain.accepts(query))
        self.assertNotEqual(
            _observation(self.baseline, query),
            _observation(mutated, query),
        )

    def test_changed_action_changes_observation(self):
        mutated = self._mutated(
            "custom_for_pairs {(v4s1, v4s32)};",
            "legal_for_pairs {(v4s1, v4s32)};",
        )
        query = Query("G_ICMP", (parse_llt("v4s1"), parse_llt("v4s32")))
        self.assertTrue(self.baseline.domain.accepts(query))
        self.assertNotEqual(
            _observation(self.baseline, query),
            _observation(mutated, query),
        )

    @unittest.skipUnless(TOY_ORACLE.exists(), "ToyCPU oracle not built")
    def test_rule_order_swap_is_detected_by_oracle(self):
        mutated = self._mutated(
            "    clamp_scalar 1, s32, s32;\n"
            "    clamp_scalar 0, s32, s32;",
            "    clamp_scalar 0, s32, s32;\n"
            "    clamp_scalar 1, s32, s32;",
        )
        report = _oracle_divergences(
            mutated, Query("G_SHL", (parse_llt("s1"), parse_llt("s1")))
        )
        self.assertGreater(report.divergence_count, 0)
        self.assertFalse(report.ok)

    @unittest.skipUnless(TOY_ORACLE.exists(), "ToyCPU oracle not built")
    def test_removed_clamp_max_is_detected_by_oracle(self):
        mutated = self._mutated("    clamp_scalar 0, s1, s32;\n", "")
        report = _oracle_divergences(
            mutated, Query("G_CONSTANT", (parse_llt("s64"),))
        )
        self.assertGreater(report.divergence_count, 0)
        self.assertFalse(report.ok)

    @unittest.skipUnless(TOY_ORACLE.exists(), "ToyCPU oracle not built")
    def test_changed_action_is_detected_by_oracle(self):
        mutated = self._mutated(
            "custom_for_pairs {(v4s1, v4s32)};",
            "legal_for_pairs {(v4s1, v4s32)};",
        )
        report = _oracle_divergences(
            mutated, Query("G_ICMP", (parse_llt("v4s1"), parse_llt("v4s32")))
        )
        self.assertGreater(report.divergence_count, 0)
        self.assertFalse(report.ok)


class ConfigGuardMutationTests(unittest.TestCase):
    DEFINES = {"is64bit": True, "sXLen": "s64"}

    @unittest.skipUnless(RISCV_ORACLE.exists(), "RV64 oracle not built")
    def test_flipped_config_guard_is_detected(self):
        source = RISCV_RULES.read_text(encoding="utf-8")
        baseline = parse_file(RISCV_RULES, config=parse_defines([
            "is64bit=true", "sXLen=s64",
        ]))
        mutated_source = source.replace(
            "legal_for {s32} when not is64bit;",
            "legal_for {s32} when is64bit;",
            1,
        )
        self.assertNotEqual(mutated_source, source, "guard anchor not found")
        mutated = parse(mutated_source, config=parse_defines([
            "is64bit=true", "sXLen=s64",
        ]))
        query = Query("G_CONSTANT", (parse_llt("s32"),))
        self.assertTrue(baseline.domain.accepts(query))
        self.assertNotEqual(
            _observation(baseline, query),
            _observation(mutated, query),
        )

        with tempfile.TemporaryDirectory() as tmp:
            query_path = pathlib.Path(tmp) / "queries.jsonl"
            _write_input(query_path, [query])
            report = ComparisonReport()
            compare(mutated, RISCV_ORACLE, query_path, [query], report)
        self.assertGreater(report.divergence_count, 0)


class ProvenanceMutationTests(unittest.TestCase):
    def _argv(self, out, wiring=None):
        argv = [
            "--rules", str(TOY_RULES),
            "--out-dir", str(out),
            "--llvm-commit", "deadbeef",
        ]
        if wiring is not None:
            argv += ["--emit-wiring", str(wiring)]
        return argv

    def test_manifest_llvm_commit_tamper_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "out"
            argv = self._argv(out)
            self.assertEqual(codegen.main(argv), 0)
            manifest_path = out / codegen.GENERATED_MANIFEST
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["llvm_commit"] = "tampered"
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(codegen.main(argv + ["--check"]), 1)

    def test_manifest_rules_sha_tamper_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "out"
            argv = self._argv(out)
            self.assertEqual(codegen.main(argv), 0)
            manifest_path = out / codegen.GENERATED_MANIFEST
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["rules_sha256"] = "0" * 64
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(codegen.main(argv + ["--check"]), 1)

    def test_header_guard_tamper_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "out"
            argv = self._argv(out)
            self.assertEqual(codegen.main(argv), 0)
            header = out / codegen.GENERATED_HEADER
            text = header.read_text(encoding="utf-8")
            mutated = text.replace(
                "TOYCPU_TOYCPU_RULES_GEN_H",
                "TOYCPU_TOYCPU_RULES_GEN_TAMPERED_H",
                1,
            )
            self.assertNotEqual(mutated, text, "header guard anchor not found")
            header.write_text(mutated, encoding="utf-8")
            self.assertEqual(codegen.main(argv + ["--check"]), 1)

    def test_wiring_header_guard_tamper_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            out = root / "out"
            wiring = root / "wiring"
            argv = self._argv(out, wiring)
            self.assertEqual(codegen.main(argv), 0)
            cmake = wiring / "legalizer-rules.cmake"
            text = cmake.read_text(encoding="utf-8")
            mutated = text.replace(
                'set(LEGALIZER_DSL_HEADER_GUARD "TOYCPU_TOYCPU_RULES_GEN_H")',
                'set(LEGALIZER_DSL_HEADER_GUARD "TOYCPU_TOYCPU_RULES_GEN_TAMPERED_H")',
                1,
            )
            self.assertNotEqual(mutated, text, "wiring guard anchor not found")
            cmake.write_text(mutated, encoding="utf-8")
            self.assertEqual(codegen.main(argv + ["--check"]), 1)


if __name__ == "__main__":
    unittest.main()
