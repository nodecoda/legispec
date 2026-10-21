#!/usr/bin/env python3
# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Compare two legalizer oracle binaries over one LDSL-derived query corpus.

This is the feature-axis companion to ``conformance.py``:

    conformance.py   LDSL policy  vs  one oracle
    diff_oracles.py  oracle A     vs  oracle B

The query corpus is the same ``D union R`` set used by conformance, derived
from the rules file.  Both binaries must implement the same target and differ
only in the configuration under study (for example ``generic`` vs ``+cssc``).

The report records every differing observation, so a feature flip can be
reviewed row by row instead of being summarized as a single percentage.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Iterable

TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from legalizer_dsl import conformance as C  # noqa: E402
from legalizer_dsl.parser import parse_defines, parse_file  # noqa: E402


def _run_oracle(path: Path, query_file: Path) -> dict[int, dict]:
    proc = subprocess.run(
        [str(path), "--input", str(query_file)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"oracle failed ({proc.returncode}): {path}\n{proc.stderr.strip()}"
        )
    rows: dict[int, dict] = {}
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        rows[int(value["id"])] = value
    return rows


def _observation(row: dict) -> tuple[str, int, str | None]:
    return (row["action"], int(row["type_idx"]), row.get("new_type"))


def _detail(rows: Iterable[dict]) -> dict[str, dict[str, int]]:
    by_opcode: dict[str, Counter] = {}
    for row in rows:
        left = row["left"]
        right = row["right"]
        key = f"{left[0]} -> {right[0]}"
        by_opcode.setdefault(row["opcode"], Counter())[key] += 1
    return {opcode: dict(sorted(counts.items())) for opcode, counts in sorted(by_opcode.items())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules", required=True, help="LDSL rules file defining the corpus")
    parser.add_argument(
        "--define",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="compile-time configuration value (repeatable)",
    )
    parser.add_argument("--oracle-left", required=True, help="baseline oracle executable")
    parser.add_argument("--oracle-right", required=True, help="variant oracle executable")
    parser.add_argument("--label-left", default="left", help="baseline label in the report")
    parser.add_argument("--label-right", default="right", help="variant label in the report")
    parser.add_argument("--out", required=True, help="output JSON report")
    args = parser.parse_args(argv)

    config = parse_defines(args.define)
    info = parse_file(args.rules, config=config)
    closure = C.closure_queries(info)
    outside = C._closure_outside_d(info, closure)
    queries = list(C._combined_queries(info, closure, outside))

    with tempfile.TemporaryDirectory(prefix="legalizer-diff-") as temp_dir:
        query_file = Path(temp_dir) / "queries.jsonl"
        count, digest = C._write_input(query_file, queries)
        left = _run_oracle(Path(args.oracle_left), query_file)
        right = _run_oracle(Path(args.oracle_right), query_file)

    if len(left) != count or len(right) != count:
        raise SystemExit(
            f"oracle row count mismatch: expected {count}, "
            f"{args.label_left}={len(left)}, {args.label_right}={len(right)}"
        )

    differences: list[dict] = []
    for index, query in enumerate(queries):
        a = _observation(left[index])
        b = _observation(right[index])
        if a != b:
            differences.append(
                {
                    "query": str(query),
                    "opcode": query.opcode,
                    "types": [str(ty) for ty in query.types],
                    "left": list(a),
                    "right": list(b),
                }
            )

    by_opcode = Counter(row["opcode"] for row in differences)
    by_action = Counter((row["left"][0], row["right"][0]) for row in differences)
    report = {
        "schema": "legalizer-dsl-oracle-diff-v1",
        "inputs": {
            "rules": str(Path(args.rules).resolve()),
            "left": {"label": args.label_left, "oracle": str(Path(args.oracle_left).resolve())},
            "right": {"label": args.label_right, "oracle": str(Path(args.oracle_right).resolve())},
            "config": {
                name: value if isinstance(value, (bool, int)) else str(value)
                for name, value in sorted(config.items())
            },
        },
        "corpus": {
            "query_count": count,
            "query_sha256": digest,
            "closure_query_count": len(closure),
            "closure_outside_d_count": len(outside),
        },
        "comparison": {
            "difference_count": len(differences),
            "difference_ratio": (len(differences) / count) if count else 0.0,
            "by_opcode": dict(sorted(by_opcode.items(), key=lambda item: (-item[1], item[0]))),
            "by_action_pair": {
                f"{a} -> {b}": value
                for (a, b), value in sorted(by_action.items())
            },
            "by_opcode_detail": _detail(differences),
        },
        "rows": differences,
    }

    out_path = Path(args.out)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        f"queries={count} differences={len(differences)} "
        f"({report['comparison']['difference_ratio']:.2%}) "
        f"sha256={digest[:16]}... out={out_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())