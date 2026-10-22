# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Capability checks (C) for Custom/Libcall/Lower rules.

The DSL states *policy*: which opcodes need a custom handler, a libcall or a
generic lowering.  It cannot state whether the target actually provides that
capability.  ``capabilities.json`` is the explicit bridge:

* every rule with action Custom/Libcall/Lower must be declared;
* every declaration must correspond to a rule (no stale entries);
* when the target source is supplied, every declared Custom opcode must have a
  ``case`` in ``ToyCPULegalizerInfo::legalizeCustom`` and the declared provider
  symbol must exist in that function.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from ..diagnostics import Diagnostic, Severity
from ..model import Action, LegalizerInfo

CAPABILITIES = {
    "custom": Action.CUSTOM,
    "libcall": Action.LIBCALL,
    "lower": Action.LOWER,
}

_CASE_RE = re.compile(r"case\s+(?:TargetOpcode::)?(G_[A-Z0-9_]+)\s*:")


class CapabilityManifestError(ValueError):
    """The manifest is not a version-1 capability document."""


def load_manifest(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CapabilityManifestError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CapabilityManifestError(f"{path}: manifest must be an object")
    if data.get("version") != 1:
        raise CapabilityManifestError(
            f"{path}: unsupported manifest version {data.get('version')!r}"
        )
    for key in CAPABILITIES:
        entry = data.get(key)
        if entry is None:
            continue
        if not isinstance(entry, dict):
            raise CapabilityManifestError(f"{path}: {key} must be an object")
        opcodes = entry.get("opcodes", [])
        if not isinstance(opcodes, list) or not all(
            isinstance(opcode, str) for opcode in opcodes
        ):
            raise CapabilityManifestError(
                f"{path}: {key}.opcodes must be a list of strings"
            )
    return data


def required_capabilities(info: LegalizerInfo) -> dict[str, list[str]]:
    required: dict[str, set[str]] = {key: set() for key in CAPABILITIES}
    for ruleset in info.rulesets:
        for rule in ruleset.rules:
            for key, action in CAPABILITIES.items():
                if rule.action is action:
                    required[key].update(ruleset.opcodes)
    return {key: sorted(value) for key, value in required.items()}


def _custom_body(source: str, symbol: Optional[str] = None) -> str:
    """Return the custom-dispatch body named by the manifest.

    Historically the checker only knew ToyCPULegalizerInfo.  AArch64 slices
    need the same check against AArch64LegalizerInfo::legalizeCustom, so the
    symbol is now data from capabilities.json.  A missing symbol falls back to
    the ToyCPU name for backwards compatibility.
    """
    anchor_name = symbol or "ToyCPULegalizerInfo::legalizeCustom"
    anchor = source.find(anchor_name)
    return source[anchor:] if anchor >= 0 else ""


def run(
    info: LegalizerInfo,
    manifest: dict,
    *,
    custom_source: Optional[str] = None,
    custom_source_name: Optional[str] = None,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    required = required_capabilities(info)

    for key in CAPABILITIES:
        entry = manifest.get(key, {})
        declared = set(entry.get("opcodes", []))
        for opcode in sorted(set(required[key]) - declared):
            diagnostics.append(
                Diagnostic(
                    "C001",
                    Severity.ERROR,
                    f"{key}: {opcode} has a rule but is not declared in the "
                    "capability manifest",
                    None,
                )
            )
        for opcode in sorted(declared - set(required[key])):
            diagnostics.append(
                Diagnostic(
                    "C002",
                    Severity.ERROR,
                    f"{key}: {opcode} is declared but has no {key} rule",
                    None,
                )
            )

    if custom_source is not None:
        symbol = manifest.get("custom", {}).get("symbol")
        handler = symbol or "the custom handler"
        body = _custom_body(custom_source, symbol)
        cases = set(_CASE_RE.findall(body))
        for opcode in sorted(set(required["custom"]) - cases):
            diagnostics.append(
                Diagnostic(
                    "C003",
                    Severity.ERROR,
                    f"custom: {opcode} is declared but has no case in "
                    f"{handler}",
                    None,
                )
            )
        for opcode in sorted(cases - set(required["custom"])):
            diagnostics.append(
                Diagnostic(
                    "C004",
                    Severity.WARN,
                    f"custom: {opcode} has a legalizeCustom case but no DSL "
                    "custom rule",
                    None,
                )
            )
        if symbol and symbol not in body:
            diagnostics.append(
                Diagnostic(
                    "C005",
                    Severity.ERROR,
                    f"custom: provider symbol {symbol!r} not found in "
                    f"{custom_source_name or 'the provided custom source'}",
                    None,
                )
            )

    return diagnostics