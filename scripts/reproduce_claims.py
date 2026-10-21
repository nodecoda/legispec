#!/usr/bin/env python3
# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Re-run every claim in the ledger and rewrite its conformance report.

This is the "rebuild the evidence" half of the project:

    python scripts/reproduce_claims.py --only aarch64-scalar
    python scripts/reproduce_claims.py                 # all 43 claims

Each claim is re-run from rules/ against the oracle binary named in the ledger,
with the compile-time configuration recorded in claims.json.  The report is
rewritten in place, so its absolute paths and hashes describe *this* checkout.

    python -m legalizer_dsl.claims verify
    python -m legalizer_dsl.claims verify --write   # refresh the ledger pins

Exit codes: 0 = every re-run report agrees with the ledger, 1 = disagreement.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
LEDGER = REPO / "claims" / "claims.json"


def define_args(config: dict) -> list[str]:
    args: list[str] = []
    for name, value in config.items():
        if isinstance(value, bool):
            text = "true" if value else "false"
        else:
            text = str(value)
        args.extend(["--define", f"{name}={text}"])
    return args


def run_claim(claim: dict, out_dir: pathlib.Path, llvm_build: str, limit: int) -> int:
    cmd = [
        sys.executable, "-X", "utf8", "-m", "legalizer_dsl.conformance",
        "--rules", str(REPO / "rules" / claim["rules"]),
        "--oracle", str(out_dir / claim["oracle"]),
        "--out", str(out_dir / claim["report"]),
        "--llvm-build", llvm_build,
        *define_args(claim.get("config") or {}),
    ]
    if limit:
        cmd.extend(["--limit", str(limit)])
    return subprocess.call(cmd, cwd=str(REPO))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", action="append", default=[],
                    help="claim id (repeatable); default is every claim")
    ap.add_argument("--out", default=str(REPO / "out"))
    ap.add_argument("--llvm-build", default="F:/llvm/build")
    ap.add_argument("--limit", type=int, default=0, help="smoke run: cap query count")
    args = ap.parse_args(argv)

    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    out_dir = pathlib.Path(args.out)
    selected = [
        c for c in ledger["claims"]
        if not args.only or c["id"] in args.only
    ]
    if not selected:
        print("no claim matched --only", file=sys.stderr)
        return 2

    failed = 0
    for claim in selected:
        print(f"== {claim['id']} ({claim['rules']}, {claim['queries']} queries)")
        rc = run_claim(claim, out_dir, args.llvm_build, args.limit)
        if rc != 0:
            failed += 1
            print(f"   re-run failed with exit code {rc}")
    print(f"reproduced {len(selected) - failed}/{len(selected)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
