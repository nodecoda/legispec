#!/usr/bin/env python3
# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Fail closed when an integration report is not an actual PASS.

``integration_smoke.py`` returns 0 for ``SKIP: environment`` so a developer
without LLVM can still run the fixture.  CI must not turn that into a silent
green: this gate prints every status and, when a toolchain was explicitly
provided, requires ``PASS``.

A PASS must be bound to the current tree: the report records the rules, query
corpus, generated sources, consumer, oracles, evaluator/codegen fingerprints,
LLVM commit and git HEAD.  Old reports without those pins are rejected rather
than treated as fresh.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from legispec import __version__ as GENERATOR_VERSION  # noqa: E402
from legispec.codegen import DSL_SPEC_VERSION  # noqa: E402
from legispec.fingerprint import (  # noqa: E402
    codegen_sha256,
    hand_evaluator_sha256,
)

SCHEMA = "legispec-integration-v2"
MIN_QUERY_COUNT = 100
REQUIRED_COMPARISONS = ("consumer", "hand_written_oracle", "generated_oracle")
TARGET_RULES = {
    "toycpu": "rules/toycpu.lspec",
    "aarch64-vector": "rules/aarch64-vector.lspec",
}
HEX64 = re.compile(r"[0-9a-f]{64}")
HEX40 = re.compile(r"[0-9a-f]{40}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_claims() -> dict:
    try:
        data = json.loads(
            (ROOT / "claims" / "claims.json").read_text(encoding="utf-8-sig")
        )
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def current_expected() -> dict:
    """Return the pins a report must match in this checkout."""

    expected: dict = {
        "generator_version": GENERATOR_VERSION,
        "dsl_spec_version": DSL_SPEC_VERSION,
        "evaluator_sha256": hand_evaluator_sha256(),
        "codegen_sha256": codegen_sha256(),
        # An empty pin is deliberate: reports then fail the comparison instead
        # of silently skipping the check when the ledger or git is unavailable.
        "llvm_commit": "",
        "head_commit": "",
    }
    llvm_commit = _load_claims().get("llvm_commit")
    if isinstance(llvm_commit, str) and llvm_commit:
        expected["llvm_commit"] = llvm_commit
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        result = None
    if result is not None and result.returncode == 0:
        head = result.stdout.strip()
        if head:
            expected["head_commit"] = head
    return expected


def _require_hex(problems: list[str], value, name: str, length: int = 64) -> None:
    pattern = HEX64 if length == 64 else HEX40
    if not isinstance(value, str) or not pattern.fullmatch(value):
        problems.append(f"{name} is not a {length}-character lowercase hex hash")


def _repo_path(relative: str) -> Optional[Path]:
    """Resolve a report-relative artifact path inside the repository.

    Absolute paths and ``..`` escapes are rejected: evidence must live in the
    tree that produced it.  Symlinks are resolved and must also stay inside.
    """

    candidate = Path(relative)
    if candidate.is_absolute():
        return None
    root = ROOT.resolve()
    try:
        resolved = (root / candidate).resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return resolved


def _check_path(
    problems: list[str], relative, expected_sha: str, name: str
) -> None:
    if not isinstance(relative, str) or not relative:
        problems.append(f"{name} is missing")
        return
    path = _repo_path(relative)
    if path is None:
        problems.append(f"{name} is absolute or escapes the repository: {relative}")
        return
    if not path.is_file():
        problems.append(f"{name} does not exist: {relative}")
        return
    try:
        actual = _sha256(path)
    except OSError as exc:
        problems.append(f"{name} is unreadable: {relative} ({exc})")
        return
    if actual != expected_sha:
        problems.append(f"{name} sha256 is stale: {relative}")


def _check_artifact(
    problems: list[str], artifacts: dict, key: str, expected_sha: str
) -> None:
    relative = artifacts.get(key)
    if not isinstance(relative, str) or not relative:
        problems.append(f"artifacts.{key} is missing")
        return
    _check_path(problems, relative, expected_sha, f"artifacts.{key}")


def _count_records(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def validate_report(data: dict, expected: Optional[dict] = None) -> list[str]:
    problems: list[str] = []
    expected = expected or {}
    if data.get("schema") != SCHEMA:
        problems.append(f"schema {data.get('schema')!r} != {SCHEMA!r}")
    if data.get("status") != "PASS":
        problems.append(f"status is {data.get('status')!r}, not PASS")
    if data.get("ok") is not True:
        problems.append("ok is not true")

    inputs = data.get("inputs")
    if not isinstance(inputs, dict):
        problems.append("inputs is missing")
        inputs = {}
    queries = inputs.get("query_count")
    if not isinstance(queries, int) or queries < MIN_QUERY_COUNT:
        problems.append(
            f"query_count is below the {MIN_QUERY_COUNT}-query evidence floor: "
            f"{queries!r}"
        )
    target = data.get("target")
    expected_rules = TARGET_RULES.get(target) if isinstance(target, str) else None
    if expected_rules is None:
        problems.append(f"target {target!r} has no declared rules file")

    rules = inputs.get("rules")
    expected_rules_sha = inputs.get("rules_sha256")
    if not isinstance(rules, str) or not rules:
        problems.append("inputs.rules is missing")
    else:
        if expected_rules is not None and rules != expected_rules:
            problems.append(
                f"inputs.rules {rules!r} != declared target rules {expected_rules!r}"
            )
        if not isinstance(expected_rules_sha, str) or not expected_rules_sha:
            problems.append("inputs.rules_sha256 is missing")
        else:
            _check_path(problems, rules, expected_rules_sha, "rules")

    query_limit = inputs.get("query_limit")
    if not isinstance(query_limit, int) or query_limit < 0:
        problems.append("inputs.query_limit is missing or negative")

    query_sha = inputs.get("query_sha256")
    _require_hex(problems, query_sha, "inputs.query_sha256")

    artifacts = data.get("artifacts")
    if not isinstance(artifacts, dict):
        problems.append("artifacts is missing")
        artifacts = {}
    if isinstance(query_sha, str) and HEX64.fullmatch(query_sha):
        _check_artifact(problems, artifacts, "query_file", query_sha)
        query_file = artifacts.get("query_file")
        if isinstance(query_file, str):
            resolved = _repo_path(query_file)
            if resolved is not None and resolved.is_file():
                try:
                    counted = _count_records(resolved)
                except (OSError, UnicodeDecodeError) as exc:
                    problems.append(f"query file is unreadable: {query_file} ({exc})")
                    counted = None
                if counted is not None and isinstance(queries, int) and counted != queries:
                    problems.append(
                        f"query file has {counted} records but query_count is {queries}"
                    )
                if (
                    isinstance(queries, int)
                    and isinstance(query_limit, int)
                    and query_limit > 0
                    and queries > query_limit
                ):
                    problems.append(
                        f"query_count {queries} exceeds query_limit {query_limit}"
                    )

    provenance = data.get("provenance")
    if not isinstance(provenance, dict):
        problems.append("provenance is missing")
        provenance = {}
    for key in ("generator_version", "dsl_spec_version"):
        if not isinstance(provenance.get(key), str) or not provenance[key]:
            problems.append(f"provenance.{key} is missing")
    for key in ("evaluator_sha256", "codegen_sha256"):
        _require_hex(problems, provenance.get(key), f"provenance.{key}")
    if not isinstance(provenance.get("llvm_commit"), str) or not provenance["llvm_commit"]:
        problems.append("provenance.llvm_commit is missing")
    elif provenance["llvm_commit"] == "unknown":
        problems.append("provenance.llvm_commit is 'unknown'")
    _require_hex(problems, provenance.get("head_commit"), "provenance.head_commit", 40)
    if provenance.get("worktree_dirty") is not False:
        problems.append("provenance.worktree_dirty is not false")
    if provenance.get("rules_sha256") != expected_rules_sha:
        problems.append("provenance.rules_sha256 != inputs.rules_sha256")
    if provenance.get("query_sha256") != query_sha:
        problems.append("provenance.query_sha256 != inputs.query_sha256")

    generated = provenance.get("generated")
    if not isinstance(generated, dict):
        problems.append("provenance.generated is missing")
        generated = {}
    for key in ("header", "source"):
        name = f"provenance.generated.{key}"
        if not isinstance(generated.get(key), str) or not generated[key]:
            problems.append(f"{name} is missing")
            continue
        digest = generated.get(f"{key}_sha256")
        _require_hex(problems, digest, f"{name}_sha256")
        if isinstance(digest, str) and HEX64.fullmatch(digest):
            _check_path(problems, generated[key], digest, name)

    consumer_sha = provenance.get("consumer_sha256")
    _require_hex(problems, consumer_sha, "provenance.consumer_sha256")
    if isinstance(consumer_sha, str) and HEX64.fullmatch(consumer_sha):
        _check_artifact(problems, artifacts, "consumer", consumer_sha)

    oracle_hashes = provenance.get("oracle_sha256")
    if not isinstance(oracle_hashes, dict):
        problems.append("provenance.oracle_sha256 is missing")
        oracle_hashes = {}
    artifacts_oracles = artifacts.get("oracles")
    if not isinstance(artifacts_oracles, dict):
        artifacts_oracles = {}
    for label in REQUIRED_COMPARISONS[1:]:
        value = oracle_hashes.get(label)
        _require_hex(problems, value, f"provenance.oracle_sha256.{label}")
        if isinstance(value, str) and HEX64.fullmatch(value):
            _check_artifact(problems, artifacts_oracles, label, value)

    for key in (
        "generator_version",
        "dsl_spec_version",
        "evaluator_sha256",
        "codegen_sha256",
        "llvm_commit",
        "head_commit",
    ):
        wanted = expected.get(key)
        if wanted is None:
            continue
        got = provenance.get(key)
        if got != wanted:
            problems.append(f"provenance.{key} {got!r} != current {wanted!r}")

    comparisons = data.get("comparisons")
    if not isinstance(comparisons, dict):
        problems.append("comparisons is missing")
        comparisons = {}
    for name in REQUIRED_COMPARISONS:
        value = comparisons.get(name)
        if not isinstance(value, dict):
            problems.append(f"comparison {name} is missing")
            continue
        if value.get("ok") is not True:
            problems.append(f"comparison {name} ok is not true")
        if value.get("divergence_count") != 0:
            problems.append(f"comparison {name} has divergences")
        if value.get("oracle_errors") != 0:
            problems.append(f"comparison {name} has oracle errors")
        if not isinstance(value.get("queries"), int):
            problems.append(f"comparison {name} has no query count")
        elif isinstance(queries, int) and value.get("queries") != queries:
            problems.append(f"comparison {name} queries != {queries}")
    return problems


def inspect(path: Path) -> tuple[str, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return "FAIL", {"reason": f"unreadable report: {exc}"}
    if not isinstance(data, dict):
        return "FAIL", {"reason": f"report root is {type(data).__name__}, expected object"}
    return str(data.get("status", "FAIL")), data


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", help="integration report JSON files")
    parser.add_argument("--require-pass", action="store_true")
    parser.add_argument(
        "--require-pass-if-env",
        default=None,
        metavar="NAME",
        help="require PASS when environment variable NAME is non-empty",
    )
    args = parser.parse_args(argv)

    require_pass = args.require_pass
    if args.require_pass_if_env and os.environ.get(args.require_pass_if_env):
        require_pass = True

    expected = current_expected()
    failures = 0
    statuses: list[str] = []
    for value in args.reports:
        path = Path(value)
        if not path.is_absolute():
            path = ROOT / path
        if not path.is_file():
            print(f"FAIL {value}: report missing")
            failures += 1
            continue
        status, data = inspect(path)
        statuses.append(status)
        target = data.get("target", "?")
        inputs = data.get("inputs") if isinstance(data.get("inputs"), dict) else {}
        queries = inputs.get("query_count", "?")
        print(f"{status:<4} {value} target={target} queries={queries}")
        if status == "FAIL":
            reason = data.get("reason")
            if reason:
                print(f"  ! {reason}")
            for message in data.get("problems", []) or []:
                print(f"  ! {message}")
            failures += 1
        elif status == "SKIP":
            if not data.get("skip_reason"):
                print("  ! SKIP report has no skip_reason")
                failures += 1
            if require_pass:
                print("  ! SKIP is not accepted when a toolchain was provided")
                failures += 1
        elif status == "PASS":
            problems = validate_report(data, expected)
            for message in problems:
                print(f"  ! {message}")
            if problems:
                failures += 1
        else:
            print(f"  ! unknown status {status!r}")
            failures += 1

    if failures:
        print(f"integration-status: FAIL reports={len(args.reports)} failures={failures}")
        return 1
    overall = "PASS" if statuses and all(value == "PASS" for value in statuses) else "SKIP"
    print(f"integration-status: {overall} reports={len(args.reports)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())