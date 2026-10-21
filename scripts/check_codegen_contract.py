#!/usr/bin/env python3
# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Deterministic codegen contract for every committed rule file.

For each ``rules/*.ldsl`` this gate:

1. generates the header, source, manifest and wiring twice in separate
   directories and requires byte-for-byte equality;
2. runs ``codegen --check`` against the first directory;
3. validates the manifest provenance (generator/DSL version, derived
   namespace, rules hash, relative source path, structure contract);
4. rejects absolute paths and wall-clock timestamps in generated text.

The gate is intentionally independent of LLVM: it checks the generator's
contract, not whether the generated C++ links.  Linking is covered by
``scripts/integration_smoke.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from legalizer_dsl import __version__, codegen  # noqa: E402
from legalizer_dsl.codegen import DSL_SPEC_VERSION  # noqa: E402

OUT = ROOT / "out" / "codegen-contract"
RULES = ROOT / "rules"
LLVM_COMMIT = "ci-codegen-contract"
_ABS_PATH_RE = re.compile(r"(?:^|[\"'\s=])(?:[A-Za-z]:[\\/]|/[A-Za-z0-9_.-]+/)")
_TIMESTAMP_RE = re.compile(r"\b20\d{2}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


# Config guards are resolved at generation time, so a rule file with guards
# has one artifact per config.  Cover both RISC-V widths explicitly.
CONFIG_VARIANTS: dict[str, tuple[tuple[tuple[str, str], ...], ...]] = {
    "riscv-conditional-gconstant.ldsl": (
        (("is64bit", "false"), ("sXLen", "s32"), ("sDoubleXLen", "s64")),
        (("is64bit", "true"), ("sXLen", "s64"), ("sDoubleXLen", "s128")),
    ),
    "riscv-scalar-conditional.ldsl": (
        (("is64bit", "false"), ("sXLen", "s32"), ("sDoubleXLen", "s64")),
        (("is64bit", "true"), ("sXLen", "s64"), ("sDoubleXLen", "s128")),
    ),
}


def _rules() -> list[Path]:
    return sorted(path for path in RULES.glob("*.ldsl") if path.is_file())


def _variants(rule: Path) -> tuple[tuple[tuple[str, str], ...], ...]:
    return CONFIG_VARIANTS.get(rule.name, ((),))


def _expected_config(config: tuple[tuple[str, str], ...]) -> dict[str, object]:
    expected: dict[str, object] = {}
    for key, value in config:
        if value in ("true", "false"):
            expected[key] = value == "true"
        else:
            expected[key] = value
    return expected


def _variant_name(config: tuple[tuple[str, str], ...]) -> str:
    if not config:
        return "default"
    return "-".join(f"{key}={value}" for key, value in config)


def _generate(
    rule: Path,
    out_dir: Path,
    wiring_dir: Path,
    config: tuple[tuple[str, str], ...],
    *,
    check: bool,
) -> int:
    argv = [
        "--rules",
        str(rule.relative_to(ROOT)),
        "--out-dir",
        str(out_dir),
        "--emit-wiring",
        str(wiring_dir),
        "--llvm-commit",
        LLVM_COMMIT,
    ]
    for key, value in config:
        argv.extend(["--define", f"{key}={value}"])
    if check:
        argv.append("--check")
    return codegen.main(argv)


def _snapshot(*directories: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                files[path.relative_to(directory.parent).as_posix()] = path.read_bytes()
    return files


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_manifest(
    manifest_path: Path,
    rule: Path,
    config: tuple[tuple[str, str], ...],
) -> list[str]:
    problems: list[str] = []
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"manifest is not readable JSON: {exc}"]

    if data.get("generator_version") != __version__:
        problems.append(
            f"generator_version {data.get('generator_version')!r} != {__version__!r}"
        )
    if data.get("dsl_spec_version") != DSL_SPEC_VERSION:
        problems.append(
            f"dsl_spec_version {data.get('dsl_spec_version')!r} != {DSL_SPEC_VERSION!r}"
        )
    if data.get("llvm_commit") != LLVM_COMMIT:
        problems.append(
            f"llvm_commit {data.get('llvm_commit')!r} != {LLVM_COMMIT!r}"
        )
    expected_config = _expected_config(config)
    if data.get("config") != expected_config:
        problems.append(
            f"config {data.get('config')!r} != {expected_config!r}"
        )
    if data.get("namespace_source") != "derived":
        problems.append(
            f"namespace_source {data.get('namespace_source')!r} is not 'derived'"
        )
    if data.get("source") != rule.relative_to(ROOT).as_posix():
        problems.append(f"source is not repository-relative: {data.get('source')!r}")
    if not isinstance(data.get("rules_sha256"), str) or not re.fullmatch(
        r"[0-9a-f]{64}", data["rules_sha256"]
    ):
        problems.append("rules_sha256 is not a sha256")
    elif data["rules_sha256"] != _sha256(rule.read_bytes()):
        problems.append("rules_sha256 does not match the rules file")
    generated = data.get("generated")
    if not isinstance(generated, dict):
        problems.append("generated file block is missing")
    else:
        for key, hash_key in (("header", "header_sha256"), ("source", "source_sha256")):
            name = generated.get(key)
            expected = generated.get(hash_key)
            if not isinstance(name, str) or not name:
                problems.append(f"generated.{key} is missing")
                continue
            path = manifest_path.parent / name
            if not path.is_file():
                problems.append(f"generated.{key} does not exist: {name}")
            elif expected != _sha256(path.read_bytes()):
                problems.append(f"generated.{hash_key} does not match {name}")
        if data.get("header_sha256") != generated.get("header_sha256"):
            problems.append("top-level header_sha256 != generated.header_sha256")
        if data.get("source_sha256") != generated.get("source_sha256"):
            problems.append("top-level source_sha256 != generated.source_sha256")
    structure = data.get("structure")
    if not isinstance(structure, dict) or structure.get("contract_version") != 1:
        problems.append("structure.contract_version is not 1")
    elif structure.get("counts", {}).get("rules") != data.get("rules"):
        problems.append("structure rule count != manifest rule count")
    return problems


def _check_text(files: dict[str, bytes]) -> list[str]:
    problems: list[str] = []
    for name, payload in files.items():
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            problems.append(f"{name}: not UTF-8: {exc}")
            continue
        normalized = text.replace("\\", "/")
        if _ABS_PATH_RE.search(normalized):
            problems.append(f"{name}: contains an absolute-looking path")
        if _TIMESTAMP_RE.search(text):
            problems.append(f"{name}: contains a wall-clock timestamp")
        if "${{" in text:
            problems.append(f"{name}: contains an unresolved template marker")
    return problems


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", action="append", default=[], help="rule file name")
    parser.add_argument("--keep", action="store_true", help="keep generated output")
    args = parser.parse_args(argv)

    selected = [rule for rule in _rules() if not args.only or rule.name in set(args.only)]
    missing = set(args.only) - {rule.name for rule in selected}
    if missing:
        print(f"error: unknown rule file(s): {', '.join(sorted(missing))}", file=sys.stderr)
        return 2
    if not selected:
        print(
            f"error: no rules/*.ldsl files found under {RULES}; refusing to "
            "report an empty contract as PASS",
            file=sys.stderr,
        )
        return 1

    if OUT.exists() and not args.keep:
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)

    failures = 0
    variants = 0
    for rule in selected:
        stem = rule.stem
        for config in _variants(rule):
            label = f"{rule.name}[{_variant_name(config)}]"
            base = OUT / stem / _variant_name(config)
            first = base / "a" / "generated"
            first_wiring = base / "a" / "wiring"
            second = base / "b" / "generated"
            second_wiring = base / "b" / "wiring"
            problems: list[str] = []
            variants += 1

            rc = _generate(rule, first, first_wiring, config, check=False)
            if rc != 0:
                problems.append(f"first generation exited {rc}")
            else:
                rc = _generate(rule, first, first_wiring, config, check=True)
                if rc != 0:
                    problems.append(f"codegen --check exited {rc}")
                rc = _generate(rule, second, second_wiring, config, check=False)
                if rc != 0:
                    problems.append(f"second generation exited {rc}")

            if not problems:
                left = _snapshot(first, first_wiring)
                right = _snapshot(second, second_wiring)
                if left != right:
                    differing = sorted(set(left) ^ set(right)) + sorted(
                        name for name in set(left) & set(right) if left[name] != right[name]
                    )
                    problems.append(
                        "generation is not deterministic: " + ", ".join(differing)
                    )
                problems.extend(_check_text(left))
                manifest = first / "codegen-manifest.json"
                if not manifest.is_file():
                    problems.append("codegen-manifest.json is missing")
                else:
                    problems.extend(_validate_manifest(manifest, rule, config))

            if problems:
                failures += 1
                print(f"FAIL {label}")
                for problem in problems:
                    print(f"  ! {problem}")
            else:
                print(f"ok   {label}")

    print(
        f"codegen-contract: {'PASS' if not failures else 'FAIL'} "
        f"rules={len(selected)} variants={variants} failures={failures}"
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
