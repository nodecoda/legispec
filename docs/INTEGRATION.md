# Integrating generated legalizer rules into LLVM

This document describes the **policy-layer** integration boundary:

```text
LDSL rules -> generated LegalizerInfo builders -> target/consumer TU
```

It does **not** cover `LegalizerHelper` rewrites, custom/libcall/lower
function bodies, instruction selection, register allocation, or ABI details.
The generated code only answers:

```text
LegalityQuery -> LegalizeActionStep
```

## 1. What integration proves

The integration fixture proves four separate claims:

1. `codegen` produces C++ and a wiring contract for the selected config;
2. a real CMake build compiles and links the generated static library against a
   pinned LLVM installation;
3. the linked consumer constructs a `LegalizerInfo` and answers JSONL queries;
4. on the sampled/declared domain, the consumer and the Python interpreter
   agree with the hand-written oracle.

Passing the fixture does **not** prove semantic correctness of the rules with
respect to the ISA. It proves that the generated policy is executable and
observationally equivalent to the reference implementation on the queried
domain. Domain-external queries remain `UNKNOWN`; they are not evidence.

## 2. Generate the artifacts

```text
python -m legalizer_dsl --rules rules/toycpu.ldsl codegen \
  --out-dir out/integration/toycpu/generated \
  --emit-wiring out/integration/toycpu/wiring \
  --llvm-commit <commit>
```

The command writes:

```text
out/integration/toycpu/generated/toycpu_rules_gen.h
out/integration/toycpu/generated/toycpu_rules_gen.cpp
out/integration/toycpu/generated/codegen-manifest.json
out/integration/toycpu/wiring/legalizer-rules.cmake
out/integration/toycpu/wiring/wiring.cpp.inc
out/integration/toycpu/wiring/wiring.md
```

`--check` verifies all of these artifacts without writing:

```text
python -m legalizer_dsl --rules rules/toycpu.ldsl codegen \
  --out-dir out/integration/toycpu/generated \
  --emit-wiring out/integration/toycpu/wiring \
  --llvm-commit <commit> --check
```

## 3. Choose the namespace and config

- `toycpu` derives `llvm::ToyCPU`.
- `aarch64-vector` derives `llvm::AArch64Vector`.
- `riscv64-scalar` derives `llvm::RISCV64Scalar`.
- `x86-scalar` derives `llvm::X86Scalar`.

Unknown families are rejected. Pass `--namespace` explicitly only when the
stem genuinely cannot identify the target/config. Every manifest records
`namespace_source = derived|explicit`.

Config guards are resolved at generation time. One generated artifact belongs
to **one** fixed config; it does not dispatch on runtime subtarget features.
Regenerate for each config variant and keep the artifacts separate.

## 4. Wire the generated library

The consumer must first provide LLVM's package exports:

```cmake
find_package(LLVM CONFIG REQUIRED)
include("path/to/out/integration/toycpu/wiring/legalizer-rules.cmake")

add_executable(my-consumer main.cpp)
target_link_libraries(my-consumer PRIVATE legalizer_dsl_toycpu_rules_gen)
```

The generated target exposes the generated directory and the wiring directory
on its public include path. It links `LLVMCodeGen`, `LLVMGlobalISel`,
`LLVMCore`, and `LLVMSupport`; `LLVMGlobalISel` is required because
`LegalizerInfo` itself lives there.

In C++:

```cpp
#include "wiring.cpp.inc"

llvm::LegalizerInfo LI;
LEGALIZER_DSL_APPLY_RULES(LI);
```

`wiring.cpp.inc` includes the generated header itself. Include it once in the
translation unit that owns the `LegalizerInfo`. Do not copy the generated
`.cpp` into the target's hand-written `LegalizerInfo.cpp`: keep it as a
separate static library so regeneration cannot overwrite upstream source.

For float-keyed targets such as AArch64, configure the consumer with
`-DLDSL_USE_EXTENDED_LLT=ON`. AArch64's `TargetMachine` normally enables
extended LLTs; a generated consumer has no `TargetMachine`, so it must enable
the same interpretation explicitly before applying the rules.

## 5. Run the smoke

```text
python scripts/integration_smoke.py --target toycpu
python scripts/integration_smoke.py --target aarch64-vector
```

Useful options:

```text
--limit 0          query the full declared domain D (default sample: 20000)
--no-build         reuse an existing out/integration/<target>/build
--skip-reference   only compare the consumer with the Python interpreter
--llvm-commit SHA  pin recorded in the report; defaults to claims/claims.json
                   (there is no "unknown" fallback: the smoke exits 2)
--out PATH         report path (default out/integration-report.json)
```

Compiler and LLVM discovery are environment-driven (there are no
`--llvm-dir`/`--cxx` flags): set `LLVM_DIR` and `CXX` for a non-default
toolchain, plus `LEGALIZER_DSL_LLVM_DIR` / `LEGALIZER_DSL_CXX` to override the
smoke's search directly.  On Windows, `LEGALIZER_DSL_VCVARS` selects the
`vcvars64.bat` used for the MSVC environment.

Reports use schema `legalizer-dsl-integration-v2`. Besides the rules and query
hashes, `provenance` pins the generator and LDSL-spec versions, the evaluator
and codegen fingerprints, the LLVM commit, the git `head_commit`, whether the
worktree was dirty, the generated header/source hashes, and the consumer and
oracle binary hashes. `artifacts` names the query file and every binary so the
status gate can re-hash them. A report that was produced from a different
tree, a different generator, or a dirty worktree is rejected.

Status semantics are deliberately narrow:

- `PASS`: the consumer ran and **both** reference comparisons
  (`hand_written_oracle`, `generated_oracle`) ran with zero divergence;
- `FAIL`: the consumer build/run failed, or any comparison diverged;
- `SKIP`: the environment could not provide a compiler/LLVM, a reference
  binary is missing, or `--skip-reference` was passed. `SKIP` is never a pass.

`scripts/check_integration_status.py` enforces the same contract. It requires
schema v2, a `query_count` of at least 100 that equals the number of records in
the hashed query file, `query_count <= query_limit` when a limit was recorded,
all three comparisons with zero divergences and zero oracle errors, and
artifact/generated-source hashes that match the files on disk. `inputs.rules`
must be the rules file declared for that `target` (ToyCPU or AArch64 vector),
so swapping in another rules file plus its hash cannot pass. The report must
also record `worktree_dirty=false`, `head_commit == HEAD`, and the current
generator, DSL-spec, evaluator, codegen, and LLVM pins.

CI runs it with `--require-pass-if-env LLVM_DIR`: a hosted runner without LLVM
leaves the reports as `SKIP`, while any job that provided `LLVM_DIR` turns
`SKIP` into a failure. Generate reports only after committing the tree; a dirty
worktree is reported as `worktree_dirty=true` and will be rejected.

The script searches the current environment, then the local LLVM builds used
by this repository. CI runners should set `LLVM_DIR`, `CXX`, and, on Windows,
`LEGALIZER_DSL_VCVARS` explicitly.

## 6. A/B against the hand-written rules

The smoke already performs the A/B when the oracle binaries exist:

```text
out/oracle/legalizer-oracle[.exe]        # hand-written ToyCPU rules
out/oracle/legalizer-oracle-gen[.exe]    # generated ToyCPU rules
out/oracle-aarch64/...                   # AArch64 counterparts
```

The smoke appends the host executable suffix (`.exe` on Windows); oracle
filenames in reports are therefore platform-relative, never hard-coded to a
Windows path.

Both are thin JSONL adapters over `LegalizerInfo::getAction`; they contain no
policy of their own. If both have zero divergence against the Python
interpreter on the same queries, they are observationally equivalent on that
domain.

For a manual A/B, feed the same JSONL to both:

```text
out/oracle/legalizer-oracle.exe --input queries.jsonl > hand.jsonl
out/oracle/legalizer-oracle-gen.exe --input queries.jsonl > gen.jsonl
```

A non-zero diff is a policy divergence. It is not automatically a bug in the
generator: check the rules, the config, the LLVM commit, and the manifest
provenance before deciding which side is stale.

## 7. Rollback

The generated library is additive. To roll back:

1. remove the consumer's `legalizer-rules.cmake` include and link target;
2. restore the target's previous `LegalizerInfo` construction path;
3. delete the generated output directory (or regenerate it from the previous
   manifest);
4. rerun the target's own tests and the conformance suite.

Never edit generated files in place. If a regeneration changes behavior, the
manifest's `rules_sha256`, `llvm_commit`, and `generator_version` identify the
exact inputs that produced it.
