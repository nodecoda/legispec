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
--llvm-dir DIR     override LLVMConfig.cmake discovery
--cxx PATH         override the C++ compiler
--out PATH         report path (default out/integration-report.json)
```

The report records the rules hash, query hash, environment, generated
artifacts, and each comparison. It distinguishes:

- `PASS`: build succeeded and every available comparison had zero divergence;
- `FAIL`: build failed, or any comparison diverged;
- `SKIP`: the environment could not provide a compiler/LLVM; the reason is
  explicit and the smoke is not counted as a pass.

The script searches the current environment, then the local LLVM builds used
by this repository. CI runners should set `LLVM_DIR`, `CXX`, and, on Windows,
`LEGALIZER_DSL_VCVARS` explicitly.

## 6. A/B against the hand-written rules

The smoke already performs the A/B when the oracle binaries exist:

```text
out/oracle/legalizer-oracle.exe          # hand-written ToyCPU rules
out/oracle/legalizer-oracle-gen.exe      # generated ToyCPU rules
out/oracle-aarch64/...                   # AArch64 counterparts
```

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
