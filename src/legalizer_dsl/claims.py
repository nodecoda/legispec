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
    python tools/legalizer_dsl/claims.py verify
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

_HERE = pathlib.Path(__file__).resolve().parent
ROOT = _HERE.parents[1]                                  # repo root
RULES = ROOT / "rules"
OUT = pathlib.Path(os.environ.get("LEGALIZER_DSL_OUT", ROOT / "out"))
LEDGER = ROOT / "claims" / "claims.json"
REPORT_MD = ROOT / "claims" / "claim-ledger.md"

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


SCALAR_EXCLUDES = (
    "vector / scalable vector",
    "memory descriptors and atomics",
    "FP (F/D) unless stated",
    "calls / ABI",
)
RISCV_FEATURE_EXCLUDES = (
    "M, Zmmul, Zbb, Zbc, Zbkc, Zbkb, P, V",
    "vector / scalable vector",
    "memory descriptors and atomics",
    "FP (F/D) and calls",
)

CLAIMS: tuple[Claim, ...] = (
    Claim(
        id="toycpu",
        rules="toycpu.ldsl",
        report="conformance-report.json",
        oracle="oracle/legalizer-oracle.exe",
        scope="full ToyCPU policy layer",
        queries=243714,
        rules_total=89,
        rules_matched=87,
        declared_unreachable=2,
    ),
    Claim(
        id="toycpu-generated",
        rules="toycpu.ldsl",
        report="conformance-gen-report.json",
        oracle="oracle/legalizer-oracle-gen.exe",
        scope="C++ generated from toycpu.ldsl by codegen.py",
        queries=243714,
        rules_total=89,
        rules_matched=87,
        declared_unreachable=2,
    ),
    Claim(
        id="x86-scalar",
        rules="x86-scalar.ldsl",
        report="x86-scalar-conformance-report.json",
        oracle="oracle/legalizer-oracle-x86.exe",
        scope="X86-64 integer + pointer shapes",
        excludes=("vector", "FP", "memory descriptors", "atomics", "custom"),
        queries=7591,
        rules_total=69,
        rules_matched=69,
    ),
    Claim(
        id="x86-generated",
        rules="x86-scalar.ldsl",
        report="x86-gen-conformance-report.json",
        oracle="oracle/legalizer-oracle-x86-gen.exe",
        scope="C++ generated from x86-scalar.ldsl by codegen.py",
        excludes=("vector", "FP", "memory descriptors", "atomics", "custom"),
        queries=7591,
        rules_total=69,
        rules_matched=69,
    ),
    Claim(
        id="aarch64-scalar",
        llvm_build=LLVM_BUILD_FULL,
        rules="aarch64-scalar.ldsl",
        report="aarch64-scalar-conformance-report.json",
        oracle="oracle-aarch64/legalizer-oracle-aarch64.exe",
        scope="AArch64 generic scalar subset",
        excludes=("vector / SVE / FP16", "memory descriptors", "atomics"),
        queries=8161,
        rules_total=91,
        rules_matched=91,
    ),
    Claim(
        id="aarch64-generated",
        llvm_build=LLVM_BUILD_FULL,
        rules="aarch64-scalar.ldsl",
        report="aarch64-gen-conformance-report.json",
        oracle="oracle-aarch64/legalizer-oracle-aarch64-gen.exe",
        scope="C++ generated from aarch64-scalar.ldsl by codegen.py",
        excludes=("vector / SVE / FP16", "memory descriptors", "atomics"),
        queries=8161,
        rules_total=91,
        rules_matched=91,
    ),
    Claim(
        id="aarch64-cssc-scalar",
        llvm_build=LLVM_BUILD_FULL,
        rules="aarch64-cssc-scalar.ldsl",
        report="aarch64-cssc-scalar-conformance-report.json",
        oracle="oracle-aarch64/legalizer-oracle-aarch64-cssc.exe",
        scope="AArch64 scalar subset with +cssc",
        excludes=("vector / SVE / FP16", "memory descriptors", "atomics"),
        queries=8161,
        rules_total=95,
        rules_matched=95,
    ),
    Claim(
        id="aarch64-cssc-generated",
        llvm_build=LLVM_BUILD_FULL,
        rules="aarch64-cssc-scalar.ldsl",
        report="aarch64-cssc-gen-conformance-report.json",
        oracle="oracle-aarch64/legalizer-oracle-aarch64-cssc-gen.exe",
        scope="C++ generated from aarch64-cssc-scalar.ldsl by codegen.py",
        excludes=("vector / SVE / FP16", "memory descriptors", "atomics"),
        queries=8161,
        rules_total=95,
        rules_matched=95,
    ),
    Claim(
        id="aarch64-pointer",
        llvm_build=LLVM_BUILD_FULL,
        rules="aarch64-pointer.ldsl",
        report="aarch64-pointer-conformance-report.json",
        oracle="oracle-aarch64/legalizer-oracle-aarch64.exe",
        scope="AArch64 pointer subset",
        excludes=("vector / SVE / FP16", "memory descriptors", "atomics"),
        queries=924,
        rules_total=17,
        rules_matched=17,
    ),
    Claim(
        id="aarch64-atomic",
        llvm_build=LLVM_BUILD_FULL,
        rules="aarch64-atomic.ldsl",
        report="aarch64-atomic-conformance-report.json",
        oracle="oracle-aarch64/legalizer-oracle-aarch64.exe",
        scope="AArch64 generic atomic subset (UseOutlineAtomics=false)",
        excludes=("vector / SVE / FP16", "memory descriptors", "outline atomics", "LSE"),
        queries=612,
        rules_total=11,
        rules_matched=11,
    ),
    Claim(
        id="aarch64-atomic-generated",
        llvm_build=LLVM_BUILD_FULL,
        rules="aarch64-atomic.ldsl",
        report="aarch64-atomic-gen-conformance-report.json",
        oracle="oracle-aarch64/legalizer-oracle-aarch64-atomic-gen.exe",
        scope="C++ generated from aarch64-atomic.ldsl by codegen.py",
        excludes=("vector / SVE / FP16", "memory descriptors", "outline atomics", "LSE"),
        queries=612,
        rules_total=11,
        rules_matched=11,
    ),
    Claim(
        id="aarch64-atomic-outline",
        llvm_build=LLVM_BUILD_FULL,
        rules="aarch64-atomic-outline.ldsl",
        report="aarch64-atomic-outline-conformance-report.json",
        oracle="oracle-aarch64/legalizer-oracle-aarch64-outline.exe",
        scope="AArch64 atomic subset with +outline-atomics (UseOutlineAtomics=true)",
        excludes=("vector / SVE / FP16", "memory descriptors", "LSE"),
        queries=612,
        rules_total=10,
        rules_matched=10,
    ),
    Claim(
        id="aarch64-atomic-outline-generated",
        llvm_build=LLVM_BUILD_FULL,
        rules="aarch64-atomic-outline.ldsl",
        report="aarch64-atomic-outline-gen-conformance-report.json",
        oracle="oracle-aarch64/legalizer-oracle-aarch64-atomic-outline-gen.exe",
        scope="C++ generated from aarch64-atomic-outline.ldsl by codegen.py",
        excludes=("vector / SVE / FP16", "memory descriptors", "LSE"),
        queries=612,
        rules_total=10,
        rules_matched=10,
    ),
    Claim(
        id="aarch64-atomic-lse",
        llvm_build=LLVM_BUILD_FULL,
        rules="aarch64-atomic.ldsl",
        report="aarch64-atomic-lse-conformance-report.json",
        oracle="oracle-aarch64/legalizer-oracle-aarch64-lse.exe",
        scope="AArch64 atomic subset with +lse (policy table identical to generic)",
        excludes=("vector / SVE / FP16", "memory descriptors", "outline atomics"),
        queries=612,
        rules_total=11,
        rules_matched=11,
    ),
    Claim(
        id="aarch64-vector",
        llvm_build=LLVM_BUILD_FULL,
        rules="aarch64-vector.ldsl",
        report="aarch64-vector-conformance-report.json",
        oracle="oracle-aarch64/legalizer-oracle-aarch64.exe",
        scope=(
            "AArch64 generic fixed-vector subset: min/max element-count "
            "clamps, abs-difference/average legalFor+lower, CLMUL legalFor"
        ),
        excludes=(
            "scalable vectors (SVE)",
            "FP16",
            "memory descriptors",
            "atomics",
            "custom handlers",
        ),
        queries=176,
        rules_total=11,
        rules_matched=10,
        declared_unreachable=1,
    ),
    Claim(
        id="aarch64-vector-generated",
        llvm_build=LLVM_BUILD_FULL,
        rules="aarch64-vector.ldsl",
        report="aarch64-vector-gen-conformance-report.json",
        oracle="oracle-aarch64/legalizer-oracle-aarch64-vector-gen.exe",
        scope=(
            "C++ generated from aarch64-vector.ldsl by codegen.py"
        ),
        excludes=(
            "scalable vectors (SVE)",
            "FP16",
            "memory descriptors",
            "atomics",
            "custom handlers",
        ),
        queries=176,
        rules_total=11,
        rules_matched=10,
        declared_unreachable=1,
    ),
    Claim(
        id="aarch64-vector-arith",
        llvm_build=LLVM_BUILD_FULL,
        rules="aarch64-vector-arith.ldsl",
        report="aarch64-vector-arith-conformance-report.json",
        oracle="oracle-aarch64/legalizer-oracle-aarch64.exe",
        scope=(
            "AArch64 generic G_ADD/G_SUB/G_AND/G_OR/G_XOR: legalFor, "
            "scalar clamps, lane-count clamps, minScalarOrEltIf, "
            "scalarizeIf and moreElementsToNextPow2"
        ),
        excludes=(
            "scalable vectors (SVE)",
            "memory descriptors",
            "atomics",
            "custom handlers",
            "FP and calls",
        ),
        queries=120,
        rules_total=15,
        rules_matched=13,
        declared_unreachable=2,
    ),
    Claim(
        id="aarch64-vector-arith-generated",
        llvm_build=LLVM_BUILD_FULL,
        rules="aarch64-vector-arith.ldsl",
        report="aarch64-vector-arith-gen-conformance-report.json",
        oracle=(
            "oracle-aarch64/legalizer-oracle-aarch64-vector-arith-gen.exe"
        ),
        scope=(
            "C++ generated from aarch64-vector-arith.ldsl by codegen.py"
        ),
        excludes=(
            "scalable vectors (SVE)",
            "memory descriptors",
            "atomics",
            "custom handlers",
            "FP and calls",
        ),
        queries=120,
        rules_total=15,
        rules_matched=13,
        declared_unreachable=2,
    ),
    Claim(
        id="aarch64-vector-mul",
        llvm_build=LLVM_BUILD_FULL,
        rules="aarch64-vector-mul.ldsl",
        report="aarch64-vector-mul-conformance-report.json",
        oracle="oracle-aarch64/legalizer-oracle-aarch64.exe",
        scope=(
            "AArch64 generic G_MUL: legalFor, scalar clamps, lane-count "
            "clamps, minScalarOrEltIf, scalarizeIf and "
            "moreElementsToNextPow2"
        ),
        excludes=(
            "scalable vectors (SVE)",
            "memory descriptors",
            "atomics",
            "custom handlers",
            "FP and calls",
        ),
        queries=24,
        rules_total=15,
        rules_matched=13,
        declared_unreachable=2,
    ),
    Claim(
        id="aarch64-vector-mul-generated",
        llvm_build=LLVM_BUILD_FULL,
        rules="aarch64-vector-mul.ldsl",
        report="aarch64-vector-mul-gen-conformance-report.json",
        oracle=(
            "oracle-aarch64/legalizer-oracle-aarch64-vector-mul-gen.exe"
        ),
        scope=(
            "C++ generated from aarch64-vector-mul.ldsl by codegen.py"
        ),
        excludes=(
            "scalable vectors (SVE)",
            "memory descriptors",
            "atomics",
            "custom handlers",
            "FP and calls",
        ),
        queries=24,
        rules_total=15,
        rules_matched=13,
        declared_unreachable=2,
    ),
    Claim(
        id="aarch64-vector-shift",
        llvm_build=LLVM_BUILD_FULL,
        rules="aarch64-vector-shift.ldsl",
        report="aarch64-vector-shift-conformance-report.json",
        oracle="oracle-aarch64/legalizer-oracle-aarch64.exe",
        scope=(
            "AArch64 generic G_SHL/G_ASHR/G_LSHR: two-slot scalar/vector "
            "interaction (minScalarSameAs, min/maxScalarEltSameAsIf), "
            "scalar clamps, lane-count clamps, scalarizeIf and "
            "moreElementsToNextPow2"
        ),
        excludes=(
            "scalable vectors (SVE)",
            "memory descriptors",
            "atomics",
            "custom handlers",
            "FP and calls",
        ),
        queries=270,
        rules_total=21,
        rules_matched=19,
        declared_unreachable=2,
    ),
    Claim(
        id="aarch64-vector-shift-generated",
        llvm_build=LLVM_BUILD_FULL,
        rules="aarch64-vector-shift.ldsl",
        report="aarch64-vector-shift-gen-conformance-report.json",
        oracle=(
            "oracle-aarch64/legalizer-oracle-aarch64-vector-shift-gen.exe"
        ),
        scope=(
            "C++ generated from aarch64-vector-shift.ldsl by codegen.py"
        ),
        excludes=(
            "scalable vectors (SVE)",
            "memory descriptors",
            "atomics",
            "custom handlers",
            "FP and calls",
        ),
        queries=270,
        rules_total=21,
        rules_matched=19,
        declared_unreachable=2,
    ),
    Claim(
        id="aarch64-icmp",
        llvm_build=LLVM_BUILD_FULL,
        rules="aarch64-icmp.ldsl",
        report="aarch64-icmp-conformance-report.json",
        oracle="oracle-aarch64/legalizer-oracle-aarch64.exe",
        scope=(
            "AArch64 generic G_ICMP: result/operand slot relation, "
            "widenScalarOrEltToNextPow2, minScalarOrElt, pointer-vector "
            "result widening and scalar/lane clamps"
        ),
        excludes=(
            "scalable vectors (SVE)",
            "memory descriptors",
            "atomics",
            "custom handler bodies",
            "FP and calls",
        ),
        queries=173,
        rules_total=23,
        rules_matched=19,
        declared_unreachable=3,
        declared_shadowed=1,
    ),
    Claim(
        id="aarch64-icmp-generated",
        llvm_build=LLVM_BUILD_FULL,
        rules="aarch64-icmp.ldsl",
        report="aarch64-icmp-gen-conformance-report.json",
        oracle=(
            "oracle-aarch64/legalizer-oracle-aarch64-icmp-gen.exe"
        ),
        scope=(
            "C++ generated from aarch64-icmp.ldsl by codegen.py"
        ),
        excludes=(
            "scalable vectors (SVE)",
            "memory descriptors",
            "atomics",
            "custom handler bodies",
            "FP and calls",
        ),
        queries=173,
        rules_total=23,
        rules_matched=19,
        declared_unreachable=3,
        declared_shadowed=1,
    ),
    Claim(
        id="aarch64-fcmp",
        llvm_build=LLVM_BUILD_FULL,
        rules="aarch64-fcmp.ldsl",
        report="aarch64-fcmp-conformance-report.json",
        oracle="oracle-aarch64/legalizer-oracle-aarch64.exe",
        scope=(
            "AArch64 generic G_FCMP: integer mask result, float operand, "
            "mixed-slot widening, scalarize of 128-bit vectors, lane clamps "
            "and the s128 libcall"
        ),
        excludes=(
            "scalable vectors (SVE)",
            "memory descriptors",
            "atomics",
            "custom handler bodies",
            "libcall target lowering / ABI",
            "+fullfp16 (HasFP16 = false is frozen)",
        ),
        queries=185,
        rules_total=14,
        rules_matched=13,
        declared_unreachable=1,
    ),
    Claim(
        id="aarch64-fcmp-generated",
        llvm_build=LLVM_BUILD_FULL,
        rules="aarch64-fcmp.ldsl",
        report="aarch64-fcmp-gen-conformance-report.json",
        oracle=(
            "oracle-aarch64/legalizer-oracle-aarch64-fcmp-gen.exe"
        ),
        scope=(
            "C++ generated from aarch64-fcmp.ldsl by codegen.py, with "
            "LLT::setUseExtended(true) declared explicitly"
        ),
        excludes=(
            "scalable vectors (SVE)",
            "memory descriptors",
            "atomics",
            "custom handler bodies",
            "libcall target lowering / ABI",
            "+fullfp16 (HasFP16 = false is frozen)",
        ),
        queries=185,
        rules_total=14,
        rules_matched=13,
        declared_unreachable=1,
    ),
    Claim(
        id="aarch64-concat-vectors",
        llvm_build=LLVM_BUILD_FULL,
        rules="aarch64-concat-vectors.ldsl",
        report="aarch64-concat-vectors-conformance-report.json",
        oracle="oracle-aarch64/legalizer-oracle-aarch64.exe",
        scope=(
            "AArch64 generic G_CONCAT_VECTORS: legalFor pairs, the sub-byte "
            "custom escape hatch, and the concat bitcast (source total size "
            "plus result lane division)"
        ),
        excludes=(
            "scalable vectors (SVE)",
            "custom handler bodies (legalizeConcatVectors MIR)",
            "bitcast successors outside the concat contract",
            "memory descriptors and atomics",
            "calls / ABI",
        ),
        queries=28,
        rules_total=3,
        rules_matched=3,
    ),
    Claim(
        id="aarch64-concat-vectors-generated",
        llvm_build=LLVM_BUILD_FULL,
        rules="aarch64-concat-vectors.ldsl",
        report="aarch64-concat-vectors-gen-conformance-report.json",
        oracle=(
            "oracle-aarch64/legalizer-oracle-aarch64-concat-gen.exe"
        ),
        scope=(
            "C++ generated from aarch64-concat-vectors.ldsl by codegen.py, "
            "with LLT::setUseExtended(true) declared explicitly"
        ),
        excludes=(
            "scalable vectors (SVE)",
            "custom handler bodies (legalizeConcatVectors MIR)",
            "bitcast successors outside the concat contract",
            "memory descriptors and atomics",
            "calls / ABI",
        ),
        queries=28,
        rules_total=3,
        rules_matched=3,
    ),
    Claim(
        id="riscv64-scalar",
        llvm_build=LLVM_BUILD_FULL,
        rules="riscv64-scalar.ldsl",
        report="riscv64-scalar-conformance-report.json",
        oracle="oracle-riscv/legalizer-oracle-riscv.exe",
        scope="RV64 generic-rv64 (+64bit,+i) scalar subset",
        excludes=RISCV_FEATURE_EXCLUDES,
        queries=7861,
        rules_total=103,
        rules_matched=103,
    ),
    Claim(
        id="riscv64-generated",
        llvm_build=LLVM_BUILD_FULL,
        rules="riscv64-scalar.ldsl",
        report="riscv64-gen-conformance-report.json",
        oracle="oracle-riscv/legalizer-oracle-riscv-gen.exe",
        scope="C++ generated from riscv64-scalar.ldsl by codegen.py (RV64)",
        excludes=RISCV_FEATURE_EXCLUDES,
        queries=7861,
        rules_total=103,
        rules_matched=103,
    ),
    Claim(
        id="riscv64-mzb-generated",
        llvm_build=LLVM_BUILD_FULL,
        rules="riscv64-mzb-scalar.ldsl",
        report="riscv64-mzb-gen-conformance-report.json",
        oracle="oracle-riscv/legalizer-oracle-riscv-mzb-gen.exe",
        scope="C++ generated from riscv64-mzb-scalar.ldsl by codegen.py",
        excludes=RISCV_FEATURE_EXCLUDES,
        queries=7861,
        rules_total=118,
        rules_matched=117,
        declared_unreachable=1,
    ),
    Claim(
        id="riscv64-pointer",
        llvm_build=LLVM_BUILD_FULL,
        rules="riscv64-pointer.ldsl",
        report="riscv64-pointer-conformance-report.json",
        oracle="oracle-riscv/legalizer-oracle-riscv.exe",
        scope="RV64 pointer subset",
        excludes=RISCV_FEATURE_EXCLUDES,
        queries=770,
        rules_total=32,
        rules_matched=32,
    ),
    Claim(
        id="riscv64-mzb-scalar",
        llvm_build=LLVM_BUILD_FULL,
        rules="riscv64-mzb-scalar.ldsl",
        report="riscv64-mzb-scalar-conformance-report.json",
        oracle="oracle-riscv/legalizer-oracle-riscv-mzb.exe",
        scope="RV64 scalar subset with +m,+zmmul,+zbb",
        excludes=RISCV_FEATURE_EXCLUDES,
        queries=7861,
        rules_total=118,
        rules_matched=117,
        declared_unreachable=1,
    ),
    Claim(
        id="riscv64-pointer-mzb",
        llvm_build=LLVM_BUILD_FULL,
        rules="riscv64-pointer.ldsl",
        report="riscv64-pointer-mzb-conformance-report.json",
        oracle="oracle-riscv/legalizer-oracle-riscv-mzb.exe",
        scope="RV64 pointer subset with +m,+zmmul,+zbb",
        excludes=RISCV_FEATURE_EXCLUDES,
        queries=770,
        rules_total=32,
        rules_matched=32,
    ),
    Claim(
        id="riscv32-scalar",
        llvm_build=LLVM_BUILD_FULL,
        rules="riscv32-scalar.ldsl",
        report="riscv32-scalar-conformance-report.json",
        oracle="oracle-riscv/legalizer-oracle-riscv32.exe",
        scope="RV32 generic-rv32 (+32bit,+i) scalar subset",
        excludes=RISCV_FEATURE_EXCLUDES,
        queries=7861,
        rules_total=100,
        rules_matched=100,
    ),
    Claim(
        id="riscv32-generated",
        llvm_build=LLVM_BUILD_FULL,
        rules="riscv32-scalar.ldsl",
        report="riscv32-gen-conformance-report.json",
        oracle="oracle-riscv/legalizer-oracle-riscv32-gen.exe",
        scope="C++ generated from riscv32-scalar.ldsl by codegen.py",
        excludes=RISCV_FEATURE_EXCLUDES,
        queries=7861,
        rules_total=100,
        rules_matched=100,
    ),
    Claim(
        id="riscv32-mzb-scalar",
        llvm_build=LLVM_BUILD_FULL,
        rules="riscv32-mzb-scalar.ldsl",
        report="riscv32-mzb-scalar-conformance-report.json",
        oracle="oracle-riscv/legalizer-oracle-riscv32-mzb.exe",
        scope="RV32 scalar subset with +m,+zmmul,+zbb",
        excludes=RISCV_FEATURE_EXCLUDES,
        queries=7861,
        rules_total=115,
        rules_matched=111,
        declared_unreachable=4,
    ),
    Claim(
        id="riscv32-pointer",
        llvm_build=LLVM_BUILD_FULL,
        rules="riscv32-pointer.ldsl",
        report="riscv32-pointer-conformance-report.json",
        oracle="oracle-riscv/legalizer-oracle-riscv32.exe",
        scope="RV32 pointer subset",
        excludes=RISCV_FEATURE_EXCLUDES,
        queries=770,
        rules_total=31,
        rules_matched=31,
    ),
    Claim(
        id="riscv32-pointer-mzb",
        llvm_build=LLVM_BUILD_FULL,
        rules="riscv32-pointer.ldsl",
        report="riscv32-pointer-mzb-conformance-report.json",
        oracle="oracle-riscv/legalizer-oracle-riscv32-mzb.exe",
        scope="RV32 pointer subset with +m,+zmmul,+zbb",
        excludes=RISCV_FEATURE_EXCLUDES,
        queries=770,
        rules_total=31,
        rules_matched=31,
    ),
    Claim(
        id="riscv-conditional-gconstant-rv64",
        llvm_build=LLVM_BUILD_FULL,
        rules="riscv-conditional-gconstant.ldsl",
        report="riscv-conditional-gconstant-rv64-conformance.json",
        oracle="oracle-riscv/legalizer-oracle-riscv.exe",
        scope="G_CONSTANT guard experiment, RV64",
        excludes=("everything outside G_CONSTANT",),
        config={"is64bit": True, "sXLen": "s64"},
        queries=9,
        rules_total=4,
        rules_matched=4,
    ),
    Claim(
        id="riscv-conditional-gconstant-rv32",
        llvm_build=LLVM_BUILD_FULL,
        rules="riscv-conditional-gconstant.ldsl",
        report="riscv-conditional-gconstant-rv32-conformance.json",
        oracle="oracle-riscv/legalizer-oracle-riscv32.exe",
        scope="G_CONSTANT guard experiment, RV32",
        excludes=("everything outside G_CONSTANT",),
        config={"is64bit": False, "sXLen": "s32"},
        queries=9,
        rules_total=4,
        rules_matched=4,
    ),
    Claim(
        id="riscv-conditional-scalar-rv64",
        llvm_build=LLVM_BUILD_FULL,
        rules="riscv-scalar-conditional.ldsl",
        report="riscv-conditional-scalar-rv64-conformance.json",
        oracle="oracle-riscv/legalizer-oracle-riscv.exe",
        scope="one-source scalar subset, RV64",
        excludes=RISCV_FEATURE_EXCLUDES,
        config={"is64bit": True, "sXLen": "s64", "sDoubleXLen": "s128"},
        queries=7861,
        rules_total=108,
        rules_matched=108,
    ),
    Claim(
        id="riscv-conditional-scalar-rv32",
        llvm_build=LLVM_BUILD_FULL,
        rules="riscv-scalar-conditional.ldsl",
        report="riscv-conditional-scalar-rv32-conformance.json",
        oracle="oracle-riscv/legalizer-oracle-riscv32.exe",
        scope="one-source scalar subset, RV32",
        excludes=RISCV_FEATURE_EXCLUDES,
        config={"is64bit": False, "sXLen": "s32", "sDoubleXLen": "s64"},
        queries=7861,
        rules_total=101,
        rules_matched=100,
        declared_unreachable=1,
    ),
)


def _norm(path: pathlib.Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _same_location(reported: object, expected: pathlib.Path) -> bool:
    """True when a report points at this file, here or in another checkout.

    Evidence may be produced anywhere (a build tree, a collector's out/ dir),
    so an absolute path is not an identity.  Paths match when they normalize
    identically, or when they end in the same file name.  Content identity is
    carried by the sha256 pins that are checked separately; this check only
    catches "the report is about some other file".
    """
    if not reported:
        return False
    if _norm(pathlib.Path(str(reported))) == _norm(expected):
        return True
    return pathlib.Path(str(reported)).name == expected.name


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(claim: Claim) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": claim.id,
        "scope": claim.scope,
        "excludes": list(claim.excludes),
        "config": claim.config,
        "problems": [],
    }
    rules = RULES / claim.rules
    report = OUT / claim.report
    oracle = OUT / claim.oracle

    if not rules.exists():
        result["problems"].append(f"rules file missing: {rules}")
        return result
    result["rules"] = claim.rules
    result["rules_sha256"] = _sha256(rules)

    if not report.exists():
        result["problems"].append(f"report missing: {report}")
        return result
    if not oracle.exists():
        result["problems"].append(f"oracle missing: {oracle}")

    data = json.loads(report.read_text(encoding="utf-8"))
    result["report"] = claim.report
    result["oracle"] = claim.oracle
    result["oracle_size"] = oracle.stat().st_size if oracle.exists() else None

    if data.get("schema") != "legalizer-dsl-conformance-v1":
        result["problems"].append(f"unexpected schema: {data.get('schema')!r}")

    reported_rules = data.get("inputs", {}).get("rules")
    if not _same_location(reported_rules, rules):
        result["problems"].append(
            f"report was produced from {reported_rules!r}, not {claim.rules!r}"
        )

    reported_oracle = data.get("reference", {}).get("oracle")
    if not _same_location(reported_oracle, oracle):
        result["problems"].append(
            f"report used oracle {reported_oracle!r}, not {claim.oracle!r}"
        )

    reported_build = data.get("reference", {}).get("llvm_build")
    if not _same_location(reported_build, pathlib.Path(claim.llvm_build)):
        result["problems"].append(
            f"report records llvm_build {reported_build!r}, "
            f"ledger says {claim.llvm_build!r}"
        )

    reported_config = data.get("inputs", {}).get("config", {})
    if reported_config != claim.config:
        result["problems"].append(
            f"report config {reported_config!r} != ledger config {claim.config!r}"
        )

    reported_rules_sha = data.get("inputs", {}).get("rules_sha256")
    if not reported_rules_sha:
        result["problems"].append("report has no rules content hash")
    elif reported_rules_sha != result["rules_sha256"]:
        result["problems"].append(
            "report was produced from different rules content; re-run the claim"
        )

    reported_oracle_sha = data.get("reference", {}).get("oracle_sha256")
    current_oracle_sha = _sha256(oracle) if oracle.exists() else None
    if not reported_oracle_sha:
        result["problems"].append(
            "report predates oracle pinning; re-run the claim"
        )
    elif reported_oracle_sha != current_oracle_sha:
        result["problems"].append(
            "oracle binary changed since the report was produced"
        )
    result["oracle_sha256"] = current_oracle_sha

    comparison = data.get("comparison", {})
    if comparison.get("divergence_count") != 0:
        result["problems"].append(
            f"divergences: {comparison.get('divergence_count')}"
        )
    if comparison.get("oracle_errors"):
        result["problems"].append(f"oracle errors: {comparison.get('oracle_errors')}")
    if comparison.get("queries_compared") != claim.queries:
        result["problems"].append(
            f"query count {comparison.get('queries_compared')} != {claim.queries}"
        )

    domain = data.get("domain", {})
    if data.get("domain_closure_ok") is False:
        result["problems"].append("domain closure not ok")
    if domain.get("closure_outside_d_count"):
        result["problems"].append(
            f"closure outside D: {domain.get('closure_outside_d_count')}"
        )

    coverage = data.get("coverage", {})
    if coverage.get("rules") != claim.rules_total:
        result["problems"].append(
            f"rule count {coverage.get('rules')} != {claim.rules_total}"
        )
    if coverage.get("rules_matched") != claim.rules_matched:
        result["problems"].append(
            f"matched {coverage.get('rules_matched')} != {claim.rules_matched}"
        )
    unmatched = coverage.get("rules_unmatched") or []
    explained = claim.declared_unreachable + claim.declared_shadowed
    if len(unmatched) != explained:
        result["problems"].append(
            f"{len(unmatched)} unmatched rules, ledger declares "
            f"{claim.declared_unreachable} unreachable + "
            f"{claim.declared_shadowed} shadowed"
        )

    result.update(
        {
            "oracle": claim.oracle,
            "queries": comparison.get("queries_compared"),
            "divergences": comparison.get("divergence_count"),
            "rules_total": coverage.get("rules"),
            "rules_matched": coverage.get("rules_matched"),
            "unmatched": unmatched,
            "declared_unreachable": claim.declared_unreachable,
            "declared_shadowed": claim.declared_shadowed,
            "llvm_commit": data.get("reference", {}).get("llvm_commit"),
            "llvm_build": claim.llvm_build,
            "ok": not result["problems"],
        }
    )
    return result


def audit_all() -> list[dict[str, Any]]:
    return [audit(claim) for claim in CLAIMS]


def check_staleness(entries: list[dict[str, Any]], previous: dict[str, Any]) -> None:
    pinned = {c["id"]: c for c in previous.get("claims", [])}
    for entry in entries:
        old = pinned.get(entry["id"], {})
        if (
            old.get("rules_sha256")
            and entry.get("rules_sha256")
            and old["rules_sha256"] != entry["rules_sha256"]
        ):
            entry["problems"].append(
                "STALE: rules file changed since this claim was validated; "
                "re-run it and refresh with `claims.py verify --write`"
            )
            entry["ok"] = False
        if (
            old.get("oracle_sha256")
            and entry.get("oracle_sha256")
            and old["oracle_sha256"] != entry["oracle_sha256"]
        ):
            entry["problems"].append(
                "STALE: oracle binary changed since this claim was validated; "
                "re-run it and refresh with `claims.py verify --write`"
            )
            entry["ok"] = False


def render_markdown(entries: list[dict[str, Any]], llvm_commit: Optional[str]) -> str:
    lines = [
        "# Claim ledger: what the DSL has proven",
        "",
        "Generated by `tools/legalizer_dsl/claims.py`. Do not edit by hand.",
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
    payload = {
        "schema": "legalizer-dsl-claims-v1",
        "llvm_commit": llvm_commit,
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
    if not LEDGER.exists():
        return {}
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("verify", "audit"), nargs="?", default="verify"
    )
    parser.add_argument("--write", action="store_true", help="rewrite the ledger")
    args = parser.parse_args(argv)

    entries = audit_all()
    previous = load_previous()
    # Staleness is a read-only warning: it says the ledger was written before
    # the artifacts changed.  With --write the caller has just re-run the
    # claims, so the hard checks in audit() decide whether the new ledger is
    # allowed to replace the old one.
    if not args.write:
        check_staleness(entries, previous)

    for e in entries:
        status = "PROVEN" if e["ok"] else "FAIL"
        print(
            f"{e['id']:<36} {status:<7} "
            f"q={e.get('queries', '-'):>7} d={e.get('divergences', '-'):>2} "
            f"r={e.get('rules_matched', '-')}/{e.get('rules_total', '-')}"
        )
        for problem in e["problems"]:
            print(f"    ! {problem}")

    failures = sum(1 for e in entries if not e["ok"])
    print(
        f"\nclaims={len(entries)} proven={len(entries) - failures} failed={failures}"
    )

    if args.write and not failures:
        write_ledger(entries)
        print(f"wrote {LEDGER.name} and {REPORT_MD.name}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())