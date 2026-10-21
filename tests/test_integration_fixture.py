# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""P4 integration fixture contract tests."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _load_smoke():
    path = ROOT / "scripts" / "integration_smoke.py"
    spec = importlib.util.spec_from_file_location("integration_smoke", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_status():
    path = ROOT / "scripts" / "check_integration_status.py"
    spec = importlib.util.spec_from_file_location("check_integration_status", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class IntegrationFixtureTests(unittest.TestCase):
    def test_consumer_is_a_policy_free_adapter(self):
        text = (ROOT / "examples" / "integration" / "main.cpp").read_text(
            encoding="utf-8"
        )
        self.assertIn('#include "wiring.cpp.inc"', text)
        self.assertIn("LEGALIZER_DSL_APPLY_RULES(LI)", text)
        self.assertIn("LI.getAction(Query)", text)
        self.assertNotIn("legal_for", text)
        self.assertNotIn("LegalizeRuleSet", text)

    def test_cmake_consumes_generated_wiring(self):
        text = (
            ROOT / "examples" / "integration" / "CMakeLists.txt"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'include("${LDSL_WIRING_DIR}/legalizer-rules.cmake")', text
        )
        self.assertIn("target_link_libraries(legalizer-dsl-consumer", text)
        self.assertIn("LLVM_DIR", text)
        self.assertIn("LDSL_USE_EXTENDED_LLT", text)

    def test_smoke_declares_toycpu_and_aarch64(self):
        smoke = _load_smoke()
        self.assertEqual(set(smoke.TARGETS), {"toycpu", "aarch64-vector"})
        for name, target in smoke.TARGETS.items():
            with self.subTest(target=name):
                self.assertTrue((ROOT / target["rules"]).is_file())
                self.assertIn("oracle", target)
                self.assertIn("llvm_dirs", target)
        self.assertFalse(smoke.TARGETS["toycpu"]["extended_llt"])
        self.assertTrue(smoke.TARGETS["aarch64-vector"]["extended_llt"])

    def test_query_selection_is_deterministic_and_bounded(self):
        from legalizer_dsl.parser import parse_file

        smoke = _load_smoke()
        info = parse_file(ROOT / "rules" / "toycpu.ldsl")
        first = smoke._select_queries(info, 40)
        second = smoke._select_queries(info, 40)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 40)
        self.assertGreater(len(smoke._select_queries(info, 0)), 40)

    def test_report_schema_is_distinct_from_conformance(self):
        smoke = _load_smoke()
        self.assertEqual(smoke.SCHEMA, "legalizer-dsl-integration-v1")

    def test_status_gate_rejects_skip_when_pass_is_required(self):
        import hashlib
        import json
        import tempfile

        checker = _load_status()
        rules_rel = "rules/toycpu.ldsl"
        rules_sha = hashlib.sha256(
            (ROOT / rules_rel).read_bytes()
        ).hexdigest()

        def report(status: str, **overrides):
            data = {
                "schema": "legalizer-dsl-integration-v1",
                "status": status,
                "ok": True,
                "target": "toycpu",
                "inputs": {
                    "rules": rules_rel,
                    "rules_sha256": rules_sha,
                    "query_count": 1,
                },
                "comparisons": {
                    "consumer": {
                        "ok": True,
                        "divergence_count": 0,
                        "oracle_errors": 0,
                    }
                },
            }
            data.update(overrides)
            return data

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            skip = root / "skip.json"
            passed = root / "pass.json"
            forged = root / "forged.json"
            skip.write_text(
                json.dumps(report("SKIP", ok=False, skip_reason="test environment")),
                encoding="utf-8",
            )
            passed.write_text(json.dumps(report("PASS")), encoding="utf-8")
            forged.write_text(
                json.dumps(
                    report(
                        "PASS",
                        comparisons={
                            "consumer": {
                                "ok": True,
                                "divergence_count": 1,
                                "oracle_errors": 0,
                            }
                        },
                    )
                ),
                encoding="utf-8",
            )
            self.assertEqual(checker.main([str(skip)]), 0)
            self.assertEqual(checker.main([str(skip), "--require-pass"]), 1)
            self.assertEqual(checker.main([str(passed), "--require-pass"]), 0)
            self.assertEqual(checker.main([str(forged), "--require-pass"]), 1)


if __name__ == "__main__":
    unittest.main()
