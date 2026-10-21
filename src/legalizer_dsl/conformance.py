#!/usr/bin/env python3
# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Differential conformance runner for the Legalizer DSL.

The oracle is the real ToyCPU C++ implementation.  This runner observes only
one step:

    (Action, TypeIdx, NewType) = LegalizerInfo::getAction(LegalityQuery)

It never reimplements LegalizerHelper rewrites.

Definitions:
    D = finite domain declared by the LDSL ``domain`` block, enumerated for
        every opcode aliased into each ruleset.
    R = closure(Q0) under one-step legalize actions.

The compared set is D union R, minus explicitly recorded UNKNOWN entries.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional

TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from legalizer_dsl.model import LegalizerInfo, Query  # noqa: E402
from legalizer_dsl.parser import parse_defines, parse_file  # noqa: E402
from legalizer_dsl.checker.analysis import shape  # noqa: E402
from legalizer_dsl.interp import TERMINAL_ACTIONS  # noqa: E402
from itertools import product  # noqa: E402


SCHEMA = "legalizer-dsl-conformance-v1"
MAX_DIVERGENCES = 200


def _config_record(config: Optional[dict[str, object]]) -> dict[str, object]:
    if not config:
        return {}
    return {
        name: value if isinstance(value, (bool, int)) else str(value)
        for name, value in sorted(config.items())
    }


@dataclass(frozen=True)
class Observation:
    action: str
    type_idx: int
    new_type: Optional[str]

    def as_dict(self) -> dict:
        return {
            "action": self.action,
            "type_idx": self.type_idx,
            "new_type": self.new_type,
        }


@dataclass
class ComparisonReport:
    queries: int = 0
    compared: int = 0
    divergences: list[dict] = field(default_factory=list)
    divergence_count: int = 0
    rules_matched: set[tuple[int, int]] = field(default_factory=set)
    oracle_errors: int = 0
    stdout_lines: int = 0

    @property
    def ok(self) -> bool:
        return self.divergence_count == 0 and self.oracle_errors == 0


def _query_key(query: Query) -> tuple:
    return (query.opcode, query.types, query.mems, query.imms)


def _json_record(index: int, query: Query) -> dict:
    return {
        "id": index,
        "opcode": query.opcode,
        "types": [str(ty) for ty in query.types],
        "mems": [
            {"type": str(mem.memory_ty), "align": mem.align_bits}
            for mem in query.mems
        ],
        "imms": list(query.imms),
    }


def iter_d_queries(info: LegalizerInfo) -> Iterator[Query]:
    """Enumerate D for every opcode, including aliases in a ruleset."""
    if info.domain is None:
        raise ValueError("rules file has no domain block")
    for ruleset in info.rulesets:
        arity, mem_arity, imm_arity = shape(info, ruleset)
        if arity is None:
            continue
        for opcode in ruleset.opcodes:
            for types in product(info.domain.types, repeat=arity):
                for mems in product(info.domain.mems, repeat=mem_arity):
                    for imms in product(info.domain.imms, repeat=imm_arity):
                        query = Query(opcode, types, mems, imms)
                        if info.domain.filter and not info.domain.filter.eval(query):
                            continue
                        yield query


def closure_queries(
    info: LegalizerInfo,
    max_steps: int = 64,
    boundary: Optional[list[Query]] = None,
) -> list[Query]:
    """Compute R = closure(Q0) under the Python policy interpreter.

    R is restricted to D.  A successor that violates the domain filter is a
    boundary state: it is recorded in *boundary* (when given) and not
    expanded, because the opcode contract no longer describes it.  Without
    this cut, a projection such as G_CONCAT_VECTORS' bitcast (result v8s8 ->
    v2s32 while the source stays v4s8) would be re-legalized as if it were a
    real concat and would run off the end of the mutation's definition.
    """
    if info.domain is None:
        raise ValueError("rules file has no domain block")

    closure: list[Query] = []
    seen: set[tuple] = set()
    boundary_seen: set[tuple] = set()
    queue: list[tuple[Query, int]] = [(query, 0) for query in info.domain.q0]
    while queue:
        query, depth = queue.pop(0)
        if not info.domain.accepts(query):
            key = _query_key(query)
            if boundary is not None and key not in boundary_seen:
                boundary_seen.add(key)
                boundary.append(query)
            continue
        key = _query_key(query)
        if key in seen:
            continue
        seen.add(key)
        closure.append(query)

        if depth >= max_steps:
            continue
        step, _ = info.action(query)
        if step.action in TERMINAL_ACTIONS:
            continue
        if step.new_type is None or step.type_idx >= len(query.types):
            continue
        types = list(query.types)
        types[step.type_idx] = step.new_type
        queue.append(
            (Query(query.opcode, tuple(types), query.mems, query.imms), depth + 1)
        )
    return closure


def _closure_outside_d(info: LegalizerInfo, queries: Iterable[Query]) -> list[Query]:
    """Return R members that D cannot represent (diagnostic, not policy)."""
    if info.domain is None:
        return list(queries)
    domain_types = set(info.domain.types)
    domain_mems = set(info.domain.mems)
    domain_imms = set(info.domain.imms)
    shapes: dict[str, tuple[int, int, int]] = {}
    for ruleset in info.rulesets:
        arity, mem_arity, imm_arity = shape(info, ruleset)
        for opcode in ruleset.opcodes:
            if arity is not None:
                shapes[opcode] = (arity, mem_arity, imm_arity)

    outside: list[Query] = []
    for query in queries:
        expected = shapes.get(query.opcode)
        if expected is None:
            outside.append(query)
            continue
        arity, mem_arity, imm_arity = expected
        if (
            len(query.types) != arity
            or len(query.mems) != mem_arity
            or len(query.imms) != imm_arity
            or any(ty not in domain_types for ty in query.types)
            or any(mem not in domain_mems for mem in query.mems)
            or any(imm not in domain_imms for imm in query.imms)
            or (
                info.domain.filter is not None
                and not info.domain.filter.eval(query)
            )
        ):
            outside.append(query)
    return outside


def python_observation(info: LegalizerInfo, query: Query) -> tuple[Observation, Optional[tuple[int, int]]]:
    ruleset = info.get(query.opcode)
    step, _ = ruleset.apply(query)
    observation = Observation(
        action=step.action.value,
        type_idx=step.type_idx,
        new_type=str(step.new_type) if step.new_type is not None else None,
    )
    rule_mark = None
    if step.rule_index is not None:
        rule_mark = (id(ruleset), step.rule_index)
    return observation, rule_mark


def _difference_reason(py: Observation, cxx: Observation) -> str:
    if py.action != cxx.action:
        return f"action: python={py.action!r} oracle={cxx.action!r}"
    if py.type_idx != cxx.type_idx:
        return f"type_idx: python={py.type_idx} oracle={cxx.type_idx}"
    return f"new_type: python={py.new_type!r} oracle={cxx.new_type!r}"


def _write_input(path: Path, queries: Iterable[Query]) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for index, query in enumerate(queries):
            record = _json_record(index, query)
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            data = line.encode("utf-8")
            digest.update(data)
            stream.write(line)
            count += 1
    return count, digest.hexdigest()


def _parse_oracle_line(line: str) -> dict:
    value = json.loads(line)
    if not isinstance(value, dict):
        raise ValueError("oracle output line is not an object")
    return value


def compare(
    info: LegalizerInfo,
    oracle: Path,
    input_path: Path,
    queries: Iterable[Query],
    report: ComparisonReport,
) -> None:
    process = subprocess.Popen(
        [str(oracle), "--input", str(input_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        bufsize=1,
    )
    assert process.stdout is not None

    expected_iter = iter(queries)
    try:
        for line in process.stdout:
            report.stdout_lines += 1
            line = line.strip()
            if not line:
                continue
            try:
                expected = next(expected_iter)
            except StopIteration:
                report.divergence_count += 1
                report.divergences.append(
                    {
                        "kind": "extra-oracle-line",
                        "line": report.stdout_lines,
                        "oracle": line,
                    }
                )
                continue

            report.queries += 1
            py, rule_mark = python_observation(info, expected)
            if rule_mark is not None:
                report.rules_matched.add(rule_mark)

            try:
                oracle_value = _parse_oracle_line(line)
            except Exception as exc:
                report.oracle_errors += 1
                report.divergence_count += 1
                if len(report.divergences) < MAX_DIVERGENCES:
                    report.divergences.append(
                        {
                            "id": report.queries - 1,
                            "query": str(expected),
                            "kind": "malformed-oracle-json",
                            "reason": str(exc),
                            "oracle_raw": line,
                        }
                    )
                continue

            if "error" in oracle_value:
                report.oracle_errors += 1
                report.divergence_count += 1
                if len(report.divergences) < MAX_DIVERGENCES:
                    report.divergences.append(
                        {
                            "id": report.queries - 1,
                            "query": str(expected),
                            "kind": "oracle-error",
                            "reason": oracle_value.get("error"),
                        }
                    )
                continue

            raw_new_type = oracle_value.get("new_type")
            cxx = Observation(
                action=str(oracle_value.get("action")),
                type_idx=int(oracle_value.get("type_idx", -1)),
                new_type=None if raw_new_type is None else str(raw_new_type),
            )
            if py != cxx:
                report.divergence_count += 1
                if len(report.divergences) < MAX_DIVERGENCES:
                    report.divergences.append(
                        {
                            "id": report.queries - 1,
                            "query": str(expected),
                            "kind": "observation-mismatch",
                            "reason": _difference_reason(py, cxx),
                            "python": py.as_dict(),
                            "oracle": cxx.as_dict(),
                        }
                    )

        for expected in expected_iter:
            report.queries += 1
            report.divergence_count += 1
            if len(report.divergences) < MAX_DIVERGENCES:
                report.divergences.append(
                    {
                        "id": report.queries - 1,
                        "query": str(expected),
                        "kind": "missing-oracle-line",
                    }
                )
    finally:
        process.stdout.close()
        stderr = process.stderr.read() if process.stderr is not None else ""
        if process.stderr is not None:
            process.stderr.close()
        returncode = process.wait()
        if returncode != 0:
            report.oracle_errors += 1
            report.divergence_count += 1
            if len(report.divergences) < MAX_DIVERGENCES:
                report.divergences.append(
                    {
                        "kind": "oracle-exit",
                        "returncode": returncode,
                        "stderr": stderr.strip()[-4000:],
                    }
                )


def _file_sha256(path: Path) -> str:
    """Hash the oracle binary so a rebuilt oracle invalidates old reports."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()

def _git_commit(path: Path) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _rule_coverage(info: LegalizerInfo, matched: set[tuple[int, int]]) -> tuple[int, list[dict]]:
    missing: list[dict] = []
    matched_count = 0
    for ruleset_id, index in matched:
        if any(id(ruleset) == ruleset_id for ruleset in info.rulesets):
            matched_count += 1

    for ruleset in info.rulesets:
        for index, rule in enumerate(ruleset.rules):
            if (id(ruleset), index) not in matched:
                missing.append(
                    {
                        "ruleset": ruleset.opcodes[0],
                        "index": index,
                        "rule": str(rule),
                    }
                )
    return matched_count, missing


def build_report(
    info: LegalizerInfo,
    rules_path: Path,
    oracle_path: Path,
    input_path: Path,
    query_count: int,
    input_sha256: str,
    closure: list[Query],
    closure_outside: list[Query],
    closure_boundary: list[Query],
    comparison: ComparisonReport,
    llvm_build: str = os.environ.get("LEGALIZER_DSL_LLVM_BUILD", "F:/llvm/build-toycpu"),
    config: Optional[dict[str, object]] = None,
) -> dict:
    matched_count, missing_rules = _rule_coverage(info, comparison.rules_matched)
    total_rules = sum(len(ruleset.rules) for ruleset in info.rulesets)
    llvm_src = os.environ.get("LEGALIZER_DSL_LLVM_SRC", "F:/llvm/llvm-project")
    return {
        "schema": SCHEMA,
        "reference": {
            "llvm_src": llvm_src,
            "llvm_commit": _git_commit(Path(llvm_src)),
            "llvm_build": llvm_build,
            "oracle": str(oracle_path),
            "oracle_sha256": _file_sha256(oracle_path),
            "oracle_size": oracle_path.stat().st_size,
            "oracle_entry": "LegalizerInfo::getAction(LegalityQuery)",
        },
        "inputs": {
            "rules": str(rules_path),
            "rules_sha256": _file_sha256(rules_path),
            "jsonl": str(input_path),
            # sha256 covers the generated query corpus, not the rules file.
            "sha256": input_sha256,
            "config": _config_record(config),
        },
        "domain": {
            "name": "D union R",
            "query_count": query_count,
            "closure_query_count": len(closure),
            "closure_outside_d": [str(query) for query in closure_outside[:MAX_DIVERGENCES]],
            "closure_outside_d_count": len(closure_outside),
            "closure_boundary": [str(query) for query in closure_boundary[:MAX_DIVERGENCES]],
            "closure_boundary_count": len(closure_boundary),
        },
        "comparison": {
            "queries_compared": comparison.queries,
            "oracle_lines": comparison.stdout_lines,
            "divergence_count": comparison.divergence_count,
            "oracle_errors": comparison.oracle_errors,
        },
        "coverage": {
            "rulesets": len(info.rulesets),
            "opcodes": len(info.by_opcode),
            "rules": total_rules,
            "rules_matched": matched_count,
            "rules_unmatched": missing_rules,
        },
        "divergences": comparison.divergences,
        "divergence_policy": {
            "spec-wrong": 0,
            "cxx-wrong": 0,
            "unspecified": 0,
            "unclassified": comparison.divergence_count,
            "note": "Never auto-classify an oracle difference; record it in spec/decisions.md.",
        },
        "ok": comparison.ok,
        "domain_closure_ok": not closure_outside,
    }


def main(argv: Optional[list[str]] = None) -> int:
    here = Path(__file__).resolve()
    repo = here.parents[1]
    out_dir = Path(os.environ.get("LEGALIZER_DSL_OUT", repo / "out"))
    default_rules = repo / "rules" / "toycpu.ldsl"
    default_oracle = out_dir / "oracle" / "legalizer-oracle.exe"
    default_out = out_dir / "conformance-report.json"

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules", default=str(default_rules), help="LDSL rules file")
    parser.add_argument(
        "--define",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="compile-time configuration value (repeatable)",
    )
    parser.add_argument("--oracle", default=str(default_oracle), help="oracle executable")
    parser.add_argument("--out", default=str(default_out), help="JSON report path")
    parser.add_argument(
        "--domain",
        default="D",
        choices=["D"],
        help="query domain; D is the finite declared domain",
    )
    parser.add_argument(
        "--closure",
        default="R",
        choices=["R", "none"],
        help="include closure(Q0) under legalize",
    )
    parser.add_argument("--limit", type=int, default=0, help="limit queries for smoke runs")
    parser.add_argument(
        "--llvm-build",
        default=os.environ.get("LEGALIZER_DSL_LLVM_BUILD", "F:/llvm/build-toycpu"),
        help="LLVM build directory recorded in the report metadata",
    )
    args = parser.parse_args(argv)

    rules_path = Path(args.rules)
    oracle_path = Path(args.oracle)
    out_path = Path(args.out)
    if not rules_path.is_file():
        print(f"error: rules file not found: {rules_path}", file=sys.stderr)
        return 2
    if not oracle_path.is_file():
        print(f"error: oracle not found: {oracle_path}", file=sys.stderr)
        return 2

    config = parse_defines(args.define)
    info = parse_file(rules_path, config=config)
    if info.domain is None:
        print("error: W014: no domain block", file=sys.stderr)
        return 2

    boundary: list[Query] = []
    closure = (
        closure_queries(info, boundary=boundary) if args.closure == "R" else []
    )
    outside = _closure_outside_d(info, closure)
    limited_queries: Optional[list[Query]] = None
    if args.limit > 0:
        # Keep the smoke set deterministic and include R-only entries first.
        outside = outside[: args.limit]
        limited_queries = list(outside)
        remaining = args.limit - len(limited_queries)
        if remaining > 0:
            limited_queries.extend(list(iter_d_queries(info))[:remaining])

    def make_queries() -> Iterable[Query]:
        if limited_queries is not None:
            return iter(limited_queries)
        return _combined_queries(info, closure, outside)

    with tempfile.TemporaryDirectory(prefix="legalizer-conformance-") as temp_dir:
        input_path = Path(temp_dir) / "queries.jsonl"
        query_count, input_sha256 = _write_input(input_path, make_queries())
        comparison = ComparisonReport()
        compare(info, oracle_path, input_path, make_queries(), comparison)
        report = build_report(
            info,
            rules_path,
            oracle_path,
            input_path,
            query_count,
            input_sha256,
            closure,
            outside,
            boundary,
            comparison,
            config=config,
            llvm_build=args.llvm_build,
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(
        f"queries={report['comparison']['queries_compared']} "
        f"divergences={report['comparison']['divergence_count']} "
        f"rules={report['coverage']['rules_matched']}/{report['coverage']['rules']} "
        f"sha256={input_sha256[:16]}..."
    )
    if report["coverage"]["rules_unmatched"]:
        print(
            f"unmatched_rules={len(report['coverage']['rules_unmatched'])}",
            file=sys.stderr,
        )
    if outside:
        print(
            f"closure_outside_d={len(outside)} "
            f"(listed in {out_path})",
            file=sys.stderr,
        )
    if boundary:
        print(
            f"closure_boundary={len(boundary)} "
            f"(successors outside D; listed in {out_path})",
            file=sys.stderr,
        )
    if not report["ok"]:
        print(f"FAIL: see {out_path}", file=sys.stderr)
        return 1
    return 0


def _combined_queries(
    info: LegalizerInfo, closure: list[Query], outside: list[Query]
) -> Iterable[Query]:
    """Yield D, then R members not representable by D (normally none)."""
    yield from iter_d_queries(info)
    if outside:
        yield from outside


if __name__ == "__main__":
    raise SystemExit(main())