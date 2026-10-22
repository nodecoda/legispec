# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Construct coverage: which LLVM builder methods the DSL can represent.

The claim ledger says *which configurations* were proven.  This module says
*what range of source constructs* the DSL can express at all.  Together they
answer the only stopping rule that survives: every construct in the source has
a representation, and every representation has been compared against an oracle.

The method list is extracted from LLVM's LegalizerInfo.h, not typed by hand.
A new upstream builder method therefore fails the check until someone
classifies it.  That is deliberate: an unclassified construct is an
unacknowledged gap, and gaps that do not fail are the ones that accumulate.

Status vocabulary:

  macro    dedicated MACRO in the DSL grammar
  core     composable from a core rule plus predicates/mutations
  guard    the `bool Pred` variants; a macro guard, erased at parse time
  partial  only a restricted shape is representable (e.g. pairs, not triples)
  none     no representation; must be added before the affected subset claims
           can be extended

Usage:
    python -m legispec.constructs check [--write]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
from dataclasses import dataclass
from typing import Optional

from . import paths

ROOT = paths.REPO_ROOT
LLVM_SRC = pathlib.Path(paths.LLVM_SRC) if paths.LLVM_SRC else ROOT / ".ci" / "llvm-src"
HEADER = LLVM_SRC / "llvm" / "include" / "llvm" / "CodeGen" / "GlobalISel" / "LegalizerInfo.h"
OUT_JSON = paths.CONSTRUCT_COVERAGE
OUT_MD = paths.DOCS / "construct-coverage.md"

EXCLUDED = {"getActionDefinitions", "getActionDefinitionsBuilder"}


class ConstructsError(RuntimeError):
    """The construct snapshot or claim registry is unreadable."""


@dataclass(frozen=True)
class Support:
    status: str
    spelling: str
    note: str = ""


SUPPORT: dict[str, Support] = {
    # --- action entry points -------------------------------------------------
    "actionFor": Support("core", "<action> [tN to <mutation>] when <predicate>"),
    "actionIf": Support("core", "<action> [tN to <mutation>] when <predicate>"),
    "legalIf": Support("core", "legal when <predicate>"),
    "customIf": Support("core", "custom when <predicate>"),
    "lowerIf": Support("core", "lower when <predicate>"),
    "libcallIf": Support("core", "libcall when <predicate>"),
    "unsupportedIf": Support("core", "unsupported when <predicate>"),
    "bitcastIf": Support("core", "bitcast tN to <type> when <predicate>"),
    "legal": Support("core", "legal when <predicate>"),
    "custom": Support("core", "custom when <predicate>"),
    "lower": Support("core", "lower when <predicate>"),
    "libcall": Support("core", "libcall when <predicate>"),
    "unsupported": Support("core", "unsupported"),
    "alwaysLegal": Support("macro", "always_legal"),
    # --- construction-time guarded variants ----------------------------------
    "legalFor": Support("guard", "legal_for {..} when <config-expr>; legal_for_pairs"),
    "customFor": Support("guard", "custom_for {..} when <config-expr>; custom_for_pairs"),
    "lowerFor": Support("guard", "lower_for {..} when <config-expr>"),
    "libcallFor": Support("guard", "libcall_for {..} when <config-expr>"),
    "unsupportedFor": Support("guard", "unsupported_for {..} when <config-expr>"),
    # --- scalar range mutations ---------------------------------------------
    "minScalar": Support(
        "core", "widen tN to <ty> when scalar(tN) and bits(tN) < <bits>"
    ),
    "minScalarIf": Support("core", "widen ... when <predicate> and <size test>"),
    "maxScalar": Support(
        "core", "narrow tN to <ty> when scalar(tN) and bits(tN) > <bits>"
    ),
    "maxScalarIf": Support("core", "narrow ... when <predicate> and <size test>"),
    "clampScalar": Support("macro", "clamp_scalar N, min, max"),
    "widenScalarToNextPow2": Support("macro", "widen_to_next_pow2 N, min"),
    "widenScalarToNextMultipleOf": Support("macro", "widen_to_next_multiple N, size"),
    "narrowScalar": Support("core", "narrow tN to <type>"),
    "narrowScalarIf": Support("core", "narrow tN to <type> when <predicate>"),
    "narrowScalarFor": Support("core", "narrow tN to <type> when tN in {..}"),
    "widenScalarIf": Support("core", "widen tN to <type> when <predicate>"),
    "widenScalarFor": Support("core", "widen tN to <type> when tN in {..}"),
    "minScalarSameAs": Support("macro", "min_scalar_same_as N, large"),
    "maxScalarSameAs": Support("core", "narrow tN to same_size_as(tK)"),
    "scalarSameSizeAs": Support(
        "core", "widen/narrow tN to same_size_as(tK) when scalar_bits differ"
    ),
    # --- element-wise scalar variants (vector lanes) -------------------------
    "minScalarOrElt": Support("macro", "min_scalar_or_elt N, elt_ty"),
    "minScalarOrEltIf": Support(
        "macro",
        "min_scalar_or_elt_if <predicate>, N, elt_ty; "
        "min_scalar_or_elt_if N, lanes <cmp> K, elt_ty",
    ),
    "maxScalarOrElt": Support("none", "-", "element-wise variant not modelled"),
    "clampScalarOrElt": Support("none", "-", "element-wise variant not modelled"),
    "widenScalarOrEltToNextPow2": Support(
        "macro", "widen_scalar_or_elt_to_next_pow2 N[, min]"
    ),
    "widenScalarOrEltToNextPow2OrMinSize": Support("none", "-", "element-wise variant"),
    "minScalarEltSameAsIf": Support(
        "macro", "min_scalar_elt_same_as_if N, large when <predicate>"
    ),
    "maxScalarEltSameAsIf": Support(
        "macro", "max_scalar_elt_same_as_if N, small when <predicate>"
    ),
    "widenVectorEltsToVectorMinSize": Support("none", "-", "vector-only"),
    # --- vector shape --------------------------------------------------------
    "moreElementsIf": Support("core", "more tN to <vector> when <predicate>"),
    "fewerElementsIf": Support("core", "fewer tN to <vector> when <predicate>"),
    "moreElementsToNextPow2": Support(
        "macro", "more_elements_to_next_pow2 N"
    ),
    "alignNumElementsTo": Support("none", "-", "vector lane-count policy"),
    "clampNumElements": Support(
        "macro", "clamp_num_elements N, min_vec, max_vec"
    ),
    "clampMinNumElements": Support(
        "macro", "clamp_min_num_elements N, elt, count"
    ),
    "clampMaxNumElements": Support(
        "macro", "clamp_max_num_elements N, elt, count"
    ),
    "clampMaxNumElementsStrict": Support("none", "-", "vector lane-count policy"),
    "scalarize": Support(
        "core", "fewer tN to element_of(tN) when vector(tN)"
    ),
    "scalarizeIf": Support(
        "core",
        "fewer tN to element_of(tN) when vector(tN) and <predicate>",
    ),
    # --- type-set helpers ----------------------------------------------------
    "legalForCartesianProduct": Support(
        "partial", "legal_for_pairs", "two type slots only; no 3+ products"
    ),
    "customForCartesianProduct": Support(
        "partial", "custom_for_pairs", "two type slots only; no 3+ products"
    ),
    "libcallForCartesianProduct": Support(
        "partial", "libcall_for", "two type slots only; no 3+ products"
    ),
    "lowerForCartesianProduct": Support(
        "partial", "lower_for", "two type slots only; no 3+ products"
    ),
    "actionForCartesianProduct": Support(
        "partial", "pair(...) predicates", "two type slots only; no 3+ products"
    ),
    "legalForTypeWithAnyImm": Support("none", "-", "imm wildcard predicate"),
    "actionForTypeWithAnyImm": Support("none", "-", "imm wildcard predicate"),
    "legalForTypesWithMemDesc": Support("macro", "legal_for_mem"),
    # --- memory-size predicates ---------------------------------------------
    "unsupportedIfMemSizeNotPow2": Support("none", "-", "mem size predicate"),
    "lowerIfMemSizeNotPow2": Support("none", "-", "mem size predicate"),
    "lowerIfMemSizeNotByteSizePow2": Support("none", "-", "mem size predicate"),
}


SNAPSHOT = paths.CLAIMS_DIR / "legalizerinfo-methods.txt"
LEDGER = paths.LEDGER


def _header_sha256() -> Optional[str]:
    if not HEADER.exists():
        return None
    return hashlib.sha256(HEADER.read_bytes()).hexdigest()


def extract_methods_from_text(text: str) -> list[str]:
    names = set(re.findall(r"LegalizeRuleSet\s*&\s*(\w+)\s*\(", text))
    return sorted(n for n in names if n not in EXCLUDED)


def extract_methods() -> list[str]:
    return extract_methods_from_text(HEADER.read_text(encoding="utf-8"))


def load_snapshot() -> tuple[dict[str, str], list[str]]:
    """Read the committed method list used when no LLVM source tree is present."""
    meta: dict[str, str] = {}
    methods: list[str] = []
    for line in SNAPSHOT.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            body = line.lstrip("#").strip()
            if ":" in body:
                key, _, value = body.partition(":")
                meta[key.strip()] = value.strip()
            continue
        methods.append(line)
    return meta, methods


def load_ledger_commit() -> Optional[str]:
    """Top-level ``llvm_commit`` from the claim registry, or None.

    A missing registry means "no ledger in this installation".  A registry
    that exists but cannot be read or parsed is corrupt, not empty: turning
    that into ``None`` would silently disable the snapshot commit gate.
    """
    if not LEDGER.exists():
        return None
    try:
        payload = json.loads(LEDGER.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConstructsError(
            f"cannot read claim registry {LEDGER}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ConstructsError(f"claim registry {LEDGER} is not an object")
    commit = payload.get("llvm_commit")
    if commit is not None and not isinstance(commit, str):
        raise ConstructsError(
            f"claim registry {LEDGER} has a non-string llvm_commit"
        )
    return commit or None


def committed_method_count() -> Optional[int]:
    """Total method count from the committed construct-coverage artifact."""
    if not OUT_JSON.is_file():
        return None
    try:
        payload = json.loads(OUT_JSON.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConstructsError(
            f"cannot read construct coverage {OUT_JSON}: {exc}"
        ) from exc
    counts = payload.get("counts") if isinstance(payload, dict) else None
    if not isinstance(counts, dict):
        return None
    try:
        return sum(int(value) for value in counts.values())
    except (TypeError, ValueError):
        raise ConstructsError(
            f"construct coverage {OUT_JSON} has non-integer counts"
        ) from None


def snapshot_commit_mismatch(
    meta: dict[str, str], ledger_commit: Optional[str]
) -> Optional[str]:
    """Describe a snapshot/ledger commit mismatch, or return None.

    An unknown snapshot commit or a missing ledger commit cannot be compared
    and is not reported as a mismatch.
    """
    snapshot_commit = meta.get("llvm_commit")
    if not snapshot_commit or snapshot_commit == "unknown":
        return None
    if not ledger_commit:
        return None
    if snapshot_commit == ledger_commit:
        return None
    return (
        f"snapshot llvm_commit {snapshot_commit} does not match "
        f"claims/claims.json llvm_commit {ledger_commit}"
    )


def write_snapshot(methods: list[str]) -> None:
    sha = _header_sha256() or "unknown"
    commit = os.environ.get("LEGISPEC_LLVM_COMMIT", "unknown")
    lines = [
        "# Snapshot of LegalizerInfo builder methods.",
        "# Generated by `legispec-constructs check --write-snapshot`; do not edit.",
        "# source: llvm/include/llvm/CodeGen/GlobalISel/LegalizerInfo.h",
        f"# header_sha256: {sha}",
        f"# llvm_commit: {commit}",
        "",
        *methods,
        "",
    ]
    SNAPSHOT.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def classify(methods: list[str]) -> tuple[list[dict], list[str]]:
    entries: list[dict] = []
    unclassified: list[str] = []
    for name in methods:
        support = SUPPORT.get(name)
        if support is None:
            unclassified.append(name)
            continue
        entries.append(
            {
                "method": name,
                "status": support.status,
                "dsl": support.spelling,
                "note": support.note,
            }
        )
    return entries, unclassified


def render_markdown(entries: list[dict], counts: dict[str, int]) -> str:
    lines = [
        "# Construct coverage: LLVM builder methods vs the DSL",
        "",
        "Generated by `src/legispec/constructs.py` from",
        "`llvm/include/llvm/CodeGen/GlobalISel/LegalizerInfo.h`. Do not edit by hand.",
        "",
        "## Counts",
        "",
        "| status | meaning | count |",
        "| --- | --- | ---: |",
        f"| macro | dedicated DSL macro | {counts.get('macro', 0)} |",
        f"| core | core rule + predicates/mutations | {counts.get('core', 0)} |",
        f"| guard | `bool Pred` variant, erased at parse time | {counts.get('guard', 0)} |",
        f"| partial | restricted shape only | {counts.get('partial', 0)} |",
        f"| none | no representation | {counts.get('none', 0)} |",
        "",
        "## Boundary",
        "",
        "A `none` construct is not a bug.  It marks a region where the DSL cannot",
        "make a claim, and therefore where no experiment may claim coverage.  A",
        "`partial` construct marks a shape restriction that must be stated whenever",
        "the affected ruleset appears in a claim.",
        "",
        "## Methods",
        "",
        "| method | status | DSL spelling | note |",
        "| --- | --- | --- | --- |",
    ]
    for e in entries:
        lines.append(
            f"| `{e['method']}` | {e['status']} | `{e['dsl']}` | {e['note']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check",), nargs="?", default="check")
    parser.add_argument("--write", action="store_true", help="rewrite the report")
    parser.add_argument(
        "--write-snapshot",
        action="store_true",
        help="rewrite claims/legalizerinfo-methods.txt from the LLVM header",
    )
    args = parser.parse_args(argv)

    try:
        ledger_commit = load_ledger_commit()
    except ConstructsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.write_snapshot:
        if not HEADER.exists():
            print(f"header not found: {HEADER}")
            return 2
        methods = extract_methods()
        if not methods:
            print(
                "ERROR: extraction found no LegalizerInfo builder methods; "
                "refusing to overwrite the committed snapshot",
                file=sys.stderr,
            )
            return 1
        write_snapshot(methods)
        print(f"wrote {SNAPSHOT.name}: {len(methods)} methods")

    if HEADER.exists():
        methods = extract_methods()
        source = f"header {HEADER}"
        if SNAPSHOT.exists():
            meta, snap_methods = load_snapshot()
            mismatch = snapshot_commit_mismatch(meta, ledger_commit)
            if mismatch is not None:
                print(f"ERROR: {mismatch}", file=sys.stderr)
                return 1
            if meta.get("header_sha256") != _header_sha256():
                print(
                    "note: claims/legalizerinfo-methods.txt is stale "
                    "(regenerate with --write-snapshot)"
                )
            elif snap_methods != methods:
                print("note: snapshot method list differs from the header")
    elif SNAPSHOT.exists():
        meta, methods = load_snapshot()
        mismatch = snapshot_commit_mismatch(meta, ledger_commit)
        if mismatch is not None:
            print(f"ERROR: {mismatch}", file=sys.stderr)
            return 1
        source = (
            f"snapshot @{meta.get('llvm_commit', 'unknown')[:12]} "
            f"(no LLVM source tree; set LEGISPEC_LLVM_SRC to check upstream)"
        )
    else:
        print(f"header not found: {HEADER}")
        print("and no committed snapshot at claims/legalizerinfo-methods.txt")
        return 2
    if not methods:
        print(
            "ERROR: no LegalizerInfo builder methods found; "
            "refusing to report empty construct coverage",
            file=sys.stderr,
        )
        return 1
    print(f"source: {source}")
    committed = committed_method_count()
    if committed is not None and len(methods) < committed:
        print(
            f"ERROR: source lists {len(methods)} methods but committed "
            f"coverage has {committed}; the snapshot looks truncated",
            file=sys.stderr,
        )
        return 1
    entries, unclassified = classify(methods)
    counts: dict[str, int] = {}
    for e in entries:
        counts[e["status"]] = counts.get(e["status"], 0) + 1

    print(f"methods={len(methods)} classified={len(entries)} unclassified={len(unclassified)}")
    print(" ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    for name in unclassified:
        print(f"  ! unclassified: {name}")

    if args.write and not unclassified:
        payload = {
            "schema": "legispec-construct-coverage-v1",
            "source": str(HEADER).replace("\\", "/"),
            "counts": counts,
            "methods": entries,
        }
        OUT_JSON.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        OUT_MD.write_text(
            render_markdown(entries, counts), encoding="utf-8", newline="\r\n"
        )
        print(f"wrote {OUT_JSON.name} and {OUT_MD.name}")

    return 1 if unclassified else 0


if __name__ == "__main__":
    sys.exit(main())