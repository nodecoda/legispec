# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Tests for the evidence tooling in scripts/.

These stay fast: they exercise path/provenance helpers only and never launch
the conformance oracle binaries.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

HERE = pathlib.Path(__file__).resolve().parents[1]


def _load_script(name: str, relative: str):
    path = HERE / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


collect_evidence = _load_script("collect_evidence", "scripts/collect_evidence.py")
reproduce_claims = _load_script("reproduce_claims", "scripts/reproduce_claims.py")
check_all_rules = _load_script("check_all_rules", "scripts/check_all_rules.py")
fetch_llvm_header = _load_script(
    "fetch_llvm_header", "scripts/fetch_llvm_header.py"
)


class SafeJoinTests(unittest.TestCase):
    def test_accepts_a_normal_relative_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            joined = collect_evidence.safe_join(base, "oracle/legispec-oracle.exe")
        self.assertEqual(joined, (base / "oracle" / "legispec-oracle.exe").resolve())

    def test_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            for rel in ("../x", "a/../../x", "..\\x"):
                with self.subTest(rel=rel):
                    with self.assertRaises(ValueError) as caught:
                        collect_evidence.safe_join(base, rel)
                    self.assertIn("..", str(caught.exception))

    def test_rejects_absolute_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            absolute = str((base / "report.json").resolve())
            with self.assertRaises(ValueError) as caught:
                collect_evidence.safe_join(base, absolute)
        self.assertIn("unsafe absolute path", str(caught.exception))

    def test_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            base = pathlib.Path(tmp)
            secret = pathlib.Path(outside) / "secret.txt"
            secret.write_text("outside", encoding="utf-8")
            link = base / "escape.txt"
            try:
                link.symlink_to(secret)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlink creation not permitted: {exc}")
            with self.assertRaises(ValueError):
                collect_evidence.safe_join(base, "escape.txt")


class ReproduceClaimsTests(unittest.TestCase):
    def test_resolve_build_prefers_claim_unless_overridden(self):
        claim = {"id": "x", "llvm_build": "F:/llvm/build-toycpu"}
        self.assertEqual(
            reproduce_claims._resolve_build(claim, None), "F:/llvm/build-toycpu"
        )
        self.assertEqual(
            reproduce_claims._resolve_build(claim, "F:/llvm/build"), "F:/llvm/build"
        )

    def test_limit_overwrite_guard(self):
        out = reproduce_claims.REPO / "out"
        smoke = reproduce_claims.REPO / "out-smoke"
        self.assertIsNone(reproduce_claims.limit_overwrite_error(out, 0, False))
        self.assertIsNone(reproduce_claims.limit_overwrite_error(smoke, 5, False))
        self.assertIsNone(reproduce_claims.limit_overwrite_error(out, 5, True))
        message = reproduce_claims.limit_overwrite_error(out, 5, False)
        self.assertIsNotNone(message)
        self.assertIn("out-smoke", message)


class FetchLlvmHeaderCacheTests(unittest.TestCase):
    def _cached_header(self, root: pathlib.Path, commit: str, data: bytes) -> pathlib.Path:
        dest = root / fetch_llvm_header.REL
        dest.parent.mkdir(parents=True)
        dest.write_bytes(data)
        pathlib.Path(f"{dest}.commit").write_text(commit + "\n", encoding="utf-8")
        pathlib.Path(f"{dest}.sha256").write_text(
            hashlib.sha256(data).hexdigest() + "\n", encoding="utf-8"
        )
        return dest

    def test_cache_requires_file_commit_and_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = pathlib.Path(tmp) / "LegalizerInfo.h"
            data = b"header"
            self.assertFalse(fetch_llvm_header._cache_valid(dest, "abc"))

            dest.write_bytes(data)
            self.assertFalse(fetch_llvm_header._cache_valid(dest, "abc"))

            pathlib.Path(f"{dest}.commit").write_text("abc\n", encoding="utf-8")
            self.assertFalse(fetch_llvm_header._cache_valid(dest, "abc"))

            pathlib.Path(f"{dest}.sha256").write_text(
                hashlib.sha256(data).hexdigest() + "\n", encoding="utf-8"
            )
            self.assertTrue(fetch_llvm_header._cache_valid(dest, "abc"))

    def test_cache_rejects_commit_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = pathlib.Path(tmp) / "LegalizerInfo.h"
            data = b"header"
            dest.write_bytes(data)
            pathlib.Path(f"{dest}.commit").write_text("old\n", encoding="utf-8")
            pathlib.Path(f"{dest}.sha256").write_text(
                hashlib.sha256(data).hexdigest() + "\n", encoding="utf-8"
            )
            self.assertFalse(fetch_llvm_header._cache_valid(dest, "new"))

    def test_cache_rejects_sha_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = pathlib.Path(tmp) / "LegalizerInfo.h"
            dest.write_bytes(b"header")
            pathlib.Path(f"{dest}.commit").write_text("abc\n", encoding="utf-8")
            pathlib.Path(f"{dest}.sha256").write_text("0" * 64 + "\n", encoding="utf-8")
            self.assertFalse(fetch_llvm_header._cache_valid(dest, "abc"))

    def test_main_reuses_cache_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._cached_header(root, "abc", b"header")
            self.assertEqual(
                fetch_llvm_header.main(["--dest", tmp, "--commit", "abc"]), 0
            )


class CheckAllRulesTests(unittest.TestCase):
    def _command(self, name: str) -> list[str]:
        rules = {rule.name: rule for rule in check_all_rules.discover_rules()}
        self.assertIn(name, rules)
        return check_all_rules.build_command(rules[name])

    def _pair(self, command: list[str], flag: str) -> str:
        self.assertIn(flag, command)
        return command[command.index(flag) + 1]

    def test_discovers_sorted_rule_files(self):
        rules = check_all_rules.discover_rules()
        names = [rule.name for rule in rules]
        self.assertGreaterEqual(len(rules), 22)
        self.assertEqual(names, sorted(names))
        self.assertTrue(all(name.endswith(".lspec") for name in names))

    def test_riscv_conditional_gconstant_override(self):
        name = "riscv-conditional-gconstant.lspec"
        command = self._command(name)
        self.assertEqual(self._pair(command, "--rules"), str((HERE / "rules" / name).resolve()))
        for define in ("is64bit=true", "sXLen=s64", "sDoubleXLen=s128"):
            self.assertIn(define, command)
        self.assertEqual(
            self._pair(command, "--capabilities"),
            "capabilities/riscv-conditional-gconstant-rv64-capabilities.json",
        )
        self.assertIn("--strict", command)

    def test_riscv_scalar_conditional_override(self):
        name = "riscv-scalar-conditional.lspec"
        command = self._command(name)
        self.assertEqual(self._pair(command, "--rules"), str((HERE / "rules" / name).resolve()))
        for define in ("is64bit=true", "sXLen=s64", "sDoubleXLen=s128"):
            self.assertIn(define, command)
        self.assertEqual(
            self._pair(command, "--capabilities"),
            "capabilities/riscv64-scalar-capabilities.json",
        )
        self.assertIn("--strict", command)

    def test_aarch64_vector_override(self):
        name = "aarch64-vector.lspec"
        command = self._command(name)
        self.assertEqual(self._pair(command, "--rules"), str((HERE / "rules" / name).resolve()))
        self.assertEqual(
            self._pair(command, "--witness"),
            "witness/aarch64-vector-unreachable.witness",
        )
        self.assertIn("--strict", command)
        self.assertNotIn("--capabilities", command)

    def test_only_toycpu_json_smoke(self):
        start = time.monotonic()
        proc = subprocess.run(
            [
                sys.executable,
                str(HERE / "scripts" / "check_all_rules.py"),
                "--only",
                "toycpu.lspec",
                "--json",
            ],
            cwd=HERE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        elapsed = time.monotonic() - start
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report = json.loads(proc.stdout)
        self.assertTrue(report["ok"])
        self.assertEqual(report["count"], 1)
        self.assertEqual(report["results"][0]["file"], "toycpu.lspec")
        self.assertIn("errors=0", report["results"][0]["summary"])
        self.assertLess(elapsed, 15.0, f"toycpu smoke took {elapsed:.2f}s")



class ReproduceOracleDirectoryTests(unittest.TestCase):
    def test_oracle_directory_is_independent_of_report_output(self):
        claim = {
            "id": "x",
            "rules": "toycpu.lspec",
            "oracle": "oracle/legispec-oracle.exe",
            "report": "report.json",
            "config": {},
        }
        captured = []
        saved = reproduce_claims.subprocess.call
        reproduce_claims.subprocess.call = (
            lambda command, cwd=None: captured.append(command) or 0
        )
        try:
            rc = reproduce_claims.run_claim(
                claim,
                pathlib.Path("reports"),
                pathlib.Path("oracles"),
                "F:/llvm/build",
                0,
            )
        finally:
            reproduce_claims.subprocess.call = saved
        self.assertEqual(rc, 0)
        self.assertIn("--oracle", captured[0])
        self.assertEqual(
            captured[0][captured[0].index("--oracle") + 1],
            str(pathlib.Path("oracles") / claim["oracle"]),
        )
        self.assertEqual(
            captured[0][captured[0].index("--out") + 1],
            str(pathlib.Path("reports") / claim["report"]),
        )


class CollectEvidenceFailClosedTests(unittest.TestCase):
    def test_unpinned_evidence_is_refused_unless_explicitly_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            (source / "report.json").write_text("{}", encoding="utf-8")
            ledger = root / "claims.json"
            ledger.write_text(
                json.dumps(
                    {
                        "claims": [
                            {
                                "id": "x",
                                "report": "report.json",
                                "oracle": "",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            saved = collect_evidence.LEDGER
            collect_evidence.LEDGER = ledger
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = collect_evidence.main(
                        ["--from", str(source), "--to", str(target)]
                    )
                    self.assertEqual(rc, 1)
                    self.assertFalse((target / "report.json").exists())
                    rc = collect_evidence.main(
                        [
                            "--from",
                            str(source),
                            "--to",
                            str(target),
                            "--allow-unpinned",
                        ]
                    )
                self.assertEqual(rc, 0)
                self.assertTrue((target / "report.json").is_file())
            finally:
                collect_evidence.LEDGER = saved


if __name__ == "__main__":
    unittest.main()
