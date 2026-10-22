#!/usr/bin/env python3
# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""End-to-end integration smoke for generated LegalizerInfo rules.

The smoke has four independent evidence points:

1. codegen writes the C++ and the wiring contract;
2. a real CMake build compiles and links the generated static library;
3. the consumer runs and answers JSONL queries;
4. its observations are compared with the Python interpreter, and with the
   hand-written and generated oracles when those binaries are present.

A missing toolchain is reported as ``SKIP: environment``.  It is never
reported as a pass.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from legalizer_dsl import __version__ as GENERATOR_VERSION  # noqa: E402
from legalizer_dsl import codegen  # noqa: E402
from legalizer_dsl.codegen import DSL_SPEC_VERSION  # noqa: E402
from legalizer_dsl.conformance import (  # noqa: E402
    ComparisonReport,
    _write_input,
    compare,
    iter_d_queries,
)
from legalizer_dsl.fingerprint import (  # noqa: E402
    codegen_sha256,
    hand_evaluator_sha256,
)
from legalizer_dsl.model import Query  # noqa: E402
from legalizer_dsl.parser import parse_file  # noqa: E402

SCHEMA = "legalizer-dsl-integration-v2"

TARGETS = {
    "toycpu": {
        "rules": "rules/toycpu.lspec",
        "namespace": None,
        "oracle": "out/oracle/legalizer-oracle",
        "generated_oracle": "out/oracle/legalizer-oracle-gen",
        "extended_llt": False,
        "llvm_dirs": (
            "F:/llvm/build-toycpu/lib/cmake/llvm",
            "F:/llvm/build/lib/cmake/llvm",
        ),
    },
    "aarch64-vector": {
        "rules": "rules/aarch64-vector.lspec",
        "namespace": None,
        "oracle": "out/oracle-aarch64/legalizer-oracle-aarch64",
        "generated_oracle": (
            "out/oracle-aarch64/legalizer-oracle-aarch64-vector-gen"
        ),
        "extended_llt": True,
        "llvm_dirs": (
            "F:/llvm/build/lib/cmake/llvm",
            "F:/llvm/build-toycpu/lib/cmake/llvm",
        ),
    },
}


def _first_existing(*paths: Iterable[Optional[str]]) -> Optional[Path]:
    for value in paths:
        if not value:
            continue
        path = Path(value)
        if path.exists():
            return path
    return None


def _find_cmake() -> Optional[Path]:
    return _first_existing(
        os.environ.get("LEGALIZER_DSL_CMAKE"),
        shutil.which("cmake"),
        "C:/Program Files/CMake/bin/cmake.exe",
    )


def _find_llvm_dir(target: dict) -> Optional[Path]:
    explicit = os.environ.get("LEGALIZER_DSL_LLVM_DIR")
    if explicit:
        path = Path(explicit)
        return path if path.is_dir() else None
    for candidate in target["llvm_dirs"]:
        path = Path(candidate)
        if (path / "LLVMConfig.cmake").is_file():
            return path
    env = os.environ.get("LLVM_DIR")
    if env and (Path(env) / "LLVMConfig.cmake").is_file():
        return Path(env)
    return None


def _find_cxx() -> Optional[Path]:
    return _first_existing(
        os.environ.get("LEGALIZER_DSL_CXX"),
        os.environ.get("CXX"),
        shutil.which("clang++"),
        "F:/llvm/install/bin/clang++.exe",
        shutil.which("cl"),
        shutil.which("g++"),
    )


def _find_linker(cxx: Optional[Path]) -> Optional[Path]:
    if os.name != "nt" or cxx is None:
        return None
    if "clang" not in cxx.name.lower():
        return None
    return _first_existing(
        os.environ.get("LEGALIZER_DSL_LINKER"),
        "F:/llvm/install/bin/lld-link.exe",
        shutil.which("lld-link"),
    )


def _find_ninja() -> Optional[Path]:
    return _first_existing(
        os.environ.get("LEGALIZER_DSL_NINJA"),
        shutil.which("ninja"),
        "D:/VStudio/2022/Enterprise/Common7/IDE/CommonExtensions/Microsoft/CMake/Ninja/ninja.exe",
        "F:/llvm/tools/ninja.exe",
    )


def _find_vcvars() -> Optional[Path]:
    return _first_existing(
        os.environ.get("LEGALIZER_DSL_VCVARS"),
        "D:/VStudio/2022/Enterprise/VC/Auxiliary/Build/vcvars64.bat",
        "C:/Program Files/Microsoft Visual Studio/2022/Enterprise/VC/Auxiliary/Build/vcvars64.bat",
        "C:/Program Files/Microsoft Visual Studio/2022/Professional/VC/Auxiliary/Build/vcvars64.bat",
        "C:/Program Files/Microsoft Visual Studio/2022/Community/VC/Auxiliary/Build/vcvars64.bat",
        "C:/Program Files (x86)/Microsoft Visual Studio/2019/BuildTools/VC/Auxiliary/Build/vcvars64.bat",
    )


def _msvc_lib_dir(vcvars: Path) -> Optional[Path]:
    # .../VC/Auxiliary/Build/vcvars64.bat -> .../VC/Tools/MSVC/<ver>/lib/x64
    vc_root = vcvars.parents[2]
    tools = vc_root / "Tools" / "MSVC"
    versions = [p for p in tools.iterdir() if (p / "lib/x64").is_dir()] if tools.is_dir() else []
    if not versions:
        return None
    return sorted(versions, key=lambda p: p.name, reverse=True)[0] / "lib/x64"


def _windows_sdk_libs() -> Optional[tuple[Path, Path]]:
    for root in (
        Path("C:/Program Files (x86)/Windows Kits/10/lib"),
        Path("C:/Program Files/Windows Kits/10/lib"),
    ):
        if not root.is_dir():
            continue
        versions = [
            p
            for p in root.iterdir()
            if (p / "um/x64/shell32.lib").is_file()
            and (p / "ucrt/x64").is_dir()
        ]
        if versions:
            newest = sorted(versions, key=lambda p: p.name, reverse=True)[0]
            return newest / "ucrt/x64", newest / "um/x64"
    return None


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def _run_windows_batch(
    commands: list[list[str]],
    *,
    cwd: Path,
    vcvars: Path,
    msvc_lib: Optional[Path],
    sdk_libs: Optional[tuple[Path, Path]],
    path_prefix: list[Path],
) -> subprocess.CompletedProcess[str]:
    lines = ["@echo off", f'call "{vcvars}" >nul', "if errorlevel 1 exit /b 1"]
    lib_parts = [p for p in (msvc_lib, *(sdk_libs or ())) if p is not None]
    if lib_parts:
        joined = ";".join(str(p).replace("\\", "/") for p in lib_parts)
        lines.append(f'set "LIB={joined};%LIB%"')
    if path_prefix:
        joined = ";".join(str(p).replace("\\", "/") for p in path_prefix)
        lines.append(f'set "PATH={joined};%PATH%"')
    for command in commands:
        lines.append(subprocess.list2cmdline([str(part) for part in command]))
        lines.append("if errorlevel 1 exit /b 1")
    batch = cwd / "integration_build.bat"
    batch.write_text("\r\n".join(lines) + "\r\n", encoding="ascii", newline="")
    return _run(["cmd.exe", "/d", "/s", "/c", str(batch)], cwd=cwd)


def _select_queries(info, limit: int) -> list[Query]:
    queries = list(iter_d_queries(info))
    if limit > 0 and len(queries) > limit:
        # Deterministic, evenly spaced sample.  The sample is only for the
        # integration build; full-domain conformance remains the conformance
        # runner's job.
        step = max(1, len(queries) // limit)
        queries = queries[::step][:limit]
    return queries


def _comparison_summary(report: ComparisonReport) -> dict:
    return {
        "queries": report.queries,
        "divergence_count": report.divergence_count,
        "oracle_errors": report.oracle_errors,
        "rules_matched": len(report.rules_matched),
        "ok": report.ok,
        "divergences": report.divergences[:20],
    }


def _binary(relative: str) -> Path:
    """Resolve an oracle binary with the host executable suffix."""

    path = ROOT / relative
    if os.name == "nt":
        path = path.with_name(path.name + ".exe")
    return path


def _consumer_path(build_dir: Path) -> Optional[Path]:
    for candidate in (
        build_dir / "legalizer-dsl-consumer.exe",
        build_dir / "legalizer-dsl-consumer",
        build_dir / "Release" / "legalizer-dsl-consumer.exe",
        build_dir / "Debug" / "legalizer-dsl-consumer.exe",
    ):
        if candidate.is_file():
            return candidate
    return None


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="toycpu", choices=sorted(TARGETS))
    parser.add_argument("--rules", help="override the target's LDSL file")
    parser.add_argument("--out-dir", default="out/integration")
    parser.add_argument("--out", default="out/integration-report.json")
    parser.add_argument("--limit", type=int, default=20000,
                        help="sampled query count; 0 means the full D")
    parser.add_argument(
        "--llvm-commit",
        default=None,
        help="LLVM commit/version pin; defaults to claims/claims.json",
    )
    parser.add_argument("--no-build", action="store_true",
                        help="reuse an existing consumer build")
    parser.add_argument("--skip-reference", action="store_true",
                        help="do not run the hand-written/generated oracle binaries")
    args = parser.parse_args(argv)
    llvm_commit = args.llvm_commit or _ledger_llvm_commit()
    if not llvm_commit or llvm_commit == "unknown":
        print(
            "error: --llvm-commit is required (or pin llvm_commit in "
            "claims/claims.json)",
            file=sys.stderr,
        )
        return 2

    target = TARGETS[args.target]
    rules = Path(args.rules) if args.rules else ROOT / target["rules"]
    if not rules.is_file():
        print(f"error: rules file not found: {rules}", file=sys.stderr)
        return 2

    out_dir = ROOT / args.out_dir
    generated_dir = out_dir / args.target / "generated"
    wiring_dir = out_dir / args.target / "wiring"
    build_dir = out_dir / args.target / "build"
    report_path = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    info = parse_file(rules)
    stem = codegen._derive_output_stem(rules)
    namespace = target["namespace"] or codegen._namespace_from_rules_stem(rules.stem)

    rules_sha = _sha256(rules.read_bytes())
    codegen.generate(
        info,
        out_dir=generated_dir,
        rules_rel=codegen._rules_label(rules),
        rules_sha=rules_sha,
        namespace=namespace,
        namespace_source="derived" if target["namespace"] is None else "explicit",
        output_stem=stem,
        llvm_commit=llvm_commit,
    )
    codegen.emit_wiring(
        info,
        out_dir=generated_dir,
        wiring_dir=wiring_dir,
        namespace=namespace,
        output_stem=stem,
        llvm_commit=llvm_commit,
    )

    queries = _select_queries(info, args.limit)
    query_path = out_dir / args.target / "queries.jsonl"
    query_count, query_sha = _write_input(query_path, queries)

    generated_header = generated_dir / f"{stem}.h"
    generated_source = generated_dir / f"{stem}.cpp"
    generated: dict = {}
    for key, path in (("header", generated_header), ("source", generated_source)):
        if path.is_file():
            generated[key] = _relative_to_root(path)
            generated[f"{key}_sha256"] = _sha256_file(path)

    report: dict = {
        "schema": SCHEMA,
        "target": args.target,
        "status": "FAIL",
        "ok": False,
        "environment": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
        },
        "provenance": {
            "generator_version": GENERATOR_VERSION,
            "dsl_spec_version": DSL_SPEC_VERSION,
            "evaluator_sha256": hand_evaluator_sha256(),
            "codegen_sha256": codegen_sha256(),
            "llvm_commit": llvm_commit,
            "head_commit": _git("rev-parse", "HEAD") or "unknown",
            "worktree_dirty": _project_dirty(),
            "rules_sha256": rules_sha,
            "query_sha256": query_sha,
            "generated": generated,
            "consumer_sha256": None,
            "oracle_sha256": {},
        },
        "inputs": {
            "rules": codegen._rules_label(rules),
            "rules_sha256": rules_sha,
            "query_source": "D",
            "query_count": query_count,
            "query_limit": args.limit,
            "query_sha256": query_sha,
        },
        "artifacts": {
            "generated_dir": _relative_to_root(generated_dir),
            "wiring_dir": _relative_to_root(wiring_dir),
            "build_dir": _relative_to_root(build_dir),
            "query_file": _relative_to_root(query_path),
            "output_stem": stem,
            "namespace": namespace,
        },
        "comparisons": {},
    }

    if not args.no_build:
        skip = _build_consumer(
            target=target,
            build_dir=build_dir,
            wiring_dir=wiring_dir,
            stem=stem,
            report=report,
            report_path=report_path,
        )
        if skip is not None:
            report["status"] = "SKIP"
            report["skip_reason"] = skip
            _write_report(report_path, report)
            print(f"SKIP: environment: {skip}")
            print(f"report: {_relative_to_root(report_path)}")
            return 0

    consumer = _consumer_path(build_dir)
    if consumer is None:
        # A successful build that produced no consumer is a broken build, not
        # a missing environment.  Treating it as SKIP would make a red CI run
        # look like an environmental no-op.
        report["status"] = "FAIL"
        report["ok"] = False
        report["problems"] = [
            "consumer executable not found after the build; "
            "pass --no-build only after a successful build"
        ]
        report["artifacts"]["consumer"] = None
        _write_report(report_path, report)
        print(
            "error: consumer executable not found after the build",
            file=sys.stderr,
        )
        return 1

    report["artifacts"]["consumer"] = _relative_to_root(consumer)
    report["provenance"]["consumer_sha256"] = _sha256_file(consumer)

    consumer_report = ComparisonReport()
    compare(info, consumer, query_path, queries, consumer_report)
    report["comparisons"]["consumer"] = _comparison_summary(consumer_report)

    references_complete = not args.skip_reference
    all_ok = consumer_report.ok
    missing_references: list[str] = []
    if args.skip_reference:
        report["skip_reason"] = "--skip-reference requested"
    else:
        for label, key in (
            ("hand_written_oracle", "oracle"),
            ("generated_oracle", "generated_oracle"),
        ):
            path = _binary(target[key])
            if not path.is_file():
                references_complete = False
                missing_references.append(label)
                report["comparisons"][label] = {
                    "status": "SKIP",
                    "reason": f"binary not found: {_relative_to_root(path)}",
                }
                continue
            report["provenance"]["oracle_sha256"][label] = _sha256_file(path)
            report["artifacts"].setdefault("oracles", {})[label] = _relative_to_root(path)
            oracle_report = ComparisonReport()
            compare(info, path, query_path, queries, oracle_report)
            report["comparisons"][label] = _comparison_summary(oracle_report)
            all_ok = all_ok and oracle_report.ok

    if missing_references:
        report["skip_reason"] = (
            "missing reference comparison(s): " + ", ".join(missing_references)
        )
    if not consumer_report.ok:
        status = "FAIL"
    elif not references_complete:
        status = "SKIP"
    elif all_ok:
        status = "PASS"
    else:
        status = "FAIL"
    report["ok"] = status == "PASS"
    report["status"] = status
    _write_report(report_path, report)

    summary = report["comparisons"]["consumer"]
    print(
        f"integration: target={args.target} status={status} "
        f"queries={summary['queries']} divergences={summary['divergence_count']} "
        f"rules={summary['rules_matched']}"
    )
    for label, value in report["comparisons"].items():
        if value.get("status") == "SKIP":
            print(f"  {label}: SKIP ({value.get('reason')})")
        else:
            print(
                f"  {label}: divergences={value['divergence_count']} "
                f"errors={value['oracle_errors']}"
            )
    print(f"report: {_relative_to_root(report_path)}")
    return 0 if status in {"PASS", "SKIP"} else 1


def _build_consumer(
    *,
    target: dict,
    build_dir: Path,
    wiring_dir: Path,
    stem: str,
    report: dict,
    report_path: Path,
) -> Optional[str]:
    cmake = _find_cmake()
    llvm_dir = _find_llvm_dir(target)
    cxx = _find_cxx()
    missing = []
    if cmake is None:
        missing.append("cmake")
    if llvm_dir is None:
        missing.append("LLVMConfig.cmake")
    if cxx is None:
        missing.append("C++ compiler")
    if missing:
        return "missing " + ", ".join(missing)

    assert cmake is not None and llvm_dir is not None and cxx is not None
    build_dir.mkdir(parents=True, exist_ok=True)

    linker = _find_linker(cxx)
    ninja = _find_ninja()
    configure = [
        str(cmake),
        "--fresh",
        "-S",
        str(ROOT / "examples" / "integration"),
        "-B",
        str(build_dir),
        "-DCMAKE_BUILD_TYPE=Release",
        f"-DLLVM_DIR={llvm_dir.as_posix()}",
        f"-DLDSL_WIRING_DIR={wiring_dir.as_posix()}",
        f"-DLDSL_GENERATED_STEM={stem}",
        f"-DCMAKE_CXX_COMPILER={cxx.as_posix()}",
    ]
    if ninja is not None:
        configure.extend(["-G", "Ninja", f"-DCMAKE_MAKE_PROGRAM={ninja.as_posix()}"])
    if linker is not None:
        configure.append(f"-DCMAKE_LINKER={linker.as_posix()}")
    if target["extended_llt"]:
        configure.append("-DLDSL_USE_EXTENDED_LLT=ON")

    build = [str(cmake), "--build", str(build_dir), "--config", "Release"]

    vcvars = _find_vcvars() if os.name == "nt" and "clang" in cxx.name.lower() else None
    if vcvars is not None:
        msvc_lib = _msvc_lib_dir(vcvars)
        sdk_libs = _windows_sdk_libs()
        path_prefix = [cxx.parent]
        if ninja is not None:
            path_prefix.append(ninja.parent)
        result = _run_windows_batch(
            [configure, build],
            cwd=build_dir,
            vcvars=vcvars,
            msvc_lib=msvc_lib,
            sdk_libs=sdk_libs,
            path_prefix=path_prefix,
        )
        report["environment"]["build_shell"] = "vcvars64"
        report["environment"]["vcvars"] = str(vcvars)
    else:
        result = _run(configure, cwd=ROOT)
        if result.returncode == 0:
            result = _run(build, cwd=ROOT)

    report["environment"]["cmake"] = str(cmake)
    report["environment"]["llvm_dir"] = llvm_dir.as_posix()
    report["environment"]["cxx"] = cxx.as_posix()
    report["environment"]["linker"] = linker.as_posix() if linker else None
    report["environment"]["ninja"] = ninja.as_posix() if ninja else None
    report["environment"]["build_returncode"] = result.returncode
    report["environment"]["build_output_tail"] = result.stdout[-8000:]

    if result.returncode != 0:
        report["status"] = "FAIL"
        report["skip_reason"] = "build failed"
        _write_report(report_path, report)
        print("error: integration build failed", file=sys.stderr)
        print(result.stdout[-8000:], file=sys.stderr)
        raise SystemExit(1)
    return None


def _sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256(path.read_bytes())


def _git(*args: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _project_dirty() -> Optional[bool]:
    status = _git("status", "--porcelain")
    if status is None:
        # Cannot prove the tree is clean; record None so the status gate
        # rejects the report instead of assuming a clean worktree.
        return None
    if not status:
        return False
    for line in status.splitlines():
        path = line[3:].strip().strip('"').replace("\\", "/")
        if path in {"AGENTS.md", ".codex"} or path.startswith(".codex/"):
            continue
        return True
    return False


def _ledger_llvm_commit() -> Optional[str]:
    try:
        ledger = json.loads(
            (ROOT / "claims" / "claims.json").read_text(encoding="utf-8-sig")
        )
    except (OSError, json.JSONDecodeError):
        return None
    value = ledger.get("llvm_commit")
    return value if isinstance(value, str) and value else None


def _relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    raise SystemExit(main())
