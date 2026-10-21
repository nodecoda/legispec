# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""The claim ledger must stay honest.

These tests do not re-run conformance.  They check that every claim in the
ledger still points at a report produced from the current rules file, with
zero divergences, and that a changed rules file invalidates the claim.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legalizer_dsl import claims


EVIDENCE_PRESENT = all(
    (claims.OUT / claim.report).is_file() and (claims.OUT / claim.oracle).is_file()
    for claim in claims.CLAIMS
)


@unittest.skipUnless(
    EVIDENCE_PRESENT,
    "claim evidence (reports + oracles) not collected; see docs/EVIDENCE.md",
)
class ClaimLedgerTests(unittest.TestCase):
    def test_every_claim_is_proven(self):
        entries = claims.audit_all()
        failures = [
            (e["id"], e["problems"]) for e in entries if not e["ok"]
        ]
        self.assertEqual(failures, [])
        self.assertGreaterEqual(len(entries), 17)

    def test_every_claim_pins_a_rules_hash(self):
        for entry in claims.audit_all():
            self.assertEqual(len(entry.get("rules_sha256", "")), 64, entry["id"])

    def test_changed_rules_file_invalidates_the_claim(self):
        entries = claims.audit_all()
        previous = {
            "claims": [
                {"id": e["id"], "rules_sha256": "0" * 64} for e in entries
            ]
        }
        claims.check_staleness(entries, previous)
        self.assertTrue(all(not e["ok"] for e in entries))
        self.assertTrue(
            all(any("STALE" in p for p in e["problems"]) for e in entries)
        )

    def test_every_claim_pins_an_oracle_hash(self):
        for entry in claims.audit_all():
            self.assertEqual(len(entry.get("oracle_sha256", "")), 64, entry["id"])
            self.assertGreater(entry.get("oracle_size") or 0, 0, entry["id"])

    def test_changed_oracle_binary_invalidates_the_claim(self):
        entries = claims.audit_all()
        previous = {
            "claims": [
                {"id": e["id"], "oracle_sha256": "0" * 64} for e in entries
            ]
        }
        claims.check_staleness(entries, previous)
        self.assertTrue(all(not e["ok"] for e in entries))
        self.assertTrue(
            all(any("STALE: oracle" in p for p in e["problems"]) for e in entries)
        )

    def test_unpinned_report_is_not_proven(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            report = pathlib.Path(tmp) / "report.json"
            oracle = claims.OUT / "oracle" / "legalizer-oracle.exe"
            report.write_text(
                json.dumps(
                    {
                        "schema": "legalizer-dsl-conformance-v1",
                        "inputs": {
                            "rules": str(claims.RULES / "toycpu.ldsl"),
                            "config": {},
                        },
                        "reference": {
                            "oracle": str(oracle),
                            "llvm_build": "F:/llvm/build-toycpu",
                        },
                        "comparison": {
                            "divergence_count": 0,
                            "oracle_errors": [],
                            "queries_compared": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )
            claim = claims.Claim(
                id="unpinned",
                rules="toycpu.ldsl",
                report=str(report),
                oracle="oracle/legalizer-oracle.exe",
                scope="test",
                excludes=(),
                config={},
                queries=1,
                rules_total=1,
                rules_matched=1,
                declared_unreachable=0,
            )
            saved_out, saved_claims = claims.OUT, claims.CLAIMS
            try:
                claims.OUT = pathlib.Path(tmp)
                claims.CLAIMS = [claim]
                entry = claims.audit(claim)
            finally:
                claims.OUT, claims.CLAIMS = saved_out, saved_claims
        self.assertFalse(entry["ok"])
        self.assertTrue(
            any("predates oracle pinning" in p for p in entry["problems"])
        )

    def test_report_with_stale_rules_content_is_not_proven(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            oracle = claims.OUT / "oracle" / "legalizer-oracle.exe"
            rules = claims.RULES / "toycpu.ldsl"
            report = pathlib.Path(tmp) / "report.json"
            report.write_text(
                json.dumps(
                    {
                        "schema": "legalizer-dsl-conformance-v1",
                        "inputs": {
                            "rules": str(rules),
                            "rules_sha256": "0" * 64,
                            "config": {},
                        },
                        "reference": {
                            "oracle": str(oracle),
                            "oracle_sha256": claims._sha256(oracle),
                            "llvm_build": "F:/llvm/build-toycpu",
                        },
                        "comparison": {
                            "divergence_count": 0,
                            "oracle_errors": [],
                            "queries_compared": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )
            claim = claims.Claim(
                id="stale-rules",
                rules="toycpu.ldsl",
                report=str(report),
                oracle="oracle/legalizer-oracle.exe",
                scope="test",
                excludes=(),
                config={},
                queries=1,
                rules_total=1,
                rules_matched=1,
                declared_unreachable=0,
            )
            saved_out = claims.OUT
            try:
                claims.OUT = pathlib.Path(tmp)
                entry = claims.audit(claim)
            finally:
                claims.OUT = saved_out
        self.assertFalse(entry["ok"])
        self.assertTrue(
            any("different rules content" in p for p in entry["problems"])
        )

    def test_claim_boundary_is_explicit(self):
        self.assertIn("getAction", claims.BOUNDARY)
        self.assertIn("LegalizerHelper", claims.BOUNDARY)


if __name__ == "__main__":
    unittest.main()