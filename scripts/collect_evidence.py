#!/usr/bin/env python3
# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Collect the pinned evidence files into this repository's out/ directory.

The claim ledger (claims/claims.json) pins every proof to three artifacts:

    rules file  ->  conformance report  ->  oracle binary

Rules live in this repository.  Reports and oracle binaries are build outputs
(they embed a local LLVM build path and a Windows x64 executable), so they are
not committed.  This script copies exactly the files the ledger names from an
evidence directory produced elsewhere, and verifies the copy against the
pinned sha256 values.

    python scripts/collect_evidence.py --from F:/llvm-study/toycpu/out
    python -m legalizer_dsl.claims verify

Exit codes: 0 = every pinned file present and matching, 1 = mismatch/missing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
LEDGER = REPO / "claims" / "claims.json"


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="source", required=True,
                    help="evidence directory that contains the reports and oracles")
    ap.add_argument("--to", dest="target", default=str(REPO / "out"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    source = pathlib.Path(args.source)
    target = pathlib.Path(args.target)
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))

    copied = 0
    bad: list[str] = []
    for claim in ledger["claims"]:
        for key, sha_key in (("report", None), ("oracle", "oracle_sha256")):
            rel = claim.get(key)
            if not rel:
                continue
            src = source / rel
            dst = target / rel
            if not src.exists():
                bad.append(f"missing at source: {rel}")
                continue
            if sha_key:
                want = claim.get(sha_key)
                got = sha256(src)
                if want and got != want:
                    bad.append(f"sha mismatch: {rel} expected {want[:12]} got {got[:12]}")
                    continue
            if not args.dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            copied += 1

    verb = "would copy" if args.dry_run else "copied"
    print(f"{verb} {copied} files -> {target}")
    for problem in bad:
        print(f"  {problem}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
