# Changelog

All notable changes to LegiSpec are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) for the
Python package and CLI. The `.lspec` language and evidence schema are versioned
separately (see `docs/COMPATIBILITY.md`).

## [1.0.0] - 2026-10-22

### Added

- Executable LegiSpec DSL for the LLVM GlobalISel `LegalizerInfo` policy layer.
- Parser, diagnostics, strict checker, reachability/shadowing analysis, and
  finite-domain interpreter.
- Deterministic C++ generator for rule tables, with a wiring contract and
  capability/provider checks.
- Differential conformance runner that compares one-step decisions against real
  target oracles.
- Claim ledger with hash pins for rules, reports, oracles, query corpora,
  evaluator, and codegen.
- Closure gate that recomputes pinned numbers and rejects scope growth.
- Construct-coverage classification for every `LegalizerInfo` builder method.
- Evidence collection, reproduction, refresh, and integration-smoke tooling.
- CI matrix for Ubuntu/Windows and Python 3.10/3.12, plus codegen-contract,
  mutation-control, coverage, evidence, and integration jobs.

### Evidence

- 43 claims over 22 rule files: 27 hand-written and 16 generated.
- 321,627 hand-evaluator queries; 63/63 builder constructs classified.
- 400 unit tests.
- Evidence verified on Windows x64; report and oracle binaries remain external
  build artifacts and are not committed.

### Known limits

- The proof is limited to one-step `LegalizerInfo::getAction` decisions over the
  declared finite domain `D union R`.
- MIR rewrites, the iterative Legalizer pass, instruction selection, register
  allocation, and provider handler bodies are outside the proof.
- The published evidence is platform-specific; other platforms must rebuild and
  re-pin the oracle and reports.
