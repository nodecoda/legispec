# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Guards for the canonical LegiSpec naming (see ``docs/BRAND.md``)."""

from __future__ import annotations

import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]

LEGACY_PHRASE = "Legalizer DSL"
SPDX_MARKER = "Part of the Legalizer DSL project"

# Files that may still carry the legacy product name outside a license header.
# Every entry needs a reason in docs/BRAND.md section 3.
LEGACY_ALLOWLIST = {
    "docs/BRAND.md",
    "docs/design-history.md",
    "docs/ldsl-v0.1.md",
    "claims/claim-schema.json",
    "src/legalizer_dsl/checker/__init__.py",
    "src/legalizer_dsl/codegen.py",
    "src/legalizer_dsl/conformance.py",
    "tests/test_brand.py",
}

SKIP_DIRS = {
    ".git",
    ".omx",
    ".codex",
    "out",
    "build",
    "dist",
    "coverage",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}

TEXT_SUFFIXES = {
    "",
    ".md",
    ".py",
    ".toml",
    ".yml",
    ".yaml",
    ".json",
    ".txt",
    ".witness",
    ".cpp",
    ".h",
    ".cmake",
    ".ps1",
    ".sh",
    ".ebnf",
}


def _iter_text_files():
    for path in sorted(REPO.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(REPO)
        if any(
            part in SKIP_DIRS or part.endswith(".egg-info")
            for part in relative.parts
        ):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        yield path, relative.as_posix()


class BrandNamingTests(unittest.TestCase):
    def test_readme_headline_is_legispec(self):
        headline = (REPO / "README.md").read_text(encoding="utf-8-sig").splitlines()[0]
        self.assertEqual(headline, "# LegiSpec")

    def test_brand_doc_declares_the_canonical_vocabulary(self):
        text = (REPO / "docs" / "BRAND.md").read_text(encoding="utf-8-sig")
        for token in ("`LegiSpec`", "`LegiSpec DSL`", "`.lspec`", "`legispec`"):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_legacy_name_is_confined_to_the_allowlist(self):
        offenders = []
        for path, relative in _iter_text_files():
            try:
                lines = path.read_text(encoding="utf-8-sig").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(lines, 1):
                if LEGACY_PHRASE not in line or SPDX_MARKER in line:
                    continue
                if relative in LEGACY_ALLOWLIST:
                    continue
                offenders.append(f"{relative}:{number}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
