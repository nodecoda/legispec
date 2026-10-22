# LegiSpec

[简体中文](README.zh-CN.md) | **English**

> **Legality, specified. Equivalence, proven.**

LegiSpec extracts LLVM GlobalISel **`LegalizerInfo` rule tables** from C++ into an
external declarative language, **LegiSpec DSL** (`.lspec` files). It provides a
strict checker, a C++ generator, and a differential-evidence toolchain that
compares the specification against the real target oracle.

In one sentence: it is a specification and falsification toolchain for rule
tables, not another legalizer.

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE.TXT)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)](pyproject.toml)
[![CI](https://github.com/nodecoda/legispec/actions/workflows/ci.yml/badge.svg)](https://github.com/nodecoda/legispec/actions/workflows/ci.yml)

> **Status: 1.0.0, scope frozen.** The rules and claims in this release are a
> finite-domain proof, not a claim of full LLVM semantic equivalence. See
> [`docs/CLOSURE.md`](docs/CLOSURE.md) and [`docs/CORRECTNESS.md`](docs/CORRECTNESS.md)
> before relying on the evidence.

## 1. The single claim

```text
DSL.getAction(q) == LegalizerInfo::getAction(q)
q in D union R
D = finite domain declared by the rule file (filtered by domain/filter)
R = one-step legalization successors of Q0 inside D
```

Boundary (full version in [`docs/CLOSURE.md`](docs/CLOSURE.md)):

- Only the **one-step decision** of `LegalizerInfo::getAction(LegalityQuery)` is compared.
- It does not model `LegalizerHelper::legalizeInstrStep` MIR rewrites, the iterative
  Legalizer pass, instruction selection, or register allocation.
- Successors outside `D` are recorded as boundaries, not evidence.
- `custom` / `libcall` / `lower` prove only which provider the policy layer selects;
  they do not prove the MIR emitted by that handler.

**LegiSpec is a complete specification of the decision function and an interface
contract for the rewrite function. It is not a complete semantic specification.**

## 2. What this is not

| Not | Reason |
|---|---|
| A general compiler front end | The primitives cover only what `LegalizerInfo` builders can express |
| A TableGen replacement | The input is rule semantics, not records; the output is C++ builder calls |
| An official LLVM project | It is a downstream tool with no affiliation (see [`NOTICE`](NOTICE)) |
| A correctness claim without evidence | Without reports and oracle hashes, `claims verify` will not say PROVEN |

## 3. Status and support matrix

| Surface | Status |
|---|---|
| Rule language and CLI | `1.0.x` maintenance: bug fixes, evidence refreshes, documentation |
| Evidence domain | Finite `D union R`; 43 claims over 22 rule files |
| Evidence platform | Windows x64 (oracle binaries); reports are pinned by hashes |
| CI platform | Ubuntu and Windows; structural checks are cross-platform |
| Python | 3.10, 3.11, 3.12 |

Evidence has three distinct states; do not collapse them:

- `STRUCTURAL_ONLY`: `legispec-closure check --skip-evidence` checks structure,
  contracts, and pinned numbers, but not report/oracle bytes.
- `EVIDENCE_VERIFIED`: `legispec-claims verify --verify-corpus` plus the full
  `legispec-closure check` audits all 43 claims.
- `INTEGRATION_VERIFIED`: `integration_smoke.py` reports `PASS`; generated C++
  was compiled, linked, executed, and compared with available oracles.

`UNKNOWN` and boundary cases are never counted as passes.

## 4. Install and quick start

From PyPI (after the first public release):

```bash
python -m pip install legispec
```

From a source checkout:

```bash
python -m pip install -e ".[dev]"
legispec --rules rules/toycpu.lspec check --strict
python -m unittest discover -s tests
```

`check --strict` runs syntax, semantics, capability contracts, reachability, and
shadowing checks. It fails when `capabilities/<rules>-capabilities.json` is
missing, so `custom` providers cannot pass silently.

Generate C++:

```bash
legispec --rules rules/aarch64-vector.lspec codegen --out-dir out/codegen
```

Generator contract check (22 rule files, every config variant, deterministic
double generation, manifest provenance):

```bash
python scripts/check_codegen_contract.py
```

Integration and coverage checks:

```bash
python scripts/integration_smoke.py --target toycpu --limit 0
python scripts/integration_smoke.py --target aarch64-vector --limit 2000 --out out/integration-aarch64-report.json
python scripts/check_integration_status.py --require-pass out/integration-report.json out/integration-aarch64-report.json
python scripts/coverage_report.py --corpus examples --out out/coverage-report.json
```

The integration smoke needs `LEGISPEC_LLVM_DIR` or `LLVM_DIR` with
`LLVMConfig.cmake`, CMake, a C++ compiler, and (on Windows) Ninja plus
`vcvars64.bat` or `LEGISPEC_VCVARS`. A missing toolchain is reported as
`SKIP: environment`, never as a pass.

## 5. Repository layout

```text
src/legispec/         implementation: parser / checker / interp / codegen / conformance / claims
  checker/                 rule-level static checks (wellformed / conflict / reachability / termination)
rules/                    22 .lspec rule files (ToyCPU, X86-64, AArch64, RISC-V)
witness/                  9 witness files: fixed-order dependencies and boundaries
capabilities/             22 provider contracts (custom / libcall / lower)
claims/                   claims.json (43 claims and hash pins), generated-evidence.json, claim-ledger.md, construct coverage
docs/                     claim, grammar, patterns, configuration, decisions; see COMPATIBILITY.md
examples/                 real MIR / LLVM IR corpora
tests/                    400 unit tests
scripts/                  evidence collection and release gates
out/                      local evidence cache (not committed; see section 7)
```

## 6. Proven numbers (pinned)

```text
claims=43            # each pins rules content hash + report hash + oracle hash
hand-claims=27
generated-claims=16
generated-covered=16
hand-only=6
rules-files=22
hand-queries=321627
constructs=63/63     # LLVM builder methods classified into DSL primitives
tests=400
```

These numbers are recomputed from the artifacts by
`python -m legispec.closure check`. `generated-covered=16` means GeneratedCpp
equivalence holds only for 16 generated claims; the other 6 rules are explicitly
registered as hand-only in `claims/generated-evidence.json`. Closure rejects a
new rule that has neither generated evidence nor a hand-only declaration.

## 7. Evidence is external

Reports and oracle executables are build outputs (Windows x64, embedding a local
LLVM build path) and are **not committed**:

```bash
python scripts/collect_evidence.py --from <evidence-dir>   # fetch and verify sha256 pins
legispec-claims verify --verify-corpus                     # 43/43 PROVEN, re-enumerates D union R
python scripts/refresh_evidence.py --all --require-all     # re-run every report after rules/evaluator changes
python scripts/refresh_evidence.py --all --write-ledger    # accept the new pins
legispec-closure check                                     # full closure
```

Without an evidence directory, `claims verify` reports `report missing` or
`oracle missing`, and the corresponding unit tests are skipped. It never
pretends to pass. See [`docs/EVIDENCE.md`](docs/EVIDENCE.md) for rebuilding
LLVM, the oracle, and the reports from scratch.

## 8. Architecture and module map

```text
.lspec rules
    |
    v
parser --> diagnostics/model --> checker --> interp (reference evaluator)
    |                                              |
    |                                              v
    +--> codegen (C++ builder calls) --> target oracle / consumer
                                                   |
                                                   v
                              conformance --> reports --> claims --> closure
```

| Module | Responsibility |
|---|---|
| `parser.py`, `diagnostics.py`, `model.py` | Parse `.lspec`, produce spans and stable diagnostic codes |
| `checker/` | Well-formedness, conflicts, reachability, termination, capability contracts |
| `interp.py` | Reference interpreter for the one-step decision |
| `codegen.py`, `checker/capability.py` | Emit C++ builder calls and a wiring contract |
| `conformance.py` | Enumerate `D union R`, diff the interpreter against the oracle |
| `claims.py` | Pin reports, oracles, rules, corpus, evaluator, and codegen hashes |
| `closure.py` | Recompute pinned numbers and enforce the closed scope |
| `constructs.py` | Classify every `LegalizerInfo` builder method against the DSL |
| `paths.py` | Resolve repository vs installed data roots through environment overrides |
| `cli.py` | User-facing `legispec` command |

## 9. Platform support

- **Evidence verified:** Windows x64. The pinned oracle binaries and reports were
  produced on that platform.
- **Structural CI:** Ubuntu and Windows, Python 3.10 and 3.12. Hosted CI has no
  LLVM build, so the integration smoke is `SKIP: environment` there.
- **Other platforms:** the Python implementation is portable, but no evidence
  claim is made until the oracle and reports are rebuilt and re-pinned.

## 10. Documentation

- [`docs/CLOSURE.md`](docs/CLOSURE.md) - frozen scope, boundaries, and final numbers
- [`docs/CORRECTNESS.md`](docs/CORRECTNESS.md) - what the correctness claim means
- [`docs/COMPATIBILITY.md`](docs/COMPATIBILITY.md) - artifact invalidation and LLVM upgrade playbook
- [`docs/EVIDENCE.md`](docs/EVIDENCE.md) - evidence storage and reproduction
- [`docs/INTEGRATION.md`](docs/INTEGRATION.md) - generated-C++ integration contract
- [`docs/RELEASE.md`](docs/RELEASE.md) - release checklist and gate matrix
- [`CHANGELOG.md`](CHANGELOG.md) - release history

## 11. License and governance

[Apache License v2.0](LICENSE.TXT). Every source file carries an
`SPDX-License-Identifier: Apache-2.0` header.

LegiSpec is an independent downstream project. LLVM is a trademark of the LLVM
Foundation; this project is not affiliated with or endorsed by the LLVM Project
or the LLVM Foundation. See [`NOTICE`](NOTICE).

- [`CONTRIBUTING.md`](CONTRIBUTING.md) - contribution, evidence, and review rules
- [`GOVERNANCE.md`](GOVERNANCE.md) - maintainer model, RFC process, version policy
- [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) - community conduct
- [`SECURITY.md`](SECURITY.md) - vulnerability and evidence-tampering reports
