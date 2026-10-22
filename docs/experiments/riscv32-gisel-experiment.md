# RISC-V GlobalISel: XLen-axis experiment (RV32)

Date: 2026-10-20
Status: PASS (RV32 scalar subset, generic-rv32)

## 0. Result first

The previous RISC-V experiments varied **features** at fixed XLen (64).  This
one varies **XLen** at fixed features (`+i` only).  The C++ constructor is the
same; only the triple, CPU and `XLen` change.

| Item | RV64 | RV32 |
|---|---:|---:|
| Modeled rulesets | 38 | 38 |
| Modeled opcodes | 73 | 73 |
| Modeled rules (after macro expansion) | 103 | 100 |
| D union R queries | 7,861 | 7,861 |
| Closure-only queries | 62 | 63 |
| Divergences | 0 | **0** |
| Oracle errors | 0 | 0 |
| Rule coverage | 103 / 103 | **100 / 100** |
| Static check | 0 errors / 168 warnings / 0 unknown | 0 errors / 164 warnings / 0 unknown |
| Answers differing from the other XLen | — | **3,220 / 7,861 (41.0%)** |

The claim:

> On the same declared finite scalar domain, the LDSL RV32 model and the real
> `RISCVLegalizerInfo::getAction` built for `riscv32 / generic-rv32` agree on
> every observed step.

The query-set hash is identical to the RV64 run
(`e03e73ddedef463e3dccd92908d6a85191f4d254aa66fc2781ccab17895de839`).  Same
domain, same queries, different answers: the XLen effect is isolated from any
change in coverage.

## 1. Configuration

```text
triple      = riscv32-unknown-linux-gnu
cpu         = generic-rv32
code model  = small
features    = +32bit, +i
XLen        = 32
sXLen       = s32
sDoubleXLen = s64
p0          = p0 (32-bit)
```

The oracle is a separate executable,
`legalizer-oracle-riscv32.exe`, built from the same `main.cpp` with
`RISCV_ORACLE_RV32`.  The RV64 and M/Zbb binaries are unchanged.

Still absent: A, F, D, C, V, M, Zmmul, Zbb, Zbc, Zbkc, Zbkb, P.  The pointer
rulesets are covered separately in `riscv32-pointer-experiment.md` (`p0:32`),
and the feature product point in `riscv32-mzb-experiment.md`.

## 2. What flips when XLen goes 64 -> 32

Smoke probe against both binaries:

| Query | RV64 | RV32 |
|---|---|---|
| `G_ADD [s32]` | Custom | **Legal** |
| `G_ADD [s64]` | Legal | **NarrowScalar s32** |
| `G_CONSTANT [s32]` | WidenScalar s64 | **Legal** |
| `G_CONSTANT [s64]` | Custom | **NarrowScalar s32** |
| `G_SEXT_INREG [s32] imm=32` | WidenScalar s64 | **Lower** |
| `G_SEXT_INREG [s64] imm=32` | Legal | **NarrowScalar s32** |
| `G_BRCOND [s32]` | WidenScalar s64 | **Legal** |
| `G_BRCOND [s64]` | Legal | **Unsupported** |
| `G_READCYCLECOUNTER [s64]` | Legal | **Custom** |
| `G_READCYCLECOUNTER [s32]` | Unsupported | Unsupported |
| `G_FREEZE [s64]` | Legal | **NarrowScalar s32** |
| `G_MUL [s32]` | WidenScalar s64 | **Libcall** |
| `G_MUL [s64]` | Libcall | Libcall |
| `G_SMULH [s32]` | Unsupported | **Lower** |
| `G_SMULH [s64]` | Lower | **Unsupported** |
| `G_SELECT [s32,s32]` | WidenScalar t1 -> s64 | **Legal** |
| `G_ICMP [s32,s32]` | WidenScalar t1 -> s64 | **Legal** |
| `G_ZEXT [s32,s32]` | Unsupported | Unsupported |
| `G_ZEXT [s32,s16]` | Legal | Legal |
| `G_PHI [s64]` | Legal | **NarrowScalar s32** |
| `G_SMULO [s32]` | WidenScalar s64 | WidenScalar s64 |
| `G_SMULO [s64]` | WidenScalar s128 | **Lower** |
| `G_CTLZ [s64,s64]` | Lower | **NarrowScalar s32** |

Rule-level changes:

```text
G_ADD/G_SUB            legal s64 -> legal s32; s32 Custom disappears
G_AND/OR/XOR           clamp s64 -> clamp s32
G_SADDE family         minScalar s64 -> s32
G_SHL family           legal (s64,s64) -> (s32,s32); s32 Custom disappears
G_ZEXT/SEXT/ANYEXT     RV64's s64 legal pairs disappear; only (s32,s16) stays
G_SEXT_INREG           s64/imm32 Legal -> unsatisfiable, everything lowers
G_CTLZ/CTTZ/CTPOP/...  maxScalar s64 -> s32
G_CONSTANT             s64 Custom -> s32 Legal (legalFor(!is64Bit,{s32}))
G_FREEZE               legal {s16,s32,s64} -> {s16,s32}; clamp max s32
G_IMPLICIT_DEF         legal {s32,s64} -> {s32}; clamp max s32
G_ICMP                 legal (s64,s64) -> (s32,s32)
G_SELECT               legal (s32,s64)/(s64,s64) -> (s32,s32)
G_PHI                  legal {s32,s64} -> {s32}; clamp max s32
G_MUL                  libcall {s64,s128} -> {s32,s64}; clamp max s64
G_SMULH/UMULH          lowerFor s64 -> s32
G_SMULO/UMULO          widen s64->s128 -> widen s32->s64, then lower
G_SDIV/UDIV/UREM/SREM  libcall {s64,s128} -> {s32,s64}; clamp max s64
G_BRCOND               legal s64 -> s32; s64 becomes Unsupported
G_READCYCLECOUNTER     s64 Legal -> s64 Custom (re-read high half)
```

The 3,220 differing answers are not noise: 1,125 come from `G_SEXT_INREG`
alone (15 types x 15 types x 5 immediates), followed by the `SADDE/SADDO/...`
family, shifts, extensions and count-zeros.  XLen is a first-class
configuration axis, not a suffix.

## 3. Traps this experiment exposed

### 3.1 `G_SEXT_INREG` has no legal width on RV32

```cpp
LegalityPredicate ValidSextInRegWidth = all(sizeIs(0, 64), immIs(0, 32));
...
.legalIf(all(typeIs(0, sXLen), ValidSextInRegWidth))
```

`sizeIs(0, 64)` is hardcoded, not `typeIs(0, sXLen)`.  On RV32
`typeIs(0,s32) && sizeIs(0,64)` is unsatisfiable, so **every** scalar lowers
(after clamping to s32).  A transcription that "generalizes" 64 to `sXLen`
would invent a legal case that the backend does not have.

### 3.2 `legalFor` + `minScalar` cannot shrink

`G_BRCOND` is `.legalFor({sXLen}).minScalar(0, sXLen)`.  On RV32 that is
`s32` Legal and `s64` **Unsupported** -- there is no clamp and no lower.  The
same shape appears in `G_SMULH/UMULH` (`lowerFor({sXLen})`): `s32` lowers,
`s64` is Unsupported.  Direction matters.

### 3.3 Counters flip Legal -> Custom, and the rest is Unsupported

```cpp
.legalFor(ST.is64Bit(), {s64})
.customFor(!ST.is64Bit(), {s64});
```

RV32 reads the 64-bit cycle/time CSRs with a re-read-the-high-half loop, so
`s64` is Custom.  There is no lower/unsupported rule, so `s32` (and every
other type) is Unsupported.  This is the only Custom ruleset in the RV32
scalar model.

### 3.4 `G_ZEXT [s32,s32]` is Unsupported

The legal extension pairs are `(s32,s16)` plus, on RV64 only,
`(s64,s16)`/`(s64,s32)`.  `maxScalar(0,sXLen)` only narrows, so RV32
`(s32,s32)` is not rescued by any rule.  The oracle confirms Unsupported.

### 3.5 `G_FREEZE` is not XLen-uniform

`legalFor({s16,s32,p0})` is unconditional, but `s64` is legal only on RV64.
On RV32 the clamp max is `sXLen = s32`, so `s64` narrows to s32 rather than
staying legal.

## 4. Orthogonality with the feature axis

The RV32 run uses the same `riscv64-mzb-scalar.lspec` domain, so the query
hash is identical, but the answers differ on 41% of queries.  Conversely, the
`+m,+zmmul,+zbb` file remains an RV64 model; it is not valid for RV32 because
`legalFor({sXLen})`, libcall widths and every `is64Bit()` branch move.

The two axes compose as a product, not a sum:

```text
configuration = XLen x feature set
```

Each point needs its own rule table and its own oracle binary.  There is no
single "RISC-V" legality table.

## 5. Reproduction

Build the RV32 oracle (the same command builds rx64, mzb and rv32):

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
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\riscv32-scalar.lspec `
  check --strict `
  --capabilities F:\llvm-study\toycpu\tools\legalizer_dsl\riscv32-scalar-capabilities.json
```

Differential conformance:

```powershell
python -X utf8 F:\llvm-study\toycpu\tools\legalizer_dsl\conformance.py `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\riscv32-scalar.lspec `
  --oracle F:\llvm-study\toycpu\out\oracle-riscv\legalizer-oracle-riscv32.exe `
  --domain D --closure R `
  --out F:\llvm-study\toycpu\out\riscv32-scalar-conformance-report.json `
  --llvm-build F:/llvm/build
```

Evidence:

```text
queries=7861 divergences=0 rules=100/100
closure_query_count=63 closure_outside_d=0
input sha256=e03e73ddedef463e3dccd92908d6a85191f4d254aa66fc2781ccab17895de839
```

## 6. What this experiment teaches

1. **XLen is a configuration axis, not a type suffix.**  It changes legal
   sets, custom handlers, clamp bounds, libcall widths and whether a rule
   exists at all.  Modeling RV32 by replacing `s64` with `s32` in the RV64
   file would be wrong in both directions.
2. **Hardcoded constants inside predicates are part of the spec.**
   `sizeIs(0,64)` is not a typo to be generalized; it is why RV32 has no
   legal `G_SEXT_INREG`.
3. **Rule direction is semantic.**  `legalFor` + `minScalar` and
   `lowerFor(t)` deliberately leave wider types Unsupported; only
   `clampScalar` shrinks.
4. **The same domain across configurations is a strong invariant.**  Equal
   query hash + different answers isolates the configuration effect and
   makes the diff auditable.

## 7. Not proven

- RV32 with D (`XLen == 32 && hasStdExtD()` activates the merge/unmerge
  s64/s32 pair and changes `G_SELECT`'s legal s64 pair);
- other feature combinations (Zbc, Zbkc, Zbkb, P, V) on RV32.  The plain
  RV32 pointer and RV32 x (+m,+zmmul,+zbb) points are covered in
  `riscv32-pointer-experiment.md` and `riscv32-mzb-experiment.md`;
- vector/scalable-vector, memory, atomics, FP, calls and the actual
  `LegalizerHelper` rewrites.

The product point RV32 x (+m,+zmmul,+zbb) is now documented in
`riscv32-mzb-experiment.md`; it exposes rule-order shadowing that neither
single-axis model can show.

## 同一配置的单源写法

`rules/riscv-scalar-conditional.lspec` 用 `--define` 把这份配置与另一个
XLen 配置写在同一份源码里；决策函数与本文件逐查询等价。见
`docs/config-guard.md`，一键验证：
`toycpu/scripts/check-riscv-conditional.ps1`。
