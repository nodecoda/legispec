# RISC-V GlobalISel: fourth-target conformance experiment

Date: 2026-10-20
Status: PASS (RV64 scalar subset, generic-rv64)

## 0. Result first

The ToyCPU LegiSpec rules was pointed at a third real GlobalISel target:
**RISC-V**, fixed to RV64 and the `generic-rv64` CPU.  This is a scalar
subset, not a model of all of RISC-V.

| Item | Result |
|---|---|
| Modeled rulesets | 38 |
| Modeled opcodes | 73 |
| Modeled rules (after macro expansion) | 103 |
| Domain LLTs | 15 |
| D union R queries | 7,861 |
| Closure-only queries | 62 |
| Divergences | **0** |
| Oracle errors | 0 |
| Rule coverage | **103 / 103** |
| Static check | 0 errors / 168 warnings / 0 unknown |
| Oracle build | `<llvm-build>` (LLVM 24.0.0git, assertions ON) |

The claim proven here is narrow and concrete:

> On the declared finite RV64 scalar domain, the Python LegiSpec model and the
> real `RISCVLegalizerInfo::getAction(LegalityQuery)` agree on every observed
> step.

It does **not** prove that the DSL models all of RISC-V, vector or scalable
vector legalization, pointer rulesets, memory descriptors, FP, atomics, calls,
or the actual `LegalizerHelper` rewrites.

## 1. Why RISC-V, and why generic-rv64

Unlike AArch64, RISC-V legality is **XLen-parameterized** and almost every
interesting rule is **extension-gated**.  Choosing a configuration is part of
the spec, not an implementation detail.

The oracle creates:

```text
triple      = riscv64-unknown-linux-gnu
cpu         = generic-rv64
code model  = small
features    = +64bit, +i
XLen        = 64
sXLen       = s64
sDoubleXLen = s128
p0          = p0:64
```

`RISCVProcessors.td` defines `generic-rv64` as `Feature64Bit,
FeatureStdExtI` only.  There is **no M, A, F, D, C, V, Zbb, Zbc, Zbkc,
Zmmul or P**.  That single fact decides most of the rule table:

```text
hasStdExtZmmul() == false  -> G_MUL is a libcall
hasStdExtM()     == false  -> div/rem are libcalls
hasStdExtZbb()   == false  -> counts/bit ops lower
hasStdExtZbc/Zbkc() == false -> CLMUL family is Unsupported
hasStdExtP()     == false  -> G_CTLS lowers
hasVInstructions() == false -> every typeIsLegal*Vec predicate is false
```

This is the opposite lesson from AArch64.  There the target was "one CPU with
feature-gated clauses".  Here the same C++ constructor is a **parameterized
family**: RV32/RV64 and each extension select a different policy.  A faithful
DSL spec must name the configuration, not just the target.

## 2. Files

Added:

```text
toycpu/tools/legispec/rules/riscv64-scalar.lspec
toycpu/tools/legispec/riscv64-scalar-capabilities.json
toycpu/out/riscv64-scalar-conformance-report.json
toycpu/out/riscv64-scalar-check.txt
toycpu/notes/riscv64-gisel-experiment.md
```

Changed:

```text
toycpu/tools/oracle/main.cpp        RISCV_ORACLE branch
toycpu/tools/oracle/CMakeLists.txt  fifth executable, ORACLE_WITH_RISCV
toycpu/tools/oracle/build.ps1       -WithRiscv
toycpu/tools/legispec/...      same_size_as / scalar_bits /
                                    widen_to_next_pow2_or_min, exact
                                    termination state graph
```

## 3. Domain and exclusions

Domain types:

```text
s1, s2, s3, s4, s8, s12, s16, s24, s32, s48, s64, s80, s96, s128, s256
```

The odd sizes are deliberate: they exercise `widenScalarToNextPow2`,
`clampScalar`, and the 96 -> 128 -> 64 two-step paths.

Immediates `{0, 8, 16, 32, 64}` are declared only for `G_SEXT_INREG`.

Omitted on purpose:

- vector and scalable-vector clauses (all are V-gated and false here);
- pointer rulesets (`G_PTR_ADD` .. `G_BRJT`), a separate experiment;
- `G_MERGE_VALUES` / `G_UNMERGE_VALUES` (per-opcode type-index choice);
- `G_BITCAST` (scalar domain is Unsupported; the rule is vector-only);
- memory descriptors (`G_LOAD`/`G_STORE`/...), atomics, FP (F/D), calls;
- `G_SEXT_INREG` Zbb widths (8/16), because Zbb is off.

Arity note: Q0 declares a **probe shape**, not necessarily the MIR arity.
For type-agnostic rules (`.lower()`, `.unsupported()`) any shape selects the
same rule; binary shapes keep D finite.  `G_SEXT_INREG` is the only ruleset
that reads an immediate.

## 4. What the rules actually say

### 4.1 Arithmetic

```text
G_ADD/G_SUB:    s64 Legal, s32 Custom, everything else clamps to s64.
G_AND/OR/XOR:   s64 Legal, everything else clamps to s64.
G_MUL:          s64 and s128 Libcall, s32 widens to s64, s96 widens to s128.
G_SMULH/UMULH:  only s64 lowers; other types are Unsupported.
G_SMULO/UMULO:  < 64 widens to s64, == 64 widens to s128, then Lower.
```

The `G_ADD` order matters: `legalFor(s64)`, then the V-only `legalIf`, then
`customFor(s32)`.  The real oracle answers `Custom` for `s32`, and the DSL
reproduces that only because it keeps the same order.

`G_SMULH` uses `lowerFor({sXLen})`, not `lower()`.  This is a visible
difference: `s32` is **Unsupported**, not Lower.

### 4.2 Extension casts

```text
G_ZEXT/G_SEXT/G_ANYEXT legal pairs: (s32,s16), (s64,s16), (s64,s32)
                                   + maxScalar(0, s64)
```

There is no widening rule for the source.  Therefore `(s64, s8)` is
**Unsupported** in the real target.  That is a real policy, not a modeling
gap: RISC-V expects the producer to reach s16 first.

### 4.3 Comparisons, selects, phis

```text
G_ICMP:   (s64,s64) Legal; t1 uses "next pow2 or min 8";
          t1 clamps to s64; t0 clamps to s64.
G_SELECT: (s32,s64) and (s64,s64) Legal; t0 clamps to [32,64].
G_PHI:    s32/s64 Legal; otherwise clamp to [32,64].
G_BRCOND: s64 Legal; narrower values widen to s64 (no upper clamp).
```

`G_ICMP` is why the DSL grew `widen_to_next_pow2_or_min`: `s1`, `s2` and
`s4` are powers of two, but `widenScalarOrEltToNextPow2OrMinSize(1, 8)`
still widens them to `s8`.  Plain `widenScalarToNextPow2` would not.

### 4.4 Count-zeros and popcount

These rulesets are the first place where the C++ reads **two type slots**:

```cpp
scalarSameSizeAs(1, 0)  // widen/narrow t1 to the scalar size of t0
```

Consequences observed from the real oracle:

- a query must supply two type slots; a one-slot query asserts in
  `ArrayRef` inside the assertions build;
- `G_CTLZ [s64,s64]` lowers, while `G_CTLZ [s64,s32]` first widens `t1` to
  `s64`;
- `G_CTPOP [s64,s32]` widens `t1` to `s64`, then lowers;
- `G_CTPOP [s32,s32]` lowers immediately (the pow2/clamp steps are no-ops).

The DSL now expresses this structurally:

```text
widen  t1 to same_size_as(t0) when scalar_bits(t1) < scalar_bits(t0);
narrow t1 to same_size_as(t0) when scalar_bits(t1) > scalar_bits(t0);
```

`same_size_as` mirrors `LegalizeMutations::changeElementSizeTo(TypeIdx,
FromTypeIdx)` and `scalar_bits` mirrors the `getScalarSizeInBits()` lambda in
`scalarSameSizeAs`.  No per-size enumeration is needed.

### 4.5 Constants and counters

```text
G_CONSTANT:          s64 Custom; wider/narrower scalars clamp to s64.
                     (p0 Legal belongs to the pointer experiment.)
G_READCYCLECOUNTER:  only s64 Legal; every other scalar is Unsupported.
```

### 4.6 Opcodes that are simply not there

`CLMUL`, `CLMULH`, `CLMULR` are `Unsupported` without Zbc/Zbkc.  Saturating
add/sub and funnel shifts lower unconditionally.  Rotates, bit-reverse,
byte-swap, abs, abds/abdu, min/max, scmp/ucmp lower because their Zbb/Zbkb/P
clauses are off.

## 5. DSL changes forced by RISC-V

Three additions were needed to state the C++ without conventions:

| Addition | Why |
|---|---|
| `same_size_as(tN)` mutation | `scalarSameSizeAs` changes a slot to another slot's scalar size |
| `scalar_bits(tA) <op> scalar_bits(tB)` predicate | the predicate half of `scalarSameSizeAs` |
| `widen_to_next_pow2_or_min idx, min` | `widenScalarOrEltToNextPow2OrMinSize` matches powers of two below `min` |

One checker fix was also required.  The static termination analysis used to
project each step to `(slot, type)`.  With a cross-slot mutation that
projection is unsound: it reported

```text
t1:s16 -> t1:s32 -> t1:s16
```

for `G_CTLZ`, although `t0` differs between the two edges and the fixpoint
interpreter terminates on every domain query.  The analysis now builds the
**exact one-step query graph**.  This is stricter and more precise, not a
relaxation; the ToyCPU frozen baseline is unchanged.

## 6. Reproduction

Build the RISC-V oracle against the full LLVM build:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  <legacy-checkout>\tools\oracle\build.ps1 `
  -BuildDir <legacy-checkout>\out\oracle-riscv `
  -LLVMDir <llvm-build>\lib\cmake\llvm `
  -WithRiscv
```

Static check:

```powershell
cd <legacy-checkout>\tools
python -m legispec `
  --rules <legacy-checkout>\tools\legispec\rules\riscv64-scalar.lspec `
  check --strict `
  --capabilities <legacy-checkout>\tools\legispec\riscv64-scalar-capabilities.json
```

Differential conformance:

```powershell
python -X utf8 <legacy-checkout>\tools\legispec\conformance.py `
  --rules <legacy-checkout>\tools\legispec\rules\riscv64-scalar.lspec `
  --oracle <legacy-checkout>\out\oracle-riscv\legispec-oracle-riscv.exe `
  --domain D --closure R `
  --out <legacy-checkout>\out\riscv64-scalar-conformance-report.json `
  --llvm-build <llvm-build>
```

Evidence:

```text
queries=7861 divergences=0 rules=103/103
closure_query_count=62 closure_outside_d=0
input sha256=e03e73ddedef463e3dccd92908d6a85191f4d254aa66fc2781ccab17895de839
```

## 7. What this experiment teaches

1. **The target is a point in a parameter space.**  "RISC-V" is not one
   legality table.  RV32/RV64 and each extension flip branches.  A migration
   checklist must start by naming the exact points to model.
2. **C++ helper names are not one thing.**  `lower()` vs `lowerFor(t)`,
   `widenScalarToNextPow2` vs `...OrMinSize`, and `scalarSameSizeAs` all look
   similar and behave differently.  The DSL is only honest if each gets its
   own primitive.
3. **Cross-slot rules break slot-local analysis.**  Both the DSL and the
   checker needed a two-slot view before `scalarSameSizeAs` could be stated
   and verified.
4. **Feature gating is the dominant source of divergence risk.**  A rule table
   copied from a feature-rich CPU is not merely incomplete on
   `generic-rv64`; it is *wrong* (native MUL vs libcall, legal CLMUL vs
   unsupported).

## 8. Not proven

- RV32 × feature combinations (`+m`, `+zbb`, `+d`, ...); the plain RV32
  model is covered separately in `riscv32-gisel-experiment.md`;
- `generic-rv64` with `+zbc`, `+zbkc`, `+p` or `+v`; the `+m,+zmmul,+zbb`
  variant is covered separately in `riscv64-mzb-experiment.md`;
- the pointer rulesets and `G_BRJT` (covered separately in
  `riscv64-pointer-experiment.md`);
- memory, atomics, FP, calls and the actual legalization rewrites.

The follow-up experiment was the same file with `+m,+zmmul,+zbb`, which flips
arithmetic, division, bit manipulation and count-zeros at once.  It is now
documented in `riscv64-mzb-experiment.md`: 7,861 queries, 0 divergences, with
the query hash deliberately unchanged.  It confirmed that the configuration
must be named explicitly, and exposed three asymmetries that are easy to
transcribe incorrectly (poison count-zeros, `minScalar` vs `clampScalar`, and
the Zmmul overflow path).

## 同一配置的单源写法

`rules/riscv-scalar-conditional.lspec` 用 `--define` 把这份配置与另一个
XLen 配置写在同一份源码里；决策函数与本文件逐查询等价。见
`docs/config-guard.md`，一键验证：
`toycpu/scripts/check-riscv-conditional.ps1`。
