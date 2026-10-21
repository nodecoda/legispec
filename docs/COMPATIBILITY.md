# Compatibility and upgrade policy

This document defines which generated artifacts and claim reports are valid
for which inputs, and the exact procedure for moving to a new LLVM pin.

The correctness contract itself is in [`CORRECTNESS.md`](CORRECTNESS.md).
Evidence storage and reproduction are in [`EVIDENCE.md`](EVIDENCE.md).

## 1. Pinned identity

| Component | Pinned value | Source of truth |
|---|---|---|
| Generator version | `0.2.0` | `pyproject.toml`, `legalizer_dsl.__version__` |
| LDSL specification | `1.0` | `codegen.DSL_SPEC_VERSION` |
| Claim contract | `1` | `claims.CLAIM_CONTRACT_VERSION` |
| Claim schema | `claims/claim-schema.json` | `claims.validate_contract` |
| LLVM commit | `d855d9e48067026317998cbff734004f328f833d` | `claims/claims.json` |
| Python | `>=3.10` | `pyproject.toml` |
| Evidence schema | `legalizer-dsl-conformance-v1`, `legalizer-dsl-integration-v1` | `conformance.py`, `integration_smoke.py` |

A generated tree is valid only when its `codegen-manifest.json` agrees with
all of the following:

```text
generator_version == legalizer_dsl.__version__
dsl_spec_version  == codegen.DSL_SPEC_VERSION
llvm_commit       == the claim/consumer pin
rules_sha256      == sha256 of the committed rules file
config            == the claim config (booleans are booleans, not strings)
namespace_source  == derived (the contract gate exercises derived namespaces;
                     explicit namespaces are covered by unit tests)
header_sha256 / source_sha256 == the emitted files
```

The header guard and the wiring include guard are part of the artifact
identity. They must differ per slice so two generated slices can coexist in
one translation unit; they must not include the `_rules_gen` suffix twice.

## 2. Target and config matrix

One generated artifact belongs to exactly one config. A config guard is
resolved at generation time; there is no runtime subtarget dispatch.

| Target | Rules file | Config | Claim id(s) | Queries | Rules matched |
|---|---|---|---|---:|---:|
| ToyCPU | `toycpu.ldsl` | default | `toycpu`, `toycpu-generated` | 243714 | 87/89 |
| X86-64 | `x86-scalar.ldsl` | default | `x86-scalar`, `x86-generated` | 7591 | 69/69 |
| AArch64 | `aarch64-scalar.ldsl` | default | `aarch64-scalar`, `aarch64-generated` | 8161 | 91/91 |
| AArch64 + CSSC | `aarch64-cssc-scalar.ldsl` | default | `aarch64-cssc-scalar`, `aarch64-cssc-generated` | 8161 | 95/95 |
| AArch64 | `aarch64-pointer.ldsl` | default | `aarch64-pointer` | 924 | 17/17 |
| AArch64 | `aarch64-atomic.ldsl` | generic | `aarch64-atomic`, `aarch64-atomic-generated` | 612 | 11/11 |
| AArch64 | `aarch64-atomic-outline.ldsl` | outline-atomics | `aarch64-atomic-outline`, `aarch64-atomic-outline-generated` | 612 | 10/10 |
| AArch64 | `aarch64-atomic.ldsl` | LSE | `aarch64-atomic-lse` | 612 | 11/11 |
| AArch64 | `aarch64-vector.ldsl` | fixed vectors | `aarch64-vector`, `aarch64-vector-generated` | 176 | 10/11 |
| AArch64 | `aarch64-vector-arith.ldsl` | fixed vectors | `aarch64-vector-arith`, `aarch64-vector-arith-generated` | 120 | 13/15 |
| AArch64 | `aarch64-vector-mul.ldsl` | fixed vectors | `aarch64-vector-mul`, `aarch64-vector-mul-generated` | 24 | 13/15 |
| AArch64 | `aarch64-vector-shift.ldsl` | fixed vectors | `aarch64-vector-shift`, `aarch64-vector-shift-generated` | 270 | 19/21 |
| AArch64 | `aarch64-icmp.ldsl` | fixed vectors | `aarch64-icmp`, `aarch64-icmp-generated` | 173 | 19/23 |
| AArch64 | `aarch64-fcmp.ldsl` | fixed vectors | `aarch64-fcmp`, `aarch64-fcmp-generated` | 185 | 13/14 |
| AArch64 | `aarch64-concat-vectors.ldsl` | fixed vectors | `aarch64-concat-vectors`, `aarch64-concat-vectors-generated` | 28 | 3/3 |
| RISC-V 64 | `riscv64-scalar.ldsl` | RV64 | `riscv64-scalar`, `riscv64-generated` | 7861 | 103/103 |
| RISC-V 64 + M/Zbb | `riscv64-mzb-scalar.ldsl` | RV64 | `riscv64-mzb-scalar`, `riscv64-mzb-generated` | 7861 | 117/118 |
| RISC-V 64 | `riscv64-pointer.ldsl` | RV64, pointer widths | `riscv64-pointer`, `riscv64-pointer-mzb` | 770 | 32/32 |
| RISC-V 32 | `riscv32-scalar.ldsl` | RV32 | `riscv32-scalar`, `riscv32-generated` | 7861 | 100/100 |
| RISC-V 32 + M/Zbb | `riscv32-mzb-scalar.ldsl` | RV32 | `riscv32-mzb-scalar` | 7861 | 111/115 |
| RISC-V 32 | `riscv32-pointer.ldsl` | RV32, pointer widths | `riscv32-pointer`, `riscv32-pointer-mzb` | 770 | 31/31 |
| RISC-V conditional | `riscv-conditional-gconstant.ldsl` | RV32 / RV64 | `riscv-conditional-gconstant-rv32`, `-rv64` | 9 | 4/4 |
| RISC-V conditional | `riscv-scalar-conditional.ldsl` | RV32 / RV64 | `riscv-conditional-scalar-rv32`, `-rv64` | 7861 | 100/101, 108/108 |

Unmatched rules are declared unreachable, not silent passes. The ledger's
`unmatched` field records each one; coverage reports summarize them.

The X86 claims were produced with `F:/llvm/build-toycpu`, which contains the
X86 backend. The identity that matters is the recorded LLVM commit and the
oracle hash, not the build-directory name.

## 3. What invalidates an artifact

| Change | Old artifact | Required action |
|---|---|---|
| Rules file bytes change | invalid | regenerate C++, rebuild generated oracles, re-run conformance, refresh ledger |
| Generator (`codegen.py`) changes | invalid | regenerate every slice; rebuild generated oracles |
| `DSL_SPEC_VERSION` changes | invalid | all generated C++ and old reports are stale; regenerate and re-run |
| LLVM commit/build changes | compatibility unknown | rebuild hand oracles from the new target backend, re-run all claims, then `--allow-commit-change` |
| Config changes | different claim | new manifest, new claim id, separate artifact directory |
| Oracle binary changes | report stale | re-run the claim; never accept the new binary without a new report |
| Evaluator changes | report stale | `refresh_evidence.py --all`, then write the ledger |
| Test count changes | closure pin stale | update `docs/CLOSURE.md` and `README.md` pinned numbers |
| Coverage corpus changes | ratchet may fail | review the delta, then `coverage_report.py --update-baseline` |

Never edit generated files in place. Regeneration is always the repair path.

## 4. Evidence states

The project distinguishes four states. They are not interchangeable.

| State | Command | What it proves | What it does not prove |
|---|---|---|---|
| `STRUCTURAL_ONLY` | `ldsl-closure check --skip-evidence` | structure, contract, pinned counts, ledger shape | report bytes, oracle identity, corpus freshness |
| `EVIDENCE_VERIFIED` | `ldsl-claims verify --verify-corpus` + `ldsl-closure check` | all 43 claims audited; report/oracle/rules/corpus pins match | that the generated C++ links |
| `INTEGRATION_VERIFIED` | `scripts/integration_smoke.py` status `PASS` | generated C++ compiles, links, runs, and matches available oracles | rules are semantically valid for the ISA |
| `UNKNOWN` | ledger `status=UNKNOWN` or smoke `SKIP` | nothing | must never be counted as a pass |

A missing toolchain produces `SKIP: environment` and exit code 0 from the
smoke, but the report status is `SKIP`, not `PASS`. CI runs
`check_integration_status.py` on every report: `SKIP` is printed explicitly,
and it becomes a job failure whenever `LLVM_DIR` was provided. The report is
also uploaded so a skipped integration cannot be mistaken for a green one.

Domain-external queries are boundaries. They are recorded for coverage
planning and excluded from the equivalence claim.

## 5. Upgrade playbook

Use this sequence for an LLVM bump or a generator change.

```text
1. Record the new LLVM commit and build directory.
2. Rebuild the target backends and every oracle used by claims/claims.json.
3. python scripts/check_all_rules.py
4. python scripts/check_codegen_contract.py
5. python scripts/integration_smoke.py --target toycpu --limit 0
   python scripts/integration_smoke.py --target aarch64-vector --limit 0
6. python scripts/refresh_evidence.py --all --require-all
7. python scripts/refresh_evidence.py --all --write-ledger
8. python -m legalizer_dsl.claims verify --verify-corpus
9. python scripts/coverage_report.py --corpus examples --out out/coverage-report.json
10. python -m legalizer_dsl.closure check
11. Update pinned tests/claims numbers in README.md and docs/CLOSURE.md.
```

Rules:

- Step 2 is not optional. A report produced by a different oracle is stale
  even if its JSON shape is valid.
- Step 6 must not report `SKIP` under `--require-all`. A skipped oracle is a
  missing proof, not a pass.
- Step 7 is allowed to move report/corpus/oracle pins only because step 6
  actually re-ran the reports. The script passes `--allow-report-change` and
  `--allow-commit-change` for that reason; do not use those flags by hand to
  bless an old report.
- If step 8 fails, stop. Do not update the closure pin and do not commit.

## 6. Environment injection

CI and developers should inject paths rather than rely on repository-local
defaults:

| Variable | Purpose |
|---|---|
| `LLVM_DIR` | directory containing `LLVMConfig.cmake` |
| `CXX` | C++ compiler used by the integration fixture |
| `LEGALIZER_DSL_VCVARS` | Windows `vcvars64.bat` used to obtain the MSVC environment |
| `LEGALIZER_DSL_OUT` | report/oracle directory (default `out/`) |
| `LEGALIZER_DSL_LLVM_BUILD` | LLVM build directory recorded in reports |
| `LEGALIZER_DSL_LLVM_SRC` | LLVM source tree for construct coverage |

The integration smoke's built-in `F:/llvm/...` candidates are developer
conveniences only. CI must not depend on them.
