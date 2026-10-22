# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0.
# See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0
#
# ===----------------------------------------------------------------------===##

"""The claim ledger must have a machine-checkable correctness contract."""

from __future__ import annotations

import copy
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legispec import claims


REPO = pathlib.Path(__file__).resolve().parents[1]
LEDGER = REPO / "claims" / "claims.json"
SCHEMA = REPO / "claims" / "claim-schema.json"
sys.path.insert(0, str(REPO / "scripts"))


def ledger() -> dict:
    return json.loads(LEDGER.read_text(encoding="utf-8-sig"))


class SchemaDocumentTests(unittest.TestCase):
    def test_schema_is_json_and_declares_the_contract(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8-sig"))
        self.assertEqual(schema.get("$schema"), "https://json-schema.org/draft/2020-12/schema")
        self.assertIn("claim_contract_version", schema["required"])
        self.assertIn("contract", schema["required"])
        self.assertIn("claims", schema["required"])
        claim = schema["$defs"]["claim"]
        for field in (
            "id",
            "rules",
            "status",
            "unknown",
            "llvm_commit",
            "rules_sha256",
            "report_sha256",
            "corpus_sha256",
            "oracle_sha256",
        ):
            self.assertIn(field, claim["required"])

    def test_real_ledger_satisfies_the_contract(self):
        problems = claims.validate_contract(ledger(), require_evidence=True)
        self.assertEqual(problems, [])

    def test_real_ledger_has_one_explicit_status_per_claim(self):
        data = ledger()
        statuses = [claim.get("status") for claim in data["claims"]]
        self.assertEqual(len(statuses), len(data["claims"]))
        self.assertTrue(all(status in ("PROVEN", "FAIL", "UNKNOWN") for status in statuses))
        for claim in data["claims"]:
            self.assertIn("unknown", claim)
            self.assertIsInstance(claim["unknown"], list)


class ContractFailureTests(unittest.TestCase):
    def test_missing_contract_version_is_reported(self):
        data = ledger()
        data.pop("claim_contract_version")
        problems = claims.validate_contract(data)
        self.assertTrue(any("claim_contract_version" in p for p in problems), problems)

    def test_duplicate_claim_id_is_reported(self):
        data = ledger()
        data["claims"][1]["id"] = data["claims"][0]["id"]
        problems = claims.validate_contract(data)
        self.assertTrue(any("duplicate id" in p for p in problems), problems)

    def test_bad_status_is_reported(self):
        data = ledger()
        data["claims"][0]["status"] = "MAYBE"
        problems = claims.validate_contract(data)
        self.assertTrue(any("status must be" in p for p in problems), problems)

    def test_unknown_without_reason_is_reported(self):
        data = ledger()
        claim = data["claims"][0]
        claim["ok"] = False
        claim["status"] = "UNKNOWN"
        claim["unknown"] = [{"query": "G_X [s32]"}]
        problems = claims.validate_contract(data)
        self.assertTrue(any("needs a non-empty reason" in p for p in problems), problems)

    def test_unknown_with_reason_is_representable(self):
        data = ledger()
        claim = data["claims"][0]
        claim["ok"] = False
        claim["status"] = "UNKNOWN"
        claim["unknown"] = [
            {
                "query": "G_X [s32]",
                "reason": "outside the declared opcode contract",
                "owner": "test",
                "reviewed_at": "2026-10-21",
            }
        ]
        problems = claims.validate_contract(data)
        self.assertEqual(problems, [])

    def test_bad_evidence_pin_is_reported(self):
        data = ledger()
        data["claims"][0]["rules_sha256"] = "not-a-sha"
        problems = claims.validate_contract(data)
        self.assertTrue(any("rules_sha256" in p for p in problems), problems)

    def test_missing_pin_is_only_required_in_evidence_mode(self):
        data = ledger()
        data["claims"][0].pop("rules_sha256")
        self.assertEqual(claims.validate_contract(data), [])
        problems = claims.validate_contract(data, require_evidence=True)
        self.assertTrue(any("missing evidence pin" in p for p in problems), problems)

    def test_proven_requires_ok_true(self):
        data = ledger()
        data["claims"][0]["ok"] = False
        problems = claims.validate_contract(data)
        self.assertTrue(any("PROVEN requires ok=true" in p for p in problems), problems)


class AuditStatusTests(unittest.TestCase):
    def test_finish_derives_status_from_ok(self):
        self.assertEqual(claims._finish({"ok": True})["status"], "PROVEN")
        self.assertEqual(claims._finish({"ok": False})["status"], "FAIL")


class RefreshEvidenceContractTests(unittest.TestCase):
    def test_ledger_write_authorizes_reports_that_were_re_run(self):
        import refresh_evidence

        command = refresh_evidence._verify_command()
        self.assertIn("--verify-corpus", command)
        self.assertIn("--write", command)
        self.assertIn("--allow-evaluator-change", command)
        self.assertIn("--allow-report-change", command)
        self.assertIn("--allow-commit-change", command)
        with self.assertRaises(SystemExit):
            refresh_evidence.main(["--claim", "toycpu", "--write-ledger"])


if __name__ == "__main__":
    unittest.main()