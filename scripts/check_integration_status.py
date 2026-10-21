#!/usr/bin/env python3
# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Fail closed when an integration report is not an actual PASS.

``integration_smoke.py`` returns 0 for ``SKIP: environment`` so a developer
without LLVM can still run the fixture.  CI must not turn that into a silent
green: this gate prints every status and, when a toolchain was explicitly
provided, requires ``PASS``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "legalizer-dsl-integration-v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_report(data: dict) -> list[str]:
    problems: list[str] = []
    if data.get("schema") != SCHEMA:
        problems.append(f"schema {data.get('schema')!r} != {SCHEMA!r}")
    if data.get("ok") is not True:
        problems.append("ok is not true")
    inputs = data.get("inputs")
    if not isinstance(inputs, dict):
        problems.append("inputs is missing")
        inputs = {}
    queries = inputs.get("query_count")
    if not isinstance(queries, int) or queries <= 0:
        problems.append(f"query_count is not positive: {queries!r}")
    rules = inputs.get("rules")
    expected = inputs.get("rules_sha256")
    if not isinstance(rules, str) or not rules:
        problems.append("inputs.rules is missing")
    elif not isinstance(expected, str) or not expected:
        problems.append("inputs.rules_sha256 is missing")
    else:
        path = ROOT / rules
        if not path.is_file():
            problems.append(f"rules file missing: {rules}")
        elif _sha256(path) != expected:
            problems.append(f"rules_sha256 is stale for {rules}")
    comparisons = data.get("comparisons")
    if not isinstance(comparisons, dict) or not comparisons:
        problems.append("comparisons is missing or empty")
    else:
        for name, value in comparisons.items():
            if not isinstance(value, dict):
                problems.append(f"comparison {name} is not an object")
                continue
            if value.get("ok") is not True:
                problems.append(f"comparison {name} ok is not true")
            if value.get("divergence_count") != 0:
                problems.append(f"comparison {name} has divergences")
            if value.get("oracle_errors") not in (0, None):
                problems.append(f"comparison {name} has oracle errors")
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

    failures = 0
    statuses: list[str] = []
    for value in args.reports:
        path = Path(value)
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
            problems = validate_report(data)
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
