# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""P5 coverage report and ratchet tests."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _load_report():
    path = ROOT / "scripts" / "coverage_report.py"
    spec = importlib.util.spec_from_file_location("coverage_report", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CoverageReportTests(unittest.TestCase):
    def test_report_contains_boundaries_and_no_parse_errors(self):
        module = _load_report()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            out = root / "report.json"
            docs = root / "COVERAGE.md"
            baseline = root / "baseline.json"
            common = [
                "--target", "toycpu",
                "--target", "aarch64-vector",
                "--out", str(out),
                "--docs", str(docs),
                "--baseline", str(baseline),
            ]
            self.assertEqual(module.main(common), 1)
            missing = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(missing["ratchet"]["status"], "NO_BASELINE")

            code = module.main(common + ["--update-baseline"])
            self.assertEqual(code, 0)
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(report["ratchet"]["status"], "UPDATED")
            self.assertTrue(docs.is_file())
            for target in ("toycpu", "aarch64-vector"):
                data = report["targets"][target]
                self.assertGreater(data["corpus"]["in_domain"], 0)
                self.assertGreater(data["corpus"]["boundary"], 0)
                self.assertEqual(data["corpus"]["parse_errors"], 0)
                self.assertGreater(data["coverage"]["rules_total"], 0)
                self.assertTrue(data["slots"]["types"]["outside_domain"])
            toy_mir = report["targets"]["toycpu"]["corpus"]["mir_files"]
            aarch64_mir = report["targets"]["aarch64-vector"]["corpus"]["mir_files"]
            self.assertTrue(toy_mir)
            self.assertTrue(aarch64_mir)
            self.assertTrue(all(name.endswith("toycpu-shapes.mir") for name in toy_mir))
            self.assertTrue(
                all(
                    name.endswith("aarch64-vector-shapes.mir")
                    or name.endswith("concat-nary.mir")
                    for name in aarch64_mir
                )
            )

    def test_ratchet_fails_on_decrease_and_accepts_waiver(self):
        module = _load_report()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            out = root / "report.json"
            docs = root / "COVERAGE.md"
            baseline = root / "baseline.json"
            common = [
                "--target", "toycpu",
                "--out", str(out),
                "--docs", str(docs),
                "--baseline", str(baseline),
            ]
            self.assertEqual(module.main(common + ["--update-baseline"]), 0)
            data = json.loads(baseline.read_text(encoding="utf-8"))
            data["targets"]["toycpu"] = 1.0
            baseline.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

            self.assertEqual(module.main(common), 1)
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(report["ratchet"]["status"], "FAIL")

            self.assertEqual(module.main(common + ["--waiver", "test waiver"]), 0)
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(report["ratchet"]["status"], "WAIVED")


if __name__ == "__main__":
    unittest.main()
