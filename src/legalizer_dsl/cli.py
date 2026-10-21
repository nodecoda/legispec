# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Command-line front end for the Legalizer rule DSL.

Examples
--------
Explain the expanded rules for one opcode::

    python legalizer-dsl.py --rules rules/toycpu.ldsl explain G_ICMP

Trace first-match evaluation for a query::

    python legalizer-dsl.py --rules rules/toycpu.ldsl trace G_ADD --type s64
    python legalizer-dsl.py --rules rules/toycpu.ldsl trace G_ICMP --type v4s1 --type v4s32
    python legalizer-dsl.py --rules rules/toycpu.ldsl trace G_LOAD --type s8 --type p0 --mem s8:8

The ``trace`` command prints every rule in order, whether it matched, and the
resulting ``LegalizeActionStep``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .diagnostics import Severity
from .model import DslError, MemDesc, Query, parse_llt
from .checker import errors, run_checks, unknowns, warnings
from .coverage import run_coverage
from .interp import legalize
from .parser import parse_defines, parse_file
from .witness import parse_witness_file, run_witnesses


def _parse_mem(spec: str) -> MemDesc:
    if ":" in spec:
        ty_text, align_text = spec.split(":", 1)
        try:
            align = int(align_text)
        except ValueError:
            raise DslError(f"bad --mem alignment {align_text!r}") from None
    else:
        ty_text, align = spec, 1
    return MemDesc(parse_llt(ty_text), align)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="legalizer-dsl",
        description="Parse and evaluate LLVM GlobalISel legality rules.",
    )
    parser.add_argument(
        "--rules",
        "-r",
        required=True,
        help="path to a .ldsl rule file",
    )
    parser.add_argument(
        "--define",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="compile-time configuration value (repeatable)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    explain = sub.add_parser("explain", help="print the expanded rule list")
    explain.add_argument("opcode")

    trace = sub.add_parser("trace", help="show first-match evaluation for a query")
    trace.add_argument("opcode")
    trace.add_argument(
        "--type",
        action="append",
        default=[],
        metavar="LLT",
        help="type at the next type index (repeatable), e.g. s64 or v4s32",
    )
    trace.add_argument(
        "--mem",
        action="append",
        default=[],
        metavar="LLT[:ALIGN_BITS]",
        help="memory descriptor (repeatable), e.g. s8:8",
    )
    trace.add_argument(
        "--imm",
        action="append",
        type=int,
        default=[],
        help="immediate at the next immediate index (repeatable)",
    )

    legalize_cmd = sub.add_parser("legalize", help="run the fixpoint interpreter")
    legalize_cmd.add_argument("opcode")
    legalize_cmd.add_argument("--type", action="append", default=[], metavar="LLT")
    legalize_cmd.add_argument("--mem", action="append", default=[], metavar="LLT[:ALIGN_BITS]")
    legalize_cmd.add_argument("--imm", action="append", type=int, default=[])
    legalize_cmd.add_argument("--max-steps", type=int, default=64)

    check = sub.add_parser("check", help="parse the file and print rule counts")
    check.add_argument("--verbose", action="store_true")
    check.add_argument(
        "--strict",
        action="store_true",
        help="require a domain declaration and run the full static checker",
    )
    check.add_argument(
        "--witness",
        default=None,
        metavar="PATH",
        help="witness corpus used to whitelist declared-unreachable rules",
    )
    check.add_argument(
        "--capabilities",
        default=None,
        metavar="PATH",
        help="capability manifest for Custom/Libcall/Lower rules",
    )
    check.add_argument(
        "--custom-source",
        default=None,
        metavar="PATH",
        help="ToyCPULegalizerInfo.cpp used to verify Custom opcode cases",
    )

    coverage = sub.add_parser("coverage", help="report type/imm slot coverage")

    opcodes = sub.add_parser("opcodes", help="list all opcodes in the file")

    witness = sub.add_parser("witness", help="replay an executable witness corpus")
    witness.add_argument("witness_file", help="path to a .witness file")

    return parser


def _cmd_explain(info, opcode: str) -> int:
    ruleset = info.get(opcode)
    print(f"RuleSet: {', '.join(ruleset.opcodes)} ({len(ruleset.rules)} rules)")
    for index, rule in enumerate(ruleset.rules):
        origin = f"  # {rule.origin}" if rule.origin else ""
        print(f"  [{index}] {rule}{origin}")
    return 0


def _cmd_trace(info, args) -> int:
    types = tuple(parse_llt(text) for text in args.type)
    mems = tuple(_parse_mem(text) for text in args.mem)
    query = Query(args.opcode, types, mems, tuple(args.imm))
    ruleset = info.get(args.opcode)
    step, entries = ruleset.apply(query, trace=True)

    print(f"Query: {query}")
    print(f"RuleSet: {', '.join(ruleset.opcodes)} ({len(ruleset.rules)} rules)")
    for entry in entries:
        mark = "MATCH" if entry.matched else "no match"
        print(f"  [{entry.index}] {entry.rule}  -> {mark}")
    print(f"Result: {step}")
    if step.rule_index is not None:
        rule = entries[-1].rule
        origin = f"  # {rule.origin}" if rule.origin else ""
        print(f"Matched rule: [{step.rule_index}] {rule}{origin}")
    return 0


def _print_coverage(report, path: str) -> int:
    for diagnostic in report.diagnostics:
        print(diagnostic.format(path), file=sys.stderr)
    return 1 if report.errors else 0


def _cmd_check(
    info,
    verbose: bool,
    strict: bool,
    witness_path: str | None,
    capabilities_path: str | None = None,
    custom_source_path: str | None = None,
) -> int:
    counts = info.check()
    print(
        f"rulesets={counts['rulesets']} "
        f"opcodes={counts['opcodes']} "
        f"rules={counts['rules']}"
    )
    if strict:
        witnesses = parse_witness_file(witness_path) if witness_path else None
        diagnostics = run_checks(info, strict=True, witnesses=witnesses)
        if info.domain is not None:
            from .interp import fixpoint_diagnostics

            diagnostics.extend(fixpoint_diagnostics(info))
        if capabilities_path is not None:
            from .checker.capability import load_manifest
            from .checker.capability import run as run_capabilities

            manifest = load_manifest(Path(capabilities_path))
            source_text = (
                Path(custom_source_path).read_text(encoding="utf-8")
                if custom_source_path is not None
                else None
            )
            diagnostics.extend(
                run_capabilities(info, manifest, custom_source=source_text)
            )
        for diagnostic in diagnostics:
            if diagnostic.severity is Severity.INFO:
                continue
            print(diagnostic.format("check"), file=sys.stderr)
        error_count = len(errors(diagnostics))
        warning_count = len(warnings(diagnostics))
        unknown_count = len(unknowns(diagnostics))
        print(
            f"checks: errors={error_count} warnings={warning_count} unknown={unknown_count}"
        )
        return 1 if error_count else 0
    for ruleset in info.rulesets:
        if not ruleset.rules:
            span = f"{ruleset.span}: " if ruleset.span is not None else ""
            print(
                f"{span}WARN: W005: {', '.join(ruleset.opcodes)} has no rules "
                "(everything is Unsupported)",
                file=sys.stderr,
            )
        if verbose:
            print(
                f"  {', '.join(ruleset.opcodes)}: {len(ruleset.rules)} rule(s)"
            )
    return 0


def _cmd_legalize(info, args) -> int:
    types = tuple(parse_llt(text) for text in args.type)
    mems = tuple(_parse_mem(text) for text in args.mem)
    query = Query(args.opcode, types, mems, tuple(args.imm))
    print(legalize(info, query, max_steps=args.max_steps))
    return 0


def _cmd_coverage(info) -> int:
    if info.domain is None:
        print(
            "FAIL: W014: no 'domain' declaration; coverage checks are undefined",
            file=sys.stderr,
        )
        return 1
    report = run_coverage(info)
    total_types = sum(len(e.uncovered_types) for e in report.entries)
    total_imms = sum(len(e.uncovered_imms) for e in report.entries)
    unknown = sum(1 for d in report.diagnostics if d.severity is Severity.UNKNOWN)
    print(
        f"rulesets={len(report.entries)} uncovered_type_slots={total_types} "
        f"uncovered_imm_slots={total_imms} unknown={unknown}"
    )
    return _print_coverage(report, "coverage")


def _cmd_opcodes(info) -> int:
    for opcode in sorted(info.by_opcode):
        ruleset = info.by_opcode[opcode]
        print(f"{opcode:32s} {len(ruleset.rules):3d} rule(s)")
    return 0


def _cmd_witness(info, path: str) -> int:
    witnesses = parse_witness_file(path)
    report = run_witnesses(info, witnesses)
    print(report.summary())
    for failure in report.failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    for opcodes in report.rulesets_without_positive:
        print(f"WARN: no positive witness for {opcodes}", file=sys.stderr)
    for opcodes in report.rulesets_without_negative:
        print(f"WARN: no negative witness for {opcodes}", file=sys.stderr)
    for rule in report.uncovered_rules:
        print(f"WARN: no witness for {rule}", file=sys.stderr)
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        config = parse_defines(args.define)
        info = parse_file(args.rules, config=config)
        if args.command == "explain":
            return _cmd_explain(info, args.opcode)
        if args.command == "trace":
            return _cmd_trace(info, args)
        if args.command == "legalize":
            return _cmd_legalize(info, args)
        if args.command == "check":
            rules_path = Path(args.rules).resolve()
            witness_path = args.witness
            if args.strict and witness_path is None:
                candidate = (
                    rules_path.parent.parent / "witness" / f"{rules_path.stem}.witness"
                )
                if candidate.exists():
                    witness_path = str(candidate)
            capabilities_path = args.capabilities
            if args.strict and capabilities_path is None:
                root = rules_path.parent.parent
                candidates = (
                    root / "capabilities" / f"{rules_path.stem}-capabilities.json",
                    root / f"{rules_path.stem}-capabilities.json",
                    root / "capabilities" / "toycpu-capabilities.json",
                    root / "capabilities.json",
                )
                for candidate in candidates:
                    if candidate.exists():
                        capabilities_path = str(candidate)
                        break
            return _cmd_check(
                info,
                args.verbose,
                args.strict,
                witness_path,
                capabilities_path,
                args.custom_source,
            )
        if args.command == "coverage":
            return _cmd_coverage(info)
        if args.command == "opcodes":
            return _cmd_opcodes(info)
        if args.command == "witness":
            return _cmd_witness(info, args.witness_file)
        parser.error(f"unknown command {args.command!r}")
    except DslError as exc:
        print(f"{args.rules}:{exc}", file=sys.stderr)
        return 2
    return 1