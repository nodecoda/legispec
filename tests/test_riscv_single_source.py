# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0.
# See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0
#
# ===----------------------------------------------------------------------===##

"""One source, two XLen configurations: equivalence with the frozen files.

The claim under test is the guard contract, not cosmetic similarity:

  * a false macro guard erases the rule before ordering, and
  * a true macro guard inserts the transcribed rule verbatim,

so evaluating the conditional source under configuration C must produce the
same one-step decision function as the frozen per-configuration file.

Rule tables are compared behaviourally over the full declared scalar domain
(every declared type in every type slot, plus the imm set), because two
transcriptions can differ in how many table entries encode the same decision
function: `legal_for {s16, s32}` + `legal_for {s64}` and one three-element
`legalFor` are the same policy, and a rule whose predicate is unsatisfiable
(e.g. G_SEXT_INREG on RV32) does not change any answer.

The frozen files stay untouched: they are the oracle-checked baseline.
"""

from __future__ import annotations

import itertools
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legispec import LLT, Query, parse_file, parse_llt

RULES = pathlib.Path(__file__).resolve().parents[1] / "rules"

CONFIGS = {
    "rv64": {
        "file": RULES / "riscv64-scalar.lspec",
        "config": {
            "is64bit": True,
            "sXLen": LLT.scalar(64),
            "sDoubleXLen": LLT.scalar(128),
        },
        "capabilities": "riscv64-scalar-capabilities.json",
    },
    "rv32": {
        "file": RULES / "riscv32-scalar.lspec",
        "config": {
            "is64bit": False,
            "sXLen": LLT.scalar(32),
            "sDoubleXLen": LLT.scalar(64),
        },
        "capabilities": "riscv32-scalar-capabilities.json",
    },
}

UNIFIED = RULES / "riscv-scalar-conditional.lspec"

# Declared scalar domain of both files (rv64/scalar and rv32/scalar agree).
DOMAIN_TYPES = tuple(
    parse_llt(t)
    for t in (
        "s1",
        "s2",
        "s3",
        "s4",
        "s8",
        "s12",
        "s16",
        "s24",
        "s32",
        "s48",
        "s64",
        "s80",
        "s96",
        "s128",
        "s256",
    )
)

IMMS = (0, 8, 16, 32, 64)


def corpus(arity: int):
    if arity == 0:
        yield ()
        return
    if arity == 1:
        for t0 in DOMAIN_TYPES:
            yield (t0,)
        return
    for t0, t1 in itertools.product(DOMAIN_TYPES, repeat=2):
        yield (t0, t1)


def decision(info, opcode: str, types, imms=()):
    step, _ = info.action(Query(opcode, types, (), imms), trace=False)
    return (step.action, step.type_idx, step.new_type)


class RiscvSingleSourceTests(unittest.TestCase):
    def test_unified_rulesets_cover_the_same_opcodes(self):
        unified = parse_file(UNIFIED, config=CONFIGS["rv64"]["config"])
        frozen = parse_file(CONFIGS["rv64"]["file"])
        self.assertEqual(
            sorted(unified.by_opcode), sorted(frozen.by_opcode)
        )
        self.assertEqual(len(unified.rulesets), len(frozen.rulesets))

    def test_rv64_unified_matches_frozen_decisions(self):
        self._compare("rv64")

    def test_rv32_unified_matches_frozen_decisions(self):
        self._compare("rv32")

    def _compare(self, name: str) -> None:
        entry = CONFIGS[name]
        unified = parse_file(UNIFIED, config=entry["config"])
        frozen = parse_file(entry["file"])

        checked = 0
        for ruleset in frozen.rulesets:
            for opcode in ruleset.opcodes:
                # Probe every slot shape, not only the slots this ruleset
                # happens to read: an unused type slot must not change the
                # answer.  Extra slots are ignored by the predicates.
                for arity in (1, 2):
                    for types in corpus(arity):
                        for imms in ((),) + tuple((i,) for i in IMMS):
                            if imms and opcode != "G_SEXT_INREG":
                                continue
                            want = decision(frozen, opcode, types, imms)
                            got = decision(unified, opcode, types, imms)
                            checked += 1
                            self.assertEqual(
                                got,
                                want,
                                f"{name} {opcode} "
                                f"types={tuple(map(str, types))} imms={imms}",
                            )
        self.assertGreater(checked, 10_000)

    def test_guard_rule_counts_are_configuration_dependent(self):
        rv64 = parse_file(UNIFIED, config=CONFIGS["rv64"]["config"])
        rv32 = parse_file(UNIFIED, config=CONFIGS["rv32"]["config"])
        # Six is64Bit-gated macros are active only on RV64:
        #   G_ADD custom, G_SHL custom pair, G_ZEXT two legal pairs,
        #   G_CONSTANT custom, G_FREEZE s64, G_IMPLICIT_DEF sXLen,
        #   G_SELECT pair, G_PHI sXLen, G_READCOUNTER legal.
        # RV32 activates its own guards instead.
        self.assertEqual(rv64.check()["rules"], 108)
        self.assertEqual(rv32.check()["rules"], 101)


if __name__ == "__main__":
    unittest.main()