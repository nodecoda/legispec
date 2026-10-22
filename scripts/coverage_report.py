#!/usr/bin/env python3
# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0.
# See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0
#
# ===----------------------------------------------------------------------===##

"""Measure corpus coverage of the declared legalizer domains.

Corpus sources:

* ``witness/*.witness``: executable specification queries (primary corpus);
* ``*.mir``: real MIR type shapes, normalized to the DSL's deduplicated type
  slots.  The normalizer is intentionally conservative: a line that cannot be
  mapped to the ruleset arity is counted as a parse error, never as a pass.

For every target the report records:

* how many corpus queries fall inside the declared domain D;
* how many are boundary queries (known opcode, outside D);
* which rules are never reached by the corpus;
* which domain type/mem/imm values are not observed in the corpus;
* which corpus values are outside the declared domain.

The report is deterministic and contains no timestamps.  ``--update-baseline``
writes the ratchet baseline; a later report fails if a target's coverage ratio
falls below that baseline without an explicit ``--waiver``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from legispec import witness as witness_mod  # noqa: E402
from legispec.checker.analysis import shape  # noqa: E402
from legispec.model import LLT, MemDesc, Query, parse_llt  # noqa: E402
from legispec.parser import parse_file  # noqa: E402

SCHEMA = "legispec-coverage-v1"

TARGETS = {
    "toycpu": "rules/toycpu.lspec",
    "aarch64-vector": "rules/aarch64-vector.lspec",
}

# A MIR file describes one target's real shapes.  Do not feed every corpus
# file to every target: cross-target shapes would inflate coverage and hide
# gaps.  Unknown targets fall back to every .mir file.
TARGET_MIR = {
    "toycpu": ("toycpu-*.mir",),
    "aarch64-vector": ("aarch64-*.mir", "concat-*.mir"),
}

_OPCODE_RE = re.compile(r"\b(G_[A-Z0-9_]+)\b")
_TYPE_ANNOTATION_RE = re.compile(r"%\d+:_\(([^)]+)\)")
_DEST_RE = re.compile(r"^\s*%\d+:_\(([^)]+)\)\s*=")
_MIR_TYPE_RE = re.compile(r"^(?:<(\d+)\s*x\s*)?(i|f|p|s)(\d+)(?::(\d+))?>?$")
_MEM_RE = re.compile(r"::\s*\((?:load|store)\s+([^)]*)\)")
_ALIGN_RE = re.compile(r"align\s+(\d+)")
_CONST_RE = re.compile(r"\bG_CONSTANT\s+i\d+\s+(-?\d+)\b")


def _mir_type_to_dsl(text: str) -> Optional[str]:
    text = text.strip()
    match = _MIR_TYPE_RE.match(text)
    if not match:
        return None
    lanes, kind, width, ptr_bits = match.groups()
    if kind == "i":
        scalar = f"s{width}"
    elif kind == "s":
        scalar = f"s{width}"
    elif kind == "f":
        scalar = f"f{width}"
    else:
        scalar = f"p{width}"
        if ptr_bits:
            scalar += f":{ptr_bits}"
    if lanes:
        return f"v{lanes}{scalar}"
    return scalar


def _mem_key(mem: MemDesc) -> tuple[str, int]:
    return (str(mem.memory_ty), mem.align_bits)


def _type_key(ty: LLT) -> str:
    return str(ty)


def _extract_mir_queries(text: str, info, source: str) -> tuple[list[Query], list[dict]]:
    queries: list[Query] = []
    errors: list[dict] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#") or stripped.startswith(";"):
            continue
        opcode_match = _OPCODE_RE.search(line)
        if not opcode_match:
            continue
        opcode = opcode_match.group(1)
        if opcode not in info.by_opcode:
            continue
        ruleset = info.get(opcode)
        arity, mem_arity, imm_arity = shape(info, ruleset)
        if arity is None:
            continue

        dest_match = _DEST_RE.match(line)
        dest = _mir_type_to_dsl(dest_match.group(1)) if dest_match else None
        # Only annotations on the right-hand side are operands.  Re-including
        # the destination would shift every multi-slot query by one.
        rhs = line.split("=", 1)[1] if "=" in line else line
        operand_types = [
            _mir_type_to_dsl(value)
            for value in _TYPE_ANNOTATION_RE.findall(rhs)
        ]
        operand_types = [value for value in operand_types if value is not None]
        candidates: list[str] = []
        if dest is not None:
            candidates.append(dest)
        candidates.extend(operand_types)
        if len(candidates) < arity or not candidates:
            errors.append({
                "source": source,
                "line": lineno,
                "opcode": opcode,
                "reason": f"need {arity} type slot(s), found {len(candidates)}",
                "text": line.strip(),
            })
            continue
        type_texts = candidates[:arity]
        try:
            types = tuple(parse_llt(text) for text in type_texts)
        except ValueError as exc:
            errors.append({
                "source": source,
                "line": lineno,
                "opcode": opcode,
                "reason": str(exc),
                "text": line.strip(),
            })
            continue

        mems: tuple[MemDesc, ...] = ()
        if mem_arity:
            mem_match = _MEM_RE.search(line)
            align_match = _ALIGN_RE.search(line)
            if not mem_match:
                errors.append({
                    "source": source,
                    "line": lineno,
                    "opcode": opcode,
                    "reason": "missing memory descriptor",
                    "text": line.strip(),
                })
                continue
            mem_type_token = mem_match.group(1).strip().lstrip("(").split()[0]
            mem_type_text = _mir_type_to_dsl(mem_type_token)
            if mem_type_text is None:
                errors.append({
                    "source": source,
                    "line": lineno,
                    "opcode": opcode,
                    "reason": f"bad memory type {mem_match.group(1)!r}",
                    "text": line.strip(),
                })
                continue
            try:
                mem_ty = parse_llt(mem_type_text)
            except ValueError as exc:
                errors.append({
                    "source": source,
                    "line": lineno,
                    "opcode": opcode,
                    "reason": str(exc),
                    "text": line.strip(),
                })
                continue
            align = int(align_match.group(1)) * 8 if align_match else 1
            mems = (MemDesc(mem_ty, align),)

        imms: tuple[int, ...] = ()
        if imm_arity:
            const_match = _CONST_RE.search(line)
            if const_match:
                values = [int(const_match.group(1))]
            else:
                values = []
            if not values:
                errors.append({
                    "source": source,
                    "line": lineno,
                    "opcode": opcode,
                    "reason": "missing immediate value",
                    "text": line.strip(),
                })
                continue
            imms = tuple(values[:imm_arity])
            if len(imms) < imm_arity:
                imms = imms + (0,) * (imm_arity - len(imms))

        queries.append(Query(opcode, types, mems, imms))
    return queries, errors


def _load_witness_queries(paths: Iterable[Path], info) -> tuple[list[Query], list[str]]:
    queries: list[Query] = []
    files: list[str] = []
    for path in sorted(paths):
        witnesses = witness_mod.parse_witness_file(path)
        files.append(_relative(path))
        for item in witnesses:
            if item.query.opcode in info.by_opcode:
                queries.append(item.query)
    return queries, files


def _load_mir_queries(paths: Iterable[Path], info) -> tuple[list[Query], list[str], list[dict]]:
    queries: list[Query] = []
    files: list[str] = []
    errors: list[dict] = []
    for path in sorted(paths):
        files.append(_relative(path))
        text = path.read_text(encoding="utf-8", errors="replace")
        found, problems = _extract_mir_queries(text, info, _relative(path))
        queries.extend(found)
        errors.extend(problems)
    return queries, files, errors


def _dedupe(queries: Iterable[Query]) -> list[Query]:
    seen = set()
    result = []
    for query in queries:
        key = (query.opcode, tuple(str(t) for t in query.types),
               tuple(_mem_key(m) for m in query.mems), query.imms)
        if key in seen:
            continue
        seen.add(key)
        result.append(query)
    return result


def _in_declared_domain(info, query: Query) -> bool:
    """Membership in the finite domain, not just satisfaction of its filter.

    ``Domain.accepts`` only evaluates the optional shape filter.  Coverage must
    also reject values that never appear in the declared type/mem/imm sets,
    otherwise every arbitrary MIR spelling would look like "inside D".
    """
    domain = info.domain
    if domain is None:
        return False
    type_values = {_type_key(ty) for ty in domain.types}
    mem_values = {_mem_key(mem) for mem in domain.mems}
    imm_values = set(domain.imms)
    if any(_type_key(ty) not in type_values for ty in query.types):
        return False
    if any(_mem_key(mem) not in mem_values for mem in query.mems):
        return False
    if any(value not in imm_values for value in query.imms):
        return False
    return domain.accepts(query)


def _analyze_target(info, corpus: list[Query], errors: list[dict], sources: dict) -> dict:
    known = [q for q in corpus if q.opcode in info.by_opcode]
    unknown_opcode = len(corpus) - len(known)
    in_domain = [q for q in known if _in_declared_domain(info, q)]
    boundary = [q for q in known if not _in_declared_domain(info, q)]

    matched: set[tuple[int, int]] = set()
    observation_errors = 0
    for query in in_domain:
        ruleset = info.get(query.opcode)
        try:
            step, _ = ruleset.apply(query)
        except Exception:
            observation_errors += 1
            continue
        if step.rule_index is not None:
            matched.add((id(ruleset), step.rule_index))

    unmatched = []
    for ruleset in info.rulesets:
        for index, rule in enumerate(ruleset.rules):
            if (id(ruleset), index) not in matched:
                unmatched.append({
                    "ruleset": ", ".join(ruleset.opcodes),
                    "index": index,
                    "rule": str(rule),
                })

    observed_types = {str(t) for q in known for t in q.types}
    observed_mems = {_mem_key(m) for q in known for m in q.mems}
    observed_imms = {value for q in known for value in q.imms}
    domain_types = {str(t) for t in (info.domain.types if info.domain else ())}
    domain_mems = {_mem_key(m) for m in (info.domain.mems if info.domain else ())}
    domain_imms = set(info.domain.imms) if info.domain else set()

    rules_total = sum(len(rs.rules) for rs in info.rulesets)
    ratio = (len(in_domain) / len(known)) if known else None
    return {
        "rules": sources.get("rules"),
        "corpus": {
            **sources,
            "queries": len(corpus),
            "known_opcode_queries": len(known),
            "unknown_opcode_queries": unknown_opcode,
            "in_domain": len(in_domain),
            "boundary": len(boundary),
            "parse_errors": len(errors),
            "parse_error_examples": errors[:20],
        },
        "coverage": {
            "ratio": ratio,
            "rules_total": rules_total,
            "rules_matched": len(matched),
            "rules_unmatched": unmatched,
            "observation_errors": observation_errors,
        },
        "slots": {
            "types": {
                "domain": sorted(domain_types),
                "observed": sorted(observed_types),
                "domain_not_observed": sorted(domain_types - observed_types),
                "outside_domain": sorted(observed_types - domain_types),
            },
            "mems": {
                "domain": [f"{t}:{a}" for t, a in sorted(domain_mems)],
                "observed": [f"{t}:{a}" for t, a in sorted(observed_mems)],
                "domain_not_observed": [
                    f"{t}:{a}" for t, a in sorted(domain_mems - observed_mems)
                ],
                "outside_domain": [
                    f"{t}:{a}" for t, a in sorted(observed_mems - domain_mems)
                ],
            },
            "imms": {
                "domain": sorted(domain_imms),
                "observed": sorted(observed_imms),
                "domain_not_observed": sorted(domain_imms - observed_imms),
                "outside_domain": sorted(observed_imms - domain_imms),
            },
        },
    }


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _corpus_paths(args, target: str) -> tuple[list[Path], list[Path]]:
    witness_dir = ROOT / args.witness_dir
    if witness_dir.is_dir():
        if target == "toycpu":
            witnesses = [witness_dir / "toycpu.witness"]
        else:
            witnesses = sorted(witness_dir.glob("aarch64-*.witness"))
    else:
        witnesses = []

    mir: list[Path] = []
    for value in args.mir:
        path = Path(value)
        if path.is_file():
            mir.append(path)
    corpus_dir = ROOT / args.corpus
    if corpus_dir.is_dir():
        # A target without an explicit pattern must not ingest unrelated
        # targets' corpus files.  Add a mapping when adding a target.
        for pattern in TARGET_MIR.get(target, ()):
            mir.extend(sorted(corpus_dir.rglob(pattern)))

    # Keep the explicit --mir files first, then deterministic unique corpus
    # files.  A pattern overlap must not duplicate queries.
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in mir:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return [p for p in witnesses if p.is_file()], unique


def _write_docs(path: Path, report: dict) -> None:
    lines = [
        "# Corpus coverage",
        "",
        "Generated by `python scripts/coverage_report.py`.  Do not edit by hand.",
        "",
        "`ratio` is the fraction of known-opcode corpus queries that fall inside",
        "the declared domain D.  Boundary queries are outside D and are not",
        "evidence.  `domain_not_observed` lists declared values the corpus never",
        "exercised; `outside_domain` lists corpus values the rules do not cover.",
        "",
        "The ratchet baseline records absolute metrics, not only the ratio:",
        "`ratio`, `in_domain`, `queries`, `rules_matched`, and `rules_total`",
        "must not decrease, and the target set must match exactly.",
        "",
        "| target | queries | in D | boundary | ratio | rules matched |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for target, data in sorted(report["targets"].items()):
        corpus = data["corpus"]
        coverage = data["coverage"]
        ratio = coverage["ratio"]
        ratio_text = "n/a" if ratio is None else f"{ratio:.4f}"
        lines.append(
            f"| `{target}` | {corpus['known_opcode_queries']} | "
            f"{corpus['in_domain']} | {corpus['boundary']} | {ratio_text} | "
            f"{coverage['rules_matched']}/{coverage['rules_total']} |"
        )
    lines.extend(["", "## Ratchet", ""])
    ratchet = report.get("ratchet", {})
    lines.append(f"- baseline: `{ratchet.get('baseline')}`")
    lines.append(f"- status: **{ratchet.get('status', 'n/a')}**")
    if ratchet.get("waiver"):
        lines.append(f"- waiver: {ratchet['waiver']}")
    for problem in ratchet.get("problems", []):
        lines.append(f"- {problem}")
    lines.extend(["", "## Details"])
    for target, data in sorted(report["targets"].items()):
        slots = data["slots"]
        lines.extend([
            "",
            f"### `{target}`",
            "",
            f"- rules: `{data['rules']}`",
            f"- unmatched rules: {len(data['coverage']['rules_unmatched'])}",
            f"- parse errors: {data['corpus']['parse_errors']}",
            f"- domain types not observed: {len(slots['types']['domain_not_observed'])}",
            f"- domain mems not observed: {len(slots['mems']['domain_not_observed'])}",
            f"- domain imms not observed: {len(slots['imms']['domain_not_observed'])}",
        ])
        if data["coverage"]["rules_unmatched"]:
            lines.append("")
            lines.append("Unmatched rules:")
            for item in data["coverage"]["rules_unmatched"]:
                lines.append(
                    f"- `{item['ruleset']}` rule {item['index']}: {item['rule']}"
                )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def metric_snapshot(data: dict) -> dict:
    corpus = data["corpus"]
    coverage = data["coverage"]
    return {
        "ratio": coverage["ratio"],
        "in_domain": corpus["in_domain"],
        "queries": corpus["queries"],
        "rules_matched": coverage["rules_matched"],
        "rules_total": coverage["rules_total"],
    }


def ratchet_problems(
    baseline_targets: dict, current_targets: dict
) -> list[str]:
    """Compare every pinned metric without shrinking a multi-target baseline.

    A baseline may describe more targets than a single-target run reports.
    Missing baseline entries fail; extra baseline entries do not.
    """

    problems: list[str] = []
    missing = set(current_targets) - set(baseline_targets)
    if missing:
        problems.append(
            "baseline has no entry for: " + ", ".join(sorted(missing))
        )
    for target, data in current_targets.items():
        previous = baseline_targets.get(target)
        if previous is None:
            continue
        if isinstance(previous, (int, float)):
            previous = {"ratio": previous}
        if not isinstance(previous, dict):
            problems.append(f"{target}: baseline entry is not an object")
            continue
        current = metric_snapshot(data)
        for key in ("ratio", "in_domain", "queries", "rules_matched", "rules_total"):
            old_value = previous.get(key)
            current_value = current.get(key)
            if old_value is None or current_value is None:
                continue
            if current_value + 1e-12 < old_value:
                problems.append(
                    f"{target}: {key} {current_value} < baseline {old_value}"
                )
    return problems


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", action="append", choices=sorted(TARGETS),
                        help="target(s) to report; default: all")
    parser.add_argument("--corpus", default="examples",
                        help="directory searched recursively for .mir files")
    parser.add_argument("--mir", action="append", default=[],
                        help="additional MIR file (repeatable)")
    parser.add_argument("--witness-dir", default="witness")
    parser.add_argument("--out", default="out/coverage-report.json")
    parser.add_argument("--docs", default="docs/COVERAGE.md")
    parser.add_argument("--baseline", default="coverage/coverage-baseline.json")
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--no-docs", action="store_true",
                        help="do not rewrite docs/COVERAGE.md")
    parser.add_argument("--waiver", help="reason for an allowed coverage decrease")
    args = parser.parse_args(argv)

    selected = args.target or sorted(TARGETS)
    report = {
        "schema": SCHEMA,
        "targets": {},
        "ratchet": {
            "baseline": args.baseline,
            "status": "SKIP",
            "problems": [],
            "waiver": args.waiver,
        },
    }

    for target in selected:
        rules = ROOT / TARGETS[target]
        info = parse_file(rules)
        witness_paths, mir_paths = _corpus_paths(args, target)
        witness_queries, witness_files = _load_witness_queries(witness_paths, info)
        mir_queries, mir_files, errors = _load_mir_queries(mir_paths, info)
        corpus = _dedupe(witness_queries + mir_queries)
        report["targets"][target] = _analyze_target(
            info,
            corpus,
            errors,
            {
                "rules": _relative(rules),
                "witness_files": witness_files,
                "mir_files": mir_files,
            },
        )

    baseline_path = ROOT / args.baseline
    if args.update_baseline:
        baseline_targets: dict = {}
        if baseline_path.is_file():
            try:
                previous = json.loads(baseline_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                report["ratchet"]["status"] = "FAIL"
                report["ratchet"]["problems"] = [
                    f"baseline unreadable: {exc}"
                ]
                out_path = ROOT / args.out
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(
                    json.dumps(report, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
                print(f"ratchet: FAIL")
                print(f"report: {_relative(out_path)}")
                print(f"  ! baseline unreadable: {exc}")
                return 1
            if not isinstance(previous, dict) or not isinstance(
                previous.get("targets"), dict
            ):
                report["ratchet"]["status"] = "FAIL"
                report["ratchet"]["problems"] = [
                    "baseline root or targets is not an object"
                ]
                out_path = ROOT / args.out
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(
                    json.dumps(report, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
                print("ratchet: FAIL")
                print(f"report: {_relative(out_path)}")
                print("  ! baseline root or targets is not an object")
                return 1
            baseline_targets.update(previous["targets"])
        baseline_targets.update(
            {
                target: metric_snapshot(data)
                for target, data in report["targets"].items()
            }
        )
        baseline = {"schema": SCHEMA, "targets": baseline_targets}
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            json.dumps(baseline, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        report["ratchet"]["status"] = "UPDATED"
    elif baseline_path.is_file():
        try:
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            report["ratchet"]["status"] = "FAIL"
            report["ratchet"]["problems"] = [f"baseline unreadable: {exc}"]
            out_path = ROOT / args.out
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            print("ratchet: FAIL")
            print(f"report: {_relative(out_path)}")
            print(f"  ! baseline unreadable: {exc}")
            return 1
        if not isinstance(baseline, dict):
            report["ratchet"]["status"] = "FAIL"
            report["ratchet"]["problems"] = [
                f"baseline root is {type(baseline).__name__}, expected object"
            ]
            out_path = ROOT / args.out
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            print("ratchet: FAIL")
            print(f"report: {_relative(out_path)}")
            print("  ! baseline root is not an object")
            return 1
        baseline_targets = baseline.get("targets", {})
        if not isinstance(baseline_targets, dict):
            baseline_targets = {}
        problems = ratchet_problems(baseline_targets, report["targets"])
        report["ratchet"]["problems"] = problems
        if problems and not args.waiver:
            report["ratchet"]["status"] = "FAIL"
        elif problems:
            report["ratchet"]["status"] = "WAIVED"
        else:
            report["ratchet"]["status"] = "PASS"
    else:
        report["ratchet"]["status"] = "NO_BASELINE"

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    docs_path = ROOT / args.docs
    if not args.no_docs:
        _write_docs(docs_path, report)

    for target, data in sorted(report["targets"].items()):
        corpus = data["corpus"]
        ratio = data["coverage"]["ratio"]
        ratio_text = "n/a" if ratio is None else f"{ratio:.4f}"
        print(
            f"coverage: target={target} queries={corpus['known_opcode_queries']} "
            f"in_domain={corpus['in_domain']} boundary={corpus['boundary']} "
            f"ratio={ratio_text} rules={data['coverage']['rules_matched']}"
            f"/{data['coverage']['rules_total']} "
            f"parse_errors={corpus['parse_errors']}"
        )
    print(f"ratchet: {report['ratchet']['status']}")
    print(f"report: {_relative(out_path)}")
    print(f"docs: {_relative(docs_path)}")
    return 0 if report["ratchet"]["status"] not in {"FAIL", "NO_BASELINE"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
