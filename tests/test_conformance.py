# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Phase 5: conformance query generation and protocol tests."""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legalizer_dsl.conformance import (
    ComparisonReport,
    Observation,
    _closure_outside_d,
    _write_input,
    build_report,
    closure_queries,
    iter_d_queries,
    python_observation,
)
from legalizer_dsl.model import Query, parse_llt
from legalizer_dsl.parser import parse, parse_file


class ConformanceTests(unittest.TestCase):
    def _info(self):
        return parse(
            """
            domain {
                types { s32, s64 };
                imms { 0 };
                mems {};
                q0 { (G_X, [s32]); }
            }
            ruleset G_X, G_Y {
                widen t0 to s64 when t0 == s32;
                legal when t0 == s64;
            }
            """
        )

    def test_d_enumerates_every_alias(self):
        info = self._info()
        queries = list(iter_d_queries(info))
        self.assertEqual(len(queries), 4)
        self.assertEqual([q.opcode for q in queries], ["G_X", "G_X", "G_Y", "G_Y"])
        self.assertEqual([str(q.types[0]) for q in queries], ["s32", "s64", "s32", "s64"])

    def test_closure_follows_transitions(self):
        info = self._info()
        closure = closure_queries(info)
        self.assertEqual(
            [(q.opcode, str(q.types[0])) for q in closure],
            [("G_X", "s32"), ("G_X", "s64")],
        )

    def test_closure_outside_d_is_detected(self):
        info = parse(
            """
            domain { types { s32 }; q0 { (G_X, [s32]); } }
            ruleset G_X { widen t0 to s64 when t0 == s32; }
            """
        )
        outside = _closure_outside_d(info, closure_queries(info))
        self.assertEqual(len(outside), 1)
        self.assertEqual(str(outside[0].types[0]), "s64")

    def test_python_observation_is_exactly_the_step_triple(self):
        info = self._info()
        observation, rule = python_observation(
            info, Query("G_X", (parse_llt("s32"),))
        )
        self.assertEqual(observation, Observation("WidenScalar", 0, "s64"))
        self.assertEqual(rule, (id(info.get("G_X")), 0))

    def test_jsonl_field_order_and_sha256_are_deterministic(self):
        info = self._info()
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "queries.jsonl"
            count, digest = _write_input(path, iter_d_queries(info))
            data = path.read_bytes()
        self.assertEqual(count, 4)
        self.assertEqual(digest, hashlib.sha256(data).hexdigest())
        first = data.splitlines()[0].decode("utf-8")
        self.assertEqual(
            first,
            '{"id":0,"opcode":"G_X","types":["s32"],"mems":[],"imms":[]}',
        )



class ReportDeterminismTests(unittest.TestCase):
    def test_report_bytes_do_not_depend_on_temp_directory_or_jsonl_path(self):
        here = pathlib.Path(__file__).resolve().parents[1]
        rules = here / "rules" / "toycpu.ldsl"
        oracle = here / "out" / "oracle" / "legalizer-oracle.exe"
        if not oracle.is_file():
            self.skipTest("toycpu oracle not collected; see docs/EVIDENCE.md")
        info = parse_file(rules)
        comparison = ComparisonReport(
            queries=0, divergence_count=0, oracle_errors=0, stdout_lines=0
        )
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            first = build_report(
                info,
                rules,
                oracle,
                pathlib.Path(one),
                0,
                "a" * 64,
                [],
                [],
                [],
                comparison,
                llvm_build="F:/llvm/build-toycpu",
            )
            second = build_report(
                info,
                rules,
                oracle,
                pathlib.Path(two),
                0,
                "a" * 64,
                [],
                [],
                [],
                comparison,
                llvm_build="F:/llvm/build-toycpu",
            )
        self.assertEqual(
            json.dumps(first, ensure_ascii=False, indent=2),
            json.dumps(second, ensure_ascii=False, indent=2),
        )
        self.assertNotIn("jsonl", first["inputs"])
        self.assertEqual(len(first["evaluator_sha256"]), 64)
        self.assertEqual(len(first["codegen_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()