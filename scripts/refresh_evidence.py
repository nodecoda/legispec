#!/usr/bin/env python3
# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Re-run the conformance reports pinned by ``claims/claims.json``.

The ledger pins each claim to a report and an oracle.  When the evaluator,
rules, or oracle changes, the old report is deliberately STALE.  This script
re-runs the claim's conformance command from the ledger metadata, then (with
``--write-ledger``) asks the claim verifier to refresh the pins.

This is the maintenance entry point for an LLVM bump:

    python scripts/refresh_evidence.py --all
    python scripts/refresh_evidence.py --all --write-ledger

``--write-ledger`` requires ``--all``.  A partial refresh can still inspect
individual claims with ``--claim``, but it must not bless pins for reports it
did not re-run.

A missing oracle is reported and skipped.  It is never silently counted as a
success; use ``--require-all`` to turn missing evidence into a non-zero exit.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "claims" / "claims.json"
OUT = ROOT / "out"


def _config_args(config: dict) -> list[str]:
    args: list[str] = []
    for key, value in sorted(config.items()):
        if isinstance(value, bool):
            text = "true" if value else "false"
        else:
            text = str(value)
        args.extend(["--define", f"{key}={text}"])
    return args


def _run_claim(claim: dict, *, dry_run: bool) -> tuple[str, str]:
    claim_id = claim["id"]
    rules = claim.get("rules")
    oracle_rel = claim.get("oracle")
    report_rel = claim.get("report")
    if not rules or not oracle_rel or not report_rel:
        return claim_id, "SKIP: incomplete ledger entry"

    rules_path = ROOT / "rules" / rules
    oracle_path = OUT / oracle_rel
    report_path = OUT / report_rel
    if not rules_path.is_file():
        return claim_id, f"SKIP: rules missing: {rules_path}"
    if not oracle_path.is_file():
        return claim_id, f"SKIP: oracle missing: {oracle_path}"

    command = [
        sys.executable,
        "-X",
        "utf8",
        "-m",
        "legalizer_dsl.conformance",
        "--rules", str(rules_path),
        "--oracle", str(oracle_path),
        "--out", str(report_path),
        "--llvm-build", str(claim.get("llvm_build") or ""),
    ]
    command.extend(_config_args(claim.get("config") or {}))
    if dry_run:
        print("  " + subprocess.list2cmdline(command))
        return claim_id, "DRY-RUN"

    process = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output = process.stdout.strip()
    if process.returncode != 0:
        return claim_id, f"FAIL rc={process.returncode}: {output[-2000:]}"
    if "divergences=0" not in output:
        return claim_id, f"FAIL: conformance output has no zero-divergence proof: {output[-2000:]}"
    return claim_id, output.splitlines()[-1] if output else "PASS"


def _verify_command() -> list[str]:
    """The ledger refresh command for reports this script has re-run."""
    return [
        sys.executable,
        "-X",
        "utf8",
        "-m",
        "legalizer_dsl.claims",
        "verify",
        "--verify-corpus",
        "--write",
        # This script just re-ran every selected report against the current
        # rules, oracle and evaluator.  The report/corpus/oracle pins and the
        # recorded LLVM commit are therefore expected to move; the staleness
        # gate still checks the new pins are present.
        "--allow-evaluator-change",
        "--allow-report-change",
        "--allow-commit-change",
    ]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="refresh every claim")
    parser.add_argument("--claim", action="append", default=[],
                        help="refresh only this claim id (repeatable)")
    parser.add_argument("--write-ledger", action="store_true",
                        help="run claims verify --verify-corpus --write after refresh")
    parser.add_argument("--require-all", action="store_true",
                        help="fail if any claim is skipped")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.write_ledger and not args.all:
        parser.error(
            "--write-ledger requires --all: a partial refresh must not re-pin "
            "claims that were not re-run"
        )

    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    claims = ledger["claims"]
    if not args.all and not args.claim:
        parser.error("pass --all or at least one --claim")
    selected = claims if args.all else [
        claim for claim in claims if claim["id"] in set(args.claim)
    ]
    missing = set(args.claim) - {claim["id"] for claim in selected}
    if missing:
        print(f"error: unknown claim id(s): {', '.join(sorted(missing))}", file=sys.stderr)
        return 2

    failures = 0
    skipped = 0
    for claim in selected:
        claim_id, result = _run_claim(claim, dry_run=args.dry_run)
        print(f"{claim_id}: {result}")
        if result.startswith("SKIP"):
            skipped += 1
        elif result.startswith("FAIL"):
            failures += 1

    if failures:
        return 1
    if skipped and args.require_all:
        return 1

    if args.write_ledger and not args.dry_run:
        process = subprocess.run(
            _verify_command(),
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        print(process.stdout.rstrip())
        if process.returncode != 0:
            print("error: claims verify --write failed", file=sys.stderr)
            return process.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
