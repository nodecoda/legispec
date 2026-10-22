# AArch64 GlobalISel: third-target conformance experiment

Date: 2026-10-20
Status: PASS (scalar subset; generic and +cssc)

## 0. Result first

The ToyCPU Legalizer DSL was pointed at a second real GlobalISel target:
**AArch64**.  This is intentionally a **scalar subset**, not a model of all
of AArch64.

| Item | Result |
|---|---|
| Modeled rulesets | 22 |
| Modeled opcodes | 48 |
| Modeled rules (after macro expansion) | 91 |
| Domain LLTs | 20 |
| D union R queries | 8,161 |
| Closure-only queries | 85 |
| Divergences | **0** |
| Oracle errors | 0 |
| Rule coverage | **91 / 91** |
| Static check | 0 errors / 187 warnings |
| Oracle build | `F:\llvm\build` (LLVM 24.0.0git, assertions ON) |

The claim proven here is narrow and concrete:

> On the declared finite scalar domain, the Python LDSL model and the real
> `AArch64LegalizerInfo::getAction(LegalityQuery)` agree on every observed
> step.

It does **not** prove that the DSL models all of AArch64, vector or scalable
vector legalization, pointer rulesets, memory descriptors, FP, atomics, or
the actual `LegalizerHelper` rewrites.  The scalar pointer rulesets are
covered separately in `aarch64-pointer-experiment.md`.

## 1. Why AArch64

The full LLVM build at `F:\llvm\build` registers:

```text
aarch64, arm, loongarch, toycpu, x86
```

AArch64 is a useful contrasting real target because its scalar policy does
not look like X86's:

- Scalar arithmetic is fundamentally 32/64-bit; `s128` is usually narrowed,
  lowered, or sent to a libcall rather than made legal.
- The constructor is feature-gated.  It returns an empty rule set unless the
  subtarget has NEON and FP ARMv8:

  ```cpp
  // AArch64LegalizerInfo.cpp:120-121
  if (!ST.hasNEON() || !ST.hasFPARMv8())
    return;
  ```

- Vector legality is a first-class part of almost every ruleset, and several
  predicates call `getNumElements()` directly.
- Pointer rulesets (`G_PTR_ADD`, `G_PTRTOINT`, `G_INTTOPTR`, `G_BRINDIRECT`,
  `G_GLOBAL_VALUE`) are separate from the integer skeleton.
- SVE introduces feature-dependent clauses that cannot be represented by one
  fixed rule table without parameterization.  CSSC is a smaller, fully
  enumerated feature flip; it is modeled separately in
  `aarch64-cssc-experiment.md`.

Configuration observed by the oracle:

```text
triple      = aarch64-unknown-linux-gnu
cpu         = generic
NEON        = true
FPARMv8     = true
HasFP16     = not modeled (affects FP rules only)
HasCSSC     = false
HasSVE      = false
sMaxScalar  = s64
```

This file is the `generic` (CSSC off) point.  The `+cssc` point is
`rules/aarch64-cssc-scalar.lspec`; it differs only in four rulesets
(`G_SMIN/G_SMAX/G_UMIN/G_UMAX`, `G_ABS`, `G_CTPOP`, `G_CTTZ`).

The `NEON`/`FPARMv8` gate is a **target-model precondition**, not a legality
rule.  If it is false, every ruleset below is absent.  The LDSL file records
the chosen configuration in its header and in
`aarch64-scalar-capabilities.json`.

## 2. Files

Added:

```text
toycpu/tools/legalizer_dsl/rules/aarch64-scalar.lspec
toycpu/tools/legalizer_dsl/aarch64-scalar-capabilities.json
toycpu/tools/legalizer_dsl/rules/aarch64-cssc-scalar.lspec
toycpu/tools/legalizer_dsl/aarch64-cssc-scalar-capabilities.json
toycpu/out/aarch64-scalar-conformance-report.json
toycpu/out/aarch64-scalar-check.txt
toycpu/out/aarch64-cssc-scalar-conformance-report.json
toycpu/out/aarch64-cssc-flips.json
toycpu/notes/aarch64-gisel-experiment.md
toycpu/notes/aarch64-cssc-experiment.md
```

Changed:

```text
toycpu/tools/oracle/main.cpp        AARCH64_ORACLE branch
toycpu/tools/oracle/CMakeLists.txt  fourth executable, ORACLE_WITH_AARCH64
toycpu/tools/oracle/build.ps1       -WithAArch64 switch and artifact check
toycpu/tools/legalizer_dsl/conformance.py
                                    --llvm-build metadata option
```

The `conformance.py` change is small but matters for evidence: the report used
to hard-code `"llvm_build": "F:/llvm/build-toycpu"`.  AArch64 is linked
against `F:\llvm\build`, so the report now takes `--llvm-build` and records the
actual build directory.

## 3. Modeled rules

All line references point at:

```text
F:\llvm\llvm-project\llvm\lib\Target\AArch64\GISel\AArch64LegalizerInfo.cpp
```

### 3.1 Shape of the scalar policy

| Ruleset | Legal scalar types / pairs | First fallback |
|---|---|---|
| `G_CONSTANT` | `s8, s16, s32, s64` | widen to pow2, then clamp `s8..s64` |
| `G_IMPLICIT_DEF`, `G_FREEZE`, `G_CONSTANT_FOLD_BARRIER` | `s8, s16, s32, s64, s128` | widen to pow2, then clamp `s8..s64` |
| `G_PHI` | `s16, s32, s64` | widen to pow2, then clamp `s16..s64` |
| `G_ADD`, `G_SUB`, `G_AND`, `G_OR`, `G_XOR` | `s32, s64` | widen to pow2, then clamp `s32..s64` |
| `G_MUL` | `s32, s64` | widen to pow2, then clamp `s32..s64` |
| `G_SHL`, `G_ASHR`, `G_LSHR` | `(s32,s32), (s32,s64), (s64,s64)` | `(s32,s32)` is Custom; clamp both; `minScalarSameAs(1,0)` |
| `G_SDIV`, `G_UDIV` | `s32, s64` | `s128` Libcall; clamp `s32..s64` |
| `G_SREM`, `G_UREM`, `G_SDIVREM`, `G_UDIVREM` | none (Lower for `s8..s64`) | `s128` Libcall; widen to pow2, then min `s32`; above `s128` Unsupported |
| `G_SMULO`, `G_UMULO` | none (always Lower) | widen to pow2 (min 32), clamp `s32..s64` |
| `G_SMULH`, `G_UMULH` | `s64` | Lower for everything else |
| `G_SMIN`, `G_SMAX`, `G_UMIN`, `G_UMAX` | none (generic) / `s32, s64` (+cssc) | generic Lower; +cssc widens `< s32` to `s32`, `s128` Lower |
| `G_ABS` | none (generic) / `s32, s64` (+cssc) | `s < 128` Custom, `s128` Lower |
| `G_CTPOP` | none | Custom for `(s32,s32)`, `(s64,s64)`, `(s128,s128)`; clamp `s32..s128`, then `t1 := t0` |
| `G_CTTZ` | none (generic) / `s32, s64` (+cssc) | clamp `t1` to `s32..s64`, then `t0 := t1`; final Custom/Legal |
| `G_UADDE` ... `G_SADDO` | `(s32,s32), (s64,s32)` | clamp both, then Lower |
| `G_BSWAP` | `s32, s64` | widen to pow2 (min 16), then clamp `s32..s64` |
| `G_ICMP` | `(s32,s32), (s32,s64)` | widen t1 to pow2, min `s8`, clamp t1 `s32..s64`, clamp t0 `s32..s32` |
| `G_ZEXT`, `G_SEXT`, `G_ANYEXT` | predicate `ExtLegalFunc` | clamp `s64..s64` for `s128` |
| `G_TRUNC` | every scalar | `alwaysLegal()` for scalars |
| `G_SELECT` | `(s32,s32), (s64,s32)` | clamp t0 `s32..s64`, then t1 `s32..s32` |
| `G_BR` | every type | `alwaysLegal()` |
| `G_BRCOND` | `s32` | clamp `s32..s32` |

The table is a summary.  The LDSL file records each rule in source order.

### 3.2 Constants

```cpp
// 732-735
getActionDefinitionsBuilder(G_CONSTANT)
    .legalFor({p0, s8, s16, s32, s64})
    .widenScalarToNextPow2(0)
    .clampScalar(0, s8, s64);
```

The scalar domain omits `p0`, so the model is:

```text
legal_for {s8, s16, s32, s64};
widen_to_next_pow2 0, 0;
clamp_scalar 0, s8, s64;
```

A query `G_CONSTANT [s128]` is therefore narrowed to `s64`.

### 3.3 Implicit def / freeze / constant-fold barrier

```cpp
// 130-144
.legalFor({p0, s8, s16, s32, s64, s128})
.widenScalarToNextPow2(0)
.clampScalar(0, s8, s64)
```

All vector clauses are omitted.  `s128` is legal here, unlike `G_CONSTANT`.

### 3.4 Phi

```cpp
// 146-158
.legalFor({p0, s16, s32, s64})
.widenScalarToNextPow2(0)
...
.clampScalar(0, s16, s64)
...
.widenScalarOrEltToNextPow2OrMinSize(0, 8)
```

The last rule is unreachable for scalars because `clampScalar(0, s16, s64)`
already widened everything below `s16`.  The model records only the reachable
scalar chain.  Probe: `G_PHI [s1]` and `G_PHI [s8]` both widen to `s16`.

### 3.5 Integer arithmetic and logic

```cpp
// 181-206
getActionDefinitionsBuilder({G_ADD, G_SUB, G_AND, G_OR, G_XOR})
    .legalFor({i32, i64, v8i8, ...})
    .widenScalarToNextPow2(0)
    .clampScalar(0, s32, s64)
    ...
```

Scalar model:

```text
legal_for {s32, s64};
widen_to_next_pow2 0, 0;
clamp_scalar 0, s32, s64;
```

Probes: `s16 -> s32`, `s24 -> s32`, `s32 -> Legal`, `s64 -> Legal`,
`s128 -> s64` via NarrowScalar.

### 3.6 Multiply

```cpp
// 208-232
getActionDefinitionsBuilder(G_MUL)
    .legalFor({i32, i64, v8i8, ...})
    .widenScalarToNextPow2(0)
    .clampScalar(0, s32, s64)
    ...
```

The scalar chain is identical to add/sub.  The vector predicates that follow
are omitted, and they are the reason the scalar domain must not contain
pointers (see section 6).

### 3.7 Shifts

```cpp
// 234-265
getActionDefinitionsBuilder({G_SHL, G_ASHR, G_LSHR})
    .customIf([](const LegalityQuery &Query) {
      const auto &SrcTy = Query.Types[0];
      const auto &AmtTy = Query.Types[1];
      return !SrcTy.isVector() && SrcTy.getSizeInBits() == 32 &&
             AmtTy.getSizeInBits() == 32;
    })
    .legalFor({{i32, i32}, {i32, i64}, {i64, i64}, ...})
    .widenScalarToNextPow2(1)
    .widenScalarToNextPow2(0)
    .clampScalar(1, s32, s64)
    .clampScalar(0, s32, s64)
    ...
    .minScalarSameAs(1, 0)
    .scalarizeIf(...)
    .minScalarEltSameAsIf(...)
    .maxScalarEltSameAsIf(...);
```

The LDSL model:

```text
custom when bits(t0) == 32 and bits(t1) == 32;
legal_for_pairs {(s32, s32), (s32, s64), (s64, s64)};
widen_to_next_pow2 1, 0;
widen_to_next_pow2 0, 0;
clamp_scalar 1, s32, s64;
clamp_scalar 0, s32, s64;
widen t1 to s64 when bits(t0) == 64 and bits(t1) < 64;
```

Two order-sensitive facts:

1. `customIf` is first, so `G_SHL [s32, s32]` is **Custom**, even though
   `(i32, i32)` also appears in the following `legalFor`.
2. After both clamps, the only reachable `minScalarSameAs(1, 0)` case is
   `t0 = s64, t1 = s32`.  The explicit `widen t1 to s64` rule models it.

Probes: `[s32,s32] -> Custom`, `[s32,s64] -> Legal`, `[s64,s32] -> Widen t1
to s64`, `[s64,s64] -> Legal`, `[s16,s16] -> Widen t1 to s32`.

### 3.8 Division and remainder

```cpp
// 274-279
getActionDefinitionsBuilder({G_SDIV, G_UDIV})
    .legalFor({i32, i64})
    .libcallFor({i128})
    .clampScalar(0, s32, s64)
    .widenScalarToNextPow2(0)
    .scalarize(0);

// 281-288
getActionDefinitionsBuilder({G_SREM, G_UREM, G_SDIVREM, G_UDIVREM})
    .lowerFor({i8, i16, i32, i64, ...})
    .libcallFor({i128})
    .widenScalarOrEltToNextPow2(0)
    .minScalarOrElt(0, s32)
    ...
    .scalarize(0);
```

The two families behave differently on scalars:

```text
SDIV / UDIV:   s32,s64 Legal; s128 Libcall; others clamp to s32..s64
SREM / UREM /
SDIVREM /
UDIVREM:       s8..s64 Lower; s128 Libcall; below s32 widen to s32
```

Probes: `G_SDIV [s64] -> Legal`, `[s128] -> Libcall`, `[s48] -> Widen s64`;
`G_SREM [s32] -> Lower`, `[s128] -> Libcall`, `[s256] -> Unsupported`.

### 3.9 Overflow intrinsics

```cpp
// 290-293
getActionDefinitionsBuilder({G_SMULO, G_UMULO})
    .widenScalarToNextPow2(0, /*Min = */ 32)
    .clampScalar(0, s32, s64)
    .lower();

// 295-297
getActionDefinitionsBuilder({G_SMULH, G_UMULH})
    .legalFor({i64, v16i8, v8i16, v4i32})
    .lower();
```

`G_SMULO/G_UMULO` clamp first and then **always Lower**.  `G_SMULH/G_UMULH`
have only scalar `s64` legal; every other scalar is Lower, not widened.

Probes: `G_SMULO [s24] -> Widen s32`, `[s64] -> Lower`; `G_SMULH [s64] ->
Legal`, `[s32] -> Lower`.

### 3.10 Carry-producing add/sub

```cpp
// 339-345
getActionDefinitionsBuilder(
    {G_SADDE, G_SSUBE, G_UADDE, G_USUBE, G_SADDO, G_SSUBO, G_UADDO, G_USUBO})
    .legalFor({{i32, i32}, {i64, i32}})
    .clampScalar(0, s32, s64)
    .clampScalar(1, s32, s64)
    .widenScalarToNextPow2(0)
    .lower();
```

Probes: `G_UADDO [s32,s32] -> Legal`, `[s64,s32] -> Legal`, `[s64,s64] ->
Lower`.  The second operand `s64` is clamped to `s32` before the final Lower.

### 3.11 Byte swap

```cpp
// 433-440
getActionDefinitionsBuilder(G_BSWAP)
    .legalFor({i32, i64, v4i16, ...})
    .widenScalarOrEltToNextPow2(0, 16)
    .clampScalar(0, s32, s64)
    ...
```

Probes: `G_BSWAP [s16] -> Widen s32`, `[s24] -> Widen s32`, `[s32] ->
Legal`.  The `s16` case first passes through the pow2/min-16 widen and then
hits the `s32` minimum clamp.

### 3.12 Integer compare

```cpp
// 740-769
getActionDefinitionsBuilder(G_ICMP)
    .legalFor({{i32, i32}, {i32, i64}, {i32, p0}})
    .widenScalarOrEltToNextPow2(1)
    .minScalarOrElt(1, s8)
    .clampScalar(1, s32, s64)
    .clampScalar(0, s32, s32)
    ...
```

Scalar model:

```text
legal_for_pairs {(s32, s32), (s32, s64)};
widen_to_next_pow2 1, 0;
widen t1 to s8 when scalar(t1) and bits(t1) < 8;
clamp_scalar 1, s32, s64;
clamp_scalar 0, s32, s32;
```

The `s1 -> s8 -> s32` path needs two `getAction` calls.  Probes:
`G_ICMP [s32,s3] -> Widen t1 s4`, `[s64,s8] -> Widen t1 s32`,
`[s32,s64] -> Legal`, `[s16,s16] -> Widen t1 s32`.

### 3.13 Extensions

```cpp
// 802-841
auto ExtLegalFunc = [=](const LegalityQuery &Query) {
  unsigned DstSize = Query.Types[0].getSizeInBits();
  if (Query.Types[0].isVector())
    return false;
  if (DstSize < 8 || DstSize >= 128 || !isPowerOf2_32(DstSize))
    return false;
  const LLT &SrcTy = Query.Types[1];
  unsigned SrcSize = SrcTy.getSizeInBits();
  if (SrcSize < 8 || !isPowerOf2_32(SrcSize))
    return false;
  return true;
};
```

The model is exactly this predicate:

```text
legal when bits(t0) >= 8 and bits(t0) < 128 and is_pow2(bits(t0))
          and bits(t1) >= 8 and is_pow2(bits(t1));
clamp_scalar 0, s64, s64;
```

`ExtLegalFunc` does **not** check that the destination is at least as wide as
the source.  A direct query `G_ANYEXT [s64, s128]` is therefore Legal.  This
may be unreachable for valid IR, but it is what `getAction` says, and the
LDSL records the code rather than the comment's intent.  Probes:
`G_ZEXT [s32,s8] -> Legal`, `[s32,s64] -> Legal`, `[s64,s128] -> Legal`,
`[s128,s8] -> Narrow s64`.

### 3.14 Truncate

```cpp
// 843-860
getActionDefinitionsBuilder(G_TRUNC)
    .legalFor({{v8s8, v8s16}, ...})
    ...
    .alwaysLegal();
```

Every scalar truncation is Legal, including `G_TRUNC [s128,s256]`.  The
vector `lowerIf` clauses are omitted because they never match a scalar query.

### 3.15 Select

```cpp
// 1081-1088
getActionDefinitionsBuilder(G_SELECT)
    .legalFor({{s32, s32}, {s64, s32}, {p0, s32}})
    .widenScalarToNextPow2(0)
    .clampScalar(0, s32, s64)
    .clampScalar(1, s32, s32)
    ...
```

Probes: `G_SELECT [s32,s64] -> Narrow t1 s32`, `[s1,s32] -> Widen t0 s32`,
`[s64,s64] -> Narrow t1 s32`.

### 3.16 Branches

```cpp
// 1075-1078
getActionDefinitionsBuilder(G_BR).alwaysLegal();
getActionDefinitionsBuilder(G_BRCOND)
    .legalFor({s32})
    .clampScalar(0, s32, s32);
```

Probes: `G_BR [] -> Legal`, `G_BRCOND [s1] -> Widen s32`, `[s32] -> Legal`.

### 3.17 Min/max (CSSC-sensitive)

```cpp
// 303-310
getActionDefinitionsBuilder({G_SMIN, G_SMAX, G_UMIN, G_UMAX})
    .legalFor({v8i8, v16i8, ...})
    .legalFor(HasCSSC, {i32, i64})
    .minScalar(HasCSSC, 0, s32)
    .clampNumElements(...)
    .lower();
```

The vector clauses are out of scope.  Generic scalar policy is just `lower`.
With `+cssc`, `s32/s64` are Legal, scalars below `s32` widen to `s32`, and
`s128` still lowers because `minScalar` has no maximum.  The oracle confirms
all three outcomes.

### 3.18 ABS

```cpp
// 313-332
getActionDefinitionsBuilder(G_ABS)
    .legalFor(HasCSSC, {i32, i64})
    .legalFor({v16i8, ...})
    .customIf(scalar && size < 128)
    ...
    .lower();
```

Generic: `s < 128` Custom, `s128` Lower.  With `+cssc`, `s32/s64` are Legal
before the `customIf`; every other sub-128 scalar stays Custom, and `s128`
still Lower.

### 3.19 CTPOP

```cpp
// 363-383
getActionDefinitionsBuilder(G_CTPOP)
    .legalFor(HasCSSC, {{i32, i32}, {i64, i64}})
    .legalFor({{v8i8, v8i8}, {v16i8, v16i8}})
    .customFor(!HasCSSC, {{s32, s32}, {s64, s64}})
    .customFor({{s128, s128}, ...})
    .clampScalar(0, s32, s128)
    .widenScalarToNextPow2(0)
    .widenScalarOrEltToNextPow2OrMinSize(0, 8)
    .minScalarEltSameAsIf(always, 1, 0)
    .maxScalarEltSameAsIf(always, 1, 0)
    ...
    .scalarizeIf(scalarOrEltWiderThan(0, 64), 0);
```

For scalars this is a pair policy: the 32/64-bit pairs are Custom when CSSC is
off and Legal when it is on; `(s128,s128)` is Custom in both.  `t0` is clamped
to `s32..s128`, widened to a power of two, and `t1` is forced to `t0`'s width.
`widenScalarOrEltToNextPow2OrMinSize(0, 8)` is shadowed by the clamp, and
`scalarizeIf` requires a vector, so neither contributes in this domain.

### 3.20 CTTZ

```cpp
// 408-414
getActionDefinitionsBuilder(G_CTTZ)
    .lowerIf(isVector(0))
    .widenScalarToNextPow2(1, 32)
    .clampScalar(1, s32, s64)
    .scalarSameSizeAs(0, 1)
    .legalFor(HasCSSC, {s32, s64})
    .customFor(!HasCSSC, {s32, s64});
```

`lowerIf(isVector(0))` is vacuous for scalars.  The normalisation rules run
before the final action: `t1` is widened/clamped to `s32..s64`, then `t0` is
forced to `t1`'s width.  Only then does the CSSC flip apply.  This is why
`s16/s16` and `s128/s128` answers are identical in both configurations; only
the 32/64-bit terminal action changes (Custom -> Legal).

## 4. Domain

```text
types {
    s1, s2, s3, s4, s5, s6, s7, s8, s12, s16, s20, s24,
    s32, s40, s48, s64, s80, s96, s128, s256
};
imms {};
mems {};
```

The non-power-of-two sizes force `widen_to_next_pow2` to produce a new type
and therefore exercise the closure.  `s80/s96/s256` exercise widths above
`s64`.

The domain deliberately contains **no pointer LLTs**.  This is a correctness
requirement, not a convenience; section 6 explains the two assertions that
forced it.

Q0 seeds (one or more per ruleset) are in the `domain` block of
`rules/aarch64-scalar.lspec`.  The seeds include the four rulesets added for the
CSSC axis, so their arity is defined and they participate in closure.

## 5. Oracle

`legalizer-oracle-aarch64.exe` is the same JSONL front end as the ToyCPU and
X86 oracles.  Its target initialization is:

```cpp
LLVMInitializeAArch64TargetInfo();
LLVMInitializeAArch64Target();
LLVMInitializeAArch64TargetMC();

Triple TT("aarch64-unknown-linux-gnu");
const Target *T = TargetRegistry::lookupTarget("aarch64", TT, Error);
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
because the LLVM build has assertions enabled; the oracle must be compiled
with the same `NDEBUG` state.

Build command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\tools\oracle\build.ps1 `
  -BuildDir F:\llvm-study\toycpu\out\oracle-aarch64 `
  -LLVMDir F:\llvm\build\lib\cmake\llvm `
  -WithAArch64
```

Expected artifact:

```text
F:\llvm-study\toycpu\out\oracle-aarch64\legalizer-oracle-aarch64.exe
```

## 6. What the experiment found

### 6.1 First match wins, always

`LegalizeRuleSet::apply()` iterates rules in insertion order and returns the
first matching rule.  It does not backtrack and does not prefer a "better"
rule.  The clearest example is `G_SHL [s32, s32]`: the leading `customIf`
wins over the later `legalFor({i32, i32})`, so the answer is `Custom`, not
`Legal`.

The 146 static-check warnings are mostly `K001/K002` order-sensitive
overlaps.  They are not noise; they identify exactly where the C++ rule set
depends on order.

### 6.2 AArch64 predicates are not total over LLTs

The first full-domain run included `p0` and `p0:64` and aborted:

```text
Assertion failed: isVector() && "Expected a vector type",
LowLevelType.h, line 363
```

The captured stderr is in:

```text
toycpu/out/aarch64-d-err.txt
```

The cause is visible in `G_ADD` and `G_MUL`:

```cpp
// 190-204, 216-230
return Query.Types[0].getNumElements() <= 2;
return Query.Types[0].getNumElements() <= 4;
return Query.Types[0].getNumElements() <= 16;
```

`getNumElements()` calls `isScalable()`, which asserts `isVector()` on a
scalar or pointer.  These predicates assume the earlier scalar clauses have
already caught non-vectors.  That assumption is not a total-function
guarantee; a direct `getAction` query with a pointer reaches the predicate and
aborts.

The LDSL has no exception/abort term, so the only honest model is a domain
that excludes those inputs.  This is a general lesson for the method: **a
rule table transcribed from C++ is only as total as the C++ predicate chain,
and `getAction` is not a total function over arbitrary LLTs.**

### 6.3 A 32-bit pointer hits `changeElementSize`

A second variant added `p0` (32-bit pointer) to the domain.  It aborted in
the `G_SHL` path that models `minScalarSameAs(1, 0)`, inside:

```cpp
// LowLevelType.h:428-430
LLT changeElementSize(unsigned NewEltSize) const {
  assert(!isPointerOrPointerVector() &&
         "invalid to directly change element size for pointers");
```

The target's scalar rules assume integer types; a pointer fed into the same
path reaches an operation that is invalid for pointers.  Pointer legality is
therefore a separate experiment with its own rulesets and its own LLT domain,
not a few extra types appended to the scalar sweep.

The follow-up pointer experiment confirms the positive case: AArch64's legal
pointer is `p0:64`, not 32-bit `p0`.  The oracle returns `Legal` for
`G_BRINDIRECT [p0:64]` and `Unsupported` for `G_BRINDIRECT [p0]`.  The 32-bit
`p0` used in the failing variant was the wrong LLT shape.  See
`aarch64-pointer-experiment.md`.

### 6.4 Multi-step legalization is normal

`G_ICMP [s32, s3]` first widens `t1` to `s4` (the next power of two) and only
on the next call widens it to `s32`.  The DSL models one `getAction` step at a
time; the closure `R` captures the chains.  For this domain:

```text
closure_query_count = 65
closure_outside_d  = 0
```

### 6.5 Extensions accept a wider source

`ExtLegalFunc` checks the destination and source widths independently.  It
never compares them.  Consequently `G_ZEXT [s64, s128]` is Legal.  The
in-source comment says "We shouldn't be allowing anything through which is
wider than the destination in the first place", but the predicate does not
enforce it.  The LDSL follows the predicate, and the oracle confirms it.

This is a good example of the method's value: the spec is extracted from the
implementation and then falsified against it, not from what the comment says
the implementation ought to do.

### 6.6 Scalar truncation is always legal

`G_TRUNC` ends with `.alwaysLegal()`.  For the scalar domain there is no
reachable lower or narrow rule, so every scalar truncation is Legal.  This is
a genuine target policy, not a modeling shortcut.

### 6.7 Unsupported is a real action, not a modelling failure

The remainder family has no rule above `s128`.  `lowerFor` stops at `i64`,
`libcallFor` is only `i128`, the pow2/min rules do not change `s256`, and
`scalarize` is a no-op for scalars.  `getAction` therefore returns
`Unsupported` for `G_SREM [s256]`.  The LDSL models this by falling through to
the default, and the oracle confirms it.

This matters because "the DSL does not model X" and "the target returns
Unsupported for X" are different claims.  The second is a target policy and
must be preserved.

### 6.8 AArch64 and X86 have different scalar "shapes"

The same DSL primitives describe both targets, but the policies differ:

```text
X86:     s8/s16/s32/s64 legal for add; byte/word/dword/qword skeleton
AArch64: s32/s64 legal for add; narrower scalars widen to 32
```

This is the point of the second-target experiment: the DSL is not fitted to
ToyCPU's rule table.  It can express a real target's scalar policy, including
first-match shadows, multi-step clamps, libcalls, and unconditional lowering.

## 7. Verification

Static check:

```text
rulesets=22 opcodes=48 rules=91
checks: errors=0 warnings=187 unknown=0
```

Differential run:

```text
queries=8161
divergences=0
oracle_errors=0
rules=91/91
closure_query_count=85
closure_outside_d=0
sha256=76734cf0ca196ef2236d8c333e13fc10afacfc74d1b81b2a16cac0971854a951
ok=true
```

Report:

```text
F:\llvm-study\toycpu\out\aarch64-scalar-conformance-report.json
```

The report's `reference.llvm_build` is now `F:/llvm/build`, not the ToyCPU
build.  The oracle executable and the LLVM source commit are recorded in the
same report.

Selected probes:

| Query | Result |
|---|---|
| `G_CONSTANT [s32]` | `Legal` |
| `G_CONSTANT [s128]` | `NarrowScalar t0 -> s64` |
| `G_PHI [s1]` | `WidenScalar t0 -> s16` |
| `G_PHI [s8]` | `WidenScalar t0 -> s16` |
| `G_ADD [s16]` | `WidenScalar t0 -> s32` |
| `G_ADD [s24]` | `WidenScalar t0 -> s32` |
| `G_ADD [s128]` | `NarrowScalar t0 -> s64` |
| `G_MUL [s24]` | `WidenScalar t0 -> s32` |
| `G_SHL [s32, s32]` | `Custom` |
| `G_SHL [s32, s64]` | `Legal` |
| `G_SHL [s64, s32]` | `WidenScalar t1 -> s64` |
| `G_ASHR [s64, s32]` | `WidenScalar t1 -> s64` |
| `G_SDIV [s64]` | `Legal` |
| `G_SDIV [s128]` | `Libcall` |
| `G_SREM [s32]` | `Lower` |
| `G_SREM [s256]` | `Unsupported` |
| `G_SMULO [s64]` | `Lower` |
| `G_SMULH [s64]` | `Legal` |
| `G_SMULH [s32]` | `Lower` |
| `G_UADDO [s32, s32]` | `Legal` |
| `G_UADDO [s64, s64]` | `Lower` |
| `G_BSWAP [s16]` | `WidenScalar t0 -> s32` |
| `G_ICMP [s32, s3]` | `WidenScalar t1 -> s4` |
| `G_ICMP [s64, s8]` | `WidenScalar t1 -> s32` |
| `G_ICMP [s32, s64]` | `Legal` |
| `G_ZEXT [s32, s64]` | `Legal` |
| `G_ZEXT [s64, s128]` | `Legal` |
| `G_ZEXT [s128, s8]` | `NarrowScalar t0 -> s64` |
| `G_TRUNC [s128, s256]` | `Legal` |
| `G_SELECT [s32, s64]` | `NarrowScalar t1 -> s32` |
| `G_BRCOND [s1]` | `WidenScalar t0 -> s32` |
| `G_SMIN [s8]` | `Lower` (generic) / `WidenScalar t0 -> s32` (+cssc) |
| `G_SMIN [s32]` | `Lower` (generic) / `Legal` (+cssc) |
| `G_ABS [s32]` | `Custom` (generic) / `Legal` (+cssc) |
| `G_ABS [s128]` | `Lower` |
| `G_CTPOP [s32,s32]` | `Custom` (generic) / `Legal` (+cssc) |
| `G_CTPOP [s16,s16]` | `WidenScalar t0 -> s32` |
| `G_CTPOP [s128,s128]` | `Custom` |
| `G_CTTZ [s32,s32]` | `Custom` (generic) / `Legal` (+cssc) |
| `G_CTTZ [s16,s16]` | `WidenScalar t1 -> s32` |
| `G_CTTZ [s128,s128]` | `NarrowScalar t1 -> s64` |

## 8. Reproduce

Build the AArch64 oracle:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\tools\oracle\build.ps1 `
  -BuildDir F:\llvm-study\toycpu\out\oracle-aarch64 `
  -LLVMDir F:\llvm\build\lib\cmake\llvm `
  -WithAArch64
```

Static check:

```powershell
cd F:\llvm-study\toycpu\tools
python -m legalizer_dsl `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\aarch64-scalar.lspec `
  check --strict
```

Differential conformance:

```powershell
python -X utf8 F:\llvm-study\toycpu\tools\legalizer_dsl\conformance.py `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\aarch64-scalar.lspec `
  --oracle F:\llvm-study\toycpu\out\oracle-aarch64\legalizer-oracle-aarch64.exe `
  --domain D --closure R `
  --out F:\llvm-study\toycpu\out\aarch64-scalar-conformance-report.json `
  --llvm-build F:/llvm/build
```

Expected tail:

```text
queries=8161 divergences=0 rules=91/91 sha256=76734cf0ca196ef2...
```

ToyCPU regression gate:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\scripts\check-legalizer-dsl.ps1
```

## 9. Limits and next steps

Proven:

```text
finite scalar domain D, closure R, 8,161 observations
Python LDSL policy == real AArch64 getAction
```

Not proven:

```text
full AArch64 legalizer
vector and scalable-vector legalization
vector pointer forms (v2p0) and address spaces
memory descriptors and G_LOAD/G_STORE
FP / atomics / SVE / FP16 feature variants
Custom legalization rewrite bodies
LegalizerHelper rewrites
IRTranslator / CallLowering / ABI behavior

The scalar pointer rulesets (G_PTR_ADD, G_PTRMASK, G_PTRTOINT, G_INTTOPTR,
G_BRINDIRECT, G_FRAME_INDEX, G_GLOBAL_VALUE, G_PTRAUTH_GLOBAL_VALUE,
G_BLOCK_ADDR, G_JUMP_TABLE, G_BRJT) are covered by the separate pointer
experiment, not by this scalar file.
```

CSSC is covered separately in `aarch64-cssc-experiment.md`.

The highest-value next experiments are:

1. **Pointer subset (done).** The scalar pointer rulesets are modeled in
   `aarch64-pointer.lspec`: 11 rulesets, 17 rules, 924 queries, 0 divergences.
   Remaining pointer work: vector pointers (`v2p0`), address spaces, and the
   custom rewrite bodies.
2. **Vector subset.** Add vector LLTs and the vector rule primitives
   (`clampNumElements`, `clampMaxNumElements`, `moreElementsToNextPow2`,
   `scalarize`, `widenVectorEltsToVectorMinSize`) and model AArch64 vector
   add/mul/logic.  This is where most of AArch64's legalizer complexity lives.
3. **Feature parameterization.**  CSSC is done: `aarch64-cssc-scalar.lspec`
   flips four rulesets and is differentially tested against a separate
   `+cssc` oracle.  Remaining: `HasSVE` and `HasFP16` variants.  The LDSL still
   records one fixed subtarget configuration per file by design.
4. **Memory descriptors.** Extend the domain with `mems` and model
   `G_LOAD/G_STORE` legality, including the `legalForTypesWithMemDesc` shape.
5. **Upstream review candidates.** The two assertion findings in 6.2 and 6.3
   are worth checking against the current upstream source and, if still
   present, discussing with the AArch64 GlobalISel owners.  They are not
   necessarily reachable in normal legalization; they are reachable through
   the `getAction` query surface used by this experiment.

## 10. One-sentence conclusion

> The ToyCPU Legalizer DSL generalizes to a second real target on the scalar
> policy layer: 22 rulesets, 48 opcodes, 91 rules, 8,161 queries, zero
> divergences -- and the exercise exposed two latent LLT totality assumptions
> in AArch64's own rule predicates.
