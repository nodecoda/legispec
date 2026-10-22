# legalizer_dsl

A small executable DSL for the policy layer of LLVM GlobalISel:

~~~text
LegalityQuery -> LegalizeRuleSet::apply -> LegalizeActionStep
~~~

It models **what to do next** (`Legal`, `NarrowScalar`, `WidenScalar`,
`Libcall`, `Custom`, ...), not the MIR rewriting helpers.

**Project status: closed.**  Start with `CLOSURE.md` for the frozen scope,
the proof boundary, the asset map, the final numbers and the extension
playbook.  This README is the long-form design history.

Grammar: `grammar.ebnf`.

Full grammar and design notes:

- `design.md`
- [`walkthrough.md`](walkthrough.md)
- `target-subset-coverage.md` (what the real-target subsets do and do not cover)
- `../claims/claim-ledger.md` (what has actually been proven, per configuration)
- `construct-coverage.md` (which LLVM builder methods the DSL represents)

## The claim, and how to audit it

Every experiment claims the same thing:

~~~text
for every q in the declared domain D and its closure R:
    DSL.getAction(q) == LegalizerInfo::getAction(q)
~~~

`R` is the closure of `Q0` taken inside `D`.  If a mutation produces a state
that fails the domain filter (the G_CONCAT_VECTORS bitcast does this), the
state is reported as a `closure_boundary`, not silently added to `R` and not
given invented semantics.

That claim is only worth what its audit is worth, so it lives in one place:

~~~powershell
python -X utf8 -m legalizer_dsl.claims verify
python -m legalizer_dsl.closure check     # CLOSURE.md pinned numbers vs artifacts
~~~

The ledger (`claims.json`, rendered as `notes/claim-ledger.md`) checks, for
each claim, that the report was produced from the current rules file and the
named oracle, that the configuration matches, that divergences and oracle
errors are zero, that the closure stayed inside D, and that the rule counts
match.  It pins two SHA-256 values: one for the rules file and one for the
oracle executable.  **Edit a rules file, or rebuild an oracle, and the old
green report stops counting** until the claim is re-run.  `conformance.py`
records both in every report (`inputs.rules_sha256` for the rules file and
`reference.oracle_sha256` / `reference.oracle_size` for the binary); both are
copied into `claims.json`, and the ledger's `Oracle provenance` table is the
list of binaries the proofs currently rest on.  A report without either hash
is rejected as unpinned rather than trusted.

All eleven one-command gates end with a read-only `claims.py verify`, so a
rebuilt binary or an edited rules file fails the gate itself rather than
silently invalidating an old proof.  Run all eleven (and a final ledger read)
with:

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\scripts\check-legalizer-suite.ps1

# rebuild every oracle first
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\scripts\check-legalizer-suite.ps1 -BuildOracles
~~~

`claims.py verify` is read-only and fails on either kind of drift
(`STALE: ...`).  `claims.py verify --write` is the explicit re-validation
step: it rewrites the ledger only when every report matches the current rules
and the current binary.  The oracle build uses `lld-link /Brepro`, so
rebuilding an unchanged oracle is byte-identical and does not invalidate
proofs.

The boundary is stated once, in the ledger and here: one step only
(`LegalizerInfo::getAction`), not the iterative Legalizer pass, not
`LegalizerHelper` MIR rewrites, not instruction selection or codegen.

The harness has negative controls, because a green run is only evidence if it
can also report red: `tests/test_harness_sensitivity.py` mutates an action and
a guard in the `G_CONSTANT` experiment and requires the oracle comparison to
report divergences both times.

Which source constructs the DSL can express at all is tracked separately:

~~~powershell
python -X utf8 -m legalizer_dsl.constructs check
~~~

`constructs.py` extracts every `LegalizeRuleSet` builder method from
`LegalizerInfo.h` and requires each one to be classified as `macro`, `core`,
`guard`, `partial` or `none`.  A new upstream method fails the check until
someone classifies it; the 15 constructs currently at `none` are the honest
edge of the model, not a to-do list that can be ignored.

## Quick start

From any directory:

~~~powershell
$dsl = 'F:\llvm-study\toycpu\tools\legalizer-dsl.py'
$rules = 'F:\llvm-study\toycpu\tools\legalizer_dsl\rules\toycpu.lspec'
$witness = 'F:\llvm-study\toycpu\tools\legalizer_dsl\witness\toycpu.witness'

python $dsl --rules $rules check --verbose
python $dsl --rules $rules check --strict
python $dsl --rules $rules coverage
python $dsl --rules $rules witness $witness
python $dsl --rules $rules explain G_ICMP
python $dsl --rules $rules trace G_ADD --type s64
python $dsl --rules $rules trace G_ICMP --type v4s1 --type v4s32
python $dsl --rules $rules trace G_LOAD --type s32 --type p0 --mem s8:8
~~~

## C++ oracle and differential conformance

The oracle links the real ToyCPU backend and observes only:

~~~cpp
LegalizerInfo::getAction(LegalityQuery) -> LegalizeActionStep
~~~

Build it with the toolchain helper (VS2022 + a Windows SDK that contains
`shell32.lib`):

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\tools\oracle\build.ps1
~~~

The executables are written to:

~~~text
F:\llvm-study\toycpu\out\oracle\legalizer-oracle.exe       # ToyCPU, hand-written rules
F:\llvm-study\toycpu\out\oracle\legalizer-oracle-gen.exe   # ToyCPU, LDSL-generated rules
F:\llvm-study\toycpu\out\oracle\legalizer-oracle-x86.exe   # X86-64, real target oracle
F:\llvm-study\toycpu\out\oracle\legalizer-oracle-x86-gen.exe  # X86-64, LDSL-generated rules
F:\llvm-study\toycpu\out\oracle-aarch64\legalizer-oracle-aarch64.exe       # AArch64 generic
F:\llvm-study\toycpu\out\oracle-aarch64\legalizer-oracle-aarch64-cssc.exe  # AArch64 +cssc
F:\llvm-study\toycpu\out\oracle-aarch64\legalizer-oracle-aarch64-gen.exe       # AArch64, LDSL-generated
F:\llvm-study\toycpu\out\oracle-aarch64\legalizer-oracle-aarch64-cssc-gen.exe  # AArch64 +cssc, LDSL-generated
F:\llvm-study\toycpu\out\oracle-aarch64\legalizer-oracle-aarch64-atomic-gen.exe # AArch64 atomics, LDSL-generated
F:\llvm-study\toycpu\out\oracle-aarch64\legalizer-oracle-aarch64-outline.exe # AArch64 +outline-atomics
F:\llvm-study\toycpu\out\oracle-aarch64\legalizer-oracle-aarch64-lse.exe     # AArch64 +lse
F:\llvm-study\toycpu\out\oracle-aarch64\legalizer-oracle-aarch64-atomic-outline-gen.exe # +outline-atomics, LDSL-generated
F:\llvm-study\toycpu\out\oracle-aarch64\legalizer-oracle-aarch64-vector-arith-gen.exe # G_ADD family, LDSL-generated
F:\llvm-study\toycpu\out\oracle-aarch64\legalizer-oracle-aarch64-vector-mul-gen.exe # G_MUL, LDSL-generated
F:\llvm-study\toycpu\out\oracle-aarch64\legalizer-oracle-aarch64-vector-shift-gen.exe # G_SHL family, LDSL-generated
F:\llvm-study\toycpu\out\oracle-aarch64\legalizer-oracle-aarch64-icmp-gen.exe # G_ICMP, LDSL-generated
F:\llvm-study\toycpu\out\oracle-aarch64\legalizer-oracle-aarch64-concat-gen.exe # G_CONCAT_VECTORS, LDSL-generated
F:\llvm-study\toycpu\out\oracle-riscv\legalizer-oracle-riscv.exe      # RISC-V RV64, real target oracle
F:\llvm-study\toycpu\out\oracle-riscv\legalizer-oracle-riscv-mzb.exe  # RISC-V RV64 +m,+zmmul,+zbb
F:\llvm-study\toycpu\out\oracle-riscv\legalizer-oracle-riscv-mzb-gen.exe  # RISC-V RV64 +m,+zmmul,+zbb, LDSL-generated
F:\llvm-study\toycpu\out\oracle-riscv\legalizer-oracle-riscv32.exe    # RISC-V RV32, real target oracle
F:\llvm-study\toycpu\out\oracle-riscv\legalizer-oracle-riscv32-gen.exe  # RISC-V RV32, LDSL-generated
F:\llvm-study\toycpu\out\oracle-riscv\legalizer-oracle-riscv32-mzb.exe  # RISC-V RV32 +m,+zmmul,+zbb
F:\llvm-study\toycpu\out\oracle-riscv\legalizer-oracle-riscv-gen.exe    # RISC-V RV64, LDSL-generated rules
~~~

Run the full D union R comparison against the hand-written oracle, then
against the generated-rules oracle:

~~~powershell
python F:\llvm-study\toycpu\tools\legalizer_dsl\conformance.py `
  --oracle F:\llvm-study\toycpu\out\oracle\legalizer-oracle.exe `
  --domain D --closure R `
  --out F:\llvm-study\toycpu\out\conformance-report.json

python F:\llvm-study\toycpu\tools\legalizer_dsl\conformance.py `
  --oracle F:\llvm-study\toycpu\out\oracle\legalizer-oracle-gen.exe `
  --domain D --closure R `
  --out F:\llvm-study\toycpu\out\conformance-gen-report.json
~~~

Current evidence (2026-10-19):

~~~text
queries=243714 divergences=0 rules=87/89
closure_outside_d=0
input sha256=8a8c9c9f26b6b4de954201bed878bb8b89b562549f070e1b4719cd7ca02b6cb2
~~~

Both the hand-written and generated-rules oracles report 0 divergences on the
same input SHA-256.  The generated oracle is built from
`out/codegen/toycpu_rules_gen.cpp`; see `test_codegen.py` for the manifest/hash
and rule-order contracts.

The two unmatched rules are the `bits < 1` lower-bound guards emitted by
`clampScalar`; they are unreachable because `s1` is the minimum LLT scalar and
are registered in the witness corpus as `unreachable`.

See `CONFORMANCE.md` for the frozen definition, exclusions, and divergence
policy.

## Second target: X86-64 (scalar subset)

`rules/x86-scalar.lspec` transcribes the scalar parts of
`X86LegalizerInfo.cpp`.  It is a migration test, not a full X86 model:
vector clauses, memory descriptors, custom legalization, FP and atomics are
out of scope.  The experiment is documented in
`experiments/x86-gisel-experiment.md`.

~~~powershell
python -m legalizer_dsl `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\x86-scalar.lspec `
  check --strict

python F:\llvm-study\toycpu\tools\legalizer_dsl\conformance.py `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\x86-scalar.lspec `
  --oracle F:\llvm-study\toycpu\out\oracle\legalizer-oracle-x86.exe `
  --domain D --closure R `
  --out F:\llvm-study\toycpu\out\x86-scalar-conformance-report.json
~~~

Current evidence (2026-10-19):

~~~text
queries=7591 divergences=0 rules=69/69
input sha256=c292fff2f28f6d122e0a249495986c5438b2067c8bdf19e6095f9d723ec6bf38
~~~

The generated-policy counterpart is `legalizer-oracle-x86-gen`, built by the
same `add_generated_oracle()` helper with namespace `X86Gen`:

~~~text
queries=7591 divergences=0 rules=69/69
~~~

The claim is `x86-generated`.  `check-legalizer-dsl.ps1` rebuilds both x86
binaries and refreshes both x86 reports as step 7, then audits the ledger as
step 8.

## Third target: AArch64 (scalar subset)

`rules/aarch64-scalar.lspec` transcribes the scalar parts of
`AArch64LegalizerInfo.cpp`.  It is a migration test, not a full AArch64
model: vector/scalable-vector clauses, memory descriptors, FP and SVE/FP16
feature variants are out of scope.  CSSC is a separate frozen configuration
(`rules/aarch64-cssc-scalar.lspec`); pointer rulesets and atomics are separate
subsets (`aarch64-pointer.lspec`, `aarch64-atomic.lspec`).  The scalar experiment
is documented in `experiments/aarch64-gisel-experiment.md`.

The oracle links against the full LLVM build at `F:\llvm\build` and is built
with the AArch64 option:

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\tools\oracle\build.ps1 `
  -BuildDir F:\llvm-study\toycpu\out\oracle-aarch64 `
  -LLVMDir F:\llvm\build\lib\cmake\llvm `
  -WithAArch64
~~~

~~~powershell
python -m legalizer_dsl `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\aarch64-scalar.lspec `
  check --strict

python F:\llvm-study\toycpu\tools\legalizer_dsl\conformance.py `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\aarch64-scalar.lspec `
  --oracle F:\llvm-study\toycpu\out\oracle-aarch64\legalizer-oracle-aarch64.exe `
  --domain D --closure R `
  --out F:\llvm-study\toycpu\out\aarch64-scalar-conformance-report.json `
  --llvm-build F:/llvm/build
~~~

Current evidence (2026-10-20):

~~~text
queries=8161 divergences=0 rules=91/91
closure_query_count=85 closure_outside_d=0
input sha256=76734cf0ca196ef2236d8c333e13fc10afacfc74d1b81b2a16cac0971854a951
~~~

The domain is scalar-only by necessity, not convenience.  AArch64's
`G_ADD`/`G_MUL` predicates call `LLT::getNumElements()` without first checking
`isVector()`, and feeding a pointer to `getAction` aborts the assertions build;
a 32-bit `p0` also reaches `LLT::changeElementSize()`, which is invalid for
pointers.

### AArch64 +CSSC

`rules/aarch64-cssc-scalar.lspec` is the `+cssc` point of the same scalar
experiment.  It differs from `aarch64-scalar.lspec` in exactly four rulesets:
`G_SMIN/G_SMAX/G_UMIN/G_UMAX`, `G_ABS`, `G_CTPOP` and `G_CTTZ`.  `HasCSSC` is a
construction-time bool in `AArch64LegalizerInfo`, so one frozen file per
configuration is exact.  The experiment is documented in
`experiments/aarch64-cssc-experiment.md`.

~~~powershell
python -X utf8 -m legalizer_dsl `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\aarch64-cssc-scalar.lspec `
  check --strict `
  --capabilities F:\llvm-study\toycpu\tools\legalizer_dsl\aarch64-cssc-scalar-capabilities.json

python -X utf8 F:\llvm-study\toycpu\tools\legalizer_dsl\conformance.py `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\aarch64-cssc-scalar.lspec `
  --oracle F:\llvm-study\toycpu\out\oracle-aarch64\legalizer-oracle-aarch64-cssc.exe `
  --domain D --closure R `
  --out F:\llvm-study\toycpu\out\aarch64-cssc-scalar-conformance-report.json `
  --llvm-build F:/llvm/build

python -X utf8 F:\llvm-study\toycpu\tools\legalizer_dsl\diff_oracles.py `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\aarch64-scalar.lspec `
  --oracle-left F:\llvm-study\toycpu\out\oracle-aarch64\legalizer-oracle-aarch64.exe `
  --oracle-right F:\llvm-study\toycpu\out\oracle-aarch64\legalizer-oracle-aarch64-cssc.exe `
  --label-left generic --label-right cssc `
  --out F:\llvm-study\toycpu\out\aarch64-cssc-flips.json
~~~

Current evidence (2026-10-20):

~~~text
generic: queries=8161 divergences=0 rules=91/91
+cssc:   queries=8161 divergences=0 rules=95/95
same input sha256=76734cf0ca196ef2...
feature diff: 62 / 8161 (0.76%), 7 opcodes
~~~

`diff_oracles.py` is the feature-axis companion to `conformance.py`: it runs
two oracle binaries over the same LDSL-derived `D union R` corpus and records
every differing observation.

### AArch64 generated-code cross-check

The AArch64 tables have generated-policy oracles as well.  The shared CMake
helper `add_generated_oracle()` runs `codegen.py` for a rules file, compiles
the result under its own namespace (`AArch64Gen`, `AArch64CsscGen`,
`AArch64AtomicGen`, `AArch64AtomicOutlineGen`), and links it into the same
JSONL adapter:

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\scripts\check-aarch64-generated.ps1

# include a full rebuild of the AArch64 oracles
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\scripts\check-aarch64-generated.ps1 -BuildOracles
~~~

~~~text
gen AArch64            queries=8161 divergences=0 rules=91/91
gen AArch64+CSSC       queries=8161 divergences=0 rules=95/95
gen AArch64+atom       queries=612  divergences=0 rules=11/11
gen AArch64+atom+outline queries=612 divergences=0 rules=10/10
~~~

The claims include `aarch64-generated`, `aarch64-cssc-generated`,
`aarch64-atomic-generated` and `aarch64-atomic-outline-generated`.  Together
with `toycpu-generated` and `riscv64-generated`, this checks the LDSL -> C++
path on eight independent rule tables, including feature-gated and atomic
configuration variants.

### AArch64 pointer subset

`rules/aarch64-pointer.lspec` models the scalar pointer rulesets
(`G_PTR_ADD`, `G_PTRMASK`, `G_PTRTOINT`, `G_INTTOPTR`, `G_BRINDIRECT`,
`G_FRAME_INDEX`, `G_GLOBAL_VALUE`, `G_PTRAUTH_GLOBAL_VALUE`,
`G_BLOCK_ADDR`, `G_JUMP_TABLE`, `G_BRJT`).  It uses the real 64-bit AArch64
pointer shape `p0:64`; 32-bit `p0` is not an AArch64 pointer.  The experiment
is documented in `experiments/aarch64-pointer-experiment.md`.

~~~powershell
python -m legalizer_dsl `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\aarch64-pointer.lspec `
  check --strict `
  --capabilities F:\llvm-study\toycpu\tools\legalizer_dsl\aarch64-pointer-capabilities.json

python F:\llvm-study\toycpu\tools\legalizer_dsl\conformance.py `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\aarch64-pointer.lspec `
  --oracle F:\llvm-study\toycpu\out\oracle-aarch64\legalizer-oracle-aarch64.exe `
  --domain D --closure R `
  --out F:\llvm-study\toycpu\out\aarch64-pointer-conformance-report.json `
  --llvm-build F:/llvm/build
~~~

Current evidence (2026-10-20):

~~~text
queries=924 divergences=0 rules=17/17
closure_query_count=25 closure_outside_d=0
input sha256=991f7f5abc20f06a9890d8a558f33e1df7a9c6c5d849092b55f36cf2eaf60c42
~~~

### AArch64 atomic policy family

`rules/aarch64-atomic.lspec` models generic AArch64 (outline atomics off) and
`rules/aarch64-atomic-outline.lspec` models `+outline-atomics`.  The source is
`AArch64LegalizerInfo.cpp:1148-1174`; the governing construction variable is:

```cpp
bool UseOutlineAtomics = ST.outlineAtomics() && !ST.hasLSE();
```

Observed action changes:

```text
generic:
  G_ATOMICRMW_ADD [s8,p0:64]    -> WidenScalar s32
  G_ATOMICRMW_ADD [s32,p0:64]   -> Legal
  G_ATOMIC_CMPXCHG [s128,p0:64] -> Custom

+outline-atomics:
  G_ATOMICRMW_ADD [s8,p0:64]    -> Libcall
  G_ATOMICRMW_ADD [s32,p0:64]   -> Libcall
  G_ATOMICRMW_ADD [s128,p0:64]  -> NarrowScalar s64
  G_ATOMIC_CMPXCHG [s128,p0:64] -> Libcall
```

The generic table uses `legalFor/customFor + clampScalar`; the outline table
uses `libcall + clampScalar`.  The `G_ATOMICRMW_*` libcall set excludes
`s128`, so `s128` falls through to `clampScalar` and narrows to `s64`.
`G_ATOMIC_CMPXCHG` includes `s128` in its libcall set.

`+lse` deliberately reuses `aarch64-atomic.lspec`: `UseOutlineAtomics` remains
false, so the policy table should equal generic.  A dedicated `+lse` oracle
and a generic-vs-lse differential prove exactly that:

```text
generic hand:       queries=612 divergences=0 rules=11/11
generic generated:  queries=612 divergences=0 rules=11/11
outline hand:       queries=612 divergences=0 rules=10/10
outline generated:  queries=612 divergences=0 rules=10/10
lse hand:           queries=612 divergences=0 rules=11/11
generic vs lse:     queries=612 differences=0
```

As a contrast, generic vs `+outline-atomics` differs on 29 queries (7
opcodes), which is the actual policy effect of the libcall branches.

The full experiment and known warnings are documented in
`experiments/aarch64-atomic-experiment.md`.  Run the family gate with:

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\scripts\check-aarch64-atomic.ps1

# rebuild the AArch64 oracles first
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\scripts\check-aarch64-atomic.ps1 -BuildOracles
~~~

The claims are:

```text
aarch64-atomic
aarch64-atomic-generated
aarch64-atomic-outline
aarch64-atomic-outline-generated
aarch64-atomic-lse
```

### AArch64 fixed-vector vertical slice

`rules/aarch64-vector.lspec` is the first vector slice.  It transcribes three
source regions from `AArch64LegalizerInfo.cpp`:

~~~text
lines 303-310  G_SMIN/G_SMAX/G_UMIN/G_UMAX
lines 334-337  G_ABDS/G_ABDU/G_UAVGFLOOR/G_UAVGCEIL/G_SAVGFLOOR/G_SAVGCEIL
line  431      G_CLMUL
~~~

The min/max family is the first experiment that exercises element-count
policy:

~~~text
legal_for {v8s8, v16s8, v4s16, v8s16, v2s32, v4s32};
clamp_num_elements 0, v8s8, v16s8;
clamp_num_elements 0, v4s16, v8s16;
clamp_num_elements 0, v2s32, v4s32;
lower;
~~~

`clamp_num_elements` expands to LLVM's two `actionIf` rules in source order:
the min half (`MoreElements`, lanes < min) first, then the max half
(`FewerElements`, lanes > max).  The DSL node behind the predicate is
`FixedVectorElemCountCmp`, which mirrors the C++ guard exactly:

~~~cpp
VecTy.isFixedVector()
  && VecTy.getElementType() == EltTy
  && VecTy.getNumElements() <op> Count
~~~

Keeping the element-type test and the `isFixedVector()` test inside one
predicate is deliberate: `LLT::getNumElements()` asserts on a scalar, and a
bit-width-only match would confuse, for example, `s8` with a hypothetical
`p0:8`.

Observed policy result:

~~~text
G_SMIN  [v4s8]  -> MoreElements v8s8
G_SMIN  [v32s8] -> FewerElements v16s8
G_SMIN  [v2s16] -> MoreElements v4s16
G_SMIN  [v8s32] -> FewerElements v4s32
G_SMIN  [v4s64] -> Lower
G_SMIN  [s32]   -> Lower
G_ABDS  [v4s32] -> Legal
G_ABDS  [v8s32] -> Lower
G_CLMUL [v8s8]  -> Legal
G_CLMUL [v4s8]  -> Unsupported
~~~

Conformance is 176 queries, 0 divergences, 10/11 rules for both the hand
transcription and the generated C++.  The one unmatched rule is the min half
of `clampNumElements(0, v2s32, v4s32)`, i.e. `lanes(t0) < 2` for `s32`.
LLVM cannot represent `v1` (`ElementCount::getFixed(1)` is scalar), so the
rule is structurally unreachable.  It is kept to preserve exact rule order
and indices, and declared in
`witness/aarch64-vector-unreachable.witness`.

The gate is:

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\scripts\check-aarch64-vector.ps1

# rebuild the AArch64 oracles first
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\scripts\check-aarch64-vector.ps1 -BuildOracles
~~~

The claims are:

```text
aarch64-vector
aarch64-vector-generated
```

The full experiment, including the first generated-code failure (a capture-less
lambda referencing a local `LLT`), is documented in
`experiments/aarch64-vector-experiment.md`.

### AArch64 G_ADD-family vector slice

`rules/aarch64-vector-arith.lspec` transcribes lines 181-206 of
`AArch64LegalizerInfo.cpp`: the shared ruleset for `G_ADD`, `G_SUB`, `G_AND`,
`G_OR` and `G_XOR`.  This is the first slice that exercises the three
element-wise vector policies together:

~~~text
minScalarOrEltIf(lanes <= 2,  0, s32);
minScalarOrEltIf(lanes <= 4,  0, s16);
minScalarOrEltIf(lanes <= 16, 0, s8);
scalarizeIf(scalarOrEltWiderThan(0, 64), 0);
moreElementsToNextPow2(0);
~~~

The DSL spellings are:

~~~text
min_scalar_or_elt_if 0, lanes <= 2, s32;
fewer t0 to element_of(t0) when vector(t0) and elem_bits(t0) > 64;
more_elements_to_next_pow2 0;
~~~

The first macro expands to `WidenScalar` with
`lanes(tN) <op> K && elem_bits(tN) < target`; the second is the core
`FewerElements` form; the third adds a `MoreElements` rule whose mutation
rounds the lane count up to the next power of two.  `widen_to_next_pow2` is
retained in the ruleset even though LLVM's `sizeNotPow2` is scalar-only, so
the rule indices stay aligned with the C++ builder.

Observed one-step results:

~~~text
G_ADD [s24]    -> WidenScalar     s32      (next-pow2 scalar)
G_ADD [s8]     -> WidenScalar     s32      (clampScalar min)
G_ADD [s128]   -> NarrowScalar    s64      (clampScalar max)
G_ADD [v2s8]   -> WidenScalar     v2s32    (lanes <= 2)
G_ADD [v4s8]   -> WidenScalar     v4s16    (lanes <= 4)
G_ADD [v8s4]   -> WidenScalar     v8s8     (lanes <= 16)
G_ADD [v2s128] -> FewerElements   s128     (scalarizeIf)
G_ADD [v6s8]   -> MoreElements    v8s8     (lane round-up)
G_ADD [v32s8]  -> FewerElements   v16s8    (clampMaxNumElements s8)
G_ADD [v16s16] -> FewerElements   v8s16    (clampMaxNumElements s16)
G_ADD [v8s32]  -> FewerElements   v4s32    (clampNumElements s32)
G_ADD [v4s64]  -> FewerElements   v2s64    (clampNumElements s64)
~~~

Conformance is 120 queries, 0 divergences, 13/15 rules for both the hand
transcription and the generated C++.  The two unmatched rules are the min
halves of `clampNumElements(0, v2s32, v4s32)` and
`clampNumElements(0, v2s64, v2s64)`.  Both require `lanes < 2`, which LLVM
cannot represent as a fixed vector (`ElementCount::getFixed(1)` is scalar).
They are declared in `witness/aarch64-vector-arith.witness`.

The gate is:

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\scripts\check-aarch64-vector-arith.ps1
~~~

The claims are:

```text
aarch64-vector-arith
aarch64-vector-arith-generated
```

The experiment is documented in
`experiments/aarch64-vector-arith-experiment.md`.

### AArch64 G_MUL vector slice

`rules/aarch64-vector-mul.lspec` transcribes lines 208-235 of
`AArch64LegalizerInfo.cpp`.  `G_MUL` is a separate
`getActionDefinitionsBuilder` call and, unlike the `G_ADD` family, has no
`HasSVE` legalFor branch.  The remaining chain is identical, so the slice
needed no new DSL primitive; keeping it as its own ruleset preserves the
builder boundary instead of aliasing into the arith family.

Observed one-step results are the same as the arith family with the opcode
changed, for example:

~~~text
G_MUL [s8]     -> WidenScalar     s32      (clampScalar min)
G_MUL [v2s8]   -> WidenScalar     v2s32    (lanes <= 2)
G_MUL [v8s4]   -> WidenScalar     v8s8     (lanes <= 16)
G_MUL [v2s128] -> FewerElements   s128     (scalarizeIf)
G_MUL [v6s8]   -> MoreElements    v8s8     (lane round-up)
~~~

Conformance is 24 queries (one opcode times the 24-type domain),
0 divergences, 13/15 rules for both the hand transcription and the generated
C++.  The two unmatched rules are the same structurally unreachable min halves
of the two `clampNumElements` pairs, declared in
`witness/aarch64-vector-mul.witness`.

The gate is:

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\scripts\check-aarch64-vector-mul.ps1
~~~

The claims are:

```text
aarch64-vector-mul
aarch64-vector-mul-generated
```

### AArch64 G_SHL / G_ASHR / G_LSHR two-slot slice

`rules/aarch64-vector-shift.lspec` transcribes lines 234-265 of
`AArch64LegalizerInfo.cpp`.  The three opcodes share one builder, so they share
one ruleset.  This is the first slice whose rules constrain the relationship
between two type slots, and the first that needs a domain-level shape filter.

The rule chain, in source order:

~~~text
 0  customIf(!vector(t0) && bits(t0)==32 && bits(t1)==32)
 1  legalFor 10 pairs
 2  widenScalarToNextPow2(1)
 3  widenScalarToNextPow2(0)
 4/5  clampScalar(1, s32, s64)
 6/7  clampScalar(0, s32, s64)
 8/9  clampNumElements(0, v8s8, v16s8)
10/11 clampNumElements(0, v4s16, v8s16)
12/13 clampNumElements(0, v2s32, v4s32)
14/15 clampNumElements(0, v2s64, v2s64)
16  moreElementsToNextPow2(0)
17  minScalarSameAs(1, 0)
18  scalarizeIf(scalarOrEltWiderThan(0,64), 0)
19  minScalarEltSameAsIf(isVector(0), 1, 0)
20  maxScalarEltSameAsIf(isVector(0), 1, 0)
~~~

Three DSL primitives were added: `min_scalar_same_as` (scalar width vs total
width -- an asymmetric comparison), `min/max_scalar_elt_same_as_if` (whole-type
copy, which can turn a scalar amount into a vector), and `domain filter
same_shape(t0, t1)`.

Representative one-step results:

~~~text
G_SHL [s32, s32]      -> Custom        (rule 0 shadows legalFor)
G_SHL [s64, s32]      -> WidenScalar   t1:s64
G_SHL [v2s8, v2s8]    -> NarrowScalar  t0:v2s32
G_SHL [v6s8, v6s8]    -> MoreElements  t0:v8s8
G_SHL [v4s64, v4s64]  -> NarrowScalar  t0:v2s64
G_SHL [v2s128,v2s128] -> FewerElements t0:s128
~~~

Conformance is 270 queries (90 shape-consistent type pairs times 3 opcodes),
0 divergences, 19/21 rules for both the hand transcription and the generated
C++.  Rules 12-min and 14-min require a `v1`, which LLVM represents as a
scalar; they are declared in `witness/aarch64-vector-shift.witness`.

The domain filter is part of the proof, not a testing convenience.  The
upstream rules do not defend against non-contract shapes, and an assertions
build aborts in `mutationIsSane` (`LegalizerInfo.cpp:204`) for both
`(v8s8, s32)` and `(v8s8, v3s16)`.  A real two-cycle also exists at
`(v2s128, s64) -> WidenScalar t1:s128 -> NarrowScalar t1:s64`; it is recorded,
not "fixed".  See `notes/aarch64-vector-shift-experiment.md`.

The gate is:

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\scripts\check-aarch64-vector-shift.ps1
~~~

The claims are:

```text
aarch64-vector-shift
aarch64-vector-shift-generated
```


### AArch64 G_ICMP result/operand slice

`rules/aarch64-icmp.lspec` transcribes lines 740-769 of
`AArch64LegalizerInfo.cpp`.  It is the first slice where `Types[0]` is not an
operand: slot 0 is the comparison result mask and slot 1 is the operand type
shared by LHS and RHS.

~~~text
 0  legalFor({(s32,s32), (s32,s64), (s32,p0:64)})
 1  widenScalarOrEltToNextPow2(1)
 2  minScalarOrElt(1, s8)
 3/4  clampScalar(1, s32, s64)
 5/6  clampScalar(0, s32, s32)
 7  scalarizeIf(scalarOrEltWiderThan(1,64), 1)
 8  minScalarEltSameAsIf(pred, 0, 1)
 9  minScalarOrEltIf(t1 == v2s16, 1, s32)
10  minScalarOrEltIf(isPointerVector(t1), 0, s64)
11  moreElementsToNextPow2(1)
12/13 clampNumElements(1, v8s8, v16s8)
14/15 clampNumElements(1, v4s16, v8s16)
16/17 clampNumElements(1, v2s32, v4s32)
18/19 clampNumElements(1, v2s64, v2s64)
20/21 clampNumElements(1, v2p0, v2p0)
22  customIf(isVector(0))
~~~

Three new primitives: `min_scalar_or_elt`, `widen_scalar_or_elt_to_next_pow2`,
and the general `min_scalar_or_elt_if <predicate>, N, ty` form (the older
`lanes` form is preserved).  Three new predicates: `is_pointer_vector`,
`elem_types_eq` and `EltSizeNotPow2`.  `elem_types_eq` matters because rule 8
compares element *types*, not widths: `v2p0:64` and `v2s64` have equal element
width but are different types.

Conformance is 173 queries, 0 divergences, 19/23 rules for both the hand
transcription and the generated C++.  Three rules are unreachable (`v1` does
not exist).  One rule is **shadowed**: the `v4s16` min clamp has satisfiable
predicates (`v3s16`) but never wins, because `v2s16` is captured by rule 9 and
`v3s16` is rounded to `v4s16` by rule 11.  This is a distinct witness category:

~~~text
unreachable = N   # predicate unsatisfiable in D
shadowed    = N   # predicate satisfiable, but every match is preceded
~~~

The checker reports R003/R004 when a `shadowed` declaration is wrong and R005
when it is correct.  See `notes/aarch64-icmp-experiment.md`.

The gate is:

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\scripts\check-aarch64-icmp.ps1
~~~

The claims are:

```text
aarch64-icmp
aarch64-icmp-generated
```

### AArch64 G_FCMP float slice

`rules/aarch64-fcmp.lspec` transcribes lines 771-799 of
`AArch64LegalizerInfo.cpp`.  It is the first slice with a float operand:
slot 0 is the integer comparison mask, slot 1 is the compared float value.

~~~text
 0  legalFor({(s32,f32), (s32,f64), (v4s32,v4f32), (v2s32,v2f32), (v2s64,v2f64)})
 1  widenScalarOrEltToNextPow2(1)
 2/3  clampScalar(0, s32, s32)
 4  widenScalarIf(f16 || bf16, changeElementTo(1, f32))
 5  scalarizeIf(scalarOrEltWiderThan(1,64), 1)
 6  minScalarEltSameAsIf(result <- operand element, 0, 1)
 7/8  clampNumElements(1, v4s16, v8s16)
 9/10 clampNumElements(1, v2s32, v4s32)
11  clampMaxNumElements(1, s64, 2)
12  moreElementsToNextPow2(1)
13  libcallFor({(s32, s128)})
~~~

The configuration is frozen to `HasFP16 = false`, so the second `legalFor`
macro emits no rule and is omitted (like the `UseOutlineAtomics` branch in
the atomic slice).  The slice adds float LLTs to the model and pins three
upstream facts:

- `LLT::scalar(N)` is `ANY_SCALAR`, so it equals any same-size scalar:
  an `s32`-keyed lane clamp fires on `v6f32` and yields `v4f32`, and the
  `s128` libcall pair also matches `f128`.  `LLT.eq_llvm()` mirrors this.
- `LLT::integer` and `LLT::scalar` are different kinds.  The `legalFor`
  result is `i32/i64`, so a float result slot is `Unsupported`; the domain
  states this with `not is_float(t0)`.
- the generated oracle must call `LLT::setUseExtended(true)` itself,
  because it has no `AArch64TargetMachine`; without it `isFloat16()`
  degrades to a 16-bit-scalar test and produces 42 divergences.

Conformance is 185 queries, 0 divergences, 13/14 rules for both the hand
transcription and the generated C++.  Rule 9 (the `v2s32` min clamp) is
unreachable because `v1` is not a representable LLT.  Nothing is shadowed:
unlike G_ICMP there is no `v2s16` special case before the `v4s16` min clamp,
and `v6s16` survives to `moreElementsToNextPow2`.

`libcallFor({s32, s128})` is proven only as a policy choice.  What call is
emitted, and with which ABI, belongs to `LegalizerHelper`, not to this
DSL.  See `experiments/aarch64-fcmp-experiment.md`.

The gate is:

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\scripts\check-aarch64-fcmp.ps1
~~~

The claims are:

```text
aarch64-fcmp
aarch64-fcmp-generated
```

### AArch64 G_CONCAT_VECTORS custom/bitcast slice

`rules/aarch64-concat-vectors.lspec` transcribes lines 1333-1357 of
`AArch64LegalizerInfo.cpp`:

~~~text
0  legalFor({(v16s8, v8s8), (v8s16, v4s16), (v4s32, v2s32)})
1  customIf(t0 fixed vector && t0 element < 8)
2  bitcastIf(t0/t1 fixed vectors && t0 element >= 8 && pow2 &&
            bits(t0) <= 128 && bits(t1) <= 64,
            concat_bitcast(t1))
~~~

The domain filter is `concat_shape_any(t0, t1)`: both slots are fixed
vectors, `lanes(t0) % lanes(t1) == 0` with `lanes(t0) / lanes(t1) >= 2`, and
the element types are LLT-equal.  This follows the real opcode contract in
`MachineVerifier.cpp:1611-1631` (`lanes(dst) == k * lanes(src)`, `k >= 2`);
the two type slots recover k.  k=2 is the two-source special case, not the
contract itself.  `GenericOpcodes.td:1609-1614` declares only `type0:$dst`
and `type1:$src0`, and `variable_ops` adds no TypeIdx
(`LegalizerInfo.cpp:347-376`), which is why the query projects two slots.

Three facts are pinned by this slice:

- rule 1 is a **custom escape hatch**.  The claim covers only the policy
  decision "hand G_CONCAT_VECTORS to `AArch64LegalizerInfo::legalizeCustom`";
  `legalizeConcatVectors`' MIR is outside the DSL.  The capability manifest
  names the provider symbol and the gate checks that the `case` exists.
- `concat_bitcast(t1)` is not `same_size_as(t1)`.  It uses the source's
  *total* size for the new element width, then divides the result lane count
  by the source lane count: `(v8s8, v4s8) -> v2s32`.
- the bitcast successor leaves the concat contract (`v2s32` with `v4s8`
  violates `lanes(t0) == k * lanes(t1)` for any `k >= 2`).  `ConcatBitcast.eval` raises
  `T017` when the lanes do not divide, `Domain.accepts()` rejects the state,
  and `interp.legalize` / `conformance.closure_queries` record it as a
  boundary instead of feeding it back into the rule set.

Conformance is 28 queries, 0 divergences, 3/3 rules for both the hand
transcription and the generated C++ (Custom 9 / Legal 3 / Bitcast 7 /
Unsupported 9).  Rule 0 and rule 2 overlap on the three
legal pairs; `legalFor` wins by order, so the static check reports the
expected K001/K002 warnings.

`measure_concat_shapes.py` audits the domain from the other side.  It feeds
`notes/probes/concat-shapes.ll` and `notes/probes/concat-nary.mir` through
`llc -debug-only=legalizer` and records every `Legalizing: ... G_CONCAT_VECTORS`
event together with the recovered k, the opcode contract, and both actions.
The current corpus observes 11 queries (k=2:9, k=4:1, k=8:1): all inside D,
0 contract violations, 0 action mismatches.  The audit also pins a pipeline
fact: `IRTranslator` emits `G_SHUFFLE_VECTOR`; the pre-legalizer combiner
creates `G_CONCAT_VECTORS`.

`fixed_vector(tN)` compiles to an inline `Q.Types[N].isFixedVector()` lambda:
`LegalityPredicates` has `isVector` but no `isFixedVector` helper.  The first
version emitted a call to the non-existent helper and the oracle build caught
it.  See `experiments/aarch64-concat-vectors-experiment.md`.

The gate is:

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\scripts\check-aarch64-concat-vectors.ps1
~~~

The claims are:

```text
aarch64-concat-vectors
aarch64-concat-vectors-generated
```

## Fourth target: RISC-V (RV64 scalar subset)

`rules/riscv64-scalar.lspec` transcribes the scalar parts of
`RISCVLegalizerInfo.cpp`, fixed to `riscv64-unknown-linux-gnu` with the
`generic-rv64` CPU.  That CPU has only `+64bit,+i`: no M, Zmmul, Zbb, Zbc,
Zbkc, P or V.  As a result `G_MUL` and division are libcalls, bit-manipulation
opcodes lower, and the CLMUL family is Unsupported.  Vector/scalable-vector
rules, pointer rulesets, memory descriptors, FP, atomics and calls are out of
scope.  The experiment is documented in
`experiments/riscv64-gisel-experiment.md`.

The oracle links against the full LLVM build (which must contain RISCV) and is
built with the RISC-V option:

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\tools\oracle\build.ps1 `
  -BuildDir F:\llvm-study\toycpu\out\oracle-riscv `
  -LLVMDir F:\llvm\build\lib\cmake\llvm `
  -WithRiscv
~~~

~~~powershell
python -m legalizer_dsl `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\riscv64-scalar.lspec `
  check --strict `
  --capabilities F:\llvm-study\toycpu\tools\legalizer_dsl\riscv64-scalar-capabilities.json

python F:\llvm-study\toycpu\tools\legalizer_dsl\conformance.py `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\riscv64-scalar.lspec `
  --oracle F:\llvm-study\toycpu\out\oracle-riscv\legalizer-oracle-riscv.exe `
  --domain D --closure R `
  --out F:\llvm-study\toycpu\out\riscv64-scalar-conformance-report.json `
  --llvm-build F:/llvm/build
~~~

Current evidence (2026-10-20):

~~~text
queries=7861 divergences=0 rules=103/103
closure_query_count=62 closure_outside_d=0
input sha256=e03e73ddedef463e3dccd92908d6a85191f4d254aa66fc2781ccab17895de839
~~~

The experiment added three DSL primitives that the C++ needs:
`same_size_as(tN)` (mutation), `scalar_bits(tA) <op> scalar_bits(tB)`
(predicate), and `widen_to_next_pow2_or_min i, min`.  It also replaced the
slot-projected termination analysis with an exact per-Query state graph,
because cross-slot rules otherwise produce false cycles.

#### Generated-code cross-check (RV64)

The same rules file is also translated to C++ and compared against the real
oracle.  This is the RISC-V counterpart of `legalizer-oracle-gen` and the
second target on which the LDSL -> C++ path is proven end to end:

~~~powershell
python F:\llvm-study\toycpu\tools\legalizer_dsl\conformance.py `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\riscv64-scalar.lspec `
  --oracle F:\llvm-study\toycpu\out\oracle-riscv\legalizer-oracle-riscv-gen.exe `
  --domain D --closure R `
  --out F:\llvm-study\toycpu\out\riscv64-gen-conformance-report.json
~~~

~~~text
queries=7861 divergences=0 rules=103/103
~~~

The claim is `riscv64-generated` in the ledger.  `codegen.py --namespace
RISCVGen` selects the namespace, and the oracle is compiled with
`GENERATED_LEGALIZER_NAMESPACE=RISCVGen` so `main.cpp` stays target-agnostic.
`check-riscv-conditional.ps1` runs this as step 8.


### RISC-V pointer subset

`rules/riscv64-pointer.lspec` models the RV64 pointer rulesets
(`G_PTR_ADD`, `G_PTRMASK`, `G_PTRTOINT`, `G_INTTOPTR`, `G_BRINDIRECT`,
`G_BRJT`, `G_FRAME_INDEX`, `G_GLOBAL_VALUE`, `G_JUMP_TABLE`,
`G_CONSTANT_POOL`, plus the pointer branches of `G_CONSTANT`, `G_PHI`,
`G_ICMP` and `G_SELECT`).  It uses the real RV64 pointer shape `p0:64`.
The experiment is documented in
`experiments/riscv64-pointer-experiment.md`.

~~~powershell
python -m legalizer_dsl `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\riscv64-pointer.lspec `
  check --strict `
  --capabilities F:\llvm-study\toycpu\tools\legalizer_dsl\riscv64-pointer-capabilities.json

python F:\llvm-study\toycpu\tools\legalizer_dsl\conformance.py `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\riscv64-pointer.lspec `
  --oracle F:\llvm-study\toycpu\out\oracle-riscv\legalizer-oracle-riscv.exe `
  --domain D --closure R `
  --out F:\llvm-study\toycpu\out\riscv64-pointer-conformance-report.json `
  --llvm-build F:/llvm/build
~~~

Current evidence (2026-10-20):

~~~text
queries=770 divergences=0 rules=32/32
closure_query_count=30 closure_outside_d=0
input sha256=020ac70e10682b2010b9b59fbae735465fe6c2cc66d044d3708c2e1d82b4794a
~~~


### RISC-V +M+Zmmul+Zbb feature variant

`rules/riscv64-mzb-scalar.lspec` is a **feature flip**, not a different
configuration shape: same triple, same `generic-rv64` CPU, same XLen, same
domain, same pointer rules.  Only the oracle's baked-in feature string
(`+m,+zmmul,+zbb`) differs.  The point is to show that the DSL models a
*configuration point* rather than "RISC-V" in the abstract, and to expose
which legality decisions depend on which extension.

The variant oracle is a separate executable so a report can never be
attributed to the wrong binary:

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\tools\oracle\build.ps1 `
  -BuildDir F:\llvm-study\toycpu\out\oracle-riscv `
  -LLVMDir F:\llvm\build\lib\cmake\llvm `
  -WithRiscv

python -m legalizer_dsl `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\riscv64-mzb-scalar.lspec `
  check --strict `
  --capabilities F:\llvm-study\toycpu\tools\legalizer_dsl\riscv64-mzb-scalar-capabilities.json

python F:\llvm-study\toycpu\tools\legalizer_dsl\conformance.py `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\riscv64-mzb-scalar.lspec `
  --oracle F:\llvm-study\toycpu\out\oracle-riscv\legalizer-oracle-riscv-mzb.exe `
  --domain D --closure R `
  --out F:\llvm-study\toycpu\out\riscv64-mzb-scalar-conformance-report.json `
  --llvm-build F:/llvm/build
~~~

Current evidence (2026-10-20):

~~~text
queries=7861 divergences=0 rules=117/118
closure_query_count=65 closure_outside_d=0
input sha256=e03e73ddedef463e3dccd92908d6a85191f4d254aa66fc2781ccab17895de839

pointer orthogonality:
queries=770 divergences=0 rules=32/32
input sha256=020ac70e10682b2010b9b59fbae735465fe6c2cc66d044d3708c2e1d82b4794a
~~~

The generated-policy counterpart is `legalizer-oracle-riscv-mzb-gen`
(namespace `RISCVGenMzb`).  It is the feature-variant answer to the question
"does generated code preserve the frozen configuration, or only the base
policy?":

~~~text
queries=7861 divergences=0 rules=117/118
~~~

The claim is `riscv64-mzb-generated`; `check-riscv-conditional.ps1` runs it as
step 9, before the ledger audit.

The one unmatched rule is the trailing `G_CTLZ` `lower`, which is
unreachable on the declared scalar domain (the static checker reports the
same fact as R002).  It stays in the transcription because the C++ really
contains it.

The DSL has no subtarget variable, so a construction-time boolean such as
`legalFor(!is64Bit(), {s32})` cannot be written as a rule predicate.  There
are two honest ways to transcribe it:

- one frozen file per configuration, where an inactive call contributes no
  rule (what the RV64/RV32 files above do), or
- one source with a **parse-time guard**, `legal_for {s32} when not is64bit;`
  (what `rules/riscv-scalar-conditional.lspec` does).

The distinction matters.  LLVM's `legalFor(bool Pred, ...)` is

~~~cpp
LegalizeRuleSet &legalFor(bool Pred, std::initializer_list<LLT> Types) {
  if (!Pred)
    return *this;
  return actionFor(LegalizeAction::Legal, Types);
}
~~~

so a false `Pred` emits **no rule**; it does not emit a rule with an
always-false predicate.  Rule indices, first-match ordering, coverage
accounting and dead-rule analysis all see the rule table, so a dead predicate
is not a substitute for erasure.  `unsupported;` is also not a substitute:
that is an explicit rejection, not a missing rule.

There are therefore two evaluation layers, and they must not be mixed:

| layer | spelling | when it runs | false result |
| --- | --- | --- | --- |
| configuration guard | `legal_for {s32} when not is64bit;` | while parsing | the macro emits no rule |
| query predicate | `custom when t0 == s32;` | per query | the rule does not match |

Configuration values are supplied with `--define NAME=VALUE`
(`true`/`false`, integers, or a type such as `s64`).  A type-valued entry is
usable wherever a type is expected, and as the right-hand side of a size
comparison: `bits(t0) < sXLen` folds to `bits(t0) < 64` at parse time, which
is how `minScalar(0, sXLen)` and `maxScalar(0, sXLen)` stay one line.  A
guard that mentions an unknown name is `P025`; a non-boolean value used as a
condition is `P026`.

Three flips are easy to get wrong and are called out in the rules file:

- `G_CTLZ_ZERO_POISON` / `G_CTTZ_ZERO_POISON` do **not** inherit the Zbb
  rules.  In C++ only `CountZerosActions` enters the Zbb branch; the poison
  builder gets nothing but the final `.lower()`.
- `G_ABS` and `G_ABDS/G_ABDU` use `minScalar`, not `clampScalar`: smaller
  scalars widen to `s64`, but wider scalars fall through to `Lower`.
- `G_SMULO/G_UMULO` lose the `s64 -> s128` widening under Zmmul, because the
  native multiply path needs no libcall overflow check.

The experiment is documented in
`experiments/riscv64-mzb-experiment.md`.


### RISC-V RV32 (XLen axis)

`rules/riscv32-scalar.lspec` is the **XLen flip** of the RV64 scalar file:
same C++ constructor, same domain, but `riscv32-unknown-linux-gnu` /
`generic-rv32`, so `XLen = 32`, `sXLen = s32` and `sDoubleXLen = s64`.
Feature-dependent branches stay off, so the two axes are orthogonal:

- `G_ADD [s32]`: RV64 Custom -> RV32 Legal
- `G_CONSTANT [s32]`: RV64 WidenScalar s64 -> RV32 Legal
- `G_SEXT_INREG`: RV64 has a legal `[s64] imm=32`; on RV32 the same rule is
  unsatisfiable (`sizeIs(0,64)` is hardcoded against `typeIs(0, s32)`).  The
  C++ still builds that rule; the frozen RV32 file omits it, and the
  single-source file keeps it and declares it unreachable in a witness.
- `G_BRCOND [s64]`: RV64 Legal -> RV32 Unsupported
- `G_READCYCLECOUNTER [s64]`: RV64 Legal -> RV32 Custom (re-read high half)

The query-set hash is identical to RV64 (`e03e73dd...`), while 3,220 of the
7,861 answers differ (41%).  Same domain, different policy: XLen is a
first-class configuration axis.

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\tools\oracle\build.ps1 `
  -BuildDir F:\llvm-study\toycpu\out\oracle-riscv `
  -LLVMDir F:\llvm\build\lib\cmake\llvm `
  -WithRiscv

python -m legalizer_dsl `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\riscv32-scalar.lspec `
  check --strict `
  --capabilities F:\llvm-study\toycpu\tools\legalizer_dsl\riscv32-scalar-capabilities.json

python F:\llvm-study\toycpu\tools\legalizer_dsl\conformance.py `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\riscv32-scalar.lspec `
  --oracle F:\llvm-study\toycpu\out\oracle-riscv\legalizer-oracle-riscv32.exe `
  --domain D --closure R `
  --out F:\llvm-study\toycpu\out\riscv32-scalar-conformance-report.json `
  --llvm-build F:/llvm/build
~~~

Current evidence (2026-10-20):

~~~text
queries=7861 divergences=0 rules=100/100
closure_query_count=63 closure_outside_d=0
input sha256=e03e73ddedef463e3dccd92908d6a85191f4d254aa66fc2781ccab17895de839
~~~

The generated-policy counterpart is `legalizer-oracle-riscv32-gen`
(namespace `RISCV32Gen`), built from the same frozen file:

~~~text
queries=7861 divergences=0 rules=100/100
~~~

The claim is `riscv32-generated`; `check-riscv-conditional.ps1` runs it as
step 10.  Together with steps 8 and 9, generated code is now checked on the
base RV64 policy, the feature axis and the XLen axis.

The experiment is documented in `experiments/riscv32-gisel-experiment.md`.


### RISC-V scalar, one source for both XLen values

`rules/riscv-scalar-conditional.lspec` is the same scalar subset written once
with `is64bit`, `sXLen` and `sDoubleXLen` as configuration values.  It is the
end-to-end test of the guard contract: the same bytes must reproduce both
frozen files' decision functions.

~~~powershell
python -X utf8 -m legalizer_dsl `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\riscv-scalar-conditional.lspec `
  --define is64bit=true --define sXLen=s64 --define sDoubleXLen=s128 `
  check --strict `
  --capabilities F:\llvm-study\toycpu\tools\legalizer_dsl\riscv64-scalar-capabilities.json

python -X utf8 -m legalizer_dsl `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\riscv-scalar-conditional.lspec `
  --define is64bit=false --define sXLen=s32 --define sDoubleXLen=s64 `
  check --strict `
  --capabilities F:\llvm-study\toycpu\tools\legalizer_dsl\riscv32-scalar-capabilities.json `
  --witness F:\llvm-study\toycpu\tools\legalizer_dsl\witness\riscv-scalar-conditional-rv32.witness
~~~

`--define` is a global option: it must come **before** the `check`
subcommand.  The RV32 run needs a witness because the retained
`G_SEXT_INREG` `legal` rule (faithful to the C++) is unreachable there; the
RV64 run must not use that witness, because under RV64 the rule is reachable.

Current evidence (2026-10-20):

~~~text
RV64: rulesets=38 opcodes=73 rules=108  errors=0 warnings=168 unknown=0
      queries=7861 divergences=0 rules=108/108
RV32: rulesets=38 opcodes=73 rules=101  errors=0 warnings=163 unknown=0
      queries=7861 divergences=0 rules=100/101
XLen axis: RV64 oracle vs RV32 oracle, same 7861 queries -> 3220 answers differ
~~~

The single unmatched RV32 rule is the unsatisfiable `G_SEXT_INREG` legal
rule.  Every one of the 7,861 decisions matches the RV32 oracle, which is the
point: the extra rule is real in the table and invisible in the answers.

`tests/test_riscv_single_source.py` pins the equivalence without an oracle
build: for each configuration it evaluates the conditional source and the
frozen file over every declared type in slots `t0`/`t1` (and the imm set),
and requires identical `(Action, TypeIdx, NewType)` for every query.

One command runs the whole experiment (unit tests, both strict checks, both
oracle runs, the cross-configuration diff and both codegen runs):

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  F:\llvm-study\toycpu\scripts\check-riscv-conditional.ps1
~~~

The design note is `config-guard.md`.


### RISC-V RV32 +M+Zmmul+Zbb (product point)


### RISC-V RV32 × +M+Zmmul+Zbb (product point)

`rules/riscv32-mzb-scalar.lspec` combines the two axes.  The product is not the
sum of the diffs: 967 queries differ for the feature axis at RV32, 3,291 for
the XLen axis at RV64+mzb, but only 3,968 for the combined endpoint, and 269
queries belong to both single-axis diffs.

The key product-only behavior is rule shadowing:

- `G_SDIV/G_UDIV/G_UREM`: `legalFor({sXLen})` precedes `customFor({s32})`.
  On RV32 `sXLen == s32`, so the custom is dead; the oracle answers Legal.
- `G_CTLZ/G_CTTZ`: the same shadowing for the `(s32,s32)` custom.
- `G_SEXT_INREG`: Zbb adds `imm in {8,16}` without a size gate, but the
  `imm=32` disjunct still requires size 64.  So `[s32] imm=8/16` is Legal
  while `[s32] imm=32` is still not.

The four unmatched rules in the conformance report are exactly these dead
rules (`G_SDIV` custom, `G_CTLZ` custom, `G_CTLZ` pow2-widen, `G_CTLZ` lower);
coverage is 111/115 and the static checker reports them as unreachable.

~~~powershell
python -m legalizer_dsl `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\riscv32-mzb-scalar.lspec `
  check --strict `
  --capabilities F:\llvm-study\toycpu\tools\legalizer_dsl\riscv32-mzb-scalar-capabilities.json

python F:\llvm-study\toycpu\tools\legalizer_dsl\conformance.py `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\riscv32-mzb-scalar.lspec `
  --oracle F:\llvm-study\toycpu\out\oracle-riscv\legalizer-oracle-riscv32-mzb.exe `
  --domain D --closure R `
  --out F:\llvm-study\toycpu\out\riscv32-mzb-scalar-conformance-report.json `
  --llvm-build F:/llvm/build
~~~

~~~text
queries=7861 divergences=0 rules=111/115
closure_query_count=60 closure_outside_d=0
input sha256=e03e73ddedef463e3dccd92908d6a85191f4d254aa66fc2781ccab17895de839
~~~

Documented in `experiments/riscv32-mzb-experiment.md`.


### RISC-V RV32 pointer subset

`rules/riscv32-pointer.lspec` is the XLen flip of the RV64 pointer model:
`p0:32`, `sXLen = s32`.  `G_CONSTANT` is Legal for both `p0` and `s32` (there
is no s64 Custom), `G_ICMP` uses `(s32,p0)`, and `G_SELECT` uses
`(p0,s32)`.  317 of the 770 answers differ from the RV64 pointer model.

~~~powershell
python -m legalizer_dsl `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\riscv32-pointer.lspec `
  check --strict `
  --capabilities F:\llvm-study\toycpu\tools\legalizer_dsl\riscv32-pointer-capabilities.json

python F:\llvm-study\toycpu\tools\legalizer_dsl\conformance.py `
  --rules F:\llvm-study\toycpu\tools\legalizer_dsl\rules\riscv32-pointer.lspec `
  --oracle F:\llvm-study\toycpu\out\oracle-riscv\legalizer-oracle-riscv32.exe `
  --domain D --closure R `
  --out F:\llvm-study\toycpu\out\riscv32-pointer-conformance-report.json `
  --llvm-build F:/llvm/build
~~~

~~~text
queries=770 divergences=0 rules=31/31
closure_query_count=32 closure_outside_d=0
input sha256=5cf8d0902ae44893...
~~~

The same file run against the RV32+mzb oracle also gives 770 / 0 / 31/31:
pointer rules are feature-independent.  Documented in
`experiments/riscv32-pointer-experiment.md`.

## Codegen note: unqualified rule predicates

The generated file enters `namespace llvm`, so `using namespace
llvm::LegalityPredicates;` and `using namespace llvm::LegalizeMutations;` are
emitted **inside** `applyGeneratedLegalizerRules`, not at global scope.  At
global scope, unqualified lookup inside `namespace llvm` finds `llvm::any`
(BitmaskEnum) first and stops, so a nested `or` emits
`any(any(a, b), c)` that does not compile.  `tests/test_codegen.py` locks the
function-scope ordering.

## Tests

~~~powershell
python -m unittest discover -s F:\llvm-study\toycpu\tools\legalizer_dsl\tests -v
~~~

## Layout

| File | Purpose |
|---|---|
| `model.py` | `LLT`, `Query`, `Domain`, predicates, mutations, `Rule`, `RuleSet`, `LegalizerInfo` |
| `parser.py` | lexer, recursive-descent parser, macro expansion, `domain`/`q0` |
| `cli.py` | `explain`, `trace`, `check`, `coverage`, `opcodes`, `witness` |
| `coverage.py` | AST read-set based type/imm slot coverage |
| `witness.py` | executable witness corpus parser and runner |
| `interp.py` | fixpoint interpreter, cycle/stuck/budget detection |
| `conformance.py` | JSONL query generation, C++ oracle runner, pointwise diff report |
| `checker/` | wellformedness, mutation, coverage, reachability, conflict, termination |
| `rules/toycpu.lspec` | ToyCPU rules transcribed from C++ |
| `rules/x86-scalar.lspec` | X86-64 scalar subset transcribed from C++ |
| `x86-scalar-capabilities.json` | X86 experiment capability manifest |
| `diff_oracles.py` | Compare two oracle configurations over one query corpus |
| `rules/aarch64-scalar.lspec` | AArch64 scalar subset transcribed from C++ |
| `aarch64-scalar-capabilities.json` | AArch64 scalar experiment capability manifest |
| `rules/aarch64-cssc-scalar.lspec` | AArch64 scalar subset with `+cssc` |
| `aarch64-cssc-scalar-capabilities.json` | AArch64 `+cssc` capability manifest |
| `rules/aarch64-pointer.lspec` | AArch64 scalar pointer subset transcribed from C++ |
| `aarch64-pointer-capabilities.json` | AArch64 pointer experiment capability manifest |
| `rules/aarch64-atomic.lspec` | AArch64 atomic subset, generic and +lse |
| `aarch64-atomic-capabilities.json` | AArch64 generic atomic capability manifest |
| `rules/aarch64-atomic-outline.lspec` | AArch64 atomic subset with +outline-atomics |
| `aarch64-atomic-outline-capabilities.json` | AArch64 +outline-atomics capability manifest |
| `rules/aarch64-vector.lspec` | AArch64 fixed-vector subset (clamp/legal/lower) |
| `aarch64-vector-capabilities.json` | AArch64 fixed-vector capability manifest |
| `witness/aarch64-vector-unreachable.witness` | Declared-unreachable v2s32 min clamp |
| `rules/aarch64-vector-arith.lspec` | AArch64 G_ADD-family vector-aware policy |
| `aarch64-vector-arith-capabilities.json` | AArch64 G_ADD-family capability manifest |
| `witness/aarch64-vector-arith.witness` | Full rule-index witnesses incl. two v1 clamps |
| `rules/aarch64-vector-mul.lspec` | AArch64 G_MUL vector-aware policy |
| `aarch64-vector-mul-capabilities.json` | AArch64 G_MUL capability manifest |
| `witness/aarch64-vector-mul.witness` | Full rule-index witnesses incl. two v1 clamps |
| `rules/aarch64-vector-shift.lspec` | AArch64 three-opcode two-slot shift policy |
| `aarch64-vector-shift-capabilities.json` | AArch64 shift capability manifest |
| `witness/aarch64-vector-shift.witness` | Full rule-index witnesses for 21 rules |
| `rules/aarch64-icmp.lspec` | AArch64 G_ICMP result/operand policy |
| `aarch64-icmp-capabilities.json` | AArch64 G_ICMP capability manifest |
| `witness/aarch64-icmp.witness` | Full rule-index witnesses for 23 rules, incl. one shadowed |
| `rules/aarch64-fcmp.lspec` | AArch64 G_FCMP float result/operand policy |
| `rules/aarch64-concat-vectors.lspec` | AArch64 G_CONCAT_VECTORS custom/bitcast policy |
| `aarch64-fcmp-capabilities.json` | AArch64 G_FCMP capability manifest |
| `aarch64-concat-vectors-capabilities.json` | AArch64 G_CONCAT_VECTORS capability manifest |
| `witness/aarch64-fcmp.witness` | Full rule-index witnesses for 14 rules, incl. ANY_SCALAR pins |
| `rules/riscv64-scalar.lspec` | RISC-V RV64 scalar subset transcribed from C++ |
| `out/codegen-riscv64-scalar/` | C++ generated from the RV64 scalar rules (`RISCVGen`) |
| `out/codegen-riscv64-mzb-scalar/` | C++ generated from the RV64 +mzb rules (`RISCVGenMzb`) |
| `out/codegen-riscv32-scalar/` | C++ generated from the RV32 scalar rules (`RISCV32Gen`) |
| `out/codegen-aarch64-scalar/` | C++ generated from the AArch64 scalar rules (`AArch64Gen`) |
| `out/codegen-aarch64-cssc-scalar/` | C++ generated from the AArch64 +CSSC rules (`AArch64CsscGen`) |
| `out/codegen-aarch64-atomic/` | C++ generated from the generic atomic rules (`AArch64AtomicGen`) |
| `out/codegen-aarch64-atomic-outline/` | C++ generated from the +outline atomic rules (`AArch64AtomicOutlineGen`) |
| `out/codegen-aarch64-vector/` | C++ generated from the fixed-vector rules (`AArch64VectorGen`) |
| `out/codegen-aarch64-vector-arith/` | C++ generated from the G_ADD-family rules (`AArch64VectorArithGen`) |
| `out/codegen-aarch64-vector-mul/` | C++ generated from the G_MUL rules (`AArch64VectorMulGen`) |
| `out/codegen-aarch64-vector-shift/` | C++ generated from the shift rules (`AArch64VectorShiftGen`) |
| `out/codegen-aarch64-icmp/` | C++ generated from the G_ICMP rules (`AArch64ICMPGen`) |
| `out/codegen-aarch64-fcmp/` | C++ generated from the G_FCMP rules (`AArch64FCMPGen`) |
| `out/codegen-aarch64-concat-vectors/` | C++ generated from the G_CONCAT_VECTORS rules (`AArch64ConcatVectorsGen`) |
| `out/codegen-x86-scalar/` | C++ generated from the X86-64 scalar rules (`X86Gen`) |
| `riscv64-scalar-capabilities.json` | RISC-V experiment capability manifest |
| `rules/riscv64-pointer.lspec` | RISC-V RV64 pointer subset transcribed from C++ |
| `riscv64-pointer-capabilities.json` | RISC-V pointer experiment capability manifest |
| `rules/riscv64-mzb-scalar.lspec` | RISC-V RV64 scalar subset with +m,+zmmul,+zbb |
| `riscv64-mzb-scalar-capabilities.json` | RISC-V feature-variant capability manifest |
| `rules/riscv32-scalar.lspec` | RISC-V RV32 scalar subset (XLen axis) |
| `riscv32-scalar-capabilities.json` | RISC-V RV32 capability manifest |
| `rules/riscv32-mzb-scalar.lspec` | RISC-V RV32 scalar with +m,+zmmul,+zbb (product point) |
| `riscv32-mzb-scalar-capabilities.json` | RISC-V RV32 product capability manifest |
| `rules/riscv32-pointer.lspec` | RISC-V RV32 pointer subset (XLen axis) |
| `riscv32-pointer-capabilities.json` | RISC-V RV32 pointer capability manifest |
| `tests/test_legalizer_dsl.py` | 51 parser/model/LLT/witness equivalence tests |
| `tests/test_diagnostics.py` | 6 diagnostic contract tests |
| `tests/test_domain.py` | 10 domain / coverage / strict-mode tests |
| `tests/test_witness.py` | 5 witness corpus tests |
| `tests/test_checker.py` | 11 checker fixture tests |
| `tests/test_interp.py` | 5 fixpoint / domain-boundary tests |
| `tests/test_conformance.py` | 5 oracle protocol and query-set tests |
| `tests/test_codegen.py` | 5 codegen determinism/hash/order tests |
| `tests/test_capability.py` | 7 capability-manifest tests |
| `tests/test_shift_primitives.py` | 8 two-slot primitive and codegen tests |
| `tests/test_icmp_primitives.py` | 11 result/operand primitive and codegen tests |
| `tests/test_fcmp_primitives.py` | 18 float LLT / predicate / macro / codegen tests |
| `tests/test_concat_primitives.py` | 14 concat-shape / bitcast / boundary tests |
| `tests/test_shadowed.py` | 7 shadowed-witness and reachability tests |
| `codegen.py` | AST -> C++ builder calls, header/source/manifest |
| `capabilities.json` | Custom / Libcall / Lower capability manifest |
| `<target tree>/tools/oracle/main.cpp` | JSONL -> `LegalityQuery` -> `getAction` adapter |