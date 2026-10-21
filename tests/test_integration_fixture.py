# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""P4 integration fixture contract tests."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import sys
import tempfile
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
        self.assertEqual(smoke.SCHEMA, "legalizer-dsl-integration-v2")
        self.assertEqual(set(smoke.TARGETS), {"toycpu", "aarch64-vector"})

    def test_status_gate_accepts_a_fully_pinned_pass(self):
        checker = _load_status()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            data = _fake_report(root)
            report = root / "pass.json"
            report.write_text(json.dumps(data), encoding="utf-8")
            with _patched_root(checker, root):
                self.assertEqual(checker.main([str(report), "--require-pass"]), 0)

    def test_status_gate_rejects_unpinned_or_overstated_reports(self):
        checker = _load_status()
        cases = {
            "v1-schema": lambda data: data.update(
                schema="legalizer-dsl-integration-v1"
            ),
            "small-sample": lambda data: data["inputs"].update(query_count=1),
            "no-provenance": lambda data: data.pop("provenance"),
            "no-generated-oracle": lambda data: data["comparisons"].pop(
                "generated_oracle"
            ),
            "dirty-worktree": lambda data: data["provenance"].update(
                worktree_dirty=True
            ),
            "short-head": lambda data: data["provenance"].update(
                head_commit="abc"
            ),
            "null-consumer": lambda data: data["provenance"].update(
                consumer_sha256=None
            ),
            "consumer-diverged": lambda data: data["comparisons"].update(
                consumer={
                    "ok": True,
                    "queries": 100,
                    "divergence_count": 1,
                    "oracle_errors": 0,
                }
            ),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                with tempfile.TemporaryDirectory() as tmp:
                    root = pathlib.Path(tmp)
                    data = _fake_report(root)
                    mutate(data)
                    report = root / "report.json"
                    report.write_text(json.dumps(data), encoding="utf-8")
                    with _patched_root(checker, root):
                        self.assertEqual(
                            checker.main([str(report), "--require-pass"]), 1
                        )

    def test_status_gate_rejects_skip_when_pass_is_required(self):
        checker = _load_status()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            skip = root / "skip.json"
            skip.write_text(
                json.dumps(
                    {
                        "schema": checker.SCHEMA,
                        "status": "SKIP",
                        "ok": False,
                        "skip_reason": "test environment",
                    }
                ),
                encoding="utf-8",
            )
            with _patched_root(checker, root):
                self.assertEqual(checker.main([str(skip)]), 0)
                self.assertEqual(checker.main([str(skip), "--require-pass"]), 1)


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fake_report(root: pathlib.Path) -> dict:
    """Build a PASS report whose artifact hashes are all real."""

    artifacts = root / "artifacts"
    artifacts.mkdir()
    rules = root / "rules" / "toycpu.ldsl"
    rules.parent.mkdir()
    rules.write_text("ruleset G_X { legal_for {s32}; }\n", encoding="utf-8")
    query_file = artifacts / "queries.jsonl"
    query_file.write_text('{"opcode": "G_X"}\n', encoding="utf-8")
    consumer = artifacts / "consumer.bin"
    consumer.write_bytes(b"consumer")
    hand_oracle = artifacts / "hand-oracle.bin"
    hand_oracle.write_bytes(b"hand")
    generated_oracle = artifacts / "generated-oracle.bin"
    generated_oracle.write_bytes(b"generated")
    header = artifacts / "rules.h"
    header.write_text("// header\n", encoding="utf-8")
    source = artifacts / "rules.cpp"
    source.write_text("// source\n", encoding="utf-8")

    query_sha = _sha(query_file)
    rules_sha = _sha(rules)
    return {
        "schema": "legalizer-dsl-integration-v2",
        "target": "toycpu",
        "status": "PASS",
        "ok": True,
        "provenance": {
            "generator_version": "test",
            "dsl_spec_version": "1",
            "evaluator_sha256": "a" * 64,
            "codegen_sha256": "b" * 64,
            "llvm_commit": "c" * 40,
            "head_commit": "d" * 40,
            "worktree_dirty": False,
            "rules_sha256": rules_sha,
            "query_sha256": query_sha,
            "generated": {
                "header": header.relative_to(root).as_posix(),
                "header_sha256": _sha(header),
                "source": source.relative_to(root).as_posix(),
                "source_sha256": _sha(source),
            },
            "consumer_sha256": _sha(consumer),
            "oracle_sha256": {
                "hand_written_oracle": _sha(hand_oracle),
                "generated_oracle": _sha(generated_oracle),
            },
        },
        "inputs": {
            "rules": rules.relative_to(root).as_posix(),
            "rules_sha256": rules_sha,
            "query_count": 100,
            "query_sha256": query_sha,
        },
        "artifacts": {
            "query_file": query_file.relative_to(root).as_posix(),
            "consumer": consumer.relative_to(root).as_posix(),
            "oracles": {
                "hand_written_oracle": hand_oracle.relative_to(root).as_posix(),
                "generated_oracle": generated_oracle.relative_to(root).as_posix(),
            },
        },
        "comparisons": {
            name: {
                "ok": True,
                "queries": 100,
                "divergence_count": 0,
                "oracle_errors": 0,
            }
            for name in ("consumer", "hand_written_oracle", "generated_oracle")
        },
    }


class _patched_root:
    """Point the status gate at a temporary fixture tree."""

    def __init__(self, module, root: pathlib.Path):
        self.module = module
        self.root = root
        self.saved = (module.ROOT, module.current_expected)

    def __enter__(self):
        self.module.ROOT = self.root
        self.module.current_expected = lambda: {}
        return self.module

    def __exit__(self, *exc):
        self.module.ROOT, self.module.current_expected = self.saved
        return False


if __name__ == "__main__":
    unittest.main()
