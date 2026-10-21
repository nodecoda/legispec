#!/usr/bin/env python3
# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Fetch the single LLVM header that `constructs check` reads.

`constructs check` classifies every public builder method of
`LegalizerInfo`.  CI does not build LLVM, so it downloads that one header at
the LLVM commit pinned in `claims/claims.json` and points
LEGALIZER_DSL_LLVM_SRC at the result.

    python scripts/fetch_llvm_header.py --dest .ci/llvm-src

Cached: a file is reused only when `<dest>.commit` names the requested commit
and `<dest>.sha256` matches the file on disk.  `--force` always downloads.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
LEDGER = REPO / "claims" / "claims.json"
REL = "llvm/include/llvm/CodeGen/GlobalISel/LegalizerInfo.h"


def _marker(dest: pathlib.Path, suffix: str) -> pathlib.Path:
    return pathlib.Path(f"{dest}.{suffix}")


def _cache_valid(dest: pathlib.Path, commit: str) -> bool:
    """True when ``dest`` matches both cache markers for ``commit``."""
    if not dest.is_file():
        return False
    try:
        cached_commit = _marker(dest, "commit").read_text(encoding="utf-8").strip()
        cached_sha = _marker(dest, "sha256").read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if not cached_commit or not cached_sha:
        return False
    if cached_commit != commit:
        return False
    return hashlib.sha256(dest.read_bytes()).hexdigest() == cached_sha


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dest", default=str(REPO / ".ci" / "llvm-src"))
    ap.add_argument("--commit", default=None)
    ap.add_argument(
        "--force", action="store_true", help="download even if the cache is valid"
    )
    args = ap.parse_args(argv)

    commit = args.commit or json.loads(
        LEDGER.read_text(encoding="utf-8-sig")
    )["llvm_commit"]
    dest = pathlib.Path(args.dest) / REL

    if not args.force and _cache_valid(dest, commit):
        digest = hashlib.sha256(dest.read_bytes()).hexdigest()
        print(f"cached {dest}  {digest[:16]}  {dest.stat().st_size} bytes  @{commit[:12]}")
        return 0

    url = f"https://raw.githubusercontent.com/llvm/llvm-project/{commit}/{REL}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read()
    digest = hashlib.sha256(data).hexdigest()
    dest.write_bytes(data)
    _marker(dest, "commit").write_text(commit + "\n", encoding="utf-8", newline="\n")
    _marker(dest, "sha256").write_text(digest + "\n", encoding="utf-8", newline="\n")
    print(f"{dest}  {digest[:16]}  {len(data)} bytes  @{commit[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
