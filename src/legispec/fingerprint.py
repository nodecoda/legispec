# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Content fingerprints for the evidence chain.

Two fingerprints matter:

``hand_evaluator_sha256``
    Every module the conformance runner imports to enumerate D, interpret the
    rules and write a report.  A report produced by a different hand evaluator
    cannot be re-pinned without re-running it.

``codegen_sha256``
    The C++ generator plus the capability checker.  Generated oracles are
    built from its output, so generated claims pin this hash in addition to
    the hand evaluator.  Hand-written claims deliberately do not: editing
    ``codegen.py`` must not invalidate evidence it never produced.

The manifests are data, not import-graph guesses.  The accompanying test
asserts that every manifest entry exists and that the conformance runner's
direct imports are covered.
"""

from __future__ import annotations

import hashlib
import pathlib

from . import paths

HAND_EVALUATOR_FILES = (
    "diagnostics.py",
    "model.py",
    "parser.py",
    "interp.py",
    "conformance.py",
    "paths.py",
    "checker/__init__.py",
    "checker/analysis.py",
)

CODEGEN_FILES = (
    "codegen.py",
    "checker/capability.py",
)


def _module_paths(names: tuple[str, ...]) -> tuple[pathlib.Path, ...]:
    modules = tuple(paths.PACKAGE_DIR / name for name in names)
    missing = [str(module) for module in modules if not module.is_file()]
    if missing:
        raise RuntimeError(
            "evidence fingerprint manifest names missing module(s): "
            + ", ".join(missing)
        )
    return modules


def _hash_modules(modules: tuple[pathlib.Path, ...]) -> str:
    digest = hashlib.sha256()
    for module in modules:
        relative = module.relative_to(paths.PACKAGE_DIR).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(module.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def hand_evaluator_paths() -> tuple[pathlib.Path, ...]:
    return _module_paths(HAND_EVALUATOR_FILES)


def codegen_paths() -> tuple[pathlib.Path, ...]:
    return _module_paths(CODEGEN_FILES)


def hand_evaluator_sha256() -> str:
    return _hash_modules(hand_evaluator_paths())


def codegen_sha256() -> str:
    return _hash_modules(codegen_paths())
