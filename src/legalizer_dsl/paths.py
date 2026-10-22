# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Repository layout and environment overrides.

Data files live outside the Python package so the rule tables stay readable
and reviewable.  This module is the single resolver for those locations:
a checkout keeps using ``<repo>/rules`` while an installed wheel reads the
same tree from ``<sysconfig data>/share/legalizer-dsl``.

Every location can be overridden for CI or for an evidence directory produced
on another machine:

  LEGALIZER_DSL_DATA   data root (rules, witness, capabilities, claims)
  LEGALIZER_DSL_ROOT   legacy alias for LEGALIZER_DSL_DATA
  LEGALIZER_DSL_OUT    output directory
"""

from __future__ import annotations

import os
import pathlib
import sysconfig

PACKAGE_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[1]          # src/legalizer_dsl -> src -> repo


def _env_path(
    name: str, default: pathlib.Path, env: dict[str, str] | None = None
) -> pathlib.Path:
    values = os.environ if env is None else env
    value = values.get(name)
    return pathlib.Path(value).expanduser() if value else default


def data_root(
    repo_root: pathlib.Path = REPO_ROOT, env: dict[str, str] | None = None
) -> pathlib.Path:
    """Resolve the directory that holds rules/, witness/, capabilities/ and claims/.

    An explicit environment override wins, then a source checkout (identified
    by a ``rules`` directory), then the installed data-files location.
    """
    values = os.environ if env is None else env
    override = values.get("LEGALIZER_DSL_DATA") or values.get("LEGALIZER_DSL_ROOT")
    if override:
        return pathlib.Path(override).expanduser()
    repo_root = pathlib.Path(repo_root)
    candidates = (
        repo_root,
        PACKAGE_DIR.parent / "share" / "legalizer-dsl",
        PACKAGE_DIR.parents[1] / "share" / "legalizer-dsl",
        pathlib.Path(sysconfig.get_path("data")) / "share" / "legalizer-dsl",
    )
    for candidate in candidates:
        if (candidate / "rules").is_dir():
            return candidate
    return candidates[-1]


def out_root(
    data_root: pathlib.Path | None = None, env: dict[str, str] | None = None
) -> pathlib.Path:
    """Resolve the output directory; ``LEGALIZER_DSL_OUT`` still wins."""
    default = (DATA_ROOT if data_root is None else pathlib.Path(data_root)) / "out"
    return _env_path("LEGALIZER_DSL_OUT", default, env)


DATA_ROOT = data_root()
RULES = DATA_ROOT / "rules"
WITNESS = DATA_ROOT / "witness"
CAPABILITIES = DATA_ROOT / "capabilities"
DOCS = DATA_ROOT / "docs"
CLAIMS_DIR = DATA_ROOT / "claims"
TESTS = DATA_ROOT / "tests"

# Canonical rule-file suffix.  ``.ldsl`` is the pre-1.0 historical spelling: an
# explicit ``--rules`` path still parses, but nothing discovers it implicitly.
RULE_SUFFIX = ".lspec"
LEGACY_RULE_SUFFIX = ".ldsl"


def iter_rule_files(root: pathlib.Path | None = None) -> list[pathlib.Path]:
    """Return the committed rule files in deterministic order.

    Packaging, the closure gate, the strict sweep and the CI scripts all call
    this, so the canonical suffix is declared exactly once.
    """
    directory = RULES if root is None else pathlib.Path(root)
    return sorted(
        path for path in directory.glob(f"*{RULE_SUFFIX}") if path.is_file()
    )

OUT = out_root()
LEDGER = CLAIMS_DIR / "claims.json"
CLAIM_LEDGER_MD = CLAIMS_DIR / "claim-ledger.md"
CONSTRUCT_COVERAGE = CLAIMS_DIR / "construct-coverage.json"

LLVM_SRC = os.environ.get("LEGALIZER_DSL_LLVM_SRC", "")
LLVM_BUILD = os.environ.get("LEGALIZER_DSL_LLVM_BUILD", "")
LLC = os.environ.get("LEGALIZER_DSL_LLC", "")
