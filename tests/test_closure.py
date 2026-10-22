# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Structural closure must not trust a tampered ledger."""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legispec import closure


class SkipEvidenceTamperTests(unittest.TestCase):
    def test_a_negative_cached_verdict_still_fails_in_structural_mode(self):
        saved = (
            closure.LEDGER,
            closure.RULES,
            closure.OUT,
            closure.CONSTRUCTS,
            closure.CLOSURE_MD,
            closure.TESTS,
            closure.GENERATED_EVIDENCE,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            rules = root / "rules"
            rules.mkdir()
            (rules / "x.lspec").write_text("ruleset G_X { legal; }\n", encoding="utf-8")
            coverage = root / "construct-coverage.json"
            coverage.write_text(
                json.dumps({"counts": {}, "methods": []}), encoding="utf-8"
            )
            pinned = root / "CLOSURE.md"
            pinned.write_text(
                "<!-- closure:pinned -->\n"
                "claims=1\nhand-claims=1\ngenerated-claims=0\n"
                "generated-covered=0\nhand-only=1\n"
                "rules-files=1\nhand-queries=0\nconstructs=0\ntests=0\n"
                "<!-- /closure:pinned -->\n",
                encoding="utf-8",
            )
            evidence = root / "generated-evidence.json"
            evidence.write_text(
                json.dumps(
                    {
                        "schema": "legispec-generated-evidence-v1",
                        "hand_only": {"x.lspec": "hand oracle only in this fixture"},
                    }
                ),
                encoding="utf-8",
            )
            tests = root / "tests"
            tests.mkdir()
            closure.LEDGER = root / "claims.json"
            closure.LEDGER.write_text(
                json.dumps(
                    {
                        "schema": "legispec-claims-v1",
                        "claim_contract_version": 1,
                        "llvm_commit": "a" * 40,
                        "boundary": "test",
                        "evaluator_sha256": "a" * 64,
                        "contract": {
                            "observation": ["action", "type_idx", "new_type"],
                            "equivalence": "componentwise; LLT::operator==",
                            "domain": "test domain",
                            "closure": "test closure",
                            "unknown": [],
                            "evidence": [
                                "rules_sha256",
                                "report_sha256",
                                "corpus_sha256",
                                "oracle_sha256",
                            ],
                        },
                        "claims": [
                            {
                                "id": "x",
                                "rules": "x.lspec",
                                "report": "x.json",
                                "oracle": "oracle/x.exe",
                                "scope": "test",
                                "config": {},
                                "queries": 0,
                                "rules_total": 1,
                                "rules_matched": 0,
                                "status": "FAIL",
                                "unknown": [],
                                "llvm_commit": "a" * 40,
                                "ok": False,
                                "divergences": 7,
                                "rules_sha256": closure.sha256(rules / "x.lspec"),
                                "report_sha256": "a" * 64,
                                "corpus_sha256": "a" * 64,
                                "oracle_sha256": "a" * 64,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            closure.RULES = rules
            closure.OUT = root / "out"
            closure.CONSTRUCTS = coverage
            closure.CLOSURE_MD = pinned
            closure.TESTS = tests
            closure.GENERATED_EVIDENCE = evidence
            try:
                with contextlib.redirect_stdout(io.StringIO()) as captured:
                    rc = closure.audit(False, True)
            finally:
                (
                    closure.LEDGER,
                    closure.RULES,
                    closure.OUT,
                    closure.CONSTRUCTS,
                    closure.CLOSURE_MD,
                    closure.TESTS,
                    closure.GENERATED_EVIDENCE,
                ) = saved
        self.assertEqual(rc, 1)
        self.assertIn("not marked ok", captured.getvalue())
        self.assertIn("divergence", captured.getvalue())


class GeneratedEvidenceTests(unittest.TestCase):
    def _run(self, rules: dict[str, str], claims: list[dict], hand_only: dict):
        saved = (closure.RULES, closure.GENERATED_EVIDENCE)
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            rule_dir = root / "rules"
            rule_dir.mkdir()
            for name in rules:
                (rule_dir / name).write_text("ruleset G_X { legal; }\n", encoding="utf-8")
            manifest = root / "generated-evidence.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema": "legispec-generated-evidence-v1",
                        "hand_only": hand_only,
                    }
                ),
                encoding="utf-8",
            )
            closure.RULES = rule_dir
            closure.GENERATED_EVIDENCE = manifest
            try:
                return closure.generated_evidence(claims)
            finally:
                closure.RULES, closure.GENERATED_EVIDENCE = saved

    def test_every_rule_needs_generated_claim_or_hand_only_reason(self):
        summary, problems = self._run(
            {"covered.lspec": "", "declared.lspec": "", "missing.lspec": ""},
            [{"id": "covered-generated", "rules": "covered.lspec"}],
            {"declared.lspec": "no generated oracle for this slice"},
        )
        self.assertEqual(summary["generated_covered"], 1)
        self.assertEqual(summary["hand_only"], ["declared.lspec"])
        self.assertTrue(
            any("missing.lspec" in problem for problem in problems), problems
        )

    def test_generated_and_hand_only_are_mutually_exclusive(self):
        _, problems = self._run(
            {"both.lspec": ""},
            [{"id": "both-generated", "rules": "both.lspec"}],
            {"both.lspec": "contradictory"},
        )
        self.assertTrue(
            any("both generated and hand-only" in problem for problem in problems),
            problems,
        )

    def test_non_object_manifest_is_a_problem_not_a_crash(self):
        saved = (closure.RULES, closure.GENERATED_EVIDENCE)
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            rule_dir = root / "rules"
            rule_dir.mkdir()
            manifest = root / "generated-evidence.json"
            manifest.write_text("[]\n", encoding="utf-8")
            closure.RULES = rule_dir
            closure.GENERATED_EVIDENCE = manifest
            try:
                _, problems = closure.generated_evidence([])
            finally:
                closure.RULES, closure.GENERATED_EVIDENCE = saved
        self.assertTrue(
            any("expected object" in problem for problem in problems), problems
        )

    def test_hand_only_declaration_must_reference_a_real_rule_with_a_reason(self):
        _, problems = self._run(
            {"real.lspec": ""},
            [],
            {"ghost.lspec": "  "},
        )
        self.assertTrue(any("missing rule" in problem for problem in problems), problems)
        self.assertTrue(any("no reason" in problem for problem in problems), problems)


if __name__ == "__main__":
    unittest.main()
