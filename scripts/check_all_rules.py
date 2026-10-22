#!/usr/bin/env python3
# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0.
# See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0
#
# ===----------------------------------------------------------------------===##

"""Run a strict check over every committed rule file.

The CI gate is intentionally simple: each ``rules/*.lspec`` file is checked with
``--strict`` (plus the defines/capability manifest/witness it needs), and the
script fails if any file does not exit zero.  Rules that are not listed in
``OVERRIDES`` rely on the CLI's normal sibling-manifest discovery.

Usage::

    python scripts/check_all_rules.py
    python scripts/check_all_rules.py --only toycpu.lspec --json
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
RULES_DIR = REPO / "rules"

if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from legispec import paths  # noqa: E402

# Rules whose strict check needs a non-default configuration.  Paths are
# relative to the repository root; the checker resolves sibling manifests for
# every other rule automatically.
OVERRIDES: dict[str, dict[str, object]] = {
    "riscv-conditional-gconstant.lspec": {
        "defines": ["is64bit=true", "sXLen=s64", "sDoubleXLen=s128"],
        "capabilities": "capabilities/riscv-conditional-gconstant-rv64-capabilities.json",
    },
    "riscv-scalar-conditional.lspec": {
        "defines": ["is64bit=true", "sXLen=s64", "sDoubleXLen=s128"],
        "capabilities": "capabilities/riscv64-scalar-capabilities.json",
    },
    "aarch64-vector.lspec": {
        "witness": "witness/aarch64-vector-unreachable.witness",
    },
}


def discover_rules() -> list[pathlib.Path]:
    """Return every committed rule file in deterministic order."""
    return paths.iter_rule_files(RULES_DIR)


def build_command(rule: pathlib.Path) -> list[str]:
    """Build the strict-check command for one rule file."""
    override = OVERRIDES.get(rule.name, {})
    command = [
        sys.executable,
        "-m",
        "legispec",
        "--rules",
        str(rule.resolve()),
    ]
    for define in override.get("defines", []):
        command.extend(["--define", str(define)])
    command.extend(["check", "--strict"])
    capabilities = override.get("capabilities")
    if capabilities:
        command.extend(["--capabilities", str(capabilities)])
    witness = override.get("witness")
    if witness:
        command.extend(["--witness", str(witness)])
    return command


def _environment() -> dict[str, str]:
    """Make the uninstalled ``src/`` checkout importable for local runs."""
    env = os.environ.copy()
    source = str(REPO / "src")
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = source + (os.pathsep + existing if existing else "")
    return env


def _summary(output: str) -> str:
    """Extract the checker's compact summary line from captured output."""
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if stripped.startswith("checks:"):
            return stripped
    return "checks: unavailable"


def _tail(output: str, limit: int = 20) -> list[str]:
    return output.splitlines()[-limit:]


def _select_rules(rules: list[pathlib.Path], only: list[str]) -> tuple[list[pathlib.Path], list[str]]:
    if not only:
        return rules, []
    wanted = {pathlib.PurePath(name).name for name in only}
    selected = [rule for rule in rules if rule.name in wanted]
    found = {rule.name for rule in selected}
    return selected, sorted(wanted - found)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="FILE",
        help="check only this rule file (repeatable)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit structured results instead of one line per rule",
    )
    args = parser.parse_args(argv)

    selected, missing = _select_rules(discover_rules(), args.only)
    if missing:
        print(f"unknown rule file(s): {', '.join(missing)}", file=sys.stderr)
        return 1
    if not selected:
        print(f"no rule files found under {RULES_DIR}", file=sys.stderr)
        return 1

    results: list[dict[str, object]] = []
    env = _environment()
    for rule in selected:
        command = build_command(rule)
        proc = subprocess.run(
            command,
            cwd=REPO,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        output = proc.stdout or ""
        summary = _summary(output)
        ok = proc.returncode == 0
        result: dict[str, object] = {
            "file": rule.name,
            "rc": proc.returncode,
            "summary": summary,
            "ok": ok,
        }
        if not ok:
            result["tail"] = _tail(output)
        results.append(result)
        if not args.json:
            print(f"{rule.name} rc={proc.returncode} {summary}")
            if not ok:
                for line in result["tail"]:
                    print(f"    {line}")

    passed = sum(1 for result in results if result["ok"])
    if args.json:
        print(
            json.dumps(
                {
                    "ok": passed == len(results),
                    "count": len(results),
                    "passed": passed,
                    "failed": len(results) - passed,
                    "results": results,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(f"sweep: {passed}/{len(results)} rules passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
