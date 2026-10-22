# AArch64 GlobalISel: +CSSC feature axis

Date: 2026-10-20
Status: PASS (scalar subset, generic vs +cssc)

## 0. Result first

This experiment isolates **one AArch64 feature bit** on the already-modeled
scalar policy: `HasCSSC` false vs true.  It is the feature-axis companion to
the RISC-V XLen experiment.

| Item | generic | +cssc |
|---|---:|---:|
| Modeled rulesets | 22 | 22 |
| Modeled opcodes | 48 | 48 |
| Modeled rules (expanded) | 91 | 95 |
| Domain LLTs | 20 | 20 |
| D union R queries | 8,161 | 8,161 |
| Divergences from the matching oracle | **0** | **0** |
| Rule coverage | **91 / 91** | **95 / 95** |
| Closure-only queries | 85 | 86 |
| Static check | 0 errors / 187 warnings | 0 errors / 194 warnings |
| Oracle binary | `legispec-oracle-aarch64.exe` | `legispec-oracle-aarch64-cssc.exe` |

The two oracle runs use the **same query corpus** (same SHA-256), so the
difference is a pure feature effect:

```text
queries=8161 divergences=0 rules=91/91 sha256=76734cf0ca196ef2...
queries=8161 divergences=0 rules=95/95 sha256=76734cf0ca196ef2...
```

Direct comparison of the two oracle binaries:

```text
queries=8161 differences=62 (0.76%)
```

Only 7 opcodes change.  CSSC is a **local feature flip**, not a global
"bit operations become legal" switch.

## 1. What CSSC is, and why this experiment is clean

FEAT_CSSC is Arm's *Common Short Sequence Compression* extension.  LLVM
exposes it as the `+cssc` subtarget feature:

```text
llvm/lib/Target/AArch64/AArch64Features.td:334-335
def FeatureCSSC : ExtensionWithMArch<"cssc", "CSSC", "FEAT_CSSC",
  "Enable Common Short Sequence Compression (CSSC) instructions">;
```

The extension is part of the Armv8.9-A default set
(`AArch64Features.td:1089-1092`).  The `generic` CPU does **not** enable it
(`AArch64Processors.td:1294` uses `ProcessorFeatures.Generic`), so
`generic` vs `generic,+cssc` changes exactly one bit of the subtarget.

This matters for the DSL argument.  `HasCSSC` is read once in the
`AArch64LegalizerInfo` constructor:

```cpp
// AArch64LegalizerInfo.cpp:126
const bool HasCSSC = ST.hasCSSC();
```

Every `legalFor(HasCSSC, ...)`, `customFor(!HasCSSC, ...)` and
`minScalar(HasCSSC, ...)` is a **construction-time bool**.  These overloads
return early when the bool is false, so the rule is not added at all.  One
frozen LegiSpec file per configuration is therefore exact, not an approximation.

## 2. Configuration and oracle separation

Both oracles use:

```text
triple      = aarch64-unknown-linux-gnu
cpu         = generic
code model  = small
NEON        = true
FPARMv8     = true
HasSVE      = false
HasFP16     = not modeled
```

The only difference is the feature string passed to `createTargetMachine`:

```cpp
#if defined(AARCH64_ORACLE_CSSC)
  const char *Features = "+cssc";
#else
  const char *Features = "";
#endif
```

The CSS variant is a **separate executable**
(`legispec-oracle-aarch64-cssc.exe`) so a conformance report can never be
silently attributed to the wrong binary.  The same pattern is used for the
RISC-V `+m,+zmmul,+zbb` variant.

Smoke check:

| Query | generic | +cssc |
|---|---|---|
| `G_ABS [s32]` | Custom | **Legal** |
| `G_CTPOP [s32,s32]` | Custom | **Legal** |
| `G_CTTZ [s32,s32]` | Custom | **Legal** |
| `G_UMAX [s32]` | Lower | **Legal** |
| `G_ABS [s128]` | Lower | Lower |
| `G_CTPOP [s16,s16]` | Widen t0 s32 | Widen t0 s32 |
| `G_CTTZ [s128,s128]` | Narrow t1 s64 | Narrow t1 s64 |

## 3. The four affected rulesets

Only four rulesets read `HasCSSC` in the scalar projection.  Everything else
is byte-for-byte identical between the two LegiSpec files.

### 3.1 `G_SMIN`, `G_SMAX`, `G_UMIN`, `G_UMAX`

```cpp
// AArch64LegalizerInfo.cpp:303-310
getActionDefinitionsBuilder({G_SMIN, G_SMAX, G_UMIN, G_UMAX})
    .legalFor({v8i8, v16i8, v4i16, v8i16, v2i32, v4i32})
    .legalFor(HasCSSC, {i32, i64})
    .minScalar(HasCSSC, 0, s32)
    .clampNumElements(0, v8s8, v16s8)
    .clampNumElements(0, v4s16, v8s16)
    .clampNumElements(0, v2s32, v4s32)
    .lower();
```

Generic scalar file:

```text
ruleset G_SMIN, G_SMAX, G_UMIN, G_UMAX {
    lower;
}
```

`+cssc` file:

```text
ruleset G_SMIN, G_SMAX, G_UMIN, G_UMAX {
    legal_for {s32, s64};
    widen t0 to s32 when scalar(t0) and bits(t0) < 32;
    lower;
}
```

Consequences:

- `s32` and `s64`: `Lower -> Legal`.
- `s1..s24`: `Lower -> WidenScalar t0 s32`.
- `s128`: still `Lower`; `minScalar` has no maximum.

This is why 48 of the 62 differences are `Lower -> WidenScalar`, not
`Lower -> Legal` -- the feature adds both a legal set and a minimum width.

### 3.2 `G_ABS`

```cpp
// AArch64LegalizerInfo.cpp:313-320
getActionDefinitionsBuilder(G_ABS)
    .legalFor(HasCSSC, {i32, i64})
    .legalFor({v16i8, ...})
    .customIf([=](const LegalityQuery &Q) {
      LLT SrcTy = Q.Types[0];
      return SrcTy.isScalar() && SrcTy.getSizeInBits() < 128;
    })
    ...
    .lower();
```

Generic: every scalar below 128 is `Custom`; `s128` is `Lower`.

`+cssc`: `s32/s64` become `Legal` before the `customIf`; other sub-128
scalars remain `Custom`; `s128` remains `Lower`.

The flip is exactly 2 queries: `s32` and `s64`.

### 3.3 `G_CTPOP`

```cpp
// AArch64LegalizerInfo.cpp:363-383
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

The 32/64-bit pairs are the only CSSC-sensitive part:

```text
generic:
    custom when pair(t0, t1) in {(s32, s32), (s64, s64), (s128, s128)};
+cssc:
    legal_for_pairs {(s32, s32), (s64, s64)};
    custom when pair(t0, t1) in {(s128, s128)};
```

The rest of the pair normalization is identical: `t0` is clamped to
`s32..s128`, widened to a power of two, and `t1` is forced to `t0`'s width.
`(s128,s128)` is `Custom` in both configurations.

`widenScalarOrEltToNextPow2OrMinSize(0, 8)` is shadowed by the preceding
`clampScalar(0, s32, s128)` for scalars; it is vector-only in this domain.
`scalarizeIf` also requires a vector.  Both are omitted from the scalar
transcription, with a comment, exactly as in `G_PHI`.

### 3.4 `G_CTTZ`

```cpp
// AArch64LegalizerInfo.cpp:408-414
getActionDefinitionsBuilder(G_CTTZ)
    .lowerIf(isVector(0))
    .widenScalarToNextPow2(1, 32)
    .clampScalar(1, s32, s64)
    .scalarSameSizeAs(0, 1)
    .legalFor(HasCSSC, {s32, s64})
    .customFor(!HasCSSC, {s32, s64});
```

The final action is at the **end**, after normalization.  `t1` is widened and
clamped to `s32..s64`, then `t0` is forced to `t1`'s width.  Only then does
`legal/custom` see `t0`.

Therefore:

- `s32/s32` and `s64/s64`: `Custom -> Legal`.
- `s16/s16`: `WidenScalar t1 s32` in both.
- `s128/s128`: `NarrowScalar t1 s64` in both.
- `s32/s64`: `WidenScalar t0 s64` in both.

The flip is exactly 2 queries.

## 4. Measurement

`diff_oracles.py` compares the two binaries over the same `D union R` corpus:

```text
queries=8161 differences=62 (0.76%)
```

| Opcode | Differences | Detail |
|---|---:|---|
| `G_SMIN` | 14 | 12 `Lower -> WidenScalar s32`, 2 `Lower -> Legal` |
| `G_SMAX` | 14 | same |
| `G_UMIN` | 14 | same |
| `G_UMAX` | 14 | same |
| `G_ABS` | 2 | `Custom -> Legal` (`s32`, `s64`) |
| `G_CTPOP` | 2 | `Custom -> Legal` (`(s32,s32)`, `(s64,s64)`) |
| `G_CTTZ` | 2 | `Custom -> Legal` (`(s32,s32)`, `(s64,s64)`) |

By action pair:

```text
Lower       -> WidenScalar   48
Lower       -> Legal          8
Custom      -> Legal          6
```

62 / 8,161 = 0.7597%.

The query SHA-256 is identical for both reports
(`76734cf0ca196ef2...`).  The closure-only counts differ (85 vs 86) because
the two policies classify some intermediate states differently, but every
closure member lies inside the declared domain (`closure_outside_d=0`), so
`D union R` is the same `D` in both runs.

## 5. Controls: what did not change

These are the negative controls that prevent over-reading the result:

| Query | generic | +cssc |
|---|---|---|
| `G_SMIN [s128]` | Lower | Lower |
| `G_ABS [s128]` | Lower | Lower |
| `G_CTPOP [s16,s16]` | Widen t0 s32 | Widen t0 s32 |
| `G_CTPOP [s128,s128]` | Custom | Custom |
| `G_CTTZ [s16,s16]` | Widen t1 s32 | Widen t1 s32 |
| `G_CTTZ [s128,s128]` | Narrow t1 s64 | Narrow t1 s64 |

The other 18 scalar rulesets (`G_ADD`, `G_MUL`, shifts, division, extensions,
`G_SELECT`, branches, ...) produce identical answers.  In the C++ source,
`G_CTLZ/G_CTLS` (385-402) has no `HasCSSC` clause at all; it is outside this
scalar subset, but the source fact is a useful reminder that "CSSC" is not a
synonym for "all bit-count operations".

## 6. What this feature axis teaches

1. **Feature axes are local; XLen axes are global.**  RV64 -> RV32 changed
   3,220 / 7,861 scalar answers (41.0%).  CSSC changes 62 / 8,161 (0.76%).
   The feature is a set of local legal/custom additions, not a width-lattice
   rewrite.
2. **`HasCSSC` is construction-time, so freezing is exact.**  `legalFor(bool)`,
   `customFor(bool)` and `minScalar(bool)` return early when false.  The C++
   rule table itself already "skips" the rule; the LegiSpec comment is a record
   of that fact, not a workaround.
3. **Placement of the conditional rule matters.**  `G_CTPOP` puts the
   legal/custom pair first; `G_CTTZ` puts it after `widen/clamp/scalarSameSizeAs`;
   `G_SMIN` uses `minScalar`, not `clampScalar`.  Three different flip shapes
   from the same feature bit.
4. **"Legal" does not mean "all widths legal".**  `s128` still lowers for
   min/max and ABS; sub-32 min/max widen; sub-128 ABS stays Custom.
5. **One oracle per configuration is provenance.**  A shared binary with a
   runtime flag would make it too easy to attribute a report to the wrong
   configuration.

## 7. Files and evidence

Rules and manifests:

```text
toycpu/tools/legispec/rules/aarch64-scalar.lspec
toycpu/tools/legispec/rules/aarch64-cssc-scalar.lspec
toycpu/tools/legispec/aarch64-scalar-capabilities.json
toycpu/tools/legispec/aarch64-cssc-scalar-capabilities.json
```

Reports:

```text
toycpu/out/aarch64-scalar-conformance-report.json
toycpu/out/aarch64-cssc-scalar-conformance-report.json
toycpu/out/aarch64-cssc-flips.json
```

The flip report records all 62 rows, not just the summary counts.

## 8. Reproduce

Build both AArch64 oracles (the switch builds the generic and `+cssc`
executables):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\tools\oracle\build.ps1 `
  -BuildDir F:\llvm-study\toycpu\out\oracle-aarch64 `
  -LLVMDir F:\llvm\build\lib\cmake\llvm `
  -WithAArch64
```

Static checks:

```powershell
cd F:\llvm-study\toycpu\tools
python -X utf8 -m legispec `
  --rules F:\llvm-study\toycpu\tools\legispec\rules\aarch64-scalar.lspec `
  check --strict `
  --capabilities F:\llvm-study\toycpu\tools\legispec\aarch64-scalar-capabilities.json

python -X utf8 -m legispec `
  --rules F:\llvm-study\toycpu\tools\legispec\rules\aarch64-cssc-scalar.lspec `
  check --strict `
  --capabilities F:\llvm-study\toycpu\tools\legispec\aarch64-cssc-scalar-capabilities.json
```

Differential conformance:

```powershell
python -X utf8 F:\llvm-study\toycpu\tools\legispec\conformance.py `
  --rules F:\llvm-study\toycpu\tools\legispec\rules\aarch64-scalar.lspec `
  --oracle F:\llvm-study\toycpu\out\oracle-aarch64\legispec-oracle-aarch64.exe `
  --domain D --closure R `
  --out F:\llvm-study\toycpu\out\aarch64-scalar-conformance-report.json `
  --llvm-build F:/llvm/build

python -X utf8 F:\llvm-study\toycpu\tools\legispec\conformance.py `
  --rules F:\llvm-study\toycpu\tools\legispec\rules\aarch64-cssc-scalar.lspec `
  --oracle F:\llvm-study\toycpu\out\oracle-aarch64\legispec-oracle-aarch64-cssc.exe `
  --domain D --closure R `
  --out F:\llvm-study\toycpu\out\aarch64-cssc-scalar-conformance-report.json `
  --llvm-build F:/llvm/build
```

Feature diff:

```powershell
python -X utf8 F:\llvm-study\toycpu\tools\legispec\diff_oracles.py `
  --rules F:\llvm-study\toycpu\tools\legispec\rules\aarch64-scalar.lspec `
  --oracle-left F:\llvm-study\toycpu\out\oracle-aarch64\legispec-oracle-aarch64.exe `
  --oracle-right F:\llvm-study\toycpu\out\oracle-aarch64\legispec-oracle-aarch64-cssc.exe `
  --label-left generic --label-right cssc `
  --out F:\llvm-study\toycpu\out\aarch64-cssc-flips.json
```

Expected tails:

```text
queries=8161 divergences=0 rules=91/91 sha256=76734cf0ca196ef2...
queries=8161 divergences=0 rules=95/95 sha256=76734cf0ca196ef2...
queries=8161 differences=62 (0.76%) sha256=76734cf0ca196ef2...
```

## 9. Limits

Proven:

```text
finite scalar domain D, closure R, 8,161 observations per configuration
Python LegiSpec generic == real AArch64 generic getAction
Python LegiSpec +cssc  == real AArch64 +cssc  getAction
```

Not proven:

```text
vector / scalable-vector CSSC rules
CSSC custom legalization rewrite bodies
instruction selection / code quality of the CSSC instructions
SVE / FP16 feature variants
memory descriptors, FP, atomics, calls
```

The experiment covers legality only: `LegalizerInfo::getAction` returns one
step.  It says nothing about whether the selected CSSC instruction is the
best one.

## 10. One-sentence conclusion

> On the same 8,161-query scalar corpus, enabling `+cssc` changes exactly 62
> answers (0.76%) across 7 opcodes; both the generic and `+cssc` LegiSpec models
> agree with their own real oracle with zero divergences, and the differences
> are fully explained by four construction-time `HasCSSC` clauses plus rule
> order.
