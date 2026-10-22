# RISC-V GlobalISel: pointer subset experiment (RV32)

Date: 2026-10-20
Status: PASS (RV32 pointer subset, generic-rv32)

## 0. Result first

| Item | RV64 pointer | RV32 pointer |
|---|---:|---:|
| Modeled rulesets | 11 | 11 |
| Modeled opcodes | 14 | 14 |
| Modeled rules (after macro expansion) | 32 | 31 |
| D union R queries | 770 | 770 |
| Closure-only queries | 30 | 32 |
| Divergences | 0 | **0** |
| Oracle errors | 0 | 0 |
| Rule coverage | 32 / 32 | **31 / 31** |
| Static check | 0 errors / 33 warnings / 0 unknown | 0 errors / 30 warnings / 0 unknown |
| Answers differing from the other XLen | — | **317 / 770 (41.2%)** |

The pointer domain is `{p0:32, s1, s8, s16, s32, s48, s64, s96, s128,
s256}`; the immediate and memory domains are empty.

The query-set hash (`5cf8d0902ae44893...`) differs from the RV64 pointer hash
because `p0:64` and `p0:32` are different LLTs in the JSONL.  The hash is
stable across the feature axis at RV32: running this file against the
`+m,+zmmul,+zbb` oracle gives the same 770 / 0 / 31/31 report.

## 1. Configuration

```text
triple      = riscv32-unknown-linux-gnu
cpu         = generic-rv32
code model  = small
pointer     = LLT::pointer(0, 32), written p0:32
features    = +32bit, +i
XLen        = 32
sXLen       = s32
```

## 2. XLen substitutions

```text
G_PTR_ADD/PTRMASK   (p0:64, s64) -> (p0:32, s32)
G_PTRTOINT          (s64, p0:64) -> (s32, p0:32); clamp max s64 -> s32
G_INTTOPTR          (p0:64, s64) -> (p0:32, s32); clamp max s64 -> s32
G_BRJT              custom (p0:64, s64) -> (p0:32, s32)
G_CONSTANT          legal {p0:64} -> {p0:32, s32}; s64 Custom disappears
G_PHI               legal {p0:64,s32,s64} -> {p0:32,s32}; clamp s32
G_ICMP              (s64,s64),(s64,p0:64) -> (s32,s32),(s32,p0:32)
G_SELECT            (s32,s64),(p0:64,s64),(s64,s64) -> (s32,s32),(p0:32,s32)
```

Smoke examples of the 317 differing answers:

| Query | RV64 | RV32 |
|---|---|---|
| `G_PTR_ADD [p0, s32]` | Unsupported | **Legal** |
| `G_PTR_ADD [p0, s64]` | Legal | **Unsupported** |
| `G_PTRTOINT [s32, p0]` | WidenScalar s64 | **Legal** |
| `G_INTTOPTR [p0, s32]` | WidenScalar s64 | **Legal** |
| `G_CONSTANT [s32]` | WidenScalar s64 | **Legal** |
| `G_CONSTANT [s64]` | Custom | **NarrowScalar s32** |
| `G_ICMP [s32, p0]` | Unsupported | **Legal** |
| `G_PHI [s64]` | Legal | **NarrowScalar s32** |

The differing queries concentrate in `G_PTRTOINT` (90), `G_INTTOPTR` (90),
`G_ICMP` (69) and `G_SELECT` (48); the single-shape rulesets differ on one
query each, which is the query that names the wrong XLen width.

## 3. Traps

### 3.1 `G_CONSTANT` has two legal forms on RV32

```cpp
.legalFor({p0})
.legalFor(!ST.is64Bit(), {s32})
.customFor(ST.is64Bit(), {s64})
```

On RV32 both `p0:32` and `s32` are Legal, and there is **no Custom rule at
all**.  On RV64 the only legal form is `p0`, and `s64` is Custom.  A pointer
file copied from RV64 would answer Custom for `s64` and miss the s32 Legal
case.

### 3.2 `G_ICMP` swaps the pointer slot

RV64's legal pairs are `(s64,s64)` and `(s64,p0:64)` -- the pointer is in the
*second* slot.  RV32's are `(s32,s32)` and `(s32,p0:32)`.  The general form is
`(sXLen, sXLen)` and `(sXLen, p0)`, so both the width and the position are
configuration-dependent.

### 3.3 `G_SELECT` loses the s64 form without D

```cpp
.legalFor(XLen == 64 || ST.hasStdExtD(), {{s64, sXLen}})
```

On RV32 without D that clause contributes nothing, so the legal set is only
`(s32,s32)` and `(p0,s32)`.  Enabling D (a future experiment) would add the
`s64` form back.

### 3.4 `G_BRJT` stays Custom

RV64: `customFor({{p0,sXLen}})`.  RV32: `customFor({{p0,s32}})`.  This is the
only Custom rule in the pointer subset at either XLen; there is no fallback,
so any other shape is Unsupported.

## 4. Feature orthogonality

Running the same pointer file against `legalizer-oracle-riscv32-mzb.exe`:

```text
queries=770 divergences=0 rules=31/31
input sha256=5cf8d0902ae44893...
```

M/Zmmul/Zbb do not change any pointer rule.  The same held at RV64
(`riscv64-pointer-mzb-conformance-report.json`).  The pointer family is
XLen-dependent and feature-independent.

## 5. Reproduction

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\tools\oracle\build.ps1 `
  -BuildDir F:\llvm-study\toycpu\out\oracle-riscv `
  -LLVMDir F:\llvm\build\lib\cmake\llvm `
  -WithRiscv

cd F:\llvm-study\toycpu\tools
python -X utf8 -m legalizer_dsl `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\riscv32-pointer.lspec `
  check --strict `
  --capabilities F:\llvm-study\toycpu\tools\legalizer_dsl\riscv32-pointer-capabilities.json

python -X utf8 F:\llvm-study\toycpu\tools\legalizer_dsl\conformance.py `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\riscv32-pointer.lspec `
  --oracle F:\llvm-study\toycpu\out\oracle-riscv\legalizer-oracle-riscv32.exe `
  --domain D --closure R `
  --out F:\llvm-study\toycpu\out\riscv32-pointer-conformance-report.json `
  --llvm-build F:/llvm/build
```

As a negative control, the RV64 pointer file run against this RV32 oracle
produces 317 divergences out of 770.  The harness therefore detects the
wrong pointer width rather than silently accepting it.

## 6. Not proven

- address spaces other than 0;
- memory descriptors (`G_LOAD`/`G_STORE`/...), alignment and atomics;
- vector pointer types and every V-gated rule;
- actual `G_PTR_ADD` custom rewrites;
- RV32 + D interactions with `G_SELECT`.