# RISC-V GlobalISel: product experiment (RV32 x +M+Zmmul+Zbb)

Date: 2026-10-20
Status: PASS (RV32 scalar subset, generic-rv32 + `+m,+zmmul,+zbb`)

## 0. Result first

This is the **product point** of the two axes already modeled separately:

```text
XLen axis     : RV64 -> RV32              (riscv32-gisel-experiment.md)
feature axis  : +i -> +m,+zmmul,+zbb      (riscv64-mzb-experiment.md)
product point : RV32 x (+m,+zmmul,+zbb)   (this file)
```

| Item | RV32 base | RV32 +m,+zmmul,+zbb |
|---|---:|---:|
| Modeled rulesets | 38 | 39 |
| Modeled opcodes | 73 | 73 |
| Modeled rules (after macro expansion) | 100 | 115 |
| D union R queries | 7,861 | 7,861 |
| Closure-only queries | 63 | 60 |
| Divergences | 0 | **0** |
| Oracle errors | 0 | 0 |
| Rule coverage | 100 / 100 | 111 / 115 |
| Static check | 0 errors / 164 warnings / 0 unknown | 0 errors / 176 warnings / 0 unknown |

The query-set hash is again identical to every other scalar run
(`e03e73ddedef463e3dccd92908d6a85191f4d254aa66fc2781ccab17895de839`).

The four unmatched rules are not transcription mistakes.  They are rules the
C++ really contains but which the rule order or the finite domain makes
unreachable:

| Ruleset | Rule | Why unreachable on RV32 |
|---|---|---|
| `G_CTLZ` (also `G_CTTZ`) | `custom when t0==s32 and t1==s32` | `legalFor({{sXLen,sXLen}})` precedes it and `sXLen == s32` |
| `G_CTLZ` | `widen_to_next_pow2 0` | after `clampScalar(0,s32,s32)` only s32 remains, and s32 is a power of two |
| `G_CTLZ` | trailing `lower` | every scalar lands on the legal/custom pair |
| `G_SDIV` (also `G_UDIV`,`G_UREM`) | `custom when t0==s32` | `legalFor({sXLen})` precedes it and `sXLen == s32` |

The coverage tool reports one entry per ruleset+rule index, so aliases share
an entry.

## 1. Configuration

```text
triple      = riscv32-unknown-linux-gnu
cpu         = generic-rv32
code model  = small
features    = +32bit, +i, +m, +zmmul, +zbb
XLen        = 32
sXLen       = s32
sDoubleXLen = s64
p0          = p0 (32-bit)
```

The oracle is `legispec-oracle-riscv32-mzb.exe`, built from the same
`main.cpp` with `RISCV_ORACLE_RV32` + `RISCV_ORACLE_FEATURE_VARIANT`.

## 2. Why the product is not the sum

The two single-axis diffs were measured on the same 7,861 queries:

| Comparison | Differing queries | Share |
|---|---:|---:|
| RV32 base -> RV32+mzb (feature effect) | 967 | 12.3% |
| RV64+mzb -> RV32+mzb (XLen effect) | 3,291 | 41.9% |
| RV32 base -> RV64+mzb (both axes) | 3,968 | 50.5% |

`967 + 3,291 = 4,258 > 3,968`, and 269 queries belong to both single-axis
diffs.  Composition is therefore **not additive**: the same query can be
changed by both axes, and the endpoint comparison can partially cancel.
Modeling the product by applying the feature diff to the RV64 file, or the
XLen diff to the RV64+mzb file, is not sound.

### 2.1 The shadowing trap

In C++:

```cpp
getActionDefinitionsBuilder({G_SDIV, G_UDIV, G_UREM})
    .legalFor({sXLen})
    .customFor({s32})
    ...

auto &CountZerosActions = getActionDefinitionsBuilder({G_CTLZ, G_CTTZ});
CountZerosActions.legalFor({{sXLen, sXLen}})
    .customFor({{s32, s32}})
    ...
```

`customFor({s32})` is evaluated unconditionally.  On RV64 it fires because
`sXLen == s64`.  On RV32 `sXLen == s32`, so `legalFor` matches first and the
custom rule is dead.  The oracle confirms `Legal` for `G_SDIV [s32]` and
`G_CTLZ [s32,s32]`.  This behavior exists at neither single-axis point; it is
created by the product.

### 2.2 `G_SEXT_INREG` composition

```text
ValidSextInRegWidth = all(sizeIs(0,64), immIs(0,32)) || imm in {8,16}
legalIf(typeIs(0,sXLen) && ValidSextInRegWidth)
```

- RV32 base: no legal width (the size-64 disjunct is unsatisfiable).
- RV64 + Zbb: `[s64] imm=32`, `[s64] imm=8`, `[s64] imm=16` legal.
- RV32 + Zbb: `[s32] imm=8/16` legal; `[s32] imm=32` is **still not legal**.
  The second disjunct is not size-gated, while the first one is hardcoded to
  64.

This is the clearest example in the corpus of a predicate whose meaning
changes by *composition*, not by a simple substitution.

## 3. Other flips at this point

Smoke probe (RV32 base -> RV32+mzb):

| Query | RV32 base | RV32+mzb |
|---|---|---|
| `G_MUL [s32]` | Libcall | **Legal** |
| `G_SDIV [s32]` | Libcall | **Legal** (custom shadowed) |
| `G_SDIV [s64]` | Libcall | Libcall |
| `G_SMULH [s32]` | Lower | **Legal** |
| `G_SMULH [s64]` | Unsupported | **Lower** |
| `G_SMULO [s32]` | WidenScalar s64 | **Lower** |
| `G_CTLZ [s32,s32]` | Lower | **Legal** (custom shadowed) |
| `G_CTLZ [s64,s64]` | NarrowScalar s32 | NarrowScalar s32 |
| `G_ROTR [s32,s32]` | Lower | **Legal** |
| `G_BSWAP [s64]` | NarrowScalar s32 | NarrowScalar s32 |
| `G_CTPOP [s32,s64]` | NarrowScalar t1 s32 | NarrowScalar t1 s32 |
| `G_ABS [s32]` | Lower | **Custom** |
| `G_ABS [s64]` | Lower | Lower |
| `G_UMAX [s32]` | Lower | **Legal** |
| `G_SEXT_INREG [s32] imm=8/16` | Lower | **Legal** |
| `G_SEXT_INREG [s32] imm=32` | Lower | Lower |
| `G_READCYCLECOUNTER [s64]` | Custom | Custom |

Rule-level changes relative to the RV32 base file:

```text
G_MUL                 libcall {s32,s64} -> legal s32, clamp s32
G_SMULH/UMULH         lower s32 only -> legal s32 + lower
G_SMULO/UMULO         widen s32->s64 then lower -> minScalar s32 then lower
G_SDIV/UDIV/UREM      libcall -> legal s32, custom s32 (shadowed), libcall s64
G_SREM                libcall -> legal s32, libcall s64
G_ROTR/ROTL           lower -> legal (s32,s32) + lower
G_BSWAP               maxScalar+lower -> legal s32 + clamp
G_CTLZ/CTTZ           maxScalar+lower -> legal/custom(s32,s32)+clamp+same-size
G_CTLZ_ZERO_POISON    maxScalar+same-size+lower -> lower only
G_CTPOP               pow2/clamp+lower -> legal (s32,s32)+clamp+same-size
G_ABS                 lower -> custom s32 + minScalar + lower
G_ABDS/ABDU           lower -> minScalar s32 + lower
G_UMAX/UMIN/SMAX/SMIN lower -> legal s32 + minScalar + lower
G_SEXT_INREG          all lower -> imm 8/16 legal (imm 32 still not)
```

## 4. Orthogonality check: pointer rules at RV32

`riscv32-pointer.lspec` against the RV32+mzb oracle:

```text
queries=770 divergences=0 rules=31/31
closure_query_count=30 closure_outside_d=0
input sha256=5cf8d0902ae44893...
```

Identical to the RV32 base oracle.  As at RV64, M/Zmmul/Zbb do not touch the
pointer rulesets.  See `riscv32-pointer-experiment.md`.

## 5. Reproduction

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  <legacy-checkout>\tools\oracle\build.ps1 `
  -BuildDir <legacy-checkout>\out\oracle-riscv `
  -LLVMDir <llvm-build>\lib\cmake\llvm `
  -WithRiscv

cd <legacy-checkout>\tools
python -X utf8 -m legispec `
  --rules <legacy-checkout>\tools\legispec\rules\riscv32-mzb-scalar.lspec `
  check --strict `
  --capabilities <legacy-checkout>\tools\legispec\riscv32-mzb-scalar-capabilities.json

python -X utf8 <legacy-checkout>\tools\legispec\conformance.py `
  --rules <legacy-checkout>\tools\legispec\rules\riscv32-mzb-scalar.lspec `
  --oracle <legacy-checkout>\out\oracle-riscv\legispec-oracle-riscv32-mzb.exe `
  --domain D --closure R `
  --out <legacy-checkout>\out\riscv32-mzb-scalar-conformance-report.json `
  --llvm-build <llvm-build>
```

Evidence:

```text
queries=7861 divergences=0 rules=111/115
closure_query_count=60 closure_outside_d=0
input sha256=e03e73ddedef463e3dccd92908d6a85191f4d254aa66fc2781ccab17895de839
```

## 6. What this experiment teaches

1. **Configuration is a product, not a list of independent switches.**
   Rule order makes `legalFor({sXLen})` and `customFor({s32})` interact; the
   interaction only appears when `sXLen == s32`.
2. **Unreachable rules are evidence, not garbage.**  Keeping the shadowed
   custom rules in the model and reporting them as unmatched proves that the
   model mirrors the C++ rule list, not just its observable behavior.
3. **Single-axis diffs do not compose additively.**  The measured overlap
   (269 queries) and the non-additive total are a direct warning for any
   "apply the diff" migration strategy.
4. **A product point is still cheap once both axes exist.**  The oracle is a
   compile definition plus the same feature TU; the rules file is a
   mechanical, auditable diff of two already-verified files.

## 7. Not proven

- RV32 + D (`XLen == 32 && hasStdExtD()` activates the merge/unmerge
  s64/s32 pair and the `G_SELECT` s64 pair);
- other feature combinations (Zbc, Zbkc, Zbkb, P, V) on either XLen;
- vector/scalable-vector, memory, atomics, FP, calls and the actual
  `LegalizerHelper` rewrites.