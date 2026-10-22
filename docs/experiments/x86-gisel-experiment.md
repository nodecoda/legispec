# X86 GlobalISel: second-target conformance experiment

Date: 2026-10-19
Status: PASS (scalar subset)

## 0. Result first

The ToyCPU LegiSpec rules was pointed at a second, real GlobalISel target:
**X86-64**.  The experiment is intentionally a **scalar subset**, not a model
of all of X86.

| Item | Result |
|---|---|
| Modeled rulesets | 18 |
| Modeled opcodes | 31 |
| Modeled rules | 69 |
| Domain LLTs | 22 |
| D union R queries | 7,591 |
| Closure-only queries | 36 |
| Divergences | **0** |
| Oracle errors | 0 |
| Rule coverage | **69 / 69** |
| Static check | 0 errors / 80 warnings |
| ToyCPU regression | 101 tests, 243,714 queries, 0 divergences, identical SHA |

The claim proven here is narrow and concrete:

> On the declared finite scalar domain, the Python LegiSpec model and the real
> `X86LegalizerInfo::getAction(LegalityQuery)` agree on every observed step.

It does **not** prove that the DSL models all of X86, vector legalization,
memory descriptors, custom legalization, or the actual `LegalizerHelper`
rewrites.

## 1. Why X86

This LLVM build registers only three targets:

```text
nvptx, nvptx64, toycpu, x86, x86-64
```

Source targets that contain a `*LegalizerInfo.cpp`:

```text
AArch64, AMDGPU, ARM, BPF, M68k, Mips,
PowerPC, RISCV, SPIRV, ToyCPU, WebAssembly, X86
```

NVPTX is registered but has no `*LegalizerInfo.cpp`, and its codegen library
is not usable as a legality oracle.  Therefore X86 is the only target in this
build that can be linked directly as a second GlobalISel oracle.

Configuration observed:

```text
triple      = x86_64-pc-windows-msvc
cpu         = generic
Is64Bit     = true
sMaxScalar  = s64
p0          = pointer(0, 64)
```

## 2. Files

Added:

```text
toycpu/tools/legispec/rules/x86-scalar.lspec
toycpu/tools/legispec/x86-scalar-capabilities.json
toycpu/tools/oracle/... -> legispec-oracle-x86.exe
toycpu/out/x86-scalar-conformance-report.json
toycpu/notes/x86-gisel-experiment.md
```

Changed:

```text
toycpu/tools/oracle/main.cpp        X86_ORACLE branch; shared subtarget probe
toycpu/tools/oracle/CMakeLists.txt  third executable
toycpu/tools/oracle/build.ps1       verify the third executable exists
toycpu/tools/legispec/cli.py   rules-stem witness/capability discovery
```

The `cli.py` change generalizes auto-discovery from a hard-coded
`toycpu.witness` / `capabilities.json` to:

```text
<rules-stem>.witness
<rules-stem>-capabilities.json   (fallback: capabilities.json)
```

For `toycpu.lspec` the resolved paths are unchanged, so the ToyCPU gate was
re-run and still passes.

## 3. Modeled rules

All line references point at:

```text
<llvm-src>\llvm\lib\Target\X86\GISel\X86LegalizerInfo.cpp
```

### 3.1 Integer add/sub

```cpp
// 187-204
getActionDefinitionsBuilder({G_ADD, G_SUB})
    .legalFor({s8, s16, s32})
    .legalFor(Is64Bit, {s64})
    ...
    .widenScalarToNextPow2(0, /*Min=*/32)
    .clampScalar(0, s8, sMaxScalar)
    .scalarize(0);
```

Scalar model:

```text
legal_for {s8, s16, s32, s64};
widen_to_next_pow2 0, 32;
clamp_scalar 0, s8, s64;
```

Vector `legalFor`, `clampMinNumElements`, `clampMaxNumElements` and
`scalarize` cannot match a scalar query and are omitted.

### 3.2 Constant

```cpp
// 114-118
legalFor({p0, s8, s16, s32})
legalFor(Is64Bit, {s64})
widenScalarToNextPow2(0, 8)
clampScalar(0, s8, sMaxScalar)
```

On x86-64, `p0` is the 64-bit pointer, written as `p0:64` in LegiSpec.

### 3.3 Division/remainder

```cpp
// 243-247
legalFor({s8, s16, s32})
legalFor(Is64Bit, {s64})
libcallFor({s64})
clampScalar(0, s8, sMaxScalar)
```

On x86-64, `libcallFor({s64})` is **shadowed** by the preceding
`legalFor(Is64Bit, {s64})` because `LegalizeRuleSet::apply()` returns the
first matching rule.  The LegiSpec model therefore contains no `libcall_for`
rule for this configuration; a comment records the dead C++ rule.

### 3.4 Shifts

```cpp
// 250-254
legalFor({{s8,s8}, {s16,s8}, {s32,s8}})
legalFor(Is64Bit, {{s64,s8}})
clampScalar(0, s8, sMaxScalar)
clampScalar(1, s8, s8)
```

The order matters: the result type is clamped first, then the shift amount.

### 3.5 Integer compare

```cpp
// 275-279
IntTypes64 = {s8, s16, s32, s64, p0}
legalForCartesianProduct({s8}, IntTypes64)
clampScalar(0, s8, s8)
widenScalarToNextPow2(1, 8)
clampScalar(1, s8, sMaxScalar)
```

### 3.6 Truncate

```cpp
// 431-432
legalForCartesianProduct(
    {s1, s8, s16, s32, s64},
    {s8, s16, s32, s64, s128})
```

This is transcribed as 25 explicit pairs.  Note that the C++ rule does not
require the destination to be narrower than the source; the DSL records the
rule as written.

### 3.7 Carry-producing add/sub

```cpp
// 206-212
legalFor({{s8,s8}, {s16,s8}, {s32,s8}})
legalFor(Is64Bit, {{s64,s8}})
widenScalarToNextPow2(0, 32)
clampScalar(0, s8, sMaxScalar)
clampScalar(1, s8, s8)
```

### 3.8 Multiply and high multiply

```cpp
// 215-240
G_MUL, G_SMULH, G_UMULH:
legalFor({s8, s16, s32})
legalFor(Is64Bit, {s64})
widenScalarToNextPow2(0, 32)
clampScalar(0, s8, sMaxScalar)
```

### 3.9 Logic

```cpp
// 257-269
G_AND, G_OR, G_XOR:
legalFor({s8, s16, s32})
legalFor(Is64Bit, {s64})
widenScalarToNextPow2(0, 32)
clampScalar(0, s8, sMaxScalar)
```

### 3.10 BSWAP

```cpp
// 282-286
legalFor({s32})
legalFor(Is64Bit, {s64})
widenScalarToNextPow2(0, 32)
clampScalar(0, s32, sMaxScalar)
```

### 3.11 Pointer operations

```cpp
// 326-339
G_PTRTOINT:
  PtrTypes64 = {s1, s8, s16, s32, s64}
  legalForCartesianProduct(PtrTypes64, {p0})
  maxScalar(0, sMaxScalar)
  widenScalarToNextPow2(0, 8)

G_INTTOPTR:
  legalFor({{p0, s64}})

G_PTR_ADD:
  legalFor({{p0, s32}})
  legalFor(Is64Bit, {{p0, s64}})
  widenScalarToNextPow2(1, 32)
  clampScalar(1, s32, sMaxScalar)
```

`maxScalar` is written as an explicit `narrow t0 to s64 when ...` rule,
because there is no scalar narrower than `s1` to justify a `clamp_scalar`
minimum rule.

### 3.12 Extensions

```cpp
// 413-429
G_ANYEXT:
  legalFor({s8, s16, s32, s128})
  legalFor(Is64Bit, {s64})
  widenScalarToNextPow2(0, 8)
  clampScalar(0, s8, sMaxScalar)
  widenScalarToNextPow2(1, 8)
  clampScalar(1, s8, sMaxScalar)

G_SEXT, G_ZEXT:
  legalFor({s8, s16, s32})
  legalFor(Is64Bit, {s64})
  ... same trailing rules
```

`legalFor` only constrains `t0`, so the corresponding LegiSpec rule is:

```text
legal when t0 in {s8, s16, s32, s64, s128};
```

### 3.13 Branch

```cpp
// 319-320
G_BR.alwaysLegal();
G_BRCOND.legalFor({s1});
```

## 4. Domain

```text
types {
    s1, s2, s3, s4, s5, s6, s7, s8, s12, s16, s20, s24,
    s32, s40, s48, s64, s80, s96, s128, s256,
    p0, p0:64
};
imms {};
mems {};
```

The non-power-of-two sizes are deliberate: they force
`widen_to_next_pow2` to produce a new type and therefore exercise the
closure.  `s80/s96/s256` also exercise scalar values above `s64`.

`p0` and `p0:64` are distinct LLTs.  X86-64's legal pointer is `p0:64`;
`p0` (a 32-bit pointer) is not silently treated as X86's `p0`.

Q0 seeds (one or more per ruleset) are in the `domain` block of
`rules/x86-scalar.lspec`.

## 5. Oracle

`legispec-oracle-x86.exe` is the same JSONL front end as the ToyCPU
oracle.  It differs only in target initialization:

```cpp
LLVMInitializeX86TargetInfo();
LLVMInitializeX86Target();
LLVMInitializeX86TargetMC();

Triple TT("x86_64-pc-windows-msvc");
const Target *T = TargetRegistry::lookupTarget("x86-64", TT, Error);
std::unique_ptr<TargetMachine> TM(T->createTargetMachine(
    TT, "generic", "", Options, Reloc::Static, CodeModel::Small,
    CodeGenOptLevel::Default, /*JIT=*/false));

const TargetSubtargetInfo *ST = TM->getSubtargetImpl(*F);
const LegalizerInfo *LI = ST->getLegalizerInfo();
```

The adapter calls only:

```cpp
LegalizerInfo::getAction(LegalityQuery)
```

It contains no legality policy and no rewrite logic.  `-UNDEBUG` is retained
because the LLVM build has assertions enabled and `LegalizeRuleSet` layout
depends on the matching `NDEBUG` state.

## 6. Observations

Selected probe results from the real X86 oracle:

| Query | Result |
|---|---|
| `G_ADD [s32]` | `Legal` |
| `G_ADD [s1]` | `WidenScalar t0 -> s8` |
| `G_ADD [s24]` | `WidenScalar t0 -> s32` |
| `G_ADD [s128]` | `NarrowScalar t0 -> s64` |
| `G_CONSTANT [p0:64]` | `Legal` |
| `G_SDIV [s64]` | `Legal` |
| `G_SHL [s32, s32]` | `NarrowScalar t1 -> s8` |
| `G_ICMP [s32, s1]` | `NarrowScalar t0 -> s8` |
| `G_TRUNC [s8, s32]` | `Legal` |
| `G_TRUNC [s64, s128]` | `Legal` |
| `G_TRUNC [s128, s8]` | `Unsupported` |
| `G_MUL [s64]` | `Legal` |
| `G_PTR_ADD [p0:64, s32]` | `Legal` |
| `G_ANYEXT [s128, s1]` | `Legal` |
| `G_UADDO [s64, s8]` | `Legal` |

Full differential run:

```text
queries=7591 divergences=0 rules=69/69
sha256=c292fff2f28f6d122e0a249495986c5438b2067c8bdf19e6095f9d723ec6bf38
```

## 7. What the experiment found

### 7.1 First match wins, always

`LegalizeRuleSet::apply()` iterates rules in insertion order and returns the
first match.  It does not backtrack and does not prefer a "better" rule.
Every subtlety below follows from that one fact.

### 7.2 `clamp_scalar` is two ordered rules

`clampScalar(TypeIdx, Min, Max)` expands to `minScalar` followed by
`maxScalar`.  The LegiSpec expands it the same way, which is why the checker
reports many `K001/K002` order-sensitive overlaps.  Those warnings are not
noise: they identify exactly where the C++ rule set depends on order.

### 7.3 A dead rule in X86's own source

For `G_SDIV/G_SREM/G_UDIV/G_UREM`, `libcallFor({s64})` is unreachable on
x86-64 because `legalFor(Is64Bit, {s64})` appears first.  It is reachable on
32-bit X86, where `Is64Bit` is false.  The DSL makes this visible; the model
does not invent a `libcall` step that the real oracle never returns.

### 7.4 A legal rule can shadow a clamp

`G_ANYEXT` legalizes `s128`, so `.clampScalar(0, s8, s64)` cannot fire for
`s128`.  The max-clamp rule becomes reachable only for widths above 64 that
are not in the legal set, such as `s80`, `s96`, and `s256`.

### 7.5 Pointer width is part of the type identity

`p0` is not "the pointer".  X86-64's `p0` is `LLT::pointer(0, 64)`, modeled
as `p0:64`.  A query using a 32-bit `p0` is a different LLT and must not be
silently accepted.

### 7.6 The current DSL covers the scalar skeleton, not X86

X86's legalizer is vector-heavy.  The following C++ constructs are outside
the current LegiSpec surface:

```text
clampMinNumElements / clampMaxNumElements
moreElementsToNextPow2 / scalarize
legalForTypesWithMemDesc / memory descriptors
feature predicates beyond a fixed configuration
custom legalization and its rewrites
```

This experiment therefore says: **the method transfers to a second target on
the scalar policy layer**.  It does not say the DSL is a complete X86 model.

## 8. Reproduce

Build the three oracles:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  <legacy-checkout>\tools\oracle\build.ps1
```

Static check:

```powershell
cd <legacy-checkout>\tools
python -m legispec `
  --rules <legacy-checkout>\tools\legispec\rules\x86-scalar.lspec `
  check --strict
```

Differential conformance:

```powershell
python -X utf8 <legacy-checkout>\tools\legispec\conformance.py `
  --rules <legacy-checkout>\tools\legispec\rules\x86-scalar.lspec `
  --oracle <legacy-checkout>\out\oracle\legispec-oracle-x86.exe `
  --domain D --closure R `
  --out <legacy-checkout>\out\x86-scalar-conformance-report.json
```

Expected tail:

```text
queries=7591 divergences=0 rules=69/69 sha256=c292fff2f28f6d12...
```

ToyCPU regression gate:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  <legacy-checkout>\scripts\check-legispec.ps1
```

Last run: `RESULT: PASS elapsed=40.3s`, 101 tests, 243,714 queries,
0 divergences, input SHA-256 unchanged.

## 9. Limits and next steps

Proven:

```text
finite scalar domain D, closure R, 7,591 observations
Python LegiSpec policy == real X86 getAction
```

Not proven:

```text
full X86 legalizer
vector element-count legalization
load/store memory descriptors
custom legalization handlers
LegalizerHelper rewrites
IRTranslator / CallLowering / ABI behavior
```

If this line continues, the highest-value next experiments are:

1. Add memory descriptors and model `G_LOAD/G_STORE` (the DSL already has
   `mem` syntax, but X86 uses `legalForTypesWithMemDesc` extensively).
2. Add vector types plus `clampNumElements` / `scalarize` to the DSL, then
   model X86 vector add/mul/logic.
3. Model `G_GLOBAL_VALUE` custom legalization and extend the capability
   manifest mechanism to X86.
4. Build AArch64 or RISCV in a separate LLVM build and repeat the same
   scalar subset protocol.  Those targets would test whether the LegiSpec
   primitives generalize beyond the scalar X86/ToyCPU shape.
