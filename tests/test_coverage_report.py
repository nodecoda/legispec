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

    def test_ratchet_checks_every_metric(self):
        module = _load_report()
        def data(ratio=0.9, in_domain=10, queries=11, matched=9, total=10):
            return {
                "corpus": {
                    "ratio": None,
                    "in_domain": in_domain,
                    "queries": queries,
                },
                "coverage": {
                    "ratio": ratio,
                    "rules_matched": matched,
                    "rules_total": total,
                },
            }
        baseline = {"toycpu": module.metric_snapshot(data())}
        lowered = {
            "ratio": 0.8,
            "in_domain": 9,
            "queries": 10,
            "rules_matched": 8,
            "rules_total": 9,
        }
        arguments = {
            "ratio": "ratio",
            "in_domain": "in_domain",
            "queries": "queries",
            "rules_matched": "matched",
            "rules_total": "total",
        }
        for key in ("ratio", "in_domain", "queries", "rules_matched", "rules_total"):
            with self.subTest(metric=key):
                current = data(**{arguments[key]: lowered[key]})
                problems = module.ratchet_problems(
                    baseline, {"toycpu": current}
                )
                self.assertTrue(
                    any(problem.startswith(f"toycpu: {key} ") for problem in problems),
                    problems,
                )

    def test_subset_run_accepts_extra_baseline_targets(self):
        module = _load_report()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            out = root / "report.json"
            docs = root / "COVERAGE.md"
            baseline = root / "baseline.json"
            both = [
                "--target", "toycpu",
                "--target", "aarch64-vector",
                "--out", str(out),
                "--docs", str(docs),
                "--baseline", str(baseline),
            ]
            toy = [
                "--target", "toycpu",
                "--out", str(out),
                "--docs", str(docs),
                "--baseline", str(baseline),
            ]
            self.assertEqual(module.main(both + ["--update-baseline"]), 0)
            self.assertEqual(module.main(toy), 0)
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(report["ratchet"]["status"], "PASS")
            self.assertEqual(set(report["targets"]), {"toycpu"})

            self.assertEqual(module.main(toy + ["--update-baseline"]), 0)
            saved = json.loads(baseline.read_text(encoding="utf-8"))
            self.assertIn("aarch64-vector", saved["targets"])

    def test_baseline_root_must_be_an_object(self):
        module = _load_report()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            out = root / "report.json"
            docs = root / "COVERAGE.md"
            baseline = root / "baseline.json"
            baseline.write_text("[]\n", encoding="utf-8")
            rc = module.main(
                [
                    "--target", "toycpu",
                    "--out", str(out),
                    "--docs", str(docs),
                    "--baseline", str(baseline),
                ]
            )
            self.assertEqual(rc, 1)
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(report["ratchet"]["status"], "FAIL")

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
