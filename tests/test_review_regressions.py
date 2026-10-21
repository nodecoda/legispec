# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""P0 regression harness for the code-review HIGH findings (H1-H5).

Every test asserts the desired post-fix behavior, so these cases are expected
to fail on the P0 baseline and turn green as the later stories land.

Mapping to the review findings:

* H1 index safety:
  ``test_negative_type_index_rejected_by_parse``,
  ``test_negative_imm_and_mem_indices_rejected_by_parse``,
  ``test_model_negative_slots_do_not_alias``.
* H2 coverage exception safety:
  ``test_coverage_handles_mem_predicates_and_same_shape``.
* H3 claims audit completeness:
  ``test_claims_audit_is_complete_when_evidence_is_missing``.
* H4 malformed numeric/mutation inputs:
  ``test_zero_divisors_rejected_by_parse``,
  ``test_more_to_vector_reports_diagnostic_not_exception``,
  ``test_empty_type_set_rejected_by_parse``.
* H5 strict/capability and CLI generation surface:
  ``test_strict_custom_without_manifest_fails``,
  ``test_cli_exposes_codegen_and_conform_and_main_guard``.

For empty type sets (H4) this harness chooses the preferred fix: reject them at
parse time.  The documented fallback was to keep parsing them and guarantee
codegen emits a safe predicate instead of ``return ;``.
"""

from __future__ import annotations

import contextlib
import io
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legalizer_dsl import DslError, Query, parse, parse_llt
from legalizer_dsl.checker import run_checks
from legalizer_dsl.claims import Claim
from legalizer_dsl import claims
from legalizer_dsl import cli, codegen
from legalizer_dsl.coverage import run_coverage
from legalizer_dsl.diagnostics import Severity
from legalizer_dsl.model import (
    Action,
    Always,
    BitsCmp,
    ImmCmp,
    IsMultipleBits,
    MemAlignCmp,
    MemDesc,
    MemTypeIn,
    NextMultiple,
    Pred,
    Rule,
    SetElementSize,
    TypeExpr,
    TypeIn,
)


class NegativeIndexParseTests(unittest.TestCase):
    def test_negative_type_index_rejected_by_parse(self):
        source = """
        domain {
            types { s64, s128 };
            q0 { (G_ADD, [s64]); }
        }
        ruleset G_ADD {
            widen -1 to s128 when scalar(t0) and bits(t0) == 64;
        }
        """
        with self.assertRaises(DslError):
            parse(source)

    def test_negative_imm_and_mem_indices_rejected_by_parse(self):
        sources = {
            "imm": "ruleset G_ADD { legal when imm -1 == 0; }",
            "mem": "ruleset G_LOAD { legal when mem -1.align >= 8; }",
        }
        for name, source in sources.items():
            with self.subTest(index=name):
                with self.assertRaises(DslError):
                    parse(source)


class NegativeModelSlotTests(unittest.TestCase):
    def test_model_negative_slots_do_not_alias(self):
        query = Query("G_ADD", (parse_llt("s64"),))
        predicates = {
            "TypeIn": TypeIn(-1, (parse_llt("s64"),)),
            "BitsCmp": BitsCmp(-1, "==", 64),
        }
        for name, pred in predicates.items():
            with self.subTest(predicate=name):
                self.assertFalse(pred.eval(query))

    def test_model_negative_mem_and_imm_slots_do_not_alias(self):
        query = Query(
            "G_LOAD",
            (parse_llt("s64"),),
            mems=(MemDesc(parse_llt("s64"), 64),),
            imms=(0,),
        )
        predicates = {
            "MemAlignCmp": MemAlignCmp(-1, ">=", 8),
            "MemTypeIn": MemTypeIn(-1, (parse_llt("s64"),)),
            "ImmCmp": ImmCmp(-1, "==", 0),
        }
        for name, pred in predicates.items():
            with self.subTest(predicate=name):
                self.assertFalse(pred.eval(query))

    def test_negative_type_expression_raises_out_of_range(self):
        query = Query("G_ADD", (parse_llt("s64"),))
        with self.assertRaises(DslError) as ctx:
            SetElementSize(-1, 32, "s32").eval(query)
        self.assertEqual(ctx.exception.code, "T010")


class CoverageExceptionSafetyTests(unittest.TestCase):
    def test_coverage_handles_mem_predicates_and_same_shape(self):
        info = parse(
            """
            domain {
                types { s32 };
                mems { s32:32 };
                q0 { (G_LOAD, [s32, s32], mem = [s32:32]); }
            }
            ruleset G_LOAD {
                legal when mem 0.align >= 32 and same_shape(t0, t1);
                legal when mem 0.type in {s32};
            }
            """
        )
        try:
            report = run_coverage(info)
        except Exception as exc:  # noqa: BLE001 - the regression is any crash
            self.fail(
                "run_coverage raised "
                f"{type(exc).__name__}: {exc}"
            )
        self.assertEqual(len(report.entries), 1)
        self.assertEqual(report.errors, [])


class CoverageMemAndPredicateTests(unittest.TestCase):
    def test_uncovered_mem_slot_is_c006(self):
        info = parse(
            """
            domain {
                types { s32 };
                mems { s32:32 };
                q0 { (G_LOAD, [s32], mem = [s32:32]); }
            }
            ruleset G_LOAD {
                legal when t0 == s32;
            }
            """
        )
        report = run_coverage(info)
        self.assertEqual(report.entries[0].mem_arity, 1)
        self.assertEqual(report.entries[0].covered_mems, ())
        self.assertEqual(report.entries[0].uncovered_mems, (0,))
        c006 = [d for d in report.diagnostics if d.code == "C006"]
        self.assertEqual(len(c006), 1)
        self.assertIs(c006[0].severity, Severity.WARN)
        self.assertIn("memory index 0", c006[0].message)

    def test_unhandled_predicate_is_c007_and_other_rulesets_survive(self):
        class UnknownPredicate(Pred):
            def eval(self, query):
                return True

            def __str__(self):
                return "unknown_predicate"

        info = parse(
            """
            domain {
                types { s32 };
                q0 { (G_X, [s32]); (G_Y, [s32]); }
            }
            ruleset G_X { legal when t0 == s32; }
            ruleset G_Y { legal when t0 == s32; }
            """
        )
        info.by_opcode["G_X"].add(Rule(Action.LEGAL, UnknownPredicate()))

        report = run_coverage(info)

        self.assertEqual([entry.opcodes for entry in report.entries], [("G_Y",)])
        c007 = [d for d in report.diagnostics if d.code == "C007"]
        self.assertEqual(len(c007), 1)
        self.assertIs(c007[0].severity, Severity.ERROR)
        self.assertIn("UnknownPredicate", c007[0].message)
        self.assertEqual(c007[0].span, info.by_opcode["G_X"].span)


class CodegenEmptySetDefenseTests(unittest.TestCase):
    def test_empty_type_alternatives_emit_return_false(self):
        table = codegen.LLTTable()
        for pred in (TypeIn(0, ()), MemTypeIn(0, ())):
            with self.subTest(predicate=type(pred).__name__):
                expression = codegen._pred(pred, table)
                self.assertIn("return false;", expression)
                self.assertNotIn("return ;", expression)


class ClaimsAuditCompletenessTests(unittest.TestCase):
    def test_claims_audit_is_complete_when_evidence_is_missing(self):
        claim = Claim(
            id="missing-evidence",
            rules="x.ldsl",
            report="report.json",
            oracle="oracle/missing.exe",
            scope="test",
            config={},
        )
        saved = (claims.RULES, claims.OUT, claims.CLAIMS, claims.LEDGER)
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            rules_dir = root / "rules"
            out_dir = root / "out"
            rules_dir.mkdir()
            out_dir.mkdir()
            rules_path = rules_dir / claim.rules
            report_path = out_dir / claim.report
            claims.RULES = rules_dir
            claims.OUT = out_dir
            claims.CLAIMS = (claim,)
            claims.LEDGER = root / "claims" / "claims.json"
            try:
                scenarios = []
                scenarios.append(("rules", lambda: None))
                scenarios.append(
                    (
                        "report",
                        lambda: rules_path.write_text(
                            "ruleset G_X { legal; }\n", encoding="utf-8"
                        ),
                    )
                )
                scenarios.append(
                    (
                        "oracle",
                        lambda: report_path.write_text("{}", encoding="utf-8"),
                    )
                )
                for name, prepare in scenarios:
                    prepare()
                    with self.subTest(missing=name):
                        entry = claims.audit(claim)
                        self.assertIn("problems", entry)
                        self.assertTrue(entry["problems"], entry)
                        self.assertIs(entry.get("ok"), False, entry)

                # Force main() through an early audit return, which used to
                # omit "ok" and raise KeyError while printing the summary.
                report_path.unlink()
                with contextlib.redirect_stdout(io.StringIO()):
                    try:
                        rc = claims.main(["audit"])
                    except KeyError as exc:
                        self.fail(f"claims.main raised KeyError: {exc}")
                self.assertEqual(rc, 1)
            finally:
                claims.RULES, claims.OUT, claims.CLAIMS, claims.LEDGER = saved


class ZeroAndEmptySetParseTests(unittest.TestCase):
    def test_zero_divisors_rejected_by_parse(self):
        sources = {
            "is_multiple": "ruleset G_X { legal when is_multiple(bits(t0), 0); }",
            "next_multiple": (
                "ruleset G_X { widen 0 to next_multiple(bits(t0), 0); }"
            ),
        }
        for name, source in sources.items():
            with self.subTest(divisor=name):
                with self.assertRaises(DslError):
                    parse(source)

    def test_more_to_vector_reports_diagnostic_not_exception(self):
        source = """
        domain {
            types { s32 };
            q0 { (G_X, [s32]); }
        }
        ruleset G_X {
            more 0 to v4s32;
        }
        """
        try:
            info = parse(source)
        except DslError:
            # Parse-time rejection is also an acceptable post-fix outcome.
            return
        try:
            diagnostics = run_checks(info, strict=True)
        except DslError as exc:
            self.fail(
                "run_checks leaked DslError from ruleset.apply: "
                f"{exc}"
            )
        self.assertTrue(
            any(d.severity is Severity.ERROR for d in diagnostics),
            [d.format("check") for d in diagnostics],
        )

    def test_empty_type_set_rejected_by_parse(self):
        source = """
        domain {
            types { s32 };
            mems { s32:32 };
            q0 { (G_X, [s32], mem = [s32:32]); }
        }
        ruleset G_X {
            legal when mem 0.type in {};
        }
        """
        with self.assertRaises(DslError):
            parse(source)


class CliSurfaceTests(unittest.TestCase):
    def test_strict_custom_without_manifest_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            rules_path = root / "rules" / "custom.ldsl"
            rules_path.parent.mkdir()
            rules_path.write_text(
                """
                domain {
                    types { s32 };
                    q0 { (G_X, [s32]); }
                }
                ruleset G_X {
                    custom when t0 == s32;
                }
                """,
                encoding="utf-8",
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
                stderr
            ):
                rc = cli.main(["--rules", str(rules_path), "check", "--strict"])
            self.assertEqual(
                rc,
                1,
                "strict check accepted a Custom rule without a capability "
                f"manifest; stdout={stdout.getvalue()!r} "
                f"stderr={stderr.getvalue()!r}",
            )

    def test_cli_exposes_codegen_and_conform_and_main_guard(self):
        invocations = {
            "codegen": [
                "--rules",
                "rules/toycpu.ldsl",
                "codegen",
                "--out-dir",
                "out/review-codegen",
            ],
            "conform": [
                "--rules",
                "rules/toycpu.ldsl",
                "conform",
                "--oracle",
                "out/oracle/legalizer-oracle.exe",
                "--out",
                "out/review-conform.json",
            ],
        }
        for command, argv in invocations.items():
            with self.subTest(command=command):
                try:
                    args = cli._build_parser().parse_args(argv)
                except SystemExit as exc:
                    self.fail(
                        f"CLI parser rejected {command!r}: SystemExit({exc.code})"
                    )
                self.assertEqual(args.command, command)

        source = pathlib.Path(cli.__file__).read_text(encoding="utf-8")
        self.assertIn('if __name__ == "__main__":', source)



class ProgrammaticZeroDivisorTests(unittest.TestCase):
    def test_is_multiple_bits_rejects_a_zero_divisor(self):
        with self.assertRaises(DslError) as caught:
            IsMultipleBits(0, 0)
        self.assertEqual(caught.exception.code, "T018")

    def test_next_multiple_rejects_a_zero_size(self):
        with self.assertRaises(DslError) as caught:
            NextMultiple(0, 0)
        self.assertEqual(caught.exception.code, "T019")

    def test_analysis_reports_arithmetic_failures_as_m006(self):
        from legalizer_dsl.checker.analysis import _mutation_issue

        class Boom(TypeExpr):
            idx = 0

            def eval(self, query):
                raise ZeroDivisionError("boom")

        rule = Rule(Action.WIDEN, Always(), 0, Boom())
        issue = _mutation_issue(rule, Query("G_X", (parse_llt("s32"),)))
        self.assertIsNotNone(issue)
        self.assertEqual(issue[0], "M006")
        self.assertIn("boom", issue[1])


if __name__ == "__main__":
    unittest.main()