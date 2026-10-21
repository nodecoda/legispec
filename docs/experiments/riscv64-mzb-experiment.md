# RISC-V GlobalISel: feature-flip experiment (+M+Zmmul+Zbb)

Date: 2026-10-20
Status: PASS (RV64 scalar subset, generic-rv64 + `+m,+zmmul,+zbb`)

## 0. Result first

`riscv64-gisel-experiment.md` modeled `generic-rv64` with only `+64bit,+i`.
This experiment keeps everything else identical and flips exactly three
extensions: **M, Zmmul, Zbb**.  It asks one question:

> Is a configuration point an explicit part of the model, or can a rule table
> silently drift between configurations?

| Item | generic-rv64 | +m,+zmmul,+zbb |
|---|---:|---:|
| Modeled rulesets | 38 | 39 |
| Modeled opcodes | 73 | 73 |
| Modeled rules (after macro expansion) | 103 | 118 |
| D union R queries | 7,861 | 7,861 |
| Closure-only queries | 62 | 65 |
| Divergences | 0 | **0** |
| Oracle errors | 0 | 0 |
| Rule coverage | 103 / 103 | 117 / 118 |
| Static check | 0 errors / 168 warnings / 0 unknown | 0 errors / 174 warnings / 0 unknown |
| Pointer ruleset on same binary | 770 / 0 divergence | **770 / 0 divergence** |

The claim is narrow, as always:

> On the declared finite RV64 scalar domain, the LDSL feature variant and the
> real `RISCVLegalizerInfo::getAction` built with `+m,+zmmul,+zbb` agree on
> every observed step.

The single unmatched rule is the trailing `G_CTLZ` `lower`.  It is unreachable
on the scalar domain (`G_CTLZ` either legalizes, customs, or clamps into one of
those two), which the static checker also reports as R002.  It stays in the
file because the C++ really contains it.

## 1. Configuration

The variant is a separate executable so a report can never be attributed to
the wrong binary.  The feature string is baked into
`tools/oracle/riscv_mzb_features.cpp` and linked only into
`legalizer-oracle-riscv-mzb.exe`.

```text
triple      = riscv64-unknown-linux-gnu
cpu         = generic-rv64
code model  = small
features    = +64bit, +i, +m, +zmmul, +zbb
XLen        = 64
sXLen       = s64
sDoubleXLen = s128
p0          = p0:64
```

`generic-rv64` still declares only `Feature64Bit, FeatureStdExtI` in
`RISCVProcessors.td`; the extensions come from the oracle's feature string.
Still absent: A, F, D, C, V, Zbc, Zbkc, Zbkb, P.  So CLMUL stays
Unsupported, `G_BITREVERSE` stays maxScalar+lower, `G_CTLS` stays
maxScalar+lower, and every vector predicate stays false.

## 2. What flips, and what does not

Smoke probe (same JSONL against both binaries):

| Query | generic-rv64 | +m,+zmmul,+zbb |
|---|---|---|
| `G_MUL [s64]` | Libcall | **Legal** |
| `G_SDIV [s64]` | Libcall | **Legal** |
| `G_UREM [s64]` | Libcall | **Legal** |
| `G_BSWAP [s64]` | Lower | **Legal** |
| `G_CTLZ [s64,s64]` | Lower | **Legal** |
| `G_CTLZ [s32,s32]` | Lower | **Custom** |
| `G_UMAX [s64]` | Lower | **Legal** |
| `G_SEXT_INREG [s64] imm=16` | Lower | **Legal** |
| `G_ABS [s32]` | Lower | **WidenScalar -> s64** |

The rule-level differences:

```text
G_MUL              libcall s64/s128 -> legal s64, pow2-widen, clamp to s64
G_SMULH/G_UMULH    lowerFor(s64)     -> legalFor(s64).lower()
G_SMULO/G_UMULO    s64 widens to s128 -> minScalar(s64) then lower
G_SDIV/UDIV/UREM   libcall           -> legal s64, custom s32, libcall s128
G_SREM             libcall           -> legal s64, libcall s128 (no s32 custom)
G_ROTR/G_ROTL      lower             -> legal (s64,s64), custom (s32,s32)
G_BSWAP            maxScalar+lower   -> legal s64, clamp to s64
G_CTLZ/G_CTTZ      maxScalar+lower   -> legal/custom + clamp + pow2 + same-size
G_CTPOP            pow2/clamp+lower  -> legal (s64,s64) + clamp + same-size
G_ABS              lower             -> custom s64, minScalar s64
G_ABDS/G_ABDU      lower             -> minScalar s64, lower
G_UMAX/Umin/SMAX/SMIN lower          -> legal s64, minScalar s64, lower
G_SEXT_INREG       imm 32 only       -> imm 8/16/32 legal
```

Unchanged by these extensions: `G_ADD/G_SUB`, `G_AND/OR/XOR`, shifts,
extensions, `G_CONSTANT`, `G_FREEZE`, `G_IMPLICIT_DEF`, `G_ICMP`, `G_SELECT`,
`G_PHI`, `G_BR`, `G_BRCOND`, carry/saturating, funnel shifts,
`G_BITREVERSE`, `G_CTLS`, CLMUL family, `G_SDIVREM/G_UDIVREM`,
`G_SCMP/G_UCMP`, `G_READCYCLECOUNTER`.

## 3. Three traps this experiment exposed

### 3.1 Poison count-zeros do not inherit Zbb

`CountZerosActions` and `CountZerosPoisonActions` are separate builders.
Only the former enters the `if (ST.hasStdExtZbb())` branch.  With Zbb on:

```text
G_CTLZ / G_CTTZ                          -> legal/custom/clamp rules
G_CTLZ_ZERO_POISON / G_CTTZ_ZERO_POISON  -> only the final .lower()
```

In the generic-rv64 file the else branch gave both builders maxScalar +
scalarSameSizeAs.  A transcription that copies the *generic* poison rules
into the feature variant would still pass a casual reading and produce wrong
answers for the whole poison family.  This is exactly the kind of
configuration-dependent asymmetry the DSL is meant to make explicit.

### 3.2 `minScalar` is not `clampScalar`

For `G_ABS`, `G_ABDS/G_ABDU` and min/max, C++ uses
`minScalar(ST.hasStdExtZbb(), 0, sXLen)`.  That is one-directional:

```text
smaller than s64 -> WidenScalar s64
s64              -> Legal or Custom or Lower (whatever the next rule says)
larger than s64  -> no minScalar match, fall through to Lower
```

`clampScalar(0, s64, s64)` would instead narrow `s96`/`s128` to s64.  The
DSL encodes the former as `widen t0 to s64 when ... bits(t0) < 64`, and the
oracle confirms `G_ABS [s128]` lowers rather than narrows.

### 3.3 Zmmul removes the s128 overflow widening

Under the generic configuration, `G_SMULO/G_UMULO` widen `s64` to `s128`
before lowering so a single libcall can produce both the low and high halves.
Under Zmmul the sequence is just `minScalar(0, s64).lower()`: the native
multiply path needs no libcall, so `s64` lowers directly.  Keeping the
generic widening would change both the action and the resulting MIR.

## 4. Orthogonality: pointer rules are untouched

Running `riscv64-pointer.ldsl` against the **mzb** oracle:

```text
queries=770 divergences=0 rules=32/32
closure_query_count=30 closure_outside_d=0
input sha256=020ac70e10682b2010b9b59fbae735465fe6c2cc66d044d3708c2e1d82b4794a
```

That is the same result as against the generic-rv64 oracle.  The pointer
rulesets (`G_PTR_ADD`, `G_PTRMASK`, `G_PTRTOINT`, `G_INTTOPTR`,
`G_BRINDIRECT`, `G_BRJT`, `G_FRAME_INDEX`, `G_GLOBAL_VALUE`, `G_JUMP_TABLE`,
`G_CONSTANT_POOL`) and the pointer branches of `G_CONSTANT`, `G_PHI`,
`G_ICMP`, `G_SELECT` do not depend on M/Zmmul/Zbb.  The two experiments are
orthogonal, which is the expected and now observed result.

## 5. Reproduction

Build both RISC-V oracles:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\tools\oracle\build.ps1 `
  -BuildDir F:\llvm-study\toycpu\out\oracle-riscv `
  -LLVMDir F:\llvm\build\lib\cmake\llvm `
  -WithRiscv
```

Static check:

```powershell
cd F:\llvm-study\toycpu\tools
python -X utf8 -m legalizer_dsl `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\riscv64-mzb-scalar.ldsl `
  check --strict `
  --capabilities F:\llvm-study\toycpu\tools\legalizer_dsl\riscv64-mzb-scalar-capabilities.json
```

Differential conformance:

```powershell
python -X utf8 F:\llvm-study\toycpu\tools\legalizer_dsl\conformance.py `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\riscv64-mzb-scalar.ldsl `
  --oracle F:\llvm-study\toycpu\out\oracle-riscv\legalizer-oracle-riscv-mzb.exe `
  --domain D --closure R `
  --out F:\llvm-study\toycpu\out\riscv64-mzb-scalar-conformance-report.json `
  --llvm-build F:/llvm/build
```

Evidence:

```text
queries=7861 divergences=0 rules=117/118
closure_query_count=65 closure_outside_d=0
input sha256=e03e73ddedef463e3dccd92908d6a85191f4d254aa66fc2781ccab17895de839
```

The input hash is identical to the generic-rv64 run because the domain is
identical: a feature flip must not change the query set, only the answers.
That equality is itself a useful invariant.

Frozen ToyCPU gate after the change:

```text
Ran 104 tests
checks: errors=0 warnings=46 unknown=0
witnesses=205 failures=0
queries=243714 divergences=0
sha256=8a8c9c9f26b6b4de954201bed878bb8b89b562549f070e1b4719cd7ca02b6cb2
RESULT: PASS
```

## 6. What this experiment teaches

1. **A target model is a point in a configuration space.**  "RISC-V" does not
   name one rule table.  The cheapest reliable way to model a second point is
   a diff against the first, with an explicit feature string and a separate
   oracle binary.
2. **Shared helper names hide policy differences.**  `lower()` vs
   `lowerFor(t)`, `minScalar` vs `clampScalar`, and the two count-zeros
   builders look interchangeable until a feature flips.  The DSL must state
   each shape separately.
3. **The domain should be held fixed while flipping configuration.**  Same
   queries, same hash, different answers: that isolates the feature effect
   from any change in coverage.
4. **Orthogonal rule families can be tested for free.**  Re-running the
   pointer file against the new binary is a cheap falsification test for
   accidental coupling.

## 7. Not proven

- RV32 × these features (plain RV32 is covered in
  `riscv32-gisel-experiment.md`);
- Zbc, Zbkc, Zbkb, P and V (each flips a different family);
- the actual `LegalizerHelper` rewrites behind Custom/Lower/Libcall;
- memory, atomics, FP, calls and ABI lowering.

The next natural step is another axis of the same target: RV32
(`sXLen = s32`, `sDoubleXLen = s64`) against an `riscv32` oracle.  That flips
XLen rather than extensions, and should be modeled as a separate domain, not
as a feature string.