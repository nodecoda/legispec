# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Claim ledger: what exactly has been proven, and against what.

The DSL's only claim is behavioural equivalence to LLVM's LegalizerInfo for a
declared (target, configuration, subset):

    for every query q in D union R:
        DSL.getAction(q) == LLVM.getAction(q)

This module does not re-run conformance.  It audits the reports that
conformance produced, and it pins each report to the exact rules file that
was validated, so a later edit cannot silently inherit an old green result.

Boundary of every claim (stated once, here):

  * one step only: LegalizerInfo::getAction(LegalityQuery);
  * not the iterative Legalizer pass;
  * not LegalizerHelper's MIR rewrites;
  * not instruction selection, ISel lowering or codegen;
  * only the declared domain D and its closure R, not all LLVM types.

R is the closure of Q0 taken *inside* D.  A mutation may project the observed
slot into a shape the opcode contract excludes (G_CONCAT_VECTORS' bitcast is
the first such case); that successor is recorded as a domain boundary, not as
a claim, because the DSL deliberately gives it no semantics.

Usage:
    python -m legalizer_dsl.claims verify [--write]
    python -m legalizer_dsl.claims audit
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
import tempfile
from dataclasses import dataclass, field, fields as dataclass_fields
from typing import Any, Optional

from . import paths
from .fingerprint import codegen_sha256, hand_evaluator_sha256

ROOT = paths.REPO_ROOT                                   # repo root (compat)
RULES = paths.RULES
OUT = paths.OUT
LEDGER = paths.LEDGER
REPORT_MD = paths.CLAIM_LEDGER_MD
CLAIMS_DIR = paths.CLAIMS_DIR
WITNESS_DIR = paths.WITNESS

BOUNDARY = (
    "one step: LegalizerInfo::getAction(LegalityQuery); "
    "not the iterative Legalizer pass, not LegalizerHelper MIR rewrites, "
    "not instruction selection or codegen; domain = declared D union R, "
    "with R taken inside D (successors outside D are boundaries, not claims)"
)


LLVM_BUILD_FULL = os.environ.get("LEGALIZER_DSL_LLVM_BUILD", "F:/llvm/build")
_TOYCPU_BUILD = os.environ.get("LEGALIZER_DSL_TOYCPU_BUILD", "F:/llvm/build-toycpu")


@dataclass(frozen=True)
class Claim:
    id: str
    rules: str
    report: str
    oracle: str
    scope: str
    excludes: tuple[str, ...] = ()
    config: dict[str, Any] = field(default_factory=dict)
    queries: int = 0
    rules_total: int = 0
    rules_matched: int = 0
    declared_unreachable: int = 0
    declared_shadowed: int = 0
    llvm_build: str = _TOYCPU_BUILD
    witness: str = ""


def _config_key(config: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    def text(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    return tuple(sorted((name, text(value)) for name, value in config.items()))


# Reachability is a property of the rules file plus its configuration, not of
# a claim id: a hand-written claim and its generated twin must attach the same
# witness.  Keyed by (rules stem, normalized config).
WITNESS_OVERRIDES: dict[tuple[str, tuple[tuple[str, str], ...]], str] = {
    ("aarch64-vector", ()): "aarch64-vector-unreachable.witness",
    (
        "riscv-scalar-conditional",
        (("is64bit", "false"), ("sDoubleXLen", "s64"), ("sXLen", "s32")),
    ): "riscv-scalar-conditional-rv32.witness",
}


def witness_override(claim: Claim) -> str:
    key = (pathlib.Path(claim.rules).stem, _config_key(claim.config))
    return WITNESS_OVERRIDES.get(key, "")


def witness_name(claim: Claim) -> str:
    """Filename of the frozen witness slice for ``claim``.

    An explicit field wins, then a rules+config override, then the rules stem.
    """
    return (
        claim.witness
        or witness_override(claim)
        or f"{pathlib.Path(claim.rules).stem}.witness"
    )


def _load_claims(path: pathlib.Path | None = None) -> tuple[Claim, ...]:
    """Load the single claim registry from ``claims/claims.json``."""
    if path is None:
        path = LEDGER
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise RuntimeError(f"cannot read claim registry {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"claim registry {path} is not valid JSON: {exc}"
        ) from exc

    records = payload.get("claims") if isinstance(payload, dict) else None
    if not isinstance(records, list) or not records:
        raise RuntimeError(f"claim registry {path} has no claims list")

    claim_fields = {item.name for item in dataclass_fields(Claim)}
    loaded: list[Claim] = []
    seen: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise RuntimeError(
                f"claim registry {path} entry {index} is not an object"
            )
        claim_id = record.get("id")
        if not isinstance(claim_id, str) or not claim_id:
            raise RuntimeError(f"claim registry {path} entry {index} has no id")
        if claim_id in seen:
            raise RuntimeError(
                f"claim registry {path} has duplicate claim id {claim_id!r}"
            )
        seen.add(claim_id)

        values: dict[str, Any] = {}
        for name in claim_fields:
            if name not in record:
                continue
            value = record[name]
            if name == "excludes":
                value = tuple(value or ())
            values[name] = value
        try:
            loaded.append(Claim(**values))
        except TypeError as exc:
            raise RuntimeError(
                f"claim registry {path} entry {claim_id!r} is malformed: {exc}"
            ) from exc

    if len(loaded) < 17:
        raise RuntimeError(
            f"claim registry {path} has only {len(loaded)} claims; "
            "expected at least 17"
        )
    return tuple(loaded)


CLAIMS: tuple[Claim, ...] = _load_claims()


def _norm(path: pathlib.Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _path_parts(path: object) -> tuple[str, ...]:
    """Split a reported path into normalized parts on any platform."""
    return tuple(
        os.path.normcase(part)
        for part in str(path).replace("\\", "/").split("/")
        if part
    )


def _same_location(
    reported: object, expected: pathlib.Path, tail: tuple[str, ...] = ()
) -> bool:
    """True when a report points at this file, here or in another checkout.

    Evidence may be produced anywhere (a build tree, a collector's out/ dir),
    so an absolute path is not an identity.  Paths match when they normalize
    identically.  Rules and oracle provenance may also match on a relative
    tail such as ``rules/toycpu.ldsl`` or ``oracle/legalizer-oracle.exe``,
    which keeps evidence produced in an older monorepo checkout valid.  Build
    paths have no tail fallback: they must normalize identically.  Content
    identity is carried by the sha256 pins checked separately; this check only
    catches "the report is about some other file".
    """
    if not reported:
        return False
    if _norm(pathlib.Path(str(reported))) == _norm(expected):
        return True
    if not tail:
        return False
    reported_parts = _path_parts(reported)
    expected_tail = tuple(os.path.normcase(part) for part in tail)
    if len(reported_parts) < len(expected_tail):
        return False
    return reported_parts[-len(expected_tail) :] == expected_tail


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluator_sha256() -> str:
    """Backward-compatible alias for the hand-evaluator fingerprint."""
    return hand_evaluator_sha256()


# These fields legitimately differ between two identical runs in different
# checkouts or temp directories.  Identity is carried by rules_sha256,
# oracle_sha256, corpus_sha256, config and the evaluator hashes, so the
# semantic report hash drops the human-readable path hints.
_VOLATILE_REPORT_FIELDS = (
    ("inputs", "rules"),
    ("inputs", "jsonl"),
    ("reference", "oracle"),
    ("reference", "llvm_src"),
    ("reference", "llvm_build"),
)


def canonical_report_bytes(
    data: dict[str, Any], *, include_codegen: bool = True
) -> bytes:
    """Canonical bytes for a report, excluding volatile path metadata.

    Hand-written claims are produced by the conformance runner, not by
    ``codegen.py``.  Their report still records the codegen hash for
    provenance, but a codegen edit must not invalidate hand evidence, so the
    canonical hash drops that one field for non-generated claims.
    """
    normalized = json.loads(json.dumps(data))
    for section, key in _VOLATILE_REPORT_FIELDS:
        value = normalized.get(section)
        if isinstance(value, dict):
            value.pop(key, None)
    if not include_codegen:
        normalized.pop("codegen_sha256", None)
    return json.dumps(
        normalized,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_report_sha256(
    data: dict[str, Any], *, include_codegen: bool = True
) -> str:
    return hashlib.sha256(
        canonical_report_bytes(data, include_codegen=include_codegen)
    ).hexdigest()


def _section(data: dict[str, Any], name: str, problems: list[str]) -> dict[str, Any]:
    value = data.get(name, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        problems.append(
            f"report {name} is {type(value).__name__}, expected an object"
        )
        return {}
    return value


def _int_field(
    section: dict[str, Any], name: str, where: str, problems: list[str]
) -> Optional[int]:
    value = section.get(name)
    if value is None:
        problems.append(f"report {where}.{name} is missing")
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        problems.append(
            f"report {where}.{name} is {type(value).__name__}, expected an integer"
        )
        return None
    return value


def _reachability_problems(
    claim: Claim, rules_path: pathlib.Path, unmatched: list[Any]
) -> list[str]:
    """Cross-check declared unreachable/shadowed counts against the checker.

    ``rules_unmatched`` means "this rule never won a query".  That conflates a
    rule whose predicate is never true (unreachable) with a rule that matches
    but is always shadowed by an earlier rule.  The ledger must say which one
    it is, and a witness is required for the former.
    """
    if not (claim.declared_unreachable or claim.declared_shadowed):
        return []
    from .checker.analysis import analyze_all
    from .parser import parse_defines, parse_file

    def text(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    try:
        config = parse_defines(
            [f"{name}={text(value)}" for name, value in claim.config.items()]
        )
        info = parse_file(rules_path, config=config)
        analyses = analyze_all(info)
    except Exception as exc:  # analysis must report, not crash the verifier
        return [f"reachability analysis failed: {exc}"]

    problems: list[str] = []
    unreachable = 0
    shadowed = 0
    for record in unmatched:
        if not isinstance(record, dict):
            problems.append("rules_unmatched entry is not an object")
            continue
        opcode = record.get("ruleset")
        index = record.get("index")
        analysis = analyses.get(opcode) if isinstance(opcode, str) else None
        if (
            analysis is None
            or not isinstance(index, int)
            or isinstance(index, bool)
            or not 0 <= index < len(analysis.matched)
        ):
            problems.append(
                f"unmatched rule {opcode!r}[{index!r}] cannot be located in D"
            )
            continue
        if analysis.matched[index] == 0:
            unreachable += 1
        else:
            shadowed += 1

    if unreachable != claim.declared_unreachable:
        problems.append(
            f"{unreachable} rules are genuinely unreachable, ledger declares "
            f"{claim.declared_unreachable}"
        )
    if shadowed != claim.declared_shadowed:
        problems.append(
            f"{shadowed} rules are shadowed, ledger declares "
            f"{claim.declared_shadowed}"
        )
    return problems


def audit(claim: Claim) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": claim.id,
        "scope": claim.scope,
        "excludes": list(claim.excludes),
        "config": claim.config,
        "rules": claim.rules,
        "queries": claim.queries,
        "evaluator_sha256": hand_evaluator_sha256(),
        "codegen_sha256": codegen_sha256(),
        "problems": [],
        "ok": False,
    }
    rules = RULES / claim.rules
    report = OUT / claim.report
    oracle = OUT / claim.oracle

    name = witness_name(claim)
    witness = WITNESS_DIR / name
    explicit_witness = (
        bool(claim.witness)
        or bool(witness_override(claim))
        or claim.declared_unreachable > 0
    )
    result["witness"] = None
    if witness.is_file():
        result["witness"] = name
        result["witness_sha256"] = _sha256(witness)
    elif explicit_witness:
        reason = (
            f"declared_unreachable={claim.declared_unreachable}"
            if claim.declared_unreachable
            else "explicit witness"
        )
        result["problems"].append(f"witness missing ({reason}): {witness}")

    if not rules.is_file():
        result["problems"].append(f"rules file missing: {rules}")
        result["ok"] = not result["problems"]
        return result
    result["rules_sha256"] = _sha256(rules)

    if not report.is_file():
        result["problems"].append(f"report missing: {report}")
        result["ok"] = not result["problems"]
        return result

    if not oracle.exists():
        result["problems"].append(f"oracle missing: {oracle}")
    elif not oracle.is_file():
        result["problems"].append(f"oracle is not a file: {oracle}")

    try:
        data = json.loads(report.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        result["problems"].append(f"report is not readable JSON: {exc}")
        result["ok"] = not result["problems"]
        return result
    if not isinstance(data, dict):
        result["problems"].append(
            f"report root is {type(data).__name__}, expected an object"
        )
        result["ok"] = not result["problems"]
        return result

    generated = claim.id.endswith("-generated")
    result["report_sha256"] = canonical_report_sha256(
        data, include_codegen=generated
    )
    result["report_file_sha256"] = _sha256(report)
    result["report"] = claim.report
    result["oracle"] = claim.oracle

    reported_evaluator = data.get("evaluator_sha256")
    if not reported_evaluator:
        result["problems"].append(
            "report has no evaluator fingerprint; re-run the claim"
        )
    elif reported_evaluator != hand_evaluator_sha256():
        result["problems"].append(
            "report was produced by a different evaluator; re-run the claims"
        )

    if generated:
        reported_codegen = data.get("codegen_sha256")
        if not reported_codegen:
            result["problems"].append(
                "generated report has no codegen fingerprint; rebuild the oracle"
            )
        elif reported_codegen != codegen_sha256():
            result["problems"].append(
                "generated oracle was built from a different codegen; rebuild it"
            )

    inputs = _section(data, "inputs", result["problems"])
    reference = _section(data, "reference", result["problems"])

    result["corpus_sha256"] = inputs.get("sha256")
    if not result["corpus_sha256"]:
        result["problems"].append("report has no query corpus hash")

    if data.get("schema") != "legalizer-dsl-conformance-v1":
        result["problems"].append(f"unexpected schema: {data.get('schema')!r}")

    reported_rules = inputs.get("rules")
    if not _same_location(reported_rules, rules, ("rules", claim.rules)):
        result["problems"].append(
            f"report was produced from {reported_rules!r}, not {claim.rules!r}"
        )

    reported_oracle = reference.get("oracle")
    if not _same_location(reported_oracle, oracle, _path_parts(claim.oracle)):
        result["problems"].append(
            f"report used oracle {reported_oracle!r}, not {claim.oracle!r}"
        )

    reported_build = reference.get("llvm_build")
    if not _same_location(reported_build, pathlib.Path(claim.llvm_build)):
        result["problems"].append(
            f"report records llvm_build {reported_build!r}, "
            f"ledger says {claim.llvm_build!r}"
        )

    reported_commit = reference.get("llvm_commit")
    if not reported_commit:
        result["problems"].append("report has no LLVM commit")

    reported_config = inputs.get("config", {})
    if not isinstance(reported_config, dict):
        result["problems"].append(
            f"report config is {type(reported_config).__name__}, expected an object"
        )
    elif reported_config != claim.config:
        result["problems"].append(
            f"report config {reported_config!r} != ledger config {claim.config!r}"
        )

    reported_rules_sha = inputs.get("rules_sha256")
    if not reported_rules_sha:
        result["problems"].append("report has no rules content hash")
    elif reported_rules_sha != result["rules_sha256"]:
        result["problems"].append(
            "report was produced from different rules content; re-run the claim"
        )

    reported_oracle_sha = reference.get("oracle_sha256")
    current_oracle_sha = None
    if oracle.is_file():
        try:
            current_oracle_sha = _sha256(oracle)
        except OSError as exc:
            result["problems"].append(f"oracle is not readable: {exc}")
    if not reported_oracle_sha:
        result["problems"].append(
            "report predates oracle pinning; re-run the claim"
        )
    elif reported_oracle_sha != current_oracle_sha:
        result["problems"].append(
            "oracle binary changed since the report was produced"
        )
    result["oracle_sha256"] = current_oracle_sha
    if oracle.is_file():
        try:
            result["oracle_size"] = oracle.stat().st_size
        except OSError as exc:
            result["problems"].append(f"cannot stat oracle: {exc}")
            result["oracle_size"] = None
    else:
        result["oracle_size"] = None

    comparison = _section(data, "comparison", result["problems"])
    divergence_count = _int_field(
        comparison, "divergence_count", "comparison", result["problems"]
    )
    queries_compared = _int_field(
        comparison, "queries_compared", "comparison", result["problems"]
    )
    oracle_errors = comparison.get("oracle_errors")
    if isinstance(oracle_errors, bool) or not isinstance(
        oracle_errors, (int, list)
    ):
        result["problems"].append(
            "report comparison.oracle_errors is not an integer or list"
        )
    elif oracle_errors:
        result["problems"].append(f"oracle errors: {oracle_errors!r}")
    if divergence_count not in (None, 0):
        result["problems"].append(f"divergences: {divergence_count}")
    if queries_compared is not None and queries_compared != claim.queries:
        result["problems"].append(
            f"query count {queries_compared} != {claim.queries}"
        )

    domain = _section(data, "domain", result["problems"])
    if data.get("domain_closure_ok") is False:
        result["problems"].append("domain closure not ok")
    closure_outside = _int_field(
        domain, "closure_outside_d_count", "domain", result["problems"]
    )
    if closure_outside:
        result["problems"].append(f"closure outside D: {closure_outside}")

    coverage = _section(data, "coverage", result["problems"])
    rules_total = _int_field(coverage, "rules", "coverage", result["problems"])
    rules_matched = _int_field(
        coverage, "rules_matched", "coverage", result["problems"]
    )
    if rules_total is not None and rules_total != claim.rules_total:
        result["problems"].append(
            f"rule count {rules_total} != {claim.rules_total}"
        )
    if rules_matched is not None and rules_matched != claim.rules_matched:
        result["problems"].append(
            f"matched {rules_matched} != {claim.rules_matched}"
        )
    unmatched = coverage.get("rules_unmatched") or []
    if not isinstance(unmatched, list):
        result["problems"].append(
            f"report coverage.rules_unmatched is {type(unmatched).__name__}, "
            "expected a list"
        )
        unmatched = []
    explained = claim.declared_unreachable + claim.declared_shadowed
    if len(unmatched) != explained:
        result["problems"].append(
            f"{len(unmatched)} unmatched rules, ledger declares "
            f"{claim.declared_unreachable} unreachable + "
            f"{claim.declared_shadowed} shadowed"
        )
    elif explained:
        result["problems"].extend(
            _reachability_problems(claim, rules, unmatched)
        )

    result.update(
        {
            "queries": queries_compared,
            "divergences": divergence_count,
            "rules_total": rules_total,
            "rules_matched": rules_matched,
            "unmatched": unmatched,
            "declared_unreachable": claim.declared_unreachable,
            "declared_shadowed": claim.declared_shadowed,
            "llvm_commit": reported_commit,
            "llvm_build": claim.llvm_build,
            "ok": not result["problems"],
        }
    )
    return result


def _recomputed_corpus_sha256(claim: Claim) -> str:
    """Re-enumerate D union R and hash it exactly as conformance does."""
    from .conformance import (
        _closure_outside_d,
        _combined_queries,
        _write_input,
        closure_queries,
    )
    from .parser import parse_defines, parse_file

    def text(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    config = parse_defines(
        [f"{name}={text(value)}" for name, value in claim.config.items()]
    )
    info = parse_file(RULES / claim.rules, config=config)
    closure = closure_queries(info)
    outside = _closure_outside_d(info, closure)
    with tempfile.TemporaryDirectory(prefix="legalizer-claims-") as temp_dir:
        path = pathlib.Path(temp_dir) / "queries.jsonl"
        _, digest = _write_input(path, _combined_queries(info, closure, outside))
    return digest


def audit_all(
    selected: Optional[tuple[Claim, ...]] = None, *, verify_corpus: bool = False
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for claim in CLAIMS if selected is None else selected:
        entry = audit(claim)
        if verify_corpus and entry["ok"]:
            try:
                expected = _recomputed_corpus_sha256(claim)
            except Exception as exc:
                entry["problems"].append(f"corpus re-enumeration failed: {exc}")
                entry["ok"] = False
            else:
                if expected != entry.get("corpus_sha256"):
                    entry["problems"].append(
                        "report corpus hash does not match a fresh enumeration "
                        f"({expected[:16]}... != "
                        f"{str(entry.get('corpus_sha256'))[:16]}...)"
                    )
                    entry["ok"] = False
        entries.append(entry)
    return entries


def check_staleness(
    entries: list[dict[str, Any]],
    previous: dict[str, Any],
    *,
    allow_evaluator_change: bool = False,
    allow_report_change: bool = False,
    allow_commit_change: bool = False,
    allow_claim_removal: bool = False,
    allow_missing_pins: bool = False,
) -> None:
    previous_claims = previous.get("claims") or []
    if not isinstance(previous_claims, list):
        previous_claims = []
    pinned = {
        entry["id"]: entry
        for entry in previous_claims
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }
    current_evaluator = hand_evaluator_sha256()
    previous_evaluator = previous.get("evaluator_sha256")
    previous_commit = previous.get("llvm_commit")
    current_ids = {entry["id"] for entry in entries}

    if pinned and not previous_evaluator and not allow_missing_pins:
        for entry in entries:
            entry["problems"].append(
                "STALE: ledger has no evaluator pin; refusing to verify "
                "(restore it or pass --allow-missing-pins for a one-time migration)"
            )
            entry["ok"] = False

    removed = sorted(set(pinned) - current_ids)
    if removed and not allow_claim_removal:
        for entry in entries:
            entry["problems"].append(
                "STALE: claims removed from the registry: " + ", ".join(removed)
            )
            entry["ok"] = False

    for entry in entries:
        old = pinned.get(entry["id"], {})
        old_evaluator = old.get("evaluator_sha256")
        if (
            not allow_evaluator_change
            and (
                (previous_evaluator and previous_evaluator != current_evaluator)
                or (old_evaluator and old_evaluator != current_evaluator)
            )
        ):
            entry["problems"].append(
                "STALE: evaluator changed since this ledger was written; "
                "re-run the claims (or pass --allow-evaluator-change after "
                "regenerating the reports)"
            )
            entry["ok"] = False
        if (
            not allow_commit_change
            and previous_commit
            and entry.get("llvm_commit")
            and previous_commit != entry["llvm_commit"]
        ):
            entry["problems"].append(
                "STALE: ledger LLVM commit "
                f"{previous_commit!r} != claim LLVM commit "
                f"{entry['llvm_commit']!r}"
            )
            entry["ok"] = False

        mandatory = (
            ("rules_sha256", "rules"),
            ("report_sha256", "report"),
            ("corpus_sha256", "query corpus"),
            ("oracle_sha256", "oracle"),
        )
        # report_file_sha256 is for collect_evidence.py's copy check, not
        # for staleness: a hand report can change bytes (codegen metadata)
        # without changing the semantic evidence it records.
        optional = (("witness_sha256", "witness"),)
        if (
            not allow_report_change
            and old.get("rules_sha256")
            and entry.get("rules_sha256")
            and old["rules_sha256"] != entry["rules_sha256"]
        ):
            entry["problems"].append(
                "STALE: rules file changed since this claim was validated; "
                "re-run it and refresh with `claims.py verify --write`"
            )
            entry["ok"] = False

        for pin, label in mandatory:
            old_value = old.get(pin)
            new_value = entry.get(pin)
            if old and not old_value and not allow_missing_pins:
                entry["problems"].append(
                    f"STALE: ledger has no {label} pin; refusing to verify "
                    "(pass --allow-missing-pins for a one-time migration)"
                )
                entry["ok"] = False
            elif old_value and not new_value and not allow_report_change:
                entry["problems"].append(
                    f"STALE: {label} pin disappeared; re-run the claim"
                )
                entry["ok"] = False
            elif (
                old_value
                and new_value
                and old_value != new_value
                and not allow_report_change
            ):
                entry["problems"].append(
                    f"STALE: {label} changed since this claim was validated; "
                    "re-run it and refresh with `claims.py verify --write`"
                )
                entry["ok"] = False

        for pin, label in optional:
            old_value = old.get(pin)
            new_value = entry.get(pin)
            if old_value and not new_value and not allow_report_change:
                entry["problems"].append(
                    f"STALE: {label} pin disappeared; re-run the claim"
                )
                entry["ok"] = False
            elif (
                old_value
                and new_value
                and old_value != new_value
                and not allow_report_change
            ):
                entry["problems"].append(
                    f"STALE: {label} changed since this claim was validated; "
                    "re-run it and refresh with `claims.py verify --write`"
                )
                entry["ok"] = False


def render_markdown(entries: list[dict[str, Any]], llvm_commit: Optional[str]) -> str:
    lines = [
        "# Claim ledger: what the DSL has proven",
        "",
        "Generated by `python -m legalizer_dsl.claims`. Do not edit by hand.",
        "",
        f"LLVM commit: `{llvm_commit or 'unknown'}`",
        "",
        "## The claim",
        "",
        "For every query `q` in the declared domain `D` and its closure `R`:",
        "",
        "```text",
        "DSL.getAction(q) == LegalizerInfo::getAction(q)",
        "```",
        "",
        "## The boundary",
        "",
        BOUNDARY + ".",
        "",
        "## Claims",
        "",
        "| claim | config | queries | divergences | rules matched | status |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for e in entries:
        cfg = ", ".join(f"{k}={v}" for k, v in sorted(e.get("config", {}).items()))
        lines.append(
            f"| `{e['id']}` | {cfg or '-'} | {e.get('queries', '-')} | "
            f"{e.get('divergences', '-')} | "
            f"{e.get('rules_matched', '-')}/{e.get('rules_total', '-')} | "
            f"{'PROVEN' if e['ok'] else 'FAIL'} |"
        )
    lines += ["", "## Oracle provenance", "", "| oracle | sha256 | size |", "| --- | --- | ---: |"]
    seen: dict[str, dict[str, Any]] = {}
    for e in entries:
        path = e.get("oracle") or "-"
        seen.setdefault(path, e)
    for path, e in sorted(seen.items()):
        sha = e.get("oracle_sha256") or "-"
        size = e.get("oracle_size", "-")
        lines.append(f"| `{path}` | `{sha}` | {size} |")
    lines += ["", "## Scope and exclusions", ""]
    for e in entries:
        lines.append(f"- `{e['id']}`: {e['scope']}")
        if e["excludes"]:
            lines.append(f"  - not covered: {', '.join(e['excludes'])}")
    failures = [e for e in entries if not e["ok"]]
    lines += ["", "## Problems", ""]
    if failures:
        for e in failures:
            for problem in e["problems"]:
                lines.append(f"- `{e['id']}`: {problem}")
    else:
        lines.append("None. Every claim matches its report, its rules file and its oracle.")
    lines.append("")
    return "\n".join(lines)


def write_ledger(entries: list[dict[str, Any]]) -> None:
    llvm_commit = next(
        (e.get("llvm_commit") for e in entries if e.get("llvm_commit")), None
    )
    evaluator = evaluator_sha256()
    for entry in entries:
        entry["evaluator_sha256"] = evaluator
    payload = {
        "schema": "legalizer-dsl-claims-v1",
        "llvm_commit": llvm_commit,
        "evaluator_sha256": evaluator,
        "boundary": BOUNDARY,
        "claims": entries,
    }
    LEDGER.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    REPORT_MD.write_text(
        render_markdown(entries, llvm_commit), encoding="utf-8", newline="\r\n"
    )


def load_previous() -> dict[str, Any]:
    """Load the previous ledger, failing closed on unreadable input."""
    if not LEDGER.exists():
        return {}
    try:
        payload = json.loads(LEDGER.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read claim ledger {LEDGER}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("claims"), list):
        raise RuntimeError(f"claim ledger {LEDGER} is malformed")
    return payload


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("verify", "audit"), nargs="?", default="verify"
    )
    parser.add_argument("--write", action="store_true", help="rewrite the ledger")
    parser.add_argument(
        "--llvm-build",
        default=None,
        help="override every claim's recorded LLVM build (and persist it with --write)",
    )
    parser.add_argument(
        "--verify-corpus",
        action="store_true",
        help="re-enumerate D union R and compare each report's corpus hash",
    )
    parser.add_argument(
        "--allow-evaluator-change",
        action="store_true",
        help="accept a changed evaluator after its reports were regenerated",
    )
    parser.add_argument(
        "--allow-report-change",
        action="store_true",
        help="accept changed report/corpus/witness/oracle pins (for a re-run)",
    )
    parser.add_argument(
        "--allow-commit-change",
        action="store_true",
        help="accept a changed LLVM commit recorded in new reports",
    )
    parser.add_argument(
        "--allow-claim-removal",
        action="store_true",
        help="accept claims removed from the registry",
    )
    parser.add_argument(
        "--allow-missing-pins",
        action="store_true",
        help="one-time migration: accept a ledger that predates a pin",
    )
    args = parser.parse_args(argv)

    selected = CLAIMS
    if args.llvm_build:
        from dataclasses import replace

        selected = tuple(
            replace(claim, llvm_build=args.llvm_build) for claim in CLAIMS
        )

    try:
        entries = audit_all(selected, verify_corpus=args.verify_corpus)
        previous = load_previous()
        check_staleness(
            entries,
            previous,
            allow_evaluator_change=args.allow_evaluator_change,
            allow_report_change=args.allow_report_change,
            allow_commit_change=args.allow_commit_change,
            allow_claim_removal=args.allow_claim_removal,
            allow_missing_pins=args.allow_missing_pins,
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    for e in entries:
        status = "PROVEN" if e["ok"] else "FAIL"
        queries = e.get("queries")
        divergences = e.get("divergences")
        query_text = f"{queries:>7}" if isinstance(queries, int) else f"{'-':>7}"
        divergence_text = (
            f"{divergences:>2}" if isinstance(divergences, int) else f"{'-':>2}"
        )
        print(
            f"{e['id']:<36} {status:<7} "
            f"q={query_text} d={divergence_text} "
            f"r={e.get('rules_matched', '-')}/{e.get('rules_total', '-')}"
        )
        for problem in e["problems"]:
            print(f"    ! {problem}")

    failures = sum(1 for e in entries if not e["ok"])
    print(
        f"\nclaims={len(entries)} proven={len(entries) - failures} failed={failures}"
    )

    if args.write:
        if failures:
            print("refusing to write: the audit is not clean", file=sys.stderr)
        else:
            write_ledger(entries)
            print(f"wrote {LEDGER.name} and {REPORT_MD.name}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())