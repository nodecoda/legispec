# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Repository layout and environment overrides.

Data files live outside the Python package so the rule tables stay readable
and reviewable.  Every location can be overridden for CI or for an evidence
directory produced on another machine.
"""

from __future__ import annotations

import os
import pathlib

PACKAGE_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[1]          # src/legalizer_dsl -> src -> repo


def _env_path(name: str, default: pathlib.Path) -> pathlib.Path:
    value = os.environ.get(name)
    return pathlib.Path(value).expanduser() if value else default


RULES = REPO_ROOT / "rules"
WITNESS = REPO_ROOT / "witness"
CAPABILITIES = REPO_ROOT / "capabilities"
DOCS = REPO_ROOT / "docs"
CLAIMS_DIR = REPO_ROOT / "claims"
TESTS = REPO_ROOT / "tests"

OUT = _env_path("LEGALIZER_DSL_OUT", REPO_ROOT / "out")
LEDGER = CLAIMS_DIR / "claims.json"
CLAIM_LEDGER_MD = CLAIMS_DIR / "claim-ledger.md"
CONSTRUCT_COVERAGE = CLAIMS_DIR / "construct-coverage.json"

LLVM_SRC = os.environ.get("LEGALIZER_DSL_LLVM_SRC", "")
LLVM_BUILD = os.environ.get("LEGALIZER_DSL_LLVM_BUILD", "")
LLC = os.environ.get("LEGALIZER_DSL_LLC", "")
