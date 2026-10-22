# RISC-V GlobalISel: pointer-subset conformance experiment

Date: 2026-10-20
Status: PASS (RV64 pointer subset, generic-rv64)

## 0. Result first

This is the pointer companion to `riscv64-gisel-experiment.md`.  It uses the
same RV64 / `generic-rv64` configuration and the same real oracle, but the
domain contains the legal 64-bit pointer shape `p0:64`.

| Item | Result |
|---|---|
| Modeled rulesets | 11 |
| Modeled opcodes | 14 |
| Modeled rules (after macro expansion) | 32 |
| Domain LLTs | 10 |
| D union R queries | 770 |
| Closure-only queries | 30 |
| Divergences | **0** |
| Oracle errors | 0 |
| Rule coverage | **32 / 32** |
| Static check | 0 errors / 33 warnings / 0 unknown |
| Oracle build | `F:\llvm\build` (LLVM 24.0.0git, assertions ON) |

The claim proven here:

> On the declared finite RV64 pointer domain, the Python LegiSpec model and the
> real `RISCVLegalizerInfo::getAction(LegalityQuery)` agree on every observed
> step.

## 1. Configuration and domain

```text
triple      = riscv64-unknown-linux-gnu
cpu         = generic-rv64
features    = +64bit, +i
XLen        = 64
sXLen       = s64
p0          = LLT::pointer(0, 64)  ->  p0:64
```

Domain:

```text
p0:64, s1, s8, s16, s32, s48, s64, s96, s128, s256
```

`p0:64` is the real RISC-V pointer shape.  Writing `p0` in this project's
notation means a **32-bit** pointer, which is not an RV64 pointer.

## 2. Files

Added:

```text
toycpu/tools/legispec/rules/riscv64-pointer.lspec
toycpu/tools/legispec/riscv64-pointer-capabilities.json
toycpu/out/riscv64-pointer-conformance-report.json
toycpu/out/riscv64-pointer-check.txt
toycpu/notes/riscv64-pointer-experiment.md
```

The RISC-V oracle and build switches were added by the scalar experiment and
are reused unchanged.

## 3. Rules

```text
G_PTR_ADD / G_PTRMASK: legal (p0:64, s64) only
G_PTRTOINT:            legal (s64, p0:64); result clamps to s64
G_INTTOPTR:            legal (p0:64, s64); input clamps to s64
G_BRINDIRECT:          legal p0:64
G_BRJT:                custom (p0:64, s64) only
G_FRAME_INDEX:         legal p0:64
G_GLOBAL_VALUE:        legal p0:64
G_JUMP_TABLE:          legal p0:64
G_CONSTANT_POOL:       legal p0:64
G_CONSTANT:            p0:64 Legal, s64 Custom, other scalars clamp to s64
G_PHI:                 p0:64 / s32 / s64 Legal; other scalars clamp to [32,64]
G_ICMP:                (s64,s64) and (s64,p0:64) Legal; t1 pow2/min8, clamps
G_SELECT:              (s32,s64), (p0:64,s64), (s64,s64) Legal; clamps
```

## 4. Findings

1. **`G_PTR_ADD` and `G_PTRMASK` have no clamp.**  The C++ is only
   `legalFor({{p0, sXLen}})`.  As a result `(p0:64, s32)` is
   **Unsupported**, not "widen the offset".  A table copied from AArch64
   (which does clamp the offset) would be wrong here.

2. **`G_BRJT` is Custom, not Legal.**  The rule is
   `customFor({{p0, sXLen}})` with no fallback, so `(p0:64, s32)` is
   Unsupported.  The custom handler is expected to materialize the jump
   table address and index.

3. **`G_INTTOPTR` clamps the integer input, not the pointer result.**
   `(p0:64, s32)` widens `t1` to `s64`; `(p0:64, s128)` narrows it to
   `s64`.  `G_PTRTOINT` is the mirror image: it clamps `t0`.

4. **`G_CONSTANT` mixes two worlds in one ruleset.**  `p0:64` is Legal,
   `s64` is Custom, and other scalars clamp to `s64`.  This is why the
   pointer experiment restates the whole ruleset instead of trying to bolt a
   `legal_for {p0:64};` onto the scalar file.

5. **`G_PHI` has a pointer-first legal list.**  `legalFor({p0, s32, sXLen})`
   means a `p0:64` phi is Legal without any widening; only non-listed scalar
   widths reach the clamp.

6. **Pointer conditions in `G_ICMP`/`G_SELECT` are legal pairs, not
   predicates.**  `(s64, p0:64)` and `(p0:64, s64)` are matched by
   `typePairInSet`, which runs before any widening.  A pointer in the other
   slot falls through to Unsupported.

## 5. Why some opcodes appear in both RISC-V files

`G_CONSTANT`, `G_PHI`, `G_ICMP` and `G_SELECT` have rulesets that mention
both scalar and pointer shapes.  The LegiSpec parser rejects duplicate opcodes
inside one file, but the two files are separate experiments and are never
loaded together.  The scalar file models only the scalar domain; the pointer
file re-states these four rulesets with `p0:64` in the domain so the pointer
branch is actually exercised.  The duplication is deliberate and is called
out in both file headers.

## 6. Reproduction

```powershell
cd F:\llvm-study\toycpu\tools
python -m legispec `
  --rules F:\llvm-study\toycpu\tools\legispec\rules\riscv64-pointer.lspec `
  check --strict `
  --capabilities F:\llvm-study\toycpu\tools\legispec\riscv64-pointer-capabilities.json

python -X utf8 F:\llvm-study\toycpu\tools\legispec\conformance.py `
  --rules F:\llvm-study\toycpu\tools\legispec\rules\riscv64-pointer.lspec `
  --oracle F:\llvm-study\toycpu\out\oracle-riscv\legispec-oracle-riscv.exe `
  --domain D --closure R `
  --out F:\llvm-study\toycpu\out\riscv64-pointer-conformance-report.json `
  --llvm-build F:/llvm/build
```

Evidence:

```text
queries=770 divergences=0 rules=32/32
closure_query_count=30 closure_outside_d=0
input sha256=020ac70e10682b2010b9b59fbae735465fe6c2cc66d044d3708c2e1d82b4794a
```

## 7. Not proven

- non-zero address spaces (`pN:64`);
- vector pointers and every V-gated pointer rule;
- `G_LOAD`/`G_STORE` with pointer operands and memory descriptors;
- the `legalizeCustom` bodies behind `G_BRJT` and `G_CONSTANT`;
- pointer arithmetic rewrites (the DSL stops at the policy step).

The same pointer file was later run against the `+m,+zmmul,+zbb` oracle
(`riscv64-pointer-mzb-conformance-report.json`): 770 queries, 0 divergences,
32/32 rules.  The feature axis is orthogonal.

As a negative control, the same file run against the RV32 oracle produces
**317 divergences out of 770** (`riscv64-pointer-on-rv32-negative-control.json`).
That is the expected failure: `p0:64` is the wrong domain for RV32, and it
confirms that the conformance harness actually detects an XLen mismatch
rather than accepting any pointer shape.  The correct RV32 model is in
`riscv32-pointer-experiment.md` (770 / 0 / 31/31).