# Legalizer DSL: configuration guards vs query predicates

Date: 2026-10-20

## 1. The question

The RISC-V legalizer contains

```cpp
getActionDefinitionsBuilder(G_CONSTANT)
    .legalFor({p0})
    .legalFor(!ST.is64Bit(), {s32})
    .customFor(ST.is64Bit(), {s64})
    .widenScalarToNextPow2(0)
    .clampScalar(0, sXLen, sXLen);
```

`RISCVLegalizerInfo.cpp:290-295`

and `legalFor(bool, ...)` is

```cpp
LegalizeRuleSet &legalFor(bool Pred, std::initializer_list<LLT> Types) {
  if (!Pred)
    return *this;
  return actionFor(LegalizeAction::Legal, Types);
}
```

`LegalizerInfo.h:612-617`

So on RV64 the second call contributes **nothing**.  The question was whether
the DSL can express that, and the answer has to distinguish three different
things:

1. a rule with an always-false **query predicate**,
2. a rule with action `unsupported` (an explicit rejection),
3. **no rule at all**.

Only the third is correct here.  Rules carry an index; `LegalizeRuleSet::apply`
is first-match-wins; coverage, ordering and dead-rule analysis all read the
rule table.  A dead predicate changes the table even when it never fires, and
`unsupported` asserts something different (a policy decision, not a missing
builder call).

## 2. The two evaluation layers

| layer | spelling | evaluated | false effect |
| --- | --- | --- | --- |
| configuration guard | `legal_for {s32} when not is64bit;` | parser, once | macro emits no rule |
| query predicate | `custom when t0 == s32;` | per query | rule does not match |

The layers must not be mixed.  `when` in a core rule is a query predicate
(`legal when t0 == s64;`).  `when` after a macro is a configuration guard.
The parser knows which one it is from the statement form, not from the
operand names.

Guards are also available on `custom_for`, `lower_for`, `unsupported_for`,
`libcall_for`, `legal_for_pairs`, `custom_for_pairs`, `clamp_scalar`,
`widen_to_next_pow2`, `widen_to_next_pow2_or_min`,
`widen_to_next_multiple`, `legal_for_mem` and `always_legal`; every macro
routes through the same `add()` helper, which evaluates the guard **before**
calling `ruleset.add`.  The rule objects are built locally and then dropped,
so nothing enters the table and no index is consumed.

## 3. Configuration values

```
--define is64bit=true
--define sXLen=s64
--define sDoubleXLen=s128
```

Values are inferred: `true`/`false` are booleans, decimal strings are
integers, anything that parses as an LLT is a type, the rest is a string.
A type-valued entry becomes a type alias and works anywhere a type works:

```
legal_for {sXLen};
clamp_scalar 0, sXLen, sXLen;
custom when t0 == sXLen;
```

It also works as the right-hand side of a size comparison:

```
widen t0 to sXLen when scalar(t0) and bits(t0) < sXLen;
```

which folds to `< 64` or `< 32` while parsing.  This is what keeps
`minScalar(0, sXLen)` and `maxScalar(0, sXLen)` to one line instead of two
guarded lines, and it does not introduce a third evaluation layer: the
comparison is constant-folded, so the resulting predicate is identical to the
literal form.

## 4. Diagnostics

| code | meaning |
| --- | --- |
| `P025` | guard names an unknown configuration variable |
| `P026` | guard uses a non-boolean value as a condition |
| `P027` | guard expression has a non-identifier operand |
| `P028` | malformed `--define` |

These are parse errors, not warnings.  A typo in a guard must not silently
evaluate to false and delete rules.

## 5. Evidence

### 5.1 Minimal case

`rules/riscv-conditional-gconstant.ldsl` is the `G_CONSTANT` builder above
written once:

```
ruleset G_CONSTANT {
    legal_for {s32} when not is64bit;
    custom_for {s64} when is64bit;
    widen_to_next_pow2 0, 0;
    clamp_scalar 0, sXLen, sXLen;
}
```

| configuration | oracle | queries | divergences | rules matched |
| --- | --- | ---: | ---: | ---: |
| RV64 | `legalizer-oracle-riscv.exe` | 9 | 0 | 4/4 |
| RV32 | `legalizer-oracle-riscv32.exe` | 9 | 0 | 4/4 |

### 5.2 Full scalar subset

`rules/riscv-scalar-conditional.ldsl` covers the whole scalar subset
(38 rulesets, 73 opcodes) with the same three configuration values.

| configuration | rules | static check | oracle | queries | divergences |
| --- | ---: | --- | --- | ---: | ---: |
| RV64 | 108 | errors=0 warnings=168 | `legalizer-oracle-riscv.exe` | 7861 | 0 |
| RV32 | 101 | errors=0 warnings=163 | `legalizer-oracle-riscv32.exe` | 7861 | 0 |

RV32 reports `rules=100/101`: one rule never matches, and that is correct.
`G_SEXT_INREG`'s first rule is the transcription of

```cpp
legalIf(all(typeIs(0, sXLen), sizeIs(0, 64), immIs(0, 32)))
```

On RV32 `typeIs(0, s32)` and `sizeIs(0, 64)` cannot both hold, so the C++
builds a rule that can never fire.  The frozen `riscv32-scalar.ldsl` dropped
it; the single-source file keeps it and declares it in
`witness/riscv-scalar-conditional-rv32.witness`.  Keeping the rule is what
makes the table match the C++; the witness is what distinguishes "the C++ has
a dead rule" from "the transcription lost a rule".

### 5.3 Equivalence without an oracle

`tests/test_riscv_single_source.py` parses both configurations of the
single-source file and the corresponding frozen file, then evaluates every
opcode over every declared type in slots `t0` and `t1`, plus the imm set.
The requirement is identical `(Action, TypeIdx, NewType)`.  This is stronger
than comparing rule counts, because two encodings of the same policy (for
example one `legalFor` with three types vs two `legalFor` calls) are allowed
to differ structurally as long as the decision function is the same.

## 6. Oracle pinning

A green report is evidence only for the binary that produced it.  Each
conformance report now records the oracle's SHA-256 and size next to the
rules-file hash, and `claims.py` refuses a proof when either has moved:

- `reference.oracle_sha256` and `reference.oracle_size` are written by
  `conformance.py`;
- `inputs.rules_sha256` records the rules content next to the corpus hash
  (`inputs.sha256` covers the generated JSONL queries, not the rules file);
- `claims.json` carries the same hashes per claim;
- `claims verify` reports `report predates oracle pinning` for old reports,
  `oracle binary changed since the report was produced` for a rebuilt binary,
  and `report was produced from different rules content` after a rules edit;
- `check_staleness` additionally compares against the previously written
  ledger and reports `STALE: ...` when either hash moved between two ledger
  writes.  This is read-only: `claims verify --write` is the explicit
  re-validation step and rewrites the ledger only when every hard check in
  `audit()` passes.

Rebuilding an oracle therefore invalidates every claim that used it, which is
the intended behavior: the proof is about a concrete artifact, not about a
target name.  To keep the artifact reproducible, the oracle links with
`lld-link /Brepro`; two builds of unchanged sources are byte-identical.  If
the binary does move, re-run the affected claim (`conformance.py`) and then
`claims.py verify --write`.

## 7. What this does not do

- It does not model `if (feature) { ... }` blocks that add rules with
  different *actions* for the same types.  Those are still transcribed with
  the macro-level guard or a separate frozen file.
- It does not put configuration into the query.  A query never sees
  `is64bit`; the guard has already removed the rule.
- It does not remove the need for per-configuration oracle binaries.  The
  guard decides which rules exist; the oracle decides whether the resulting
  table agrees with LLVM.