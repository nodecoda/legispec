# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Fail-closed behavior of the release gates themselves."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
for entry in (ROOT, ROOT / "src", ROOT / "scripts"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))


def _load(name: str, filename: str):
    path = ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CodegenContractGateTests(unittest.TestCase):
    def test_zero_rule_selection_fails_instead_of_passing(self):
        contract = _load("check_codegen_contract", "check_codegen_contract.py")
        with tempfile.TemporaryDirectory() as tmp:
            contract.RULES = pathlib.Path(tmp)
            self.assertEqual(contract.main([]), 1)


class ExpectedSkipsTests(unittest.TestCase):
    def setUp(self):
        self.module = _load("check_skips", "check_skips.py")

    def test_unregistered_skip_fails(self):
        problems = self.module.compare_skips(
            {"test_x.Case.test_a": "no oracle"}, {}
        )
        self.assertTrue(any("unregistered skip" in p for p in problems), problems)

    def test_registered_skip_that_no_longer_happens_is_stale(self):
        problems = self.module.compare_skips(
            {}, {"test_x.Case.test_a": "no oracle"}
        )
        self.assertTrue(any("did not happen" in p for p in problems), problems)

    def test_platform_scoped_entries_only_apply_on_that_platform(self):
        entries = [
            {
                "test": "test_x.Case.test_a",
                "reason": "no oracle",
                "platforms": ["linux"],
            }
        ]
        self.assertEqual(
            self.module.allowed_for_platform(entries, "linux"),
            {"test_x.Case.test_a": "no oracle"},
        )
        self.assertEqual(self.module.allowed_for_platform(entries, "windows"), {})

    def test_manifest_requires_a_reason(self):
        with self.assertRaises(ValueError):
            self.module.allowed_for_platform(
                [{"test": "test_x.Case.test_a", "reason": " "}], "linux"
            )


class FinalVerificationTests(unittest.TestCase):
    def setUp(self):
        self.module = _load("final_verification", "final_verification.py")
        self.out_rel = "out/test-final-verification.json"
        self.out_path = ROOT / self.out_rel
        self.addCleanup(
            lambda: self.out_path.unlink() if self.out_path.exists() else None
        )
        self.clean_repo = {
            "project_dirty": False,
            "dirty": False,
            "worktree_sha256": "0" * 64,
            "head_commit": "a" * 40,
        }
        self.claims = {
            "schema": "legalizer-dsl-claims-v1",
            "claim_contract_version": 1,
            "llvm_commit": "b" * 40,
            "boundary": "test boundary",
            "claims": [],
        }

    def _gate(self, name="gate"):
        return {"name": name, "status": "PASS", "exit_code": 0}

    def _patch(self, *, repository=None, integration_problems=None, unit_problems=None):
        repository = repository or (lambda base: (dict(self.clean_repo), []))
        integration_problems = integration_problems or []
        unit_problems = unit_problems or []
        unit = self._gate("unit-tests")
        unit.update({"tests": 1, "skipped": 0})
        patches = [
            mock.patch.object(self.module, "collect_repository", side_effect=repository),
            mock.patch.object(self.module, "load_json", return_value=dict(self.claims)),
            mock.patch.object(self.module, "git", return_value="a" * 40),
            mock.patch.object(
                self.module,
                "gate_unit_tests",
                return_value=(unit, list(unit_problems)),
            ),
            mock.patch.object(
                self.module, "gate_claims", return_value=(self._gate(), [])
            ),
            mock.patch.object(
                self.module,
                "gate_closure",
                return_value=(self._gate(), [], {}),
            ),
            mock.patch.object(
                self.module, "gate_coverage", return_value=(self._gate(), [])
            ),
            mock.patch.object(
                self.module,
                "gate_codegen_contract",
                return_value=(self._gate(), []),
            ),
            mock.patch.object(
                self.module,
                "gate_integration",
                return_value=(
                    self._gate("integration-status"),
                    [],
                    [{"target": "toycpu", "environment": {}}],
                    list(integration_problems),
                ),
            ),
            mock.patch.object(
                self.module,
                "collect_environment",
                return_value=({"llvm_builds": ["test"]}, []),
            ),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _artifact(self) -> dict:
        return json.loads(self.out_path.read_text(encoding="utf-8"))

    def test_unittest_skip_count_ignores_nested_gate_summaries(self):
        text = (
            "final-verification: PASS mode=artifact-consistency gates=6/6 "
            "dirty=false tests=1 skipped=0\n"
            "Ran 375 tests in 61.405s\n"
            "OK (skipped=1)\n"
        )
        with mock.patch.object(
            self.module, "run_gate", return_value=(self._gate("unit-tests"), text)
        ):
            gate, problems = self.module.gate_unit_tests(375, 1)
        self.assertEqual(problems, [])
        self.assertEqual(gate["tests"], 375)
        self.assertEqual(gate["skipped"], 1)

    def test_unittest_without_skips_parses_zero(self):
        text = "Ran 375 tests in 61.405s\nOK\n"
        self.assertEqual(self.module.parse_unittest_skips(text), 0)

    def test_tests_and_skipped_flags_are_mandatory(self):
        self._patch()
        self.assertEqual(self.module.main(["--skipped", "0", "--out", self.out_rel]), 1)
        self.assertTrue(
            any("--tests is required" in p for p in self._artifact()["problems"])
        )
        self.assertEqual(self.module.main(["--tests", "1", "--out", self.out_rel]), 1)
        self.assertTrue(
            any("--skipped is required" in p for p in self._artifact()["problems"])
        )

    def test_dirty_worktree_fails_without_allow_dirty(self):
        self._patch()
        dirty = dict(self.clean_repo, project_dirty=True, dirty=True)
        with mock.patch.object(
            self.module, "collect_repository", return_value=(dirty, [])
        ):
            rc = self.module.main(
                ["--tests", "1", "--skipped", "0", "--out", self.out_rel]
            )
        self.assertEqual(rc, 1)
        problems = self._artifact()["problems"]
        self.assertTrue(any("project worktree is dirty" in p for p in problems), problems)

    def test_worktree_mutation_during_gates_fails(self):
        first = dict(self.clean_repo, worktree_sha256="1" * 64)
        second = dict(self.clean_repo, worktree_sha256="2" * 64)
        self._patch(repository=[(first, []), (second, [])])
        rc = self.module.main(["--tests", "1", "--skipped", "0", "--out", self.out_rel])
        self.assertEqual(rc, 1)
        problems = self._artifact()["problems"]
        self.assertTrue(
            any("worktree changed while the gates ran" in p for p in problems),
            problems,
        )

    def test_clean_artifact_consistency_run_reports_mode_and_limitations(self):
        self._patch()
        rc = self.module.main(["--tests", "1", "--skipped", "0", "--out", self.out_rel])
        self.assertEqual(rc, 0)
        artifact = self._artifact()
        self.assertEqual(artifact["result"], "PASS")
        self.assertEqual(artifact["verification_mode"], "artifact-consistency")
        self.assertEqual(artifact["execution"]["claims"], "hash-and-corpus-audit")
        self.assertEqual(artifact["execution"]["integration"], "existing-reports")
        self.assertTrue(artifact["limitations"])
        self.assertEqual(artifact["problems"], [])

    def test_integration_problems_propagate_to_the_result(self):
        self._patch(integration_problems=["report is stale"])
        rc = self.module.main(["--tests", "1", "--skipped", "0", "--out", self.out_rel])
        self.assertEqual(rc, 1)
        self.assertIn("report is stale", self._artifact()["problems"])


if __name__ == "__main__":
    unittest.main()
