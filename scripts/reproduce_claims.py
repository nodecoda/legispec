#!/usr/bin/env python3
# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0.
# See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0
#
# ===----------------------------------------------------------------------===##

"""Re-run every claim in the ledger and rewrite its conformance report.

This is the "rebuild the evidence" half of the project:

    python scripts/reproduce_claims.py --only aarch64-scalar
    python scripts/reproduce_claims.py                 # all 43 claims
    python scripts/reproduce_claims.py --limit 50 --out out-smoke

Each claim is re-run from rules/ against the oracle binary named in the ledger,
with the compile-time configuration recorded in claims.json and the per-claim
LLVM build recorded as ``llvm_build``.  The report is rewritten in place.
Paths are recorded relative to the data root and the report omits the
temporary JSONL name, so two identical runs produce the same canonical hash.

A full run ends with the canonical claim audit:

    python -m legispec.claims verify
    python -m legispec.claims verify --write   # refresh the ledger pins

Refreshing after an evaluator change also needs
``--allow-evaluator-change``; the reports must already carry the new
fingerprint.  ``--oracle-dir`` (default ``out/``) keeps pinned oracles in place
while a ``--limit`` smoke run writes reports somewhere else.

A ``--limit`` run is a smoke test: it never audits, because its truncated
reports are not the pinned evidence.

Exit codes: 0 = every re-run report agrees with the ledger, 1 = disagreement,
2 = refused (for example a smoke run aimed at the pinned out/ directory).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
LEDGER = REPO / "claims" / "claims.json"
DEFAULT_LLVM_BUILD = "F:/llvm/build"


def define_args(config: dict) -> list[str]:
    args: list[str] = []
    for name, value in config.items():
        if isinstance(value, bool):
            text = "true" if value else "false"
        else:
            text = str(value)
        args.extend(["--define", f"{name}={text}"])
    return args


def _resolve_build(claim: dict, override: str | None) -> str:
    """Return the LLVM build for one claim.

    The ledger's per-claim ``llvm_build`` wins unless the user passed an
    explicit ``--llvm-build`` override.
    """
    if override is not None:
        return override
    return claim.get("llvm_build") or DEFAULT_LLVM_BUILD


def limit_overwrite_error(
    out_dir: pathlib.Path | str, limit: int, allow_overwrite: bool
) -> str | None:
    """Refuse a smoke run that would truncate the pinned repo ``out/``.

    Returns an error message, or None when the run is allowed.
    """
    if limit <= 0 or allow_overwrite:
        return None
    if pathlib.Path(out_dir).resolve() != (REPO / "out").resolve():
        return None
    return (
        "refusing to run --limit into the pinned repo out/ directory: a smoke "
        "run would overwrite pinned reports; use --out out-smoke (or pass "
        "--allow-overwrite if you really intend to truncate them)"
    )


def run_claim(
    claim: dict,
    out_dir: pathlib.Path,
    oracle_dir: pathlib.Path,
    llvm_build: str,
    limit: int,
) -> int:
    cmd = [
        sys.executable, "-X", "utf8", "-m", "legispec.conformance",
        "--rules", str(REPO / "rules" / claim["rules"]),
        "--oracle", str(oracle_dir / claim["oracle"]),
        "--out", str(out_dir / claim["report"]),
        "--llvm-build", llvm_build,
        *define_args(claim.get("config") or {}),
    ]
    if limit:
        cmd.extend(["--limit", str(limit)])
    return subprocess.call(cmd, cwd=str(REPO))


def run_audit(out_dir: pathlib.Path, llvm_build: str | None = None) -> int:
    """Audit the reports just rewritten, with the canonical verifier."""
    os.environ["LEGISPEC_OUT"] = str(pathlib.Path(out_dir).resolve())
    src = REPO / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from legispec import claims

    claims.OUT = pathlib.Path(out_dir).resolve()
    argv = ["verify"]
    if llvm_build is not None:
        argv.extend(["--llvm-build", llvm_build])
    return claims.main(argv)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", action="append", default=[],
                    help="claim id (repeatable); default is every claim")
    ap.add_argument("--out", default=str(REPO / "out"))
    ap.add_argument(
        "--oracle-dir",
        default=str(REPO / "out"),
        help="directory holding the pinned oracle binaries (default: out/)",
    )
    ap.add_argument("--llvm-build", default=None,
                    help="override every claim's recorded llvm_build")
    ap.add_argument("--limit", type=int, default=0, help="smoke run: cap query count")
    ap.add_argument("--allow-overwrite", action="store_true",
                    help="allow --limit to truncate the pinned repo out/ directory")
    args = ap.parse_args(argv)

    out_dir = pathlib.Path(args.out)
    oracle_dir = pathlib.Path(args.oracle_dir)
    error = limit_overwrite_error(out_dir, args.limit, args.allow_overwrite)
    if error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    ledger = json.loads(LEDGER.read_text(encoding="utf-8-sig"))
    selected = [
        c for c in ledger["claims"]
        if not args.only or c["id"] in args.only
    ]
    if not selected:
        print("no claim matched --only", file=sys.stderr)
        return 2

    failed = 0
    for claim in selected:
        build = _resolve_build(claim, args.llvm_build)
        print(f"== {claim['id']} ({claim['rules']}, {claim['queries']} queries)")
        print(f"   llvm_build={build}")
        rc = run_claim(claim, out_dir, oracle_dir, build, args.limit)
        if rc != 0:
            failed += 1
            print(f"   re-run failed with exit code {rc}")
    print(f"reproduced {len(selected) - failed}/{len(selected)}")

    if args.limit:
        print("smoke run: audit skipped")
        return 1 if failed else 0

    audit_rc = run_audit(out_dir, args.llvm_build)
    return 1 if failed or audit_rc else 0


if __name__ == "__main__":
    sys.exit(main())
