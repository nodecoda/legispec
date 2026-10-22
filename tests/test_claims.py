# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0.
# See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0
#
# ===----------------------------------------------------------------------===##

"""The claim ledger must stay honest.

These tests do not re-run conformance.  They check that every claim in the
ledger still points at a report produced from the current rules file, with
zero divergences, and that a changed rules file invalidates the claim.
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legispec import claims


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
            oracle = claims.OUT / "oracle" / "legispec-oracle.exe"
            report.write_text(
                json.dumps(
                    {
                        "schema": "legispec-conformance-v1",
                        "evaluator_sha256": claims.evaluator_sha256(),
                        "inputs": {
                            "rules": str(claims.RULES / "toycpu.lspec"),
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
                rules="toycpu.lspec",
                report=str(report),
                oracle="oracle/legispec-oracle.exe",
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
            oracle = claims.OUT / "oracle" / "legispec-oracle.exe"
            rules = claims.RULES / "toycpu.lspec"
            report = pathlib.Path(tmp) / "report.json"
            report.write_text(
                json.dumps(
                    {
                        "schema": "legispec-conformance-v1",
                        "evaluator_sha256": claims.evaluator_sha256(),
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
                rules="toycpu.lspec",
                report=str(report),
                oracle="oracle/legispec-oracle.exe",
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


def _make_claim(**overrides):
    values = {
        "id": "test-claim",
        "rules": "test.lspec",
        "report": "report.json",
        "oracle": "oracle/test-oracle.exe",
        "scope": "test",
        "config": {},
        "queries": 1,
        "rules_total": 1,
        "rules_matched": 1,
    }
    values.update(overrides)
    return claims.Claim(**values)


class MissingEvidenceTests(unittest.TestCase):
    def test_missing_evidence_is_a_complete_failure(self):
        claim = _make_claim()
        saved = (claims.RULES, claims.OUT, claims.CLAIMS, claims.LEDGER)
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            rules_dir = root / "rules"
            out_dir = root / "out"
            rules_dir.mkdir()
            out_dir.mkdir()
            claims.RULES = rules_dir
            claims.OUT = out_dir
            claims.CLAIMS = (claim,)
            claims.LEDGER = root / "claims" / "claims.json"
            try:
                for stage in ("rules", "report", "oracle"):
                    if stage == "report":
                        (rules_dir / claim.rules).write_text(
                            "ruleset G_X { legal; }\n", encoding="utf-8"
                        )
                    elif stage == "oracle":
                        (out_dir / claim.report).write_text("{}", encoding="utf-8")
                    with self.subTest(missing=stage):
                        entry = claims.audit(claim)
                        self.assertIs(entry.get("ok"), False, entry)
                        self.assertTrue(entry.get("problems"), entry)
                        self.assertIn("id", entry)
                        self.assertIn("problems", entry)

                # main() must survive an early audit return instead of raising
                # KeyError while printing the summary.
                (out_dir / claim.report).unlink()
                with contextlib.redirect_stdout(io.StringIO()):
                    try:
                        rc = claims.main(["verify"])
                    except KeyError as exc:
                        self.fail(f"claims.main raised KeyError: {exc}")
                self.assertEqual(rc, 1)
            finally:
                claims.RULES, claims.OUT, claims.CLAIMS, claims.LEDGER = saved


class MalformedReportTests(unittest.TestCase):
    def test_malformed_json_report_is_a_failure_not_a_crash(self):
        claim = _make_claim()
        saved = (claims.RULES, claims.OUT, claims.CLAIMS, claims.LEDGER)
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            rules_dir = root / "rules"
            out_dir = root / "out"
            rules_dir.mkdir()
            out_dir.mkdir()
            (rules_dir / claim.rules).write_text(
                "ruleset G_X { legal; }\n", encoding="utf-8"
            )
            (out_dir / claim.report).write_text("{not json", encoding="utf-8")
            claims.RULES = rules_dir
            claims.OUT = out_dir
            claims.CLAIMS = (claim,)
            claims.LEDGER = root / "claims.json"
            try:
                entry = claims.audit(claim)
                self.assertIs(entry.get("ok"), False, entry)
                self.assertTrue(
                    any("not readable JSON" in p for p in entry["problems"]), entry
                )
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(claims.main(["verify"]), 1)
            finally:
                claims.RULES, claims.OUT, claims.CLAIMS, claims.LEDGER = saved


class EvaluatorStalenessTests(unittest.TestCase):
    def test_evaluator_hash_mismatch_invalidates_every_entry(self):
        entries = [
            {"id": "a", "ok": True, "problems": [], "llvm_commit": "abc"},
            {"id": "b", "ok": True, "problems": [], "llvm_commit": "abc"},
        ]
        previous = {
            "evaluator_sha256": "0" * 64,
            "llvm_commit": "abc",
            "claims": [{"id": "a"}, {"id": "b"}],
        }
        claims.check_staleness(entries, previous)
        self.assertTrue(all(not e["ok"] for e in entries))
        self.assertTrue(
            all(any("STALE: evaluator" in p for p in e["problems"]) for e in entries)
        )

    def test_report_and_corpus_pin_mismatch_invalidates_entry(self):
        entries = [
            {
                "id": "a",
                "ok": True,
                "problems": [],
                "report_sha256": "new-report",
                "corpus_sha256": "new-corpus",
            }
        ]
        previous = {
            "claims": [
                {
                    "id": "a",
                    "report_sha256": "old-report",
                    "corpus_sha256": "old-corpus",
                }
            ]
        }
        claims.check_staleness(entries, previous)
        self.assertFalse(entries[0]["ok"])
        messages = " ".join(entries[0]["problems"])
        self.assertIn("STALE: report changed", messages)
        self.assertIn("STALE: query corpus changed", messages)

    def test_missing_previous_pins_are_a_migration_path(self):
        entries = [
            {
                "id": "a",
                "ok": True,
                "problems": [],
                "report_sha256": "r",
                "corpus_sha256": "c",
            }
        ]
        claims.check_staleness(
            entries, {"claims": [{"id": "a"}]}, allow_missing_pins=True
        )
        self.assertTrue(entries[0]["ok"])
        self.assertEqual(entries[0]["problems"], [])


class ProvenancePathTests(unittest.TestCase):
    def test_rules_tail_accepts_old_monorepo_path_but_not_other_checkout(self):
        claim = _make_claim(id="toycpu", rules="toycpu.lspec")
        saved = (claims.RULES, claims.OUT)
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            rules_dir = root / "rules"
            out_dir = root / "out"
            rules_dir.mkdir()
            out_dir.mkdir()
            rules_path = rules_dir / claim.rules
            rules_path.write_text(
                "ruleset G_X { legal; }\n", encoding="utf-8"
            )
            oracle_path = out_dir / claim.oracle
            oracle_path.parent.mkdir(parents=True)
            oracle_path.write_bytes(b"oracle-bytes")
            payload = {
                "schema": "legispec-conformance-v1",
                "evaluator_sha256": claims.evaluator_sha256(),
                "inputs": {
                    "rules": (
                        "F:\\llvm-study\\toycpu\\tools\\"
                        "legispec\\rules\\toycpu.lspec"
                    ),
                    "rules_sha256": claims._sha256(rules_path),
                    "sha256": "a" * 64,
                    "config": {},
                },
                "reference": {
                    "oracle": str(oracle_path),
                    "oracle_sha256": claims._sha256(oracle_path),
                    "llvm_build": claim.llvm_build,
                    "llvm_commit": "abc123",
                },
                "comparison": {
                    "divergence_count": 0,
                    "oracle_errors": [],
                    "queries_compared": claim.queries,
                },
                "domain_closure_ok": True,
                "domain": {"closure_outside_d_count": 0},
                "coverage": {
                    "rules": claim.rules_total,
                    "rules_matched": claim.rules_matched,
                    "rules_unmatched": [],
                },
            }
            claims.RULES = rules_dir
            claims.OUT = out_dir
            report_path = out_dir / claim.report
            try:
                # A BOM must not break report parsing.
                report_path.write_bytes(
                    b"\xef\xbb\xbf" + json.dumps(payload).encode("utf-8")
                )
                entry = claims.audit(claim)
                self.assertTrue(entry["ok"], entry["problems"])

                payload["inputs"]["rules"] = "other/toycpu.lspec"
                report_path.write_bytes(
                    b"\xef\xbb\xbf" + json.dumps(payload).encode("utf-8")
                )
                entry = claims.audit(claim)
                self.assertFalse(entry["ok"])
                self.assertTrue(
                    any("not 'toycpu.lspec'" in p for p in entry["problems"]), entry
                )
            finally:
                claims.RULES, claims.OUT = saved


class WitnessPinTests(unittest.TestCase):
    def test_aarch64_vector_uses_unreachable_witness_override(self):
        claim = next(c for c in claims.CLAIMS if c.id == "aarch64-vector")
        entry = claims.audit(claim)
        self.assertEqual(entry["witness"], "aarch64-vector-unreachable.witness")
        self.assertFalse(
            any("witness missing" in p for p in entry["problems"]), entry["problems"]
        )
        self.assertEqual(len(entry.get("witness_sha256", "")), 64)

    def test_derived_missing_witness_is_allowed(self):
        claim = _make_claim(id="derived-missing", rules="derived-missing.lspec")
        entry = claims.audit(claim)
        self.assertIsNone(entry["witness"])
        self.assertFalse(
            any("witness missing" in p for p in entry["problems"]), entry["problems"]
        )

    def test_explicit_missing_witness_is_a_failure(self):
        claim = _make_claim(witness="missing-witness-for-test.witness")
        entry = claims.audit(claim)
        self.assertIsNone(entry["witness"])
        self.assertTrue(
            any("witness missing" in p for p in entry["problems"]), entry["problems"]
        )
        self.assertFalse(entry["ok"])

    def test_witness_pin_mismatch_invalidates_entry(self):
        entries = [
            {
                "id": "a",
                "ok": True,
                "problems": [],
                "witness_sha256": "new-witness",
            }
        ]
        previous = {"claims": [{"id": "a", "witness_sha256": "old-witness"}]}
        claims.check_staleness(entries, previous)
        self.assertFalse(entries[0]["ok"])
        self.assertIn("STALE: witness changed", " ".join(entries[0]["problems"]))



def _valid_report(
    claim,
    rules_path,
    oracle_path,
    *,
    evaluator=None,
    codegen=None,
    corpus_sha="a" * 64,
    comparison=None,
    domain=None,
    coverage=None,
    llvm_build=None,
    oracle_sha=None,
):
    return {
        "schema": "legispec-conformance-v1",
        "evaluator_sha256": (
            claims.hand_evaluator_sha256() if evaluator is None else evaluator
        ),
        **(
            {"codegen_sha256": claims.codegen_sha256()}
            if codegen is None and claim.id.endswith("-generated")
            else ({"codegen_sha256": codegen} if codegen is not None else {})
        ),
        "inputs": {
            "rules": str(rules_path),
            "rules_sha256": claims._sha256(rules_path),
            "sha256": corpus_sha,
            "config": dict(claim.config),
        },
        "reference": {
            "oracle": str(oracle_path),
            "oracle_sha256": (
                claims._sha256(oracle_path) if oracle_sha is None else oracle_sha
            ),
            "llvm_build": llvm_build or claim.llvm_build,
            "llvm_commit": "abc123",
        },
        "comparison": (
            {
                "divergence_count": 0,
                "oracle_errors": [],
                "queries_compared": claim.queries,
            }
            if comparison is None
            else comparison
        ),
        "domain": (
            {"closure_outside_d_count": 0} if domain is None else domain
        ),
        "coverage": (
            {
                "rules": claim.rules_total,
                "rules_matched": claim.rules_matched,
                "rules_unmatched": [],
            }
            if coverage is None
            else coverage
        ),
        "domain_closure_ok": True,
    }


class _TempEvidence:
    def __init__(self, claim, *, rules_text="ruleset G_X { legal; }\n"):
        self.claim = claim
        self.rules_text = rules_text
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.rules_dir = self.root / "rules"
        self.out_dir = self.root / "out"
        self.rules_dir.mkdir()
        self.out_dir.mkdir()
        self.rules = self.rules_dir / claim.rules
        self.rules.write_text(self.rules_text, encoding="utf-8")
        self.oracle = self.out_dir / claim.oracle
        self.oracle.parent.mkdir(parents=True)
        self.oracle.write_bytes(b"oracle")
        self.report = self.out_dir / claim.report
        self.saved = (
            claims.RULES,
            claims.OUT,
            claims.CLAIMS,
            claims.LEDGER,
            claims.REPORT_MD,
        )
        claims.RULES = self.rules_dir
        claims.OUT = self.out_dir
        claims.CLAIMS = (claim,)
        claims.LEDGER = self.root / "claims.json"
        claims.REPORT_MD = self.root / "claim-ledger.md"

    def write_report(self, **overrides):
        payload = _valid_report(
            self.claim, self.rules, self.oracle, **overrides
        )
        self.report.write_text(
            json.dumps(payload), encoding="utf-8", newline="\n"
        )
        return payload

    def close(self):
        (
            claims.RULES,
            claims.OUT,
            claims.CLAIMS,
            claims.LEDGER,
            claims.REPORT_MD,
        ) = self.saved
        self.tmp.cleanup()


class EvidenceFingerprintTests(unittest.TestCase):
    def test_report_without_evaluator_fingerprint_fails(self):
        claim = _make_claim(id="fp-missing")
        evidence = _TempEvidence(claim)
        try:
            payload = evidence.write_report()
            payload.pop("evaluator_sha256")
            evidence.report.write_text(json.dumps(payload), encoding="utf-8")
            entry = claims.audit(claim)
        finally:
            evidence.close()
        self.assertFalse(entry["ok"])
        self.assertTrue(
            any("no evaluator fingerprint" in p for p in entry["problems"]), entry
        )

    def test_generated_report_requires_codegen_fingerprint(self):
        claim = _make_claim(id="fp-generated", rules="fp.lspec")
        evidence = _TempEvidence(claim)
        try:
            payload = evidence.write_report()
            payload.pop("codegen_sha256")
            evidence.report.write_text(json.dumps(payload), encoding="utf-8")
            entry = claims.audit(claim)
        finally:
            evidence.close()
        self.assertFalse(entry["ok"])
        self.assertTrue(
            any("no codegen fingerprint" in p for p in entry["problems"]), entry
        )

    def test_write_refuses_an_evaluator_change_without_regenerated_reports(self):
        claim = _make_claim(id="fp-write")
        evidence = _TempEvidence(claim)
        try:
            evidence.write_report(evaluator="0" * 64)
            previous = {
                "evaluator_sha256": "0" * 64,
                "llvm_commit": "abc123",
                "claims": [
                    {
                        "id": claim.id,
                        "evaluator_sha256": "0" * 64,
                        "rules_sha256": claims._sha256(evidence.rules),
                    }
                ],
            }
            claims.LEDGER.write_text(
                json.dumps(previous), encoding="utf-8", newline="\n"
            )
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                rc = claims.main(["verify", "--write"])
            self.assertEqual(rc, 1)
            self.assertEqual(
                json.loads(claims.LEDGER.read_text(encoding="utf-8")),
                previous,
            )

            evidence.write_report()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                rc = claims.main(["verify"])
                self.assertEqual(rc, 1)
                rc = claims.main(
                    [
                        "verify",
                        "--write",
                        "--allow-evaluator-change",
                        "--allow-report-change",
                        "--allow-missing-pins",
                    ]
                )
            self.assertEqual(rc, 0)
            written = json.loads(claims.LEDGER.read_text(encoding="utf-8"))
            self.assertEqual(
                written["evaluator_sha256"], claims.hand_evaluator_sha256()
            )
        finally:
            evidence.close()

    def test_missing_top_level_evaluator_pin_fails_closed(self):
        entries = [
            {
                "id": "a",
                "ok": True,
                "problems": [],
                "evaluator_sha256": claims.hand_evaluator_sha256(),
            }
        ]
        previous = {
            "claims": [
                {
                    "id": "a",
                    "evaluator_sha256": claims.hand_evaluator_sha256(),
                }
            ]
        }
        claims.check_staleness(entries, previous)
        self.assertFalse(entries[0]["ok"])
        self.assertIn("no evaluator pin", " ".join(entries[0]["problems"]))

    def test_removed_claims_and_disappeared_pins_are_stale(self):
        entries = [
            {
                "id": "a",
                "ok": True,
                "problems": [],
                "rules_sha256": "r",
                "report_sha256": "rp",
                "report_file_sha256": "rf",
                "corpus_sha256": "c",
                "oracle_sha256": "o",
                "evaluator_sha256": claims.hand_evaluator_sha256(),
            }
        ]
        previous = {
            "evaluator_sha256": claims.hand_evaluator_sha256(),
            "claims": [
                {
                    "id": "a",
                    "rules_sha256": "r",
                    "report_sha256": "rp",
                    "report_file_sha256": "rf",
                    "corpus_sha256": "c",
                    "oracle_sha256": "o",
                    "witness_sha256": "w",
                    "evaluator_sha256": claims.hand_evaluator_sha256(),
                },
                {"id": "b"},
            ],
        }
        claims.check_staleness(entries, previous, allow_missing_pins=True)
        text = " ".join(entries[0]["problems"])
        self.assertIn("witness pin disappeared", text)
        self.assertIn("claims removed", text)


class MalformedSectionTests(unittest.TestCase):
    def test_shape_confused_sections_fail_instead_of_crashing(self):
        claim = _make_claim(id="shape")
        evidence = _TempEvidence(claim)
        try:
            evidence.write_report(comparison=[], domain=None, coverage=[])
            entry = claims.audit(claim)
        finally:
            evidence.close()
        self.assertFalse(entry["ok"])
        text = " ".join(entry["problems"])
        self.assertIn("comparison is list", text)
        self.assertIn("coverage is list", text)

    def test_directory_oracle_fails_instead_of_crashing(self):
        claim = _make_claim(id="dir-oracle")
        evidence = _TempEvidence(claim)
        try:
            evidence.oracle.unlink()
            evidence.oracle.mkdir()
            evidence.write_report(oracle_sha="0" * 64)
            entry = claims.audit(claim)
        finally:
            evidence.close()
        self.assertFalse(entry["ok"])
        self.assertTrue(
            any("oracle is not a file" in p for p in entry["problems"]), entry
        )

    def test_corrupt_ledger_is_a_clean_error(self):
        claim = _make_claim(id="corrupt")
        evidence = _TempEvidence(claim)
        try:
            evidence.write_report()
            claims.LEDGER.write_text("{not json", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                rc = claims.main(["verify"])
        finally:
            evidence.close()
        self.assertEqual(rc, 2)


class CorpusVerificationTests(unittest.TestCase):
    def test_verify_corpus_rejects_a_fabricated_hash(self):
        rules_text = (
            "domain { types { s32 }; q0 { (G_X, [s32]); } }\n"
            "ruleset G_X { legal; }\n"
        )
        claim = _make_claim(id="corpus", rules="corpus.lspec")
        evidence = _TempEvidence(claim, rules_text=rules_text)
        try:
            evidence.write_report(corpus_sha="0" * 64)
            entry = claims.audit_all((claim,), verify_corpus=True)[0]
        finally:
            evidence.close()
        self.assertFalse(entry["ok"])
        self.assertTrue(
            any("fresh enumeration" in p for p in entry["problems"]), entry
        )


class CanonicalReportHashTests(unittest.TestCase):
    def test_hand_report_hash_ignores_codegen_fingerprint(self):
        first = {"codegen_sha256": "a", "comparison": {"divergence_count": 0}}
        second = {"codegen_sha256": "b", "comparison": {"divergence_count": 0}}
        self.assertEqual(
            claims.canonical_report_sha256(first, include_codegen=False),
            claims.canonical_report_sha256(second, include_codegen=False),
        )
        self.assertNotEqual(
            claims.canonical_report_sha256(first),
            claims.canonical_report_sha256(second),
        )


class LlvmBuildOverrideTests(unittest.TestCase):
    def test_override_is_audited_and_written(self):
        claim = _make_claim(id="build", llvm_build="F:/old/build")
        evidence = _TempEvidence(claim)
        try:
            evidence.write_report(llvm_build="F:/new/build")
            with contextlib.redirect_stdout(io.StringIO()):
                rc = claims.main(
                    ["verify", "--write", "--llvm-build", "F:/new/build"]
                )
            self.assertEqual(rc, 0)
            written = json.loads(claims.LEDGER.read_text(encoding="utf-8"))
            self.assertEqual(written["claims"][0]["llvm_build"], "F:/new/build")
        finally:
            evidence.close()


if __name__ == "__main__":
    unittest.main()
