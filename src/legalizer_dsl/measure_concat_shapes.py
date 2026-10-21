# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Measure G_CONCAT_VECTORS shapes that really reach LegalizerInfo::getAction.

The conformance runner proves `DSL.getAction(q) == LegalizerInfo::getAction(q)`
on a declared finite domain.  This tool audits the other side of that claim:
it runs the real legalizer (`-debug-only=legalizer`) on two corpora and
records every `Legalizing: ... G_CONCAT_VECTORS ...` event together with:

  * dst/src LLTs and the recovered k = lanes(dst) / lanes(src)
  * whether the n-ary opcode contract holds
  * the action LLVM chose
  * the action the LDSL rule table chooses for the same two type slots

Every real event must be inside the declared domain; a k=1 or non-dividing
shape is a contract violation, not an acceptable "boundary".
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
REPO = TOOLS.parent
sys.path.insert(0, str(TOOLS))

from legalizer_dsl import Query, parse_llt  # noqa: E402
from legalizer_dsl.parser import parse_file  # noqa: E402

TRIPLE = "aarch64-unknown-linux-gnu"

# The summary line printed by the Legalizer pass, mapped to DSL Action names.
ACTION_LINES = {
    ".. Already legal": "LEGAL",
    ".. Convert to libcall": "LIBCALL",
    ".. Narrow scalar": "NARROW",
    ".. Widen scalar": "WIDEN",
    ".. Bitcast type": "BITCAST",
    ".. Lower": "LOWER",
    ".. Fewer elements": "FEWER",
    ".. More elements": "MORE",
    ".. Custom legalization": "CUSTOM",
    ".. Unable to legalize": "UNSUPPORTED",
}

_ELEM_RE = re.compile(r"^([ifsp])(\d+)$")
_VEC_RE = re.compile(r"^<(\d+) x ([ifsp]\d+)>$")


class ShapeError(ValueError):
    """A type spelling this tool does not understand."""


def parse_shape_type(text: str) -> dict:
    """Parse `<16 x i32>` / `i32` / `f32` / `p0` into lanes + element.

    The MIR printer and the LLT printer share the `iN`/`fN`/`pN` spelling for
    scalars, so one parser covers both corpora.
    """
    text = text.strip()
    vm = _VEC_RE.match(text)
    if vm:
        lanes = int(vm.group(1))
        elem_text = vm.group(2)
    else:
        lanes = None
        elem_text = text

    em = _ELEM_RE.match(elem_text)
    if not em:
        raise ShapeError(f"unsupported type spelling: {text!r}")
    kind, width = em.group(1), int(em.group(2))

    if kind == "p":
        elem = {"kind": "ptr", "addrspace": width}
        elem_key = ("ptr", width)
        scalar = f"p{width}"
    elif kind == "f":
        elem = {"kind": "float", "bits": width}
        elem_key = ("float", width)
        scalar = f"f{width}"
    else:
        elem = {"kind": "int", "bits": width}
        elem_key = ("int", width)
        scalar = f"s{width}"

    if lanes is None:
        canonical = scalar
    else:
        canonical = f"v{lanes}{scalar[0]}{width}"
    return {
        "text": text,
        "lanes": lanes,
        "elem": elem,
        "elem_key": elem_key,
        "canonical": canonical,
    }


def check_contract(dst: dict, sources: list[dict]) -> tuple[int | None, list[str]]:
    """Return (k, problems) for the MachineVerifier n-ary concat contract."""
    problems: list[str] = []
    if dst["lanes"] is None:
        problems.append("destination is not a vector")
    if len(sources) < 2:
        problems.append(f"only {len(sources)} source(s); the opcode requires >= 2")
    for index, src in enumerate(sources):
        if src["lanes"] is None:
            problems.append(f"source {index} is not a vector")
        if src["elem_key"] != dst["elem_key"]:
            problems.append(
                f"source {index} element {src['elem_key']} != destination {dst['elem_key']}"
            )
        if src["elem_key"] != sources[0]["elem_key"]:
            problems.append(f"source {index} is not homogeneous with source 0")

    if problems:
        return None, problems

    src_lanes = sources[0]["lanes"]
    dst_lanes = dst["lanes"]
    if src_lanes == 0:
        problems.append("source lane count is zero")
        return None, problems
    if dst_lanes % src_lanes != 0:
        problems.append(f"lanes(dst)={dst_lanes} is not divisible by lanes(src)={src_lanes}")
        return None, problems
    k = dst_lanes // src_lanes
    if k < 2:
        problems.append(f"k={k} violates k >= 2 (lanes(dst)={dst_lanes}, lanes(src)={src_lanes})")
        return k, problems
    return k, problems


def _consecutive_sources(sources: list[dict]) -> bool:
    return all(src["canonical"] == sources[0]["canonical"] for src in sources)


def iter_events(debug_text: str):
    """Yield every G_CONCAT_VECTORS legalizing event in `llc -debug-only` output."""
    lines = debug_text.splitlines()
    function = "?"
    for index, line in enumerate(lines):
        if line.startswith("Legalize Machine IR for:"):
            function = line.split(":", 1)[1].strip()
            continue
        if "Legalizing:" not in line or "G_CONCAT_VECTORS" not in line:
            continue

        match = re.search(r"G_CONCAT_VECTORS\s+(.*)$", line)
        if not match:
            yield {"function": function, "line": line, "types": [], "llvm_action": None}
            continue
        # The destination type appears before `= G_CONCAT_VECTORS`, so scan
        # the whole instruction: [dst, src0, src1, ...].  Ignore memory
        # operands after `::` so a future MIR printer change cannot add types.
        instruction = line.split("::", 1)[0]
        types = re.findall(r"_\(([^()]*)\)", instruction)
        action = None
        for follow in lines[index + 1:]:
            if follow.startswith("Legalizing:") or follow.startswith("Legalize Machine IR for:"):
                break
            stripped = follow.strip()
            if stripped.startswith(".. "):
                action = ACTION_LINES.get(stripped)
                if action is not None:
                    break
        yield {"function": function, "line": line.strip(), "types": types, "llvm_action": action}


def run_llc(llc: str, mode: str, path: Path) -> str:
    if mode == "ir":
        args = [
            llc,
            f"-mtriple={TRIPLE}",
            "-global-isel",
            "-global-isel-abort=0",
            "-stop-after=legalizer",
        ]
    else:
        args = [llc, f"-mtriple={TRIPLE}", "-mattr=+dotprod", "-run-pass=legalizer"]
    args += ["-debug-only=legalizer", "-o", os.devnull, str(path)]
    proc = subprocess.run(args, capture_output=True, text=True, errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(
            f"llc failed for {path} (exit {proc.returncode}):\n{proc.stderr.strip()}"
        )
    if "Unknown command line argument" in proc.stderr and "-debug-only" in proc.stderr:
        raise RuntimeError("llc was built without assertions; -debug-only is unavailable")
    return proc.stderr


def measure(rules: str, inputs: list[tuple[str, Path]], llc: str) -> dict:
    info = parse_file(rules)
    events: list[dict] = []
    parse_errors: list[str] = []

    for mode, path in inputs:
        debug_text = run_llc(llc, mode, path)
        source = f"{mode}:{path.stem}"
        for raw in iter_events(debug_text):
            entry = {
                "source": source,
                "function": raw["function"],
                "line": raw["line"],
                "llvm_action": raw["llvm_action"],
                "dst": None,
                "sources": [],
                "k": None,
                "contract_ok": False,
                "problems": [],
                "in_domain": False,
                "dsl_action": None,
                "action_match": False,
            }
            if len(raw["types"]) < 3:
                entry["problems"].append(f"could not read dst+2 sources: {raw['types']}")
                parse_errors.append(f"{source}/{raw['function']}: {raw['line']}")
                events.append(entry)
                continue
            try:
                dst = parse_shape_type(raw["types"][0])
                sources = [parse_shape_type(t) for t in raw["types"][1:]]
            except ShapeError as exc:
                entry["problems"].append(str(exc))
                parse_errors.append(f"{source}/{raw['function']}: {exc}")
                events.append(entry)
                continue

            k, problems = check_contract(dst, sources)
            entry.update(
                {
                    "dst": dst["canonical"],
                    "sources": [src["canonical"] for src in sources],
                    "k": k,
                    "contract_ok": not problems and _consecutive_sources(sources),
                    "problems": problems,
                }
            )
            if not _consecutive_sources(sources):
                entry["problems"].append("sources are not homogeneous")

            if not problems:
                query = Query(
                    "G_CONCAT_VECTORS", (parse_llt(dst["canonical"]), parse_llt(sources[0]["canonical"]))
                )
                domain = info.domain
                entry["in_domain"] = bool(domain and domain.filter and domain.filter.eval(query))
                if entry["in_domain"]:
                    try:
                        entry["dsl_action"] = info.action(query, trace=False)[0].action.name
                    except Exception as exc:  # noqa: BLE001 - record, do not hide
                        entry["problems"].append(f"DSL action failed: {exc}")
                else:
                    entry["problems"].append("real query is outside the declared domain")
                entry["action_match"] = entry["dsl_action"] == raw["llvm_action"]
            events.append(entry)

    unique: dict[tuple, dict] = {}
    for event in events:
        key = (
            event["source"],
            event["function"],
            event["dst"],
            tuple(event["sources"]),
            event["llvm_action"],
        )
        if key in unique:
            unique[key]["seen"] += 1
        else:
            event["seen"] = 1
            unique[key] = event

    k_distribution: dict[str, int] = {}
    for event in unique.values():
        if event["k"] is not None:
            k_distribution[str(event["k"])] = k_distribution.get(str(event["k"]), 0) + 1

    contract_violations = sum(1 for e in unique.values() if not e["contract_ok"])
    action_mismatches = sum(1 for e in unique.values() if e["dsl_action"] is not None and not e["action_match"])
    return {
        "schema": "concat-shape-measurement-v1",
        "rules": str(rules),
        "inputs": [{"mode": mode, "path": str(path)} for mode, path in inputs],
        "summary": {
            "events": len(events),
            "unique_queries": len(unique),
            "k_distribution": k_distribution,
            "contract_violations": contract_violations,
            "action_mismatches": action_mismatches,
            "parse_errors": len(parse_errors),
        },
        "parse_errors": parse_errors,
        "events": list(unique.values()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--llc", default=os.environ.get("LEGALIZER_DSL_LLC", r"F:\llvm\build\bin\llc.exe")
    )
    parser.add_argument("--rules", default=str(REPO / "rules" / "aarch64-concat-vectors.ldsl"))
    parser.add_argument(
        "--ir", action="append", default=[], help="IR corpus compiled with -stop-after=legalizer"
    )
    parser.add_argument(
        "--mir", action="append", default=[], help="MIR corpus run with -run-pass=legalizer"
    )
    parser.add_argument("--out", default=str(REPO / "out" / "concat-shapes-measurement.json"))
    args = parser.parse_args(argv)

    inputs: list[tuple[str, Path]] = []
    for text in args.ir:
        inputs.append(("ir", Path(text)))
    for text in args.mir:
        inputs.append(("mir", Path(text)))
    if not inputs:
        inputs = [
            ("ir", REPO / "examples" / "concat-shapes.ll"),
            ("mir", REPO / "examples" / "concat-nary.mir"),
        ]

    try:
        report = measure(args.rules, inputs, args.llc)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"measurement failed: {exc}", file=sys.stderr)
        return 2

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")

    summary = report["summary"]
    ks = " ".join(f"k={k}:{v}" for k, v in sorted(summary["k_distribution"].items()))
    print(
        "real MIR shapes  "
        f"events={summary['events']} unique={summary['unique_queries']} {ks} "
        f"contract_violations={summary['contract_violations']} "
        f"action_mismatches={summary['action_mismatches']} "
        f"parse_errors={summary['parse_errors']}"
    )
    for event in report["events"]:
        if not event["contract_ok"] or (
            event["dsl_action"] is not None and not event["action_match"]
        ):
            print(
                f"  {event['function']}: dst={event['dst']} sources={event['sources']} "
                f"llvm={event['llvm_action']} dsl={event['dsl_action']} {event['problems']}"
            )
    print(f"report           {out}")

    return 0 if (
        summary["contract_violations"] == 0
        and summary["action_mismatches"] == 0
        and summary["parse_errors"] == 0
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())