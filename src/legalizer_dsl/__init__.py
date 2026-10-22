# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""A small, executable DSL for LLVM GlobalISel legality rules."""

__version__ = "0.3.0"

from .diagnostics import DslError
from .model import (
    Action,
    LegalizerInfo,
    LLT,
    MemDesc,
    Query,
    Rule,
    RuleSet,
    Step,
    parse_llt,
)
from .parser import parse, parse_defines, parse_file
from .witness import Witness, WitnessReport, parse_witness_file, run_witnesses

__all__ = [
    "__version__",
    "Action",
    "DslError",
    "LegalizerInfo",
    "LLT",
    "MemDesc",
    "Query",
    "Rule",
    "RuleSet",
    "Step",
    "Witness",
    "WitnessReport",
    "parse",
    "parse_defines",
    "parse_file",
    "parse_llt",
    "parse_witness_file",
    "run_witnesses",
]