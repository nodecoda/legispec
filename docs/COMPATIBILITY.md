# Compatibility and upgrade policy

This document defines which generated artifacts and claim reports are valid
for which inputs, and the exact procedure for moving to a new LLVM pin.

The correctness contract itself is in [`CORRECTNESS.md`](CORRECTNESS.md).
Evidence storage and reproduction are in [`EVIDENCE.md`](EVIDENCE.md).

## 1. Pinned identity

| Component | Pinned value | Source of truth |
|---|---|---|
| Generator version | `1.0.0` | `pyproject.toml`, `legispec.__version__` |
| LegiSpec specification | `1.0` | `codegen.DSL_SPEC_VERSION` |
| Claim contract | `1` | `claims.CLAIM_CONTRACT_VERSION` |
| Claim schema | `claims/claim-schema.json` | `claims.validate_contract` |
| LLVM commit | `d855d9e48067026317998cbff734004f328f833d` | `claims/claims.json` |
| Python | `>=3.10` | `pyproject.toml` |
| Evidence schema | `legispec-conformance-v1`, `legispec-integration-v2` | `conformance.py`, `integration_smoke.py` |

### Rule-file suffix

`.lspec` is the canonical rule-file suffix. Discovery
(`paths.iter_rule_files`) is suffix-driven and returns `.lspec` only, so a rule
file with any other suffix is never picked up implicitly. The parser itself is
suffix-agnostic, so an explicitly named file still parses; packaging and
repository tooling only recognize `.lspec`.

An integration report is valid only when it was produced from this exact
tree and toolchain. The status gate re-checks `head_commit == HEAD`,
`worktree_dirty == false`, the generator/DSL-spec versions, the
evaluator/codegen fingerprints, the rules and query hashes, and the hashes of
the generated sources, consumer, and both oracle binaries. A report from an
older commit, a dirty worktree, or a different generator is stale even if its
JSON shape looks valid.

The rules binding is target-specific: `inputs.rules` must equal the declared
rules file for `target` (for example `rules/toycpu.lspec` for `toycpu`), not
just any file whose hash matches. `query_count` must equal the number of JSONL
records in the hashed query file and must not exceed the recorded
`query_limit` (when the limit is non-zero). Generated header/source paths are
resolved inside the repository and re-hashed from disk, so a report cannot
point at stale or external artifacts.

A generated tree is valid only when its `codegen-manifest.json` agrees with
all of the following:

```text
generator_version == legispec.__version__
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
| ToyCPU | `toycpu.lspec` | default | `toycpu`, `toycpu-generated` | 243714 | 87/89 |
| X86-64 | `x86-scalar.lspec` | default | `x86-scalar`, `x86-generated` | 7591 | 69/69 |
| AArch64 | `aarch64-scalar.lspec` | default | `aarch64-scalar`, `aarch64-generated` | 8161 | 91/91 |
| AArch64 + CSSC | `aarch64-cssc-scalar.lspec` | default | `aarch64-cssc-scalar`, `aarch64-cssc-generated` | 8161 | 95/95 |
| AArch64 | `aarch64-pointer.lspec` | default | `aarch64-pointer` | 924 | 17/17 |
| AArch64 | `aarch64-atomic.lspec` | generic | `aarch64-atomic`, `aarch64-atomic-generated` | 612 | 11/11 |
| AArch64 | `aarch64-atomic-outline.lspec` | outline-atomics | `aarch64-atomic-outline`, `aarch64-atomic-outline-generated` | 612 | 10/10 |
| AArch64 | `aarch64-atomic.lspec` | LSE | `aarch64-atomic-lse` | 612 | 11/11 |
| AArch64 | `aarch64-vector.lspec` | fixed vectors | `aarch64-vector`, `aarch64-vector-generated` | 176 | 10/11 |
| AArch64 | `aarch64-vector-arith.lspec` | fixed vectors | `aarch64-vector-arith`, `aarch64-vector-arith-generated` | 120 | 13/15 |
| AArch64 | `aarch64-vector-mul.lspec` | fixed vectors | `aarch64-vector-mul`, `aarch64-vector-mul-generated` | 24 | 13/15 |
| AArch64 | `aarch64-vector-shift.lspec` | fixed vectors | `aarch64-vector-shift`, `aarch64-vector-shift-generated` | 270 | 19/21 |
| AArch64 | `aarch64-icmp.lspec` | fixed vectors | `aarch64-icmp`, `aarch64-icmp-generated` | 173 | 19/23 |
| AArch64 | `aarch64-fcmp.lspec` | fixed vectors | `aarch64-fcmp`, `aarch64-fcmp-generated` | 185 | 13/14 |
| AArch64 | `aarch64-concat-vectors.lspec` | fixed vectors | `aarch64-concat-vectors`, `aarch64-concat-vectors-generated` | 28 | 3/3 |
| RISC-V 64 | `riscv64-scalar.lspec` | RV64 | `riscv64-scalar`, `riscv64-generated` | 7861 | 103/103 |
| RISC-V 64 + M/Zbb | `riscv64-mzb-scalar.lspec` | RV64 | `riscv64-mzb-scalar`, `riscv64-mzb-generated` | 7861 | 117/118 |
| RISC-V 64 | `riscv64-pointer.lspec` | RV64, pointer widths | `riscv64-pointer`, `riscv64-pointer-mzb` | 770 | 32/32 |
| RISC-V 32 | `riscv32-scalar.lspec` | RV32 | `riscv32-scalar`, `riscv32-generated` | 7861 | 100/100 |
| RISC-V 32 + M/Zbb | `riscv32-mzb-scalar.lspec` | RV32 | `riscv32-mzb-scalar` | 7861 | 111/115 |
| RISC-V 32 | `riscv32-pointer.lspec` | RV32, pointer widths | `riscv32-pointer`, `riscv32-pointer-mzb` | 770 | 31/31 |
| RISC-V conditional | `riscv-conditional-gconstant.lspec` | RV32 / RV64 | `riscv-conditional-gconstant-rv32`, `-rv64` | 9 | 4/4 |
| RISC-V conditional | `riscv-scalar-conditional.lspec` | RV32 / RV64 | `riscv-conditional-scalar-rv32`, `-rv64` | 7861 | 100/101, 108/108 |

Unmatched rules are declared unreachable, not silent passes. The ledger's
`unmatched` field records each one; coverage reports summarize them.

The X86 claims were produced with the ToyCPU-enabled LLVM build (ledger label
`toycpu`), which also contains the X86 backend. The identity that matters is the
recorded LLVM commit and the oracle hash, not the build-directory name.

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
| `STRUCTURAL_ONLY` | `legispec-closure check --skip-evidence` | structure, contract, pinned counts, ledger shape | report bytes, oracle identity, corpus freshness |
| `EVIDENCE_VERIFIED` | `legispec-claims verify --verify-corpus` + `legispec-closure check` | all 43 claims audited; report/oracle/rules/corpus pins match | that the generated C++ links |
| `INTEGRATION_VERIFIED` | `scripts/integration_smoke.py` status `PASS` | generated C++ compiles, links, runs, and matches available oracles | rules are semantically valid for the ISA |
| `UNKNOWN` | ledger `status=UNKNOWN` or smoke `SKIP` | nothing | must never be counted as a pass |

A missing toolchain produces `SKIP: environment` and exit code 0 from the
smoke, but the report status is `SKIP`, not `PASS`. The same applies to a
missing reference oracle or `--skip-reference`: the consumer can run, but the
report cannot claim `PASS` without both `hand_written_oracle` and
`generated_oracle` comparisons. CI runs `check_integration_status.py` on every
report. CI invokes the gate with `--require-pass-if-env LLVM_DIR`, so `SKIP`
is printed explicitly and becomes a job failure whenever `LLVM_DIR` was
provided. The report is also uploaded so a skipped integration cannot be
mistaken for a green one.

GeneratedCpp equivalence is claimed only for rules that have a
`*-generated` claim. The remaining rule files are listed with reasons in
`claims/generated-evidence.json` (`hand_only`); for those slices the hand
oracle proves the interpreter, not the generated C++ path. The closure gate
fails if a new rule has neither a generated claim nor a reasoned hand-only
declaration. It does not yet machine-enforce a generated-to-hand twin
invariant for every slice; the per-claim reports and the integration fixture
are the current evidence, and the hand-only split is explicit rather than
silently counted as generated coverage.

`python scripts/final_verification.py --tests N --skipped M` is the local or
self-hosted release gate. It requires an explicit test/skip pin, a clean
worktree, both integration reports, and records whether claims/integration
were actually re-executed (`--run-claims --run-integration`) or only
hash-audited. The artifact carries `release_eligible`: it is true only for a
`PASS` in `independent-execution` mode on a clean tree. An
`artifact-consistency` run can still report `PASS`, but
`release_eligible=false`. `--allow-dirty` produces `result=DIAGNOSTIC` (not
`PASS`) and exits non-zero.

The unit-test gate runs unittest verbosely, parses the actual skipped test ids,
and checks them against `tests/expected-skips.json`; a skip-count-only match is
not enough. Hosted CI does not run final verification because it has neither
the evidence directory nor the oracle binaries; CI's own jobs remain the
per-push gates.

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
8. python -m legispec.claims verify --verify-corpus
9. python scripts/coverage_report.py --corpus examples --out out/coverage-report.json
10. python -m legispec.closure check
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
| `LEGISPEC_VCVARS` | Windows `vcvars64.bat` used to obtain the MSVC environment |
| `LEGISPEC_OUT` | report/oracle directory (default `out/`) |
| `LEGISPEC_LLVM_BUILD` | LLVM build provenance label recorded in reports |
| `LEGISPEC_TOYCPU_BUILD` | ToyCPU-enabled LLVM build for the `toycpu` target |
| `LEGISPEC_LLVM_SRC` | LLVM source tree for construct coverage and commit provenance |
| `LEGISPEC_LLVM_DIR` / `LLVM_DIR` | LLVM cmake directory containing `LLVMConfig.cmake` |
| `LEGISPEC_CMAKE` / `LEGISPEC_CXX` / `LEGISPEC_NINJA` | Tool overrides for the integration smoke |
| `LEGISPEC_LINKER` / `LEGISPEC_LLC` | Linker and `llc` overrides |

The integration smoke resolves LLVM, CMake, the C++ compiler, Ninja and
`vcvars64.bat` from environment variables and `PATH`; there are no built-in
machine-specific candidates. CI must set these variables explicitly.

`scripts/check_skips.py` makes host-specific skips explicit: every skipped
test id must be registered with a reason in `tests/expected-skips.json`, and a
registered required skip that stops happening also fails. Entries marked
`optional: true` may be absent (an environment-dependent control that ran is
strictly better than one that skipped), but when they do skip the reason must
still be registered. The CI unit-test job uses the full check, and the
mutation-controls job uses `-p "test_mutation_controls.py"`.
