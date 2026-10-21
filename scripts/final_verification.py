#!/usr/bin/env python3
# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Produce the final verification artifact for the P0-P7 roadmap.

The artifact is deliberately fail-closed.  It re-runs every locally runnable
gate, validates the two integration reports through
``check_integration_status.validate_report`` and records the exact dirty
worktree it observed, so a PASS cannot be detached from the tree that produced
it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT, ROOT / "src", ROOT / "scripts"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from check_integration_status import validate_report  # noqa: E402
from check_skips import (  # noqa: E402
    allowed_for_platform,
    compare_skips,
    load_manifest,
    optional_for_platform,
)
from legalizer_dsl import __version__ as GENERATOR_VERSION  # noqa: E402
from legalizer_dsl.codegen import DSL_SPEC_VERSION  # noqa: E402
from legalizer_dsl.fingerprint import codegen_sha256, hand_evaluator_sha256  # noqa: E402

SCHEMA = "legalizer-dsl-final-verification-v1"
BASE_COMMIT = "9578630"
CLAIMS_PATH = ROOT / "claims" / "claims.json"
CLOSURE_PATH = ROOT / "out" / "closure.json"
COVERAGE_PATH = ROOT / "out" / "coverage-report.json"
INTEGRATION_PATHS = (
    ROOT / "out" / "integration-report.json",
    ROOT / "out" / "integration-aarch64-report.json",
)
EXPECTED_TARGETS = {"toycpu", "aarch64-vector"}
EXCLUDED_UNTRACKED = (".codex/", ".omx/", "out/", "AGENTS.md")
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
WORKTREE_HASH_DEFINITION = (
    "sha256 of canonical JSON {head_commit, tracked_changes, "
    "tracked_diff_sha256, untracked_project_files}; excluded paths are "
    ".codex/, .omx/, out/, AGENTS.md and Python cache directories"
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} root is {type(data).__name__}, expected object")
    return data


def git(*args: str, binary: bool = False):
    proc = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, check=False
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    if binary:
        return proc.stdout
    return proc.stdout.decode("utf-8", "replace").rstrip("\r\n")


def run_gate(name: str, args: list[str]) -> tuple[dict, str]:
    proc = subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        check=False,
    )
    text = "\n".join(part for part in (proc.stdout, proc.stderr) if part)
    lines = [line for line in text.splitlines() if line.strip()]
    gate = {
        "name": name,
        "command": "python " + " ".join(args),
        "exit_code": proc.returncode,
        "output_sha256": sha256_bytes(text.encode("utf-8")),
        "output_tail": lines[-20:],
    }
    return gate, text


def parse_ints(pattern: str, text: str) -> Optional[tuple[int, ...]]:
    """Return the last matching summary line.

    Gate output can contain the text of nested runs (for example a test that
    invokes this script).  The final match belongs to the run that produced the
    gate, so scanning forward would accept an earlier, unrelated count.
    """

    matches = list(re.finditer(pattern, text, re.M))
    if not matches:
        return None
    return tuple(int(value) for value in matches[-1].groups())


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def is_excluded_untracked(relative: str) -> bool:
    if relative == "AGENTS.md":
        return True
    if any(relative.startswith(prefix) for prefix in EXCLUDED_UNTRACKED):
        return True
    return any(part in EXCLUDED_PARTS for part in Path(relative).parts)


def collect_repository(base_commit: str) -> tuple[dict, list[str]]:
    problems: list[str] = []
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    head = git("rev-parse", "HEAD")
    try:
        base = git("rev-parse", "--verify", f"{base_commit}^{{commit}}")
    except RuntimeError as exc:
        base = base_commit
        problems.append(str(exc))

    status_lines = [
        line for line in git("status", "--porcelain").splitlines() if line.strip()
    ]
    diff_bytes = git("diff", "--binary", "HEAD", binary=True)
    tracked_changes: list[dict] = []
    for line in git("diff", "--name-status", "HEAD").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        relative = parts[-1]
        path = ROOT / relative
        tracked_changes.append(
            {
                "status": parts[0],
                "path": relative,
                "sha256": sha256_file(path) if path.is_file() else None,
            }
        )
    tracked_changes.sort(key=lambda item: item["path"])

    all_untracked = [
        line.replace("\\", "/")
        for line in git("ls-files", "--others", "--exclude-standard").splitlines()
        if line.strip()
    ]
    untracked: dict[str, str] = {}
    excluded_untracked: list[str] = []
    for relative in sorted(all_untracked):
        if is_excluded_untracked(relative):
            excluded_untracked.append(relative)
            continue
        path = ROOT / relative
        untracked[relative] = sha256_file(path) if path.is_file() else ""

    payload = {
        "head_commit": head,
        "tracked_changes": tracked_changes,
        "tracked_diff_sha256": sha256_bytes(diff_bytes),
        "untracked_project_files": untracked,
    }
    worktree_sha256 = sha256_bytes(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )

    repository = {
        "branch": branch,
        "head_commit": head,
        "base_commit": base,
        "base_commit_ref": base_commit,
        "dirty": bool(tracked_changes) or bool(untracked),
        "git_status_dirty": bool(status_lines),
        "project_dirty": bool(tracked_changes) or bool(untracked),
        "dirty_definition": (
            "tracked changes or untracked project files; excluded harness "
            "paths (.codex/, AGENTS.md) are reported separately and do not "
            "count as project dirt"
        ),
        "status_porcelain": status_lines,
        "tracked_changes": tracked_changes,
        "tracked_diff_sha256": payload["tracked_diff_sha256"],
        "untracked_project_files": untracked,
        "excluded_untracked": excluded_untracked,
        "worktree_sha256": worktree_sha256,
        "worktree_sha256_definition": WORKTREE_HASH_DEFINITION,
    }
    return repository, problems


def collect_environment(claims: dict, reports: list[dict]) -> tuple[dict, list[str]]:
    problems: list[str] = []
    llvm_commits = set()
    if isinstance(claims.get("llvm_commit"), str):
        llvm_commits.add(claims["llvm_commit"])
    else:
        problems.append("claims/claims.json has no llvm_commit")
    for claim in claims.get("claims", []):
        if isinstance(claim, dict) and claim.get("llvm_commit"):
            llvm_commits.add(claim["llvm_commit"])
    if len(llvm_commits) != 1:
        problems.append(f"claims disagree on llvm_commit: {sorted(llvm_commits)}")
    llvm_commit = next(iter(llvm_commits)) if len(llvm_commits) == 1 else ""
    if llvm_commit and not re.fullmatch(r"[0-9a-f]{40}", llvm_commit):
        problems.append(f"llvm_commit is not a full sha: {llvm_commit!r}")

    evaluator = hand_evaluator_sha256()
    if claims.get("evaluator_sha256") != evaluator:
        problems.append("claims ledger evaluator_sha256 is stale")
    for claim in claims.get("claims", []):
        if not isinstance(claim, dict):
            continue
        cid = str(claim.get("id", "?"))
        if claim.get("evaluator_sha256") != evaluator:
            problems.append(f"{cid}: evaluator_sha256 is stale")
        # Hand-written claims record the codegen hash for provenance only; a
        # codegen edit must not invalidate hand evidence.  Only generated
        # claims assert that their oracle was built from this codegen.
        if cid.endswith("-generated"):
            pin = claim.get("codegen_sha256")
            if not isinstance(pin, str) or not pin:
                problems.append(f"{cid}: generated claim has no codegen_sha256")
            elif pin != codegen_sha256():
                problems.append(f"{cid}: codegen_sha256 is stale")

    llvm_builds = set()
    toolchains: dict[str, dict] = {}
    for report in reports:
        if not isinstance(report, dict):
            problems.append("integration report is not an object")
            continue
        target = str(report.get("target", "?"))
        env = _dict(report.get("environment"))
        if env.get("llvm_dir"):
            llvm_builds.add(env["llvm_dir"])
        if env.get("build_returncode") not in (0, None):
            problems.append(
                f"integration build failed for {target}: "
                f"build_returncode={env.get('build_returncode')}"
            )
        toolchains[target] = {
            key: env.get(key)
            for key in ("llvm_dir", "cxx", "linker", "cmake", "ninja")
        }

    environment = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "generator_version": GENERATOR_VERSION,
        "dsl_spec_version": DSL_SPEC_VERSION,
        "llvm_commit": llvm_commit,
        "llvm_builds": sorted(llvm_builds),
        "evaluator_sha256": evaluator,
        "codegen_sha256": codegen_sha256(),
        "integration_toolchains": toolchains,
    }
    if not llvm_builds:
        problems.append("integration reports do not record an LLVM build")
    return environment, problems

def parse_unittest_skips(text: str) -> int:
    """Read the skip count from unittest's own final summary line.

    The gate output can contain other ``skipped=`` text (for example this
    script's own summary printed by a nested test), so only the last
    ``OK``/``FAILED`` line is authoritative.
    """

    summaries = list(re.finditer(r"^(?:OK|FAILED) \((.*)\)$", text, re.M))
    if not summaries:
        return 0
    skipped = re.search(r"\bskipped=(\d+)", summaries[-1].group(1))
    return int(skipped.group(1)) if skipped else 0


def parse_unittest_skip_ids(text: str) -> dict[str, str]:
    """Read ``test (id) ... skipped 'reason'`` lines from verbose output."""

    skipped: dict[str, str] = {}
    pattern = re.compile(r"^.*? \(([^()]+)\) \.\.\. skipped '(.*)'$", re.M)
    for match in pattern.finditer(text):
        test_id = match.group(1)
        if test_id.startswith("tests."):
            test_id = test_id[len("tests.") :]
        skipped[test_id] = match.group(2)
    return skipped


def _skip_manifest_problems(skipped: dict[str, str]) -> list[str]:
    manifest = ROOT / "tests" / "expected-skips.json"
    try:
        entries = load_manifest(manifest)
        system = platform.system().lower()
        allowed = allowed_for_platform(entries, system)
        optional = optional_for_platform(entries, system)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"skip manifest is unusable: {exc}"]
    return compare_skips(skipped, allowed, optional)


def gate_unit_tests(expected_tests: Optional[int], expected_skipped: Optional[int]):
    gate, text = run_gate(
        "unit-tests",
        ["-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-v"],
    )
    problems: list[str] = []
    ran = parse_ints(r"Ran (\d+) tests?", text)
    tests = ran[0] if ran else None
    skip_count = parse_unittest_skips(text)
    skip_ids = parse_unittest_skip_ids(text)
    gate["tests"] = tests
    gate["skipped"] = skip_count
    gate["skipped_tests"] = sorted(skip_ids)
    if gate["exit_code"] != 0:
        problems.append(f"unit tests failed with exit {gate['exit_code']}")
    if tests is None:
        problems.append("unit test output has no 'Ran N tests' line")
    if expected_tests is not None and tests != expected_tests:
        problems.append(f"unit tests ran {tests}, expected {expected_tests}")
    if expected_skipped is not None and skip_count != expected_skipped:
        problems.append(f"unit tests skipped {skip_count}, expected {expected_skipped}")
    if len(skip_ids) != skip_count:
        problems.append(
            f"unit test output reported {skip_count} skip(s) but "
            f"{len(skip_ids)} skip id(s) were recoverable"
        )
    problems.extend(_skip_manifest_problems(skip_ids))
    return gate, problems


def gate_claims(claims: dict):
    gate, text = run_gate(
        "claims-verify-corpus",
        ["-X", "utf8", "-m", "legalizer_dsl.claims", "verify", "--verify-corpus"],
    )
    problems: list[str] = []
    parsed = parse_ints(r"claims=(\d+)\s+proven=(\d+)\s+failed=(\d+)", text)
    entries = claims.get("claims")
    if not isinstance(entries, list):
        problems.append("claims/claims.json has no claims list")
        entries = []
    expected = len(entries)
    if parsed is None:
        problems.append("claims output has no claims/proven/failed summary")
    else:
        total, proven, failed = parsed
        gate.update({"claims": total, "proven": proven, "failed": failed})
        if gate["exit_code"] != 0:
            problems.append(f"claims verification failed with exit {gate['exit_code']}")
        if (total, proven, failed) != (expected, expected, 0):
            problems.append(
                f"claims summary {total}/{proven}/{failed} != {expected}/{expected}/0"
            )

    for claim in entries:
        if not isinstance(claim, dict):
            problems.append("claims list contains a non-object entry")
            continue
        cid = str(claim.get("id", "?"))
        if claim.get("status") != "PROVEN":
            problems.append(f"{cid}: status is {claim.get('status')!r}, not PROVEN")
        if claim.get("ok") is not True:
            problems.append(f"{cid}: ok is not true")
        if claim.get("problems"):
            problems.append(f"{cid}: has problems")
        if claim.get("divergences") != 0:
            problems.append(f"{cid}: has divergences")
        rules_name = claim.get("rules")
        rules_hash = claim.get("rules_sha256")
        if not isinstance(rules_name, str) or not isinstance(rules_hash, str):
            problems.append(f"{cid}: rules/rules_sha256 missing")
            continue
        rules_path = ROOT / "rules" / rules_name
        if not rules_path.is_file():
            problems.append(f"{cid}: rules file missing: {rules_name}")
        elif sha256_file(rules_path) != rules_hash:
            problems.append(f"{cid}: rules_sha256 is stale for {rules_name}")

    gate["status"] = "PASS" if not problems else "FAIL"
    return gate, problems


def gate_closure(claims: dict, expected_tests: Optional[int]):
    gate, text = run_gate(
        "closure", ["-X", "utf8", "-m", "legalizer_dsl.closure", "check", "--write"]
    )
    problems: list[str] = []
    if gate["exit_code"] != 0:
        problems.append(f"closure check failed with exit {gate['exit_code']}")
    try:
        closure = load_json(CLOSURE_PATH)
    except (OSError, ValueError) as exc:
        problems.append(f"closure report unreadable: {exc}")
        gate["status"] = "FAIL"
        return gate, problems, None
    computed = closure.get("computed") if isinstance(closure.get("computed"), dict) else {}
    gate["computed"] = computed
    closure_problems = closure.get("problems")
    gate["problems_count"] = (
        len(closure_problems)
        if isinstance(closure_problems, list)
        else closure_problems
    )
    if closure.get("schema") != "legalizer-dsl-closure-v1":
        problems.append(f"closure schema is {closure.get('schema')!r}")
    if isinstance(closure_problems, list):
        if closure_problems:
            problems.append(
                f"closure reports {len(closure_problems)} problem(s): "
                + "; ".join(str(item) for item in closure_problems[:5])
            )
    elif closure_problems not in (0, None):
        problems.append(f"closure reports {closure_problems} problem(s)")
    expected_claims = len(claims.get("claims", []))
    if computed.get("claims") != expected_claims:
        problems.append(
            f"closure claims {computed.get('claims')} != {expected_claims}"
        )
    if expected_tests is not None and computed.get("tests") != expected_tests:
        problems.append(
            f"closure tests {computed.get('tests')} != {expected_tests}"
        )
    if not isinstance(computed.get("constructs"), int) or computed["constructs"] <= 0:
        problems.append("closure constructs is not positive")
    gate["status"] = "PASS" if not problems else "FAIL"
    return gate, problems, closure


def gate_coverage():
    gate, text = run_gate(
        "coverage-ratchet",
        [
            "-X",
            "utf8",
            "scripts/coverage_report.py",
            "--corpus",
            "examples",
            "--out",
            "out/coverage-report.json",
            "--no-docs",
        ],
    )
    problems: list[str] = []
    if gate["exit_code"] != 0:
        problems.append(f"coverage report failed with exit {gate['exit_code']}")
    try:
        report = load_json(COVERAGE_PATH)
    except (OSError, ValueError) as exc:
        problems.append(f"coverage report unreadable: {exc}")
        gate["status"] = "FAIL"
        return gate, problems
    ratchet = report.get("ratchet") if isinstance(report.get("ratchet"), dict) else {}
    if ratchet.get("status") != "PASS":
        problems.append(f"coverage ratchet is {ratchet.get('status')!r}")
    if ratchet.get("problems"):
        problems.append("coverage ratchet reports problems")
    targets = report.get("targets") if isinstance(report.get("targets"), dict) else {}
    summary: dict[str, dict] = {}
    for name, value in targets.items():
        corpus = value.get("corpus") if isinstance(value, dict) else {}
        coverage = value.get("coverage") if isinstance(value, dict) else {}
        queries = corpus.get("queries")
        in_domain = corpus.get("in_domain")
        boundary = corpus.get("boundary")
        summary[name] = {
            "queries": queries,
            "in_domain": in_domain,
            "boundary": boundary,
            "ratio": coverage.get("ratio"),
            "rules_matched": coverage.get("rules_matched"),
            "rules_total": coverage.get("rules_total"),
        }
        if not isinstance(queries, int) or queries <= 0:
            problems.append(f"coverage {name}: no queries")
            continue
        if not isinstance(in_domain, int) or in_domain <= 0:
            problems.append(f"coverage {name}: no in-domain queries")
        if not isinstance(boundary, int):
            problems.append(f"coverage {name}: boundary count missing")
        elif in_domain is not None and in_domain + boundary != queries:
            problems.append(f"coverage {name}: in_domain + boundary != queries")
        if corpus.get("parse_errors") != 0:
            problems.append(f"coverage {name}: parse errors")
        if coverage.get("observation_errors") != 0:
            problems.append(f"coverage {name}: observation errors")
    if set(summary) != EXPECTED_TARGETS:
        problems.append(f"coverage targets {sorted(summary)} != {sorted(EXPECTED_TARGETS)}")
    gate["targets"] = summary
    gate["ratchet"] = ratchet.get("status")
    gate["status"] = "PASS" if not problems else "FAIL"
    return gate, problems

def gate_codegen_contract():
    gate, text = run_gate(
        "codegen-contract", ["-X", "utf8", "scripts/check_codegen_contract.py"]
    )
    problems: list[str] = []
    parsed = parse_ints(
        r"codegen-contract:\s*PASS\s+rules=(\d+)\s+variants=(\d+)\s+failures=(\d+)",
        text,
    )
    expected_rules = len(sorted((ROOT / "rules").glob("*.ldsl")))
    if parsed is None:
        problems.append("codegen contract output has no PASS summary")
    else:
        rules, variants, failures = parsed
        gate.update({"rules": rules, "variants": variants, "failures": failures})
        if gate["exit_code"] != 0:
            problems.append(f"codegen contract failed with exit {gate['exit_code']}")
        if failures != 0:
            problems.append(f"codegen contract reports {failures} failure(s)")
        if rules != expected_rules:
            problems.append(f"codegen contract checked {rules}/{expected_rules} rules")
        if variants < rules:
            problems.append("codegen contract variants < rules")
    gate["status"] = "PASS" if not problems else "FAIL"
    return gate, problems


def gate_integration(expected: dict):
    status_gate, text = run_gate(
        "integration-status",
        [
            "-X",
            "utf8",
            "scripts/check_integration_status.py",
            "--require-pass",
            *[path.relative_to(ROOT).as_posix() for path in INTEGRATION_PATHS],
        ],
    )
    problems: list[str] = []
    report_gates: list[dict] = []
    reports: list[dict] = []
    targets: list[str] = []
    for path in INTEGRATION_PATHS:
        relative = path.relative_to(ROOT).as_posix()
        try:
            report = load_json(path)
        except (OSError, ValueError) as exc:
            problems.append(f"{relative}: unreadable integration report: {exc}")
            report_gates.append(
                {
                    "name": f"integration-{path.stem}",
                    "report": relative,
                    "status": "FAIL",
                    "exit_code": 1,
                }
            )
            continue
        if not isinstance(report, dict):
            problems.append(
                f"{relative}: report root is {type(report).__name__}, expected object"
            )
            report_gates.append(
                {
                    "name": f"integration-{path.stem}",
                    "report": relative,
                    "status": "FAIL",
                    "exit_code": 1,
                }
            )
            continue
        reports.append(report)
        target = str(report.get("target", "?"))
        targets.append(target)
        inputs = _dict(report.get("inputs"))
        provenance = _dict(report.get("provenance"))
        gate = {
            "name": f"integration-{target}",
            "report": relative,
            "report_sha256": sha256_file(path),
            "status": report.get("status"),
            "query_count": inputs.get("query_count"),
            "rules": inputs.get("rules"),
            "rules_sha256": inputs.get("rules_sha256"),
            "provenance": provenance,
            "comparisons": {},
            "exit_code": 0,
        }
        if report.get("status") != "PASS":
            problems.append(f"{relative}: status is {report.get('status')!r}")
        for message in validate_report(report, expected):
            problems.append(f"{relative}: {message}")
        comparisons = report.get("comparisons")
        if not isinstance(comparisons, dict) or not comparisons:
            problems.append(f"{relative}: comparisons missing")
            comparisons = {}
        queries = gate["query_count"]
        for name, value in comparisons.items():
            if not isinstance(value, dict):
                problems.append(f"{relative}: comparison {name} is not an object")
                continue
            item = {
                "queries": value.get("queries"),
                "divergences": value.get("divergence_count"),
                "errors": value.get("oracle_errors"),
                "ok": value.get("ok"),
            }
            gate["comparisons"][name] = item
            if value.get("ok") is not True:
                problems.append(f"{relative}: comparison {name} is not ok")
            if value.get("divergence_count") != 0:
                problems.append(f"{relative}: comparison {name} diverged")
            if value.get("oracle_errors") != 0:
                problems.append(f"{relative}: comparison {name} had oracle errors")
            if not isinstance(value.get("queries"), int):
                problems.append(f"{relative}: comparison {name} has no query count")
            elif isinstance(queries, int) and value.get("queries") != queries:
                problems.append(
                    f"{relative}: comparison {name} queries != {queries}"
                )
        if gate["query_count"] in (None, 0):
            problems.append(f"{relative}: query_count is not positive")
        if any(
            message.startswith(f"{relative}:")
            for message in problems
        ):
            gate["status"] = "FAIL"
            gate["exit_code"] = 1
        report_gates.append(gate)

    if set(targets) != EXPECTED_TARGETS or len(targets) != len(EXPECTED_TARGETS):
        problems.append(
            f"integration targets {sorted(targets)} != {sorted(EXPECTED_TARGETS)}"
        )
    if status_gate["exit_code"] != 0:
        problems.append("integration status gate failed")
    status_gate["reports"] = len(INTEGRATION_PATHS)
    status_gate["status"] = "PASS" if not problems else "FAIL"
    return status_gate, report_gates, reports, problems


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def boundaries(claims: dict) -> dict:
    return {
        "scope": claims.get("boundary", ""),
        "excluded": [
            "the iterative Legalizer pass and LegalizerHelper MIR rewrites",
            "instruction selection, register allocation and full ABI lowering",
            "SVE and any target or policy without a claim",
            "end-to-end code generation beyond LegalizerInfo::getAction",
        ],
        "pass_rule": (
            "Only in-domain queries with a decided action count as evidence. "
            "UNKNOWN, boundary, SKIP and out-of-domain queries never count as "
            "a pass."
        ),
    }

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tests", type=int, default=None, help="expected unittest test count"
    )
    parser.add_argument(
        "--skipped", type=int, default=None, help="expected unittest skip count"
    )
    parser.add_argument("--base-commit", default=BASE_COMMIT)
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help=(
            "run the gates on a dirty worktree for diagnostics; the artifact "
            "is DIAGNOSTIC, not PASS, and is not release-eligible"
        ),
    )
    parser.add_argument(
        "--run-claims",
        action="store_true",
        help="re-run every claim report before auditing it",
    )
    parser.add_argument(
        "--run-integration",
        action="store_true",
        help="rebuild and rerun both integration consumers before validating reports",
    )
    parser.add_argument("--out", default="out/final-verification.json")
    args = parser.parse_args(argv)

    problems: list[str] = []
    repository_start, repo_problems = collect_repository(args.base_commit)
    problems.extend(repo_problems)
    if repository_start["project_dirty"] and not args.allow_dirty:
        problems.append(
            "project worktree is dirty; commit the changes or pass --allow-dirty "
            "for a diagnostic run"
        )
    if args.tests is None:
        problems.append("--tests is required: the test-count pin must be explicit")
    if args.skipped is None:
        problems.append("--skipped is required: the skip-count pin must be explicit")

    try:
        claims = load_json(CLAIMS_PATH)
    except (OSError, ValueError) as exc:
        problems.append(f"claims ledger unreadable: {exc}")
        claims = {"claims": []}
    if claims.get("claim_contract_version") != 1:
        problems.append("claims ledger claim_contract_version is not 1")
    if not isinstance(claims.get("claims"), list):
        problems.append("claims ledger has no claims list")
        claims["claims"] = []

    gates: list[dict] = []

    if args.run_claims:
        reproduce_gate, _ = run_gate(
            "claims-reproduce", ["-X", "utf8", "scripts/reproduce_claims.py", "--all"]
        )
        reproduce_gate["status"] = (
            "PASS" if reproduce_gate["exit_code"] == 0 else "FAIL"
        )
        gates.append(reproduce_gate)
        if reproduce_gate["exit_code"] != 0:
            problems.append("claim report re-execution failed")

    unit_gate, unit_problems = gate_unit_tests(args.tests, args.skipped)
    unit_gate["status"] = "PASS" if not unit_problems else "FAIL"
    gates.append(unit_gate)
    problems.extend(unit_problems)

    claims_gate, claims_problems = gate_claims(claims)
    gates.append(claims_gate)
    problems.extend(claims_problems)

    closure_gate, closure_problems, _ = gate_closure(claims, args.tests)
    gates.append(closure_gate)
    problems.extend(closure_problems)

    coverage_gate, coverage_problems = gate_coverage()
    gates.append(coverage_gate)
    problems.extend(coverage_problems)

    codegen_gate, codegen_problems = gate_codegen_contract()
    gates.append(codegen_gate)
    problems.extend(codegen_problems)

    if args.run_integration:
        llvm_commit = claims.get("llvm_commit") or ""
        for target, limit, output in (
            ("toycpu", 0, "out/integration-report.json"),
            ("aarch64-vector", 2000, "out/integration-aarch64-report.json"),
        ):
            integration_gate, _ = run_gate(
                f"integration-rerun-{target}",
                [
                    "-X",
                    "utf8",
                    "scripts/integration_smoke.py",
                    "--target",
                    target,
                    "--limit",
                    str(limit),
                    "--out",
                    output,
                    "--llvm-commit",
                    llvm_commit,
                ],
            )
            integration_gate["status"] = (
                "PASS" if integration_gate["exit_code"] == 0 else "FAIL"
            )
            gates.append(integration_gate)
            if integration_gate["exit_code"] != 0:
                problems.append(f"integration re-execution failed for {target}")

    try:
        head_commit = git("rev-parse", "HEAD")
    except RuntimeError as exc:
        head_commit = ""
        problems.append(str(exc))
    expected = {
        "generator_version": GENERATOR_VERSION,
        "dsl_spec_version": DSL_SPEC_VERSION,
        "evaluator_sha256": hand_evaluator_sha256(),
        "codegen_sha256": codegen_sha256(),
        "llvm_commit": claims.get("llvm_commit"),
        "head_commit": head_commit,
    }
    status_gate, report_gates, reports, integration_problems = gate_integration(
        expected
    )
    gates.extend(report_gates)
    gates.append(status_gate)
    problems.extend(integration_problems)

    environment, environment_problems = collect_environment(claims, reports)
    problems.extend(environment_problems)

    repository_end, end_problems = collect_repository(args.base_commit)
    problems.extend(end_problems)
    if repository_end["project_dirty"] and not args.allow_dirty:
        problems.append("project worktree is dirty after the gates")
    if repository_start["worktree_sha256"] != repository_end["worktree_sha256"]:
        problems.append(
            "worktree changed while the gates ran; evidence was not produced "
            "from one immutable tree"
        )

    for gate in gates:
        if gate.get("status") != "PASS":
            problems.append(f"gate {gate.get('name')} is {gate.get('status')!r}")

    artifacts = {}
    for path in (
        CLAIMS_PATH,
        CLOSURE_PATH,
        COVERAGE_PATH,
        *INTEGRATION_PATHS,
    ):
        if path.is_file():
            artifacts[path.relative_to(ROOT).as_posix()] = sha256_file(path)

    if args.run_claims and args.run_integration:
        mode = "independent-execution"
    elif args.run_claims or args.run_integration:
        mode = "partial-execution"
    else:
        mode = "artifact-consistency"
    execution = {
        "claims": "rerun" if args.run_claims else "hash-and-corpus-audit",
        "integration": "rerun" if args.run_integration else "existing-reports",
        "oracles": "rerun" if args.run_claims else "not-re-executed",
    }
    limitations = []
    if not args.run_claims:
        limitations.append(
            "Claim reports were hashed and corpus-checked, but oracles were not "
            "re-executed; use --run-claims for independent claim execution."
        )
    if not args.run_integration:
        limitations.append(
            "Integration reports were validated, but consumers were not rebuilt; "
            "use --run-integration for a fresh build."
        )

    result = "PASS" if not problems else "FAIL"
    dirty = bool(
        repository_start.get("project_dirty") or repository_end.get("project_dirty")
    )
    if result == "PASS" and dirty:
        # --allow-dirty is a diagnostic mode.  It may write an artifact for
        # inspection, but it must never look like a release PASS.
        result = "DIAGNOSTIC"
        limitations.append(
            "The worktree was dirty and --allow-dirty was used; this artifact "
            "is diagnostic and is not release-eligible."
        )
    release_eligible = (
        result == "PASS" and mode == "independent-execution" and not dirty
    )
    artifact = {
        "schema": SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "result": result,
        "release_eligible": release_eligible,
        "verification_mode": mode,
        "repository": repository_end,
        "repository_at_start": repository_start,
        "environment": environment,
        "execution": execution,
        "gates": gates,
        "boundaries": boundaries(claims),
        "artifacts": artifacts,
        "limitations": limitations,
        "problems": problems,
    }

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    passed = sum(1 for gate in gates if gate.get("status") == "PASS")
    print(
        f"final-verification: {result} mode={mode} gates={passed}/{len(gates)} "
        f"release_eligible={str(release_eligible).lower()} "
        f"dirty={str(repository_end['dirty']).lower()} "
        f"tests={unit_gate.get('tests')} skipped={unit_gate.get('skipped')}"
    )
    print(f"worktree: {repository_end['worktree_sha256']}")
    print(f"report: {_display_path(out_path)}")
    for problem in problems:
        print(f"  ! {problem}")
    return 0 if result == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())