# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0.
# See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0
#
# ===----------------------------------------------------------------------===##

"""Negative controls: prove the conformance harness can fail.

A green conformance run is only evidence if the harness is able to report red.
These tests mutate a rule that the oracle disagrees with, and require the
harness to detect it.  Without this, `divergences=0` could mean "nothing was
actually compared" or "the oracle path is dead".

Kept deliberately tiny: the G_CONSTANT guard experiment is 9 queries.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
RULES = REPO / "rules" / "riscv-conditional-gconstant.lspec"
CONFORMANCE = REPO / "src" / "legispec" / "conformance.py"
ORACLE = REPO / "out" / "oracle-riscv" / "legispec-oracle-riscv.exe"
DEFINES = [
    "--define",
    "is64bit=true",
    "--define",
    "sXLen=s64",
]


def run_conformance(rules: pathlib.Path) -> tuple[int, dict]:
    with tempfile.TemporaryDirectory() as tmp:
        report = pathlib.Path(tmp) / "report.json"
        proc = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                str(CONFORMANCE),
                "--rules",
                str(rules),
                *DEFINES,
                "--oracle",
                str(ORACLE),
                "--out",
                str(report),
            ],
            capture_output=True,
            text=True,
        )
        data = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
        return proc.returncode, data


@unittest.skipUnless(ORACLE.exists(), "RV64 oracle not built")
class HarnessSensitivityTests(unittest.TestCase):
    def test_baseline_is_clean(self):
        code, data = run_conformance(RULES)
        self.assertEqual(code, 0, data)
        self.assertEqual(data["comparison"]["divergence_count"], 0)

    def test_mutated_action_is_detected(self):
        source = RULES.read_text(encoding="utf-8")
        mutated = source.replace(
            "custom_for {s64} when is64bit;", "legal_for {s64} when is64bit;"
        )
        self.assertNotEqual(mutated, source, "mutation anchor not found")
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "mutated.lspec"
            path.write_text(mutated, encoding="utf-8", newline="\n")
            code, data = run_conformance(path)
        self.assertNotEqual(code, 0, "harness accepted a wrong rule table")
        self.assertGreater(data["comparison"]["divergence_count"], 0)

    def test_mutated_guard_is_detected(self):
        source = RULES.read_text(encoding="utf-8")
        mutated = source.replace("when not is64bit;", "when is64bit;")
        self.assertNotEqual(mutated, source, "mutation anchor not found")
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "mutated.lspec"
            path.write_text(mutated, encoding="utf-8", newline="\n")
            code, data = run_conformance(path)
        self.assertNotEqual(code, 0, "harness accepted a flipped guard")
        self.assertGreater(data["comparison"]["divergence_count"], 0)


if __name__ == "__main__":
    unittest.main()