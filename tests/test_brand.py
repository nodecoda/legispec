# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0.
# See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0
#
# ===----------------------------------------------------------------------===##

"""Guards for the canonical LegiSpec naming (see ``docs/BRAND.md``)."""

from __future__ import annotations

import pathlib
import re
import subprocess
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]

# Two files are frozen history.  The guard test itself contains the patterns
# it searches for, so it is exempt as well.
ARCHIVES = {
    "docs/design-history.md",
    "docs/ldsl-v0.1.md",
    "tests/test_brand.py",
}

LEGACY_PATTERNS = (
    re.compile(r"legalizer[-_ ]dsl", re.IGNORECASE),
    re.compile(r"legalizer-oracle", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])ldsl(?![A-Za-z0-9])", re.IGNORECASE),
    re.compile(r"ldsl-", re.IGNORECASE),
    re.compile(r"\.ldsl\b", re.IGNORECASE),
)

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


def _tracked_text_files():
    output = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=REPO
    ).decode("utf-8")
    for relative in output.split("\0"):
        if not relative:
            continue
        path = REPO / relative
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        yield path, relative


class BrandNamingTests(unittest.TestCase):
    def test_readme_headline_is_legispec(self):
        headline = (REPO / "README.md").read_text(encoding="utf-8-sig").splitlines()[0]
        self.assertEqual(headline, "# LegiSpec")

    def test_brand_doc_declares_the_canonical_vocabulary(self):
        text = (REPO / "docs" / "BRAND.md").read_text(encoding="utf-8-sig")
        for token in ("`LegiSpec`", "`LegiSpec DSL`", "`.lspec`", "`legispec`"):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_legacy_tokens_are_confined_to_the_archives(self):
        offenders = []
        for path, relative in _tracked_text_files():
            if relative in ARCHIVES:
                continue
            try:
                lines = path.read_text(encoding="utf-8-sig").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(lines, 1):
                for pattern in LEGACY_PATTERNS:
                    if pattern.search(line):
                        offenders.append(f"{relative}:{number}: {line.strip()}")
                        break
        self.assertEqual(offenders, [])

    def test_pyproject_uses_only_canonical_identifiers(self):
        text = (REPO / "pyproject.toml").read_text(encoding="utf-8-sig")
        self.assertIn('name = "legispec"', text)
        self.assertIn('legispec = "legispec.cli:main"', text)
        self.assertIn('legispec-claims = "legispec.claims:main"', text)
        self.assertIn('"share/legispec/rules"', text)
        self.assertNotIn("Deprecated aliases", text)
        for pattern in LEGACY_PATTERNS:
            self.assertIsNone(pattern.search(text), pattern.pattern)

    def test_canonical_package_directory_exists(self):
        self.assertTrue((REPO / "src" / "legispec" / "__init__.py").is_file())
        legacy = "legalizer" + "_dsl"
        self.assertFalse((REPO / "src" / legacy).exists())


if __name__ == "__main__":
    unittest.main()
