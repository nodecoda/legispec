#!/usr/bin/env python3
# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Fail on any unittest skip that is not explicitly registered.

A skip is invisible green.  On hosted runners the oracle-backed mutation
controls cannot run because the oracle binaries are built locally, so those
skips are allowed -- but only when the exact test id is listed in
``tests/expected-skips.json``.  A new skip, or a registered skip that no longer
happens, fails the gate so the manifest cannot rot.
"""

from __future__ import annotations

import argparse
import io
import json
import platform
import sys
import unittest
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
DEFAULT_MANIFEST = TESTS / "expected-skips.json"
SCHEMA = "legalizer-dsl-expected-skips-v1"


def load_manifest(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError(f"{path}: schema {data.get('schema')!r} != {SCHEMA!r}")
    allowed = data.get("allowed")
    if not isinstance(allowed, list) or not all(
        isinstance(entry, dict) for entry in allowed
    ):
        raise ValueError(f"{path}: allowed must be a list of objects")
    return allowed


def _entries_for_platform(entries: list[dict], system: str) -> list[dict]:
    selected: list[dict] = []
    for entry in entries:
        test = entry.get("test")
        reason = entry.get("reason")
        platforms = entry.get("platforms")
        optional = entry.get("optional", False)
        if not isinstance(test, str) or not test:
            raise ValueError("expected-skips entry has no test id")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"expected-skips entry has no reason: {test}")
        if not isinstance(optional, bool):
            raise ValueError(f"expected-skips optional must be a boolean: {test}")
        if platforms is not None:
            if not isinstance(platforms, list) or not all(
                isinstance(value, str) for value in platforms
            ):
                raise ValueError(f"expected-skips platforms must be a list: {test}")
            if system not in platforms:
                continue
        selected.append(entry)
    return selected


def allowed_for_platform(entries: list[dict], system: str) -> dict[str, str]:
    allowed: dict[str, str] = {}
    for entry in _entries_for_platform(entries, system):
        test = entry["test"]
        if test in allowed:
            raise ValueError(f"duplicate expected-skips entry: {test}")
        allowed[test] = entry["reason"]
    return allowed


def optional_for_platform(entries: list[dict], system: str) -> set[str]:
    """Return tests whose skip may be absent on this platform.

    Environment-dependent controls (for example oracle-backed tests) are
    optional: when they run, that is strictly better than skipping, but a
    skip must still be registered with a reason.
    """

    return {
        entry["test"]
        for entry in _entries_for_platform(entries, system)
        if entry.get("optional", False)
    }


def compare_skips(
    skipped: dict[str, str],
    allowed: dict[str, str],
    optional: Optional[set[str]] = None,
) -> list[str]:
    optional = optional or set()
    problems: list[str] = []
    for test, reason in sorted(skipped.items()):
        if test not in allowed:
            problems.append(f"unregistered skip: {test} ({reason})")
    for test in sorted(set(allowed) - set(skipped)):
        if test in optional:
            continue
        problems.append(
            f"registered skip did not happen: {test} "
            "(remove it from tests/expected-skips.json or fix the test)"
        )
    return problems


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-p",
        "--pattern",
        default="test_*.py",
        help="unittest discovery pattern (default: test_*.py)",
    )
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    args = parser.parse_args(argv)

    try:
        entries = load_manifest(Path(args.manifest))
        system = platform.system().lower()
        allowed = allowed_for_platform(entries, system)
        optional = optional_for_platform(entries, system)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"check-skips: FAIL: {exc}", file=sys.stderr)
        return 1

    suite = unittest.defaultTestLoader.discover(str(TESTS), pattern=args.pattern)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=1).run(suite)
    skipped = {
        getattr(test, "id", lambda: str(test))(): reason
        for test, reason in result.skipped
    }

    problems = compare_skips(skipped, allowed, optional)
    if result.testsRun == 0:
        problems.append("no tests ran")
    for test, _ in result.failures:
        problems.append(f"test failed: {test.id()}")
    for test, _ in result.errors:
        problems.append(f"test errored: {test.id()}")
    if result.unexpectedSuccesses:
        problems.append("unexpected successes in a suite using expected failures")

    status = "PASS" if not problems else "FAIL"
    print(
        f"check-skips: {status} tests={result.testsRun} "
        f"skipped={len(skipped)} allowed={len(allowed)} "
        f"optional={len(optional)} "
        f"failures={len(result.failures)} errors={len(result.errors)} "
        f"expected_failures={len(result.expectedFailures)}"
    )
    for test, reason in sorted(skipped.items()):
        marker = "allowed" if test in allowed else "UNREGISTERED"
        print(f"  skip {test}: {marker} ({reason})")
    for test, reason in sorted(
        (test.id(), reason) for test, reason in result.expectedFailures
    ):
        print(f"  expected failure {test}: {reason}")
    for problem in problems:
        print(f"  ! {problem}")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
