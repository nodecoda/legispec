# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Project closure audit for the Legalizer DSL.

`claims.py verify` proves each claim.  This tool checks that the *project story*
is internally consistent and that the pinned numbers in CLOSURE.md still match
the artifacts on disk:

  * every claims.json entry is ok and has zero divergences
  * every claim's rules file exists and its content hash matches
  * every claim's report exists and was produced from that same rules hash
  * the rules directory and the referenced rules files are the same set
  * construct coverage adds up (methods == sum of status buckets)
  * tests (AST count) and gates (suite script) match the pinned block

Run with --write to also emit out/closure.json.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from pathlib import Path

from . import paths

ROOT = paths.REPO_ROOT                      # repo root (compat)
RULES = paths.RULES
OUT = paths.OUT
LEDGER = paths.LEDGER
CONSTRUCTS = paths.CONSTRUCT_COVERAGE
CLOSURE_MD = paths.DOCS / "CLOSURE.md"
TESTS = paths.TESTS

PIN_KEYS = (
    "claims",
    "hand-claims",
    "generated-claims",
    "rules-files",
    "hand-queries",
    "constructs",
    "tests",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def count_tests() -> int:
    total = 0
    for path in sorted(TESTS.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        total += sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
        )
    return total


def read_pinned() -> dict[str, int]:
    text = CLOSURE_MD.read_text(encoding="utf-8")
    match = re.search(r"<!-- closure:pinned -->(.*?)<!-- /closure:pinned -->", text, re.S)
    if not match:
        raise ValueError("CLOSURE.md has no closure:pinned block")
    pinned: dict[str, int] = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line:
            continue
        key, _, value = line.partition("=")
        pinned[key.strip()] = int(value.strip())
    missing = [k for k in PIN_KEYS if k not in pinned]
    if missing:
        raise ValueError(f"pinned block missing keys: {missing}")
    return pinned


def aggregate(claims: list[dict]) -> dict[str, dict]:
    targets: dict[str, dict] = {}
    for claim in claims:
        rules = claim["rules"]
        if rules.startswith("aarch64-"):
            target = "aarch64"
        elif rules.startswith("riscv"):
            target = "riscv"
        elif rules.startswith("x86"):
            target = "x86"
        else:
            target = "toycpu"
        entry = targets.setdefault(target, {"claims": 0, "hand_claims": 0, "hand_queries": 0})
        entry["claims"] += 1
        if not claim["id"].endswith("-generated"):
            entry["hand_claims"] += 1
            entry["hand_queries"] += claim["queries"]
    return targets


def audit(write: bool, skip_evidence: bool = False, skip_tests: bool = False) -> int:
    ledger = json.loads(LEDGER.read_text(encoding="utf-8-sig"))
    if skip_evidence:
        entries = ledger["claims"]
    else:
        from . import claims as claims_module

        entries = claims_module.audit_all()
    problems: list[str] = []

    for claim in entries:
        cid = claim["id"]
        # Even in structural mode the ledger's cached verdict must not be
        # negative: a tampered ledger (ok=false / divergences>0) must fail.
        if claim.get("ok") is not True:
            problems.append(
                f"{cid}: ledger entry is not marked ok; run claims verify"
            )
            for problem in claim.get("problems", []):
                problems.append(f"{cid}: {problem}")
        if claim.get("divergences", 0) != 0:
            problems.append(f"{cid}: {claim['divergences']} divergence(s)")

        rules_path = RULES / claim["rules"]
        if not rules_path.exists():
            problems.append(f"{cid}: rules file missing: {rules_path}")
            continue
        current = sha256(rules_path)
        if claim.get("rules_sha256") != current:
            problems.append(f"{cid}: rules hash is stale")

        if skip_evidence:
            continue
        report_path = OUT / claim["report"]
        if not report_path.exists():
            problems.append(f"{cid}: report missing: {report_path}")
            continue
        try:
            report = json.loads(report_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            problems.append(f"{cid}: report is not JSON: {exc}")
            continue
        reported = report.get("inputs", {}).get("rules_sha256")
        if reported != claim.get("rules_sha256"):
            problems.append(f"{cid}: report was produced from a different rules hash")

    referenced = {claim["rules"] for claim in entries}
    on_disk = {path.name for path in RULES.glob("*.ldsl")}
    for missing in sorted(on_disk - referenced):
        problems.append(f"rules file has no claim: {missing}")
    for missing in sorted(referenced - on_disk):
        problems.append(f"claim references a missing rules file: {missing}")

    coverage = json.loads(CONSTRUCTS.read_text(encoding="utf-8-sig"))
    buckets = coverage["counts"]
    methods = coverage["methods"]
    if sum(buckets.values()) != len(methods):
        problems.append(
            f"construct coverage does not add up: {sum(buckets.values())} != {len(methods)}"
        )
    unknown = set(buckets) - {"core", "guard", "macro", "none", "partial"}
    if unknown:
        problems.append(f"unknown construct buckets: {sorted(unknown)}")

    computed = {
        "claims": len(entries),
        "hand-claims": sum(1 for c in entries if not c["id"].endswith("-generated")),
        "generated-claims": sum(1 for c in entries if c["id"].endswith("-generated")),
        "rules-files": len(on_disk),
        "hand-queries": sum(
            c["queries"] for c in entries if not c["id"].endswith("-generated")
        ),
        "constructs": len(methods),
        "tests": 0 if skip_tests else count_tests(),
    }
    try:
        pinned = read_pinned()
    except ValueError as exc:
        problems.append(str(exc))
        pinned = {}
    for key in PIN_KEYS:
        if skip_tests and key == "tests":
            continue
        if key in pinned and pinned[key] != computed[key]:
            problems.append(f"pinned {key}={pinned[key]} but computed {computed[key]}")

    report = {
        "schema": "legalizer-dsl-closure-v1",
        "llvm_commit": ledger.get("llvm_commit"),
        "boundary": ledger.get("boundary"),
        "computed": computed,
        "pinned": pinned,
        "targets": aggregate(entries),
        "constructs": buckets,
        "problems": problems,
    }
    if write:
        OUT.mkdir(parents=True, exist_ok=True)
        target = OUT / "closure.json"
        target.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )

    print(
        "closure  "
        f"claims={computed['claims']}/{computed['claims']} "
        f"hand={computed['hand-claims']} generated={computed['generated-claims']} "
        f"rules-files={computed['rules-files']} hand-queries={computed['hand-queries']} "
        f"constructs={computed['constructs']} tests={computed['tests']} "
        f"problems={len(problems)}"
    )
    for problem in problems:
        print(f"  {problem}")
    if any("report missing" in problem for problem in problems):
        print(
            "  hint: evidence is a build output and is not committed; "
            "run scripts/collect_evidence.py or scripts/reproduce_claims.py "
            "(see docs/EVIDENCE.md)"
        )
    return 0 if not problems else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="check", choices=("check",))
    parser.add_argument("--write", action="store_true", help="write out/closure.json")
    parser.add_argument(
        "--skip-evidence",
        action="store_true",
        help="check structure and pinned numbers only; claims are not re-audited",
    )
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="do not compare the pinned test count (installed wheel has no tests/)",
    )
    args = parser.parse_args(argv)
    if args.skip_evidence:
        print("evidence: skipped (--skip-evidence); claims are NOT audited by this run")
    if args.skip_tests:
        print("tests: pinned test count not checked (--skip-tests)")
    return audit(args.write, args.skip_evidence, args.skip_tests)


if __name__ == "__main__":
    raise SystemExit(main())