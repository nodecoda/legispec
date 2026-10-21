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

Cached: an existing file with the expected sha256 is reused.
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dest", default=str(REPO / ".ci" / "llvm-src"))
    ap.add_argument("--commit", default=None)
    args = ap.parse_args(argv)

    commit = args.commit or json.loads(LEDGER.read_text(encoding="utf-8"))["llvm_commit"]
    url = f"https://raw.githubusercontent.com/llvm/llvm-project/{commit}/{REL}"
    dest = pathlib.Path(args.dest) / REL
    dest.parent.mkdir(parents=True, exist_ok=True)

    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read()
    dest.write_bytes(data)
    print(f"{dest}  {hashlib.sha256(data).hexdigest()[:16]}  {len(data)} bytes  @{commit[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
