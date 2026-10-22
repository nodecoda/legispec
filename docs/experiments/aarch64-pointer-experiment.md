# AArch64 GlobalISel: pointer-subset conformance experiment

Date: 2026-10-20
Status: PASS (pointer subset)

## 0. Result first

This is the follow-up to the AArch64 scalar experiment.  It models the
**scalar pointer rulesets** of `AArch64LegalizerInfo`, using the real 64-bit
AArch64 pointer shape `p0:64`.

| Item | Result |
|---|---|
| Modeled rulesets | 11 |
| Modeled opcodes | 11 |
| Modeled rules (after macro expansion) | 17 |
| Domain LLTs | 12 |
| D union R queries | 924 |
| Closure-only queries | 25 |
| Divergences | **0** |
| Oracle errors | 0 |
| Rule coverage | **17 / 17** |
| Static check | 0 errors / 5 warnings |
| Oracle build | `F:\llvm\build` (LLVM 24.0.0git, assertions ON) |

The claim is narrow:

> On the declared finite pointer-shaped domain, the Python LDSL model and the
> real `AArch64LegalizerInfo::getAction(LegalityQuery)` agree on every
> observed step.

It does not model vector pointers (`v2p0`), memory descriptors, atomics,
address spaces, or the custom/selection rewrite bodies.

## 1. Why a separate pointer experiment

The scalar experiment deliberately excluded pointer LLTs.  The reason was
concrete: `G_ADD` and `G_MUL` call `LLT::getNumElements()` in predicates
without first checking `isVector()`, and a 32-bit `p0` also reached
`LLT::changeElementSize()`, which is invalid for pointers.

This experiment shows the other half of that story:

> AArch64's legal pointer is `LLT::pointer(0, 64)`, written `p0:64` in LDSL.
> The 32-bit `p0` used in the failing probe was the wrong LLT shape, not a
> legal AArch64 pointer.

Direct oracle probes:

```text
G_BRINDIRECT [p0]     -> Unsupported
G_BRINDIRECT [p0:64]  -> Legal
G_PTR_ADD    [p0, s64]    -> Unsupported
G_PTR_ADD    [p0:64, s64] -> Legal
```

Therefore this file uses `p0:64` everywhere.  The scalar file remains
scalar-only; the two domains are intentionally separate.

## 2. Files

Added:

```text
toycpu/tools/legalizer_dsl/rules/aarch64-pointer.lspec
toycpu/tools/legalizer_dsl/aarch64-pointer-capabilities.json
toycpu/out/aarch64-pointer-conformance-report.json
toycpu/out/aarch64-pointer-check.txt
toycpu/notes/aarch64-pointer-experiment.md
```

No oracle change was needed.  The AArch64 oracle built for the scalar
experiment already exposes the pointer rules through the same
`getAction(LegalityQuery)` entry point.

## 3. Modeled rules

All line references point at:

```text
F:\llvm\llvm-project\llvm\lib\Target\AArch64\GISel\AArch64LegalizerInfo.cpp
```

### 3.1 `G_PTR_ADD`

```cpp
// 267-270
getActionDefinitionsBuilder(G_PTR_ADD)
    .legalFor({{p0, i64}, {v2p0, v2i64}})
    .clampScalarOrElt(1, s64, s64)
    .clampNumElements(0, v2p0, v2p0);
```

Scalar pointer model:

```text
legal_for_pairs {(p0:64, s64)};
clamp_scalar 1, s64, s64;
```

The offset `t1` is clamped to `s64`; the pointer result `t0` is not converted.
Probes:

```text
G_PTR_ADD [p0:64, s64]  -> Legal
G_PTR_ADD [p0:64, s16]  -> WidenScalar t1 -> s64
G_PTR_ADD [p0:64, s256] -> NarrowScalar t1 -> s64
G_PTR_ADD [s64, s64]    -> Unsupported
```

### 3.2 `G_PTRMASK`

```cpp
// 272
getActionDefinitionsBuilder(G_PTRMASK).legalFor({{p0, s64}});
```

There is no fallback rule.  Any other shape is Unsupported:

```text
G_PTRMASK [p0:64, s64] -> Legal
G_PTRMASK [p0:64, s32] -> Unsupported
```

### 3.3 `G_BRINDIRECT`, `G_FRAME_INDEX`

```cpp
// 1079
getActionDefinitionsBuilder(G_BRINDIRECT).legalFor({p0});

// 1091
getActionDefinitionsBuilder(G_FRAME_INDEX).legalFor({p0});
```

Both are legal only for `p0:64`; no fallback.  `G_FRAME_INDEX [s64]` and
`G_BRINDIRECT [s64]` are Unsupported.

### 3.4 `G_GLOBAL_VALUE`

```cpp
// 1093-1096
if (TM.getCodeModel() == CodeModel::Small)
  getActionDefinitionsBuilder(G_GLOBAL_VALUE).custom();
else
  getActionDefinitionsBuilder(G_GLOBAL_VALUE).legalFor({p0});
```

The oracle configures `CodeModel::Small`, so the rule is unconditional
`.custom()`:

```text
ruleset G_GLOBAL_VALUE {
    custom;
}
```

This means **every** query is Custom, not just pointer queries:

```text
G_GLOBAL_VALUE [p0:64] -> Custom
G_GLOBAL_VALUE [s64]   -> Custom
G_GLOBAL_VALUE [s128]  -> Custom
```

In valid IR, `G_GLOBAL_VALUE` has a pointer result, so the missing type
predicate is harmless in practice.  The DSL checker still flags it:

```text
WARN: C001: G_GLOBAL_VALUE: type index t0 is never mentioned by a rule predicate
```

That warning is accurate and worth keeping: it records a real assumption of
the target rule set.

### 3.5 `G_PTRAUTH_GLOBAL_VALUE`

```cpp
// 1098-1099
getActionDefinitionsBuilder(G_PTRAUTH_GLOBAL_VALUE)
    .legalIf(all(typeIs(0, p0), typeIs(1, p0)));
```

Model:

```text
legal when t0 == p0:64 and t1 == p0:64;
```

No fallback: a mixed query is Unsupported.

### 3.6 `G_PTRTOINT`

```cpp
// 1101-1105
getActionDefinitionsBuilder(G_PTRTOINT)
    .legalFor({{i64, p0}, {v2i64, v2p0}})
    .widenScalarToNextPow2(0, 64)
    .clampScalar(0, s64, s64)
    .clampMaxNumElements(0, s64, 2);
```

Scalar model:

```text
legal_for_pairs {(s64, p0:64)};
widen_to_next_pow2 0, 64;
clamp_scalar 0, s64, s64;
```

The integer result is forced to `s64`; the pointer operand is untouched.
Probes:

```text
G_PTRTOINT [s64, p0:64]  -> Legal
G_PTRTOINT [s32, p0:64]  -> WidenScalar t0 -> s64
G_PTRTOINT [s80, p0:64]  -> WidenScalar t0 -> s128
G_PTRTOINT [s128, p0:64] -> NarrowScalar t0 -> s64
G_PTRTOINT [p0:64, s64]  -> Unsupported
```

`G_PTRTOINT [s80, p0:64]` is a two-step chain: widen to `s128`, then narrow to
`s64`.  The closure captures both steps.

### 3.7 `G_INTTOPTR`

```cpp
// 1107-1112
getActionDefinitionsBuilder(G_INTTOPTR)
    .unsupportedIf([](const LegalityQuery &Query) {
      return Query.Types[0].getSizeInBits() != Query.Types[1].getSizeInBits();
    })
    .legalFor({{p0, i64}, {v2p0, v2i64}})
    .clampMaxNumElements(1, s64, 2);
```

Model:

```text
unsupported when not same_size(t0, t1);
legal_for_pairs {(p0:64, s64)};
```

The size guard comes **before** legalization.  `G_INTTOPTR` therefore refuses
to widen or narrow the integer operand:

```text
G_INTTOPTR [p0:64, s64]  -> Legal
G_INTTOPTR [p0:64, s32]  -> Unsupported
G_INTTOPTR [p0:64, s128] -> Unsupported
```

This is the key asymmetry with `G_PTRTOINT`: `ptrtoint` clamps its integer
result to `s64`, while `inttoptr` rejects a size mismatch outright.

### 3.8 `G_BLOCK_ADDR`, `G_JUMP_TABLE`, `G_BRJT`

```cpp
// 1176
getActionDefinitionsBuilder(G_BLOCK_ADDR).legalFor({p0});

// 1374
getActionDefinitionsBuilder(G_JUMP_TABLE).legalFor({p0});

// 1376
getActionDefinitionsBuilder(G_BRJT).legalFor({{p0, s64}});
```

All three have no fallback.  Non-legal shapes are Unsupported:

```text
G_BLOCK_ADDR [p0:64]    -> Legal
G_BLOCK_ADDR [s64]      -> Unsupported
G_JUMP_TABLE [p0:64]    -> Legal
G_JUMP_TABLE [s64]      -> Unsupported
G_BRJT [p0:64, s64]     -> Legal
G_BRJT [p0:64, s16]     -> Unsupported
```

## 4. Domain

```text
types {
    p0:64, s1, s8, s16, s24, s32, s48, s64, s80, s96, s128, s256
};
imms {};
mems {};
```

The integer types exist to exercise the offset/result clamps.  The
non-power-of-two sizes (`s24`, `s48`, `s80`, `s96`) exercise the
`widen_to_next_pow2` closure; `s256` exercises narrowing.

There is deliberately **no 32-bit `p0`** in the domain.  It is not a legal
AArch64 pointer; it is a useful negative probe, not part of the modeled
target shape.

D is the Cartesian product of the domain types for each ruleset arity:

```text
5 one-operand rulesets  x 12 types        =  60
6 two-operand rulesets  x 12 x 12 types   = 864
total D                                      = 924
```

The domain's cross-product includes nonsensical shapes such as
`G_PTR_ADD [s64, s64]` and `G_PTRTOINT [p0:64, s64]`.  The real oracle
returns Unsupported for them; it does not assert.  This is a useful contrast
with the scalar `G_ADD`/`G_MUL` predicates.

## 5. Oracle

The oracle is the same AArch64 oracle built for the scalar experiment:

```text
F:\llvm-study\toycpu\out\oracle-aarch64\legalizer-oracle-aarch64.exe
```

It configures:

```text
triple      = aarch64-unknown-linux-gnu
cpu         = generic
code model  = small
NEON/FPARMv8 enabled
```

and calls only:

```cpp
LegalizerInfo::getAction(LegalityQuery)
```

No oracle changes were needed for the pointer subset.

## 6. What the experiment found

### 6.1 Pointer width is part of the LLT identity

`p0` and `p0:64` are different LLTs.  AArch64's legal pointer is `p0:64`.
The scalar experiment's 32-bit `p0` probe was not "a pointer" in the target's
sense; it was a mismatched pointer shape.  The pointer experiment makes the
positive case explicit.

### 6.2 Unconditional `.custom()` is visible at the query boundary

`G_GLOBAL_VALUE` in the small code model is `.custom()` with no predicate.
Every query is Custom, including non-pointer result types.  The checker's
C001 warning is the right response: the rule is intentionally total, but it
silently assumes the IR only creates pointer-shaped `G_GLOBAL_VALUE`.

### 6.3 `inttoptr` guards size; `ptrtoint` clamps size

`G_INTTOPTR` rejects a size mismatch before legalFor.  `G_PTRTOINT` clamps its
integer result to `s64`.  The two directions are not symmetric:

```text
inttoptr p0:64, s32  -> Unsupported
ptrtoint s32, p0:64  -> WidenScalar t0 -> s64
```

A model that treated both as "convert to pointer width" would be wrong.

### 6.4 legalFor-only rules mean Unsupported, not clamp

`G_PTRMASK`, `G_BRINDIRECT`, `G_FRAME_INDEX`, `G_BLOCK_ADDR`,
`G_JUMP_TABLE`, and `G_BRJT` have no fallback.  A non-legal shape is
Unsupported.  They do not widen or narrow their operands.

### 6.5 `ptr_add` clamps the offset, not the pointer

`G_PTR_ADD` has `clampScalarOrElt(1, s64, s64)`.  The pointer result `t0` is
not converted; only `t1` is forced to `s64`.  `G_PTR_ADD [p0:64, s16]` widens
the offset, and `[p0:64, s256]` narrows it.

### 6.6 A non-monotonic chain: widen then narrow

`G_PTRTOINT [s80, p0:64]` first widens `s80` to the next power of two
(`s128`), then the `clampScalar(0, s64, s64)` rule narrows `s128` to `s64`.
The closure records both steps.  This is a good test that the DSL models one
`getAction` step at a time rather than trying to jump to a final type.

### 6.7 Pointer rules are total over the declared domain

The scalar `G_ADD`/`G_MUL` predicates asserted when fed a pointer.  The
pointer rulesets tested here are total over the 12-type domain: nonsensical
shapes return Unsupported.  The difference is not accidental; the pointer
rules use type/legalFor checks and size guards rather than `getNumElements()`
predicates on scalar slots.

## 7. Verification

Static check:

```text
rulesets=11 opcodes=11 rules=17
checks: errors=0 warnings=5 unknown=0
```

Warnings:

```text
C001: G_GLOBAL_VALUE: type index t0 is never mentioned by a rule predicate
K001/K002: G_PTRTOINT rules 1/2 and 1/3 are order-sensitive overlaps
```

The `G_PTRTOINT` overlaps are the expected `widen_to_next_pow2` vs
`clamp_scalar` ordering.  The first matching rule wins.

Differential run:

```text
queries=924
divergences=0
oracle_errors=0
rules=17/17
closure_query_count=25
closure_outside_d=0
sha256=991f7f5abc20f06a9890d8a558f33e1df7a9c6c5d849092b55f36cf2eaf60c42
ok=true
```

Report:

```text
F:\llvm-study\toycpu\out\aarch64-pointer-conformance-report.json
```

Selected probes:

| Query | Result |
|---|---|
| `G_PTR_ADD [p0:64, s64]` | `Legal` |
| `G_PTR_ADD [p0:64, s16]` | `WidenScalar t1 -> s64` |
| `G_PTR_ADD [p0:64, s256]` | `NarrowScalar t1 -> s64` |
| `G_PTR_ADD [s64, s64]` | `Unsupported` |
| `G_PTRMASK [p0:64, s64]` | `Legal` |
| `G_PTRMASK [p0:64, s32]` | `Unsupported` |
| `G_PTRTOINT [s64, p0:64]` | `Legal` |
| `G_PTRTOINT [s32, p0:64]` | `WidenScalar t0 -> s64` |
| `G_PTRTOINT [s80, p0:64]` | `WidenScalar t0 -> s128` |
| `G_PTRTOINT [s128, p0:64]` | `NarrowScalar t0 -> s64` |
| `G_PTRTOINT [p0:64, s64]` | `Unsupported` |
| `G_INTTOPTR [p0:64, s64]` | `Legal` |
| `G_INTTOPTR [p0:64, s32]` | `Unsupported` |
| `G_INTTOPTR [p0:64, s128]` | `Unsupported` |
| `G_BRINDIRECT [p0:64]` | `Legal` |
| `G_BRINDIRECT [p0]` | `Unsupported` |
| `G_FRAME_INDEX [p0:64]` | `Legal` |
| `G_GLOBAL_VALUE [p0:64]` | `Custom` |
| `G_GLOBAL_VALUE [s64]` | `Custom` |
| `G_PTRAUTH_GLOBAL_VALUE [p0:64, p0:64]` | `Legal` |
| `G_PTRAUTH_GLOBAL_VALUE [p0:64, s64]` | `Unsupported` |
| `G_BLOCK_ADDR [p0:64]` | `Legal` |
| `G_JUMP_TABLE [p0:64]` | `Legal` |
| `G_BRJT [p0:64, s64]` | `Legal` |
| `G_BRJT [p0:64, s16]` | `Unsupported` |

## 8. Reproduce

The AArch64 oracle is the one from the scalar experiment:

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
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\aarch64-pointer.lspec `
  check --strict `
  --capabilities F:\llvm-study\toycpu\tools\legalizer_dsl\aarch64-pointer-capabilities.json
```

Differential conformance:

```powershell
python -X utf8 F:\llvm-study\toycpu\tools\legalizer_dsl\conformance.py `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\aarch64-pointer.lspec `
  --oracle F:\llvm-study\toycpu\out\oracle-aarch64\legalizer-oracle-aarch64.exe `
  --domain D --closure R `
  --out F:\llvm-study\toycpu\out\aarch64-pointer-conformance-report.json `
  --llvm-build F:/llvm/build
```

Expected tail:

```text
queries=924 divergences=0 rules=17/17 sha256=991f7f5abc20f06a...
```

ToyCPU regression gate:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\scripts\check-legalizer-dsl.ps1
```

## 9. Limits and next steps

Proven:

```text
finite pointer-shaped domain D, closure R, 924 observations
Python LDSL policy == real AArch64 getAction for the modeled pointer rules
```

Not proven:

```text
vector pointers (v2p0) and clampNumElements
G_LOAD / G_STORE memory descriptors and atomics
address spaces other than 0
G_BITCAST and G_SELECT pointer forms
G_PTR_ADD selection/combiner rewrites (InstructionSelector, outside the legalizer)
G_GLOBAL_VALUE custom rewrite body
LegalizerHelper rewrites
IRTranslator / CallLowering / ABI behavior
```

Next experiments, in order:

1. **Vector pointers.** Add `v2p0` and `clampNumElements` to model
   `G_PTR_ADD`/`G_PTRTOINT`/`G_INTTOPTR` vector pairs.
2. **Memory descriptors.** Model `G_LOAD`/`G_STORE` with `mems`, including
   the `legalForTypesWithMemDesc` shape and alignment.
3. **Address spaces.** Add `p1:64`, `p2:64`, ... and test whether the rules
   are address-space agnostic.
4. **Rewrite bodies.** The current model stops at the `Custom` action;
   `legalizeSmallCMGlobalValue` and the InstructionSelector's
   `convertPtrAddToAdd` are separate algorithms.

## 10. One-sentence conclusion

> AArch64's scalar pointer rules are fully modeled on a 12-type domain:
> 11 rulesets, 17 rules, 924 queries, zero divergences -- and the experiment
> shows that `p0:64` is the target's pointer shape, that `G_GLOBAL_VALUE` is
> unconditionally Custom in the small code model, and that `inttoptr` treats
> size mismatch as Unsupported while `ptrtoint` clamps to `s64`.
