# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Tests for the central data-location resolver in ``legalizer_dsl.paths``."""

from __future__ import annotations

import pathlib
import sysconfig
import tempfile
import unittest

from legalizer_dsl import fingerprint, paths

REPO = pathlib.Path(__file__).resolve().parents[1]


class DataRootTests(unittest.TestCase):
    def test_explicit_env_wins_over_a_repo_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            override = root / "shared-data"
            (repo / "rules").mkdir(parents=True)
            resolved = paths.data_root(
                repo_root=repo, env={"LEGALIZER_DSL_DATA": str(override)}
            )
        self.assertEqual(resolved, override)

    def test_legacy_root_alias_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            override = root / "legacy-data"
            resolved = paths.data_root(
                repo_root=root / "repo", env={"LEGALIZER_DSL_ROOT": str(override)}
            )
        self.assertEqual(resolved, override)

    def test_data_env_takes_precedence_over_legacy_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            primary = root / "primary"
            legacy = root / "legacy"
            resolved = paths.data_root(
                repo_root=root / "repo",
                env={
                    "LEGALIZER_DSL_DATA": str(primary),
                    "LEGALIZER_DSL_ROOT": str(legacy),
                },
            )
        self.assertEqual(resolved, primary)

    def test_repo_with_rules_directory_is_the_data_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            (repo / "rules").mkdir()
            resolved = paths.data_root(repo_root=repo, env={})
        self.assertEqual(resolved, repo)

    def test_empty_repo_falls_back_to_installed_share_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            resolved = paths.data_root(repo_root=repo, env={})
        expected = pathlib.Path(sysconfig.get_path("data")) / "share" / "legalizer-dsl"
        self.assertEqual(resolved, expected)
        self.assertEqual(resolved.parts[-2:], ("share", "legalizer-dsl"))


class OutRootTests(unittest.TestCase):
    def test_out_env_override_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            data = root / "data"
            custom = root / "custom-out"
            resolved = paths.out_root(
                data_root=data, env={"LEGALIZER_DSL_OUT": str(custom)}
            )
        self.assertEqual(resolved, custom)

    def test_out_defaults_below_the_data_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = pathlib.Path(tmp) / "data"
            resolved = paths.out_root(data_root=data, env={})
        self.assertEqual(resolved, data / "out")


class PyprojectDataFilesTests(unittest.TestCase):
    def test_four_data_file_groups_are_declared(self):
        text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("[tool.setuptools.data-files]", text)
        for key in (
            '"share/legalizer-dsl/rules" = ["rules/*.ldsl"]',
            '"share/legalizer-dsl/witness" = ["witness/*.witness"]',
            '"share/legalizer-dsl/capabilities" = ["capabilities/*.json"]',
            '"share/legalizer-dsl/claims" = ["claims/*.json", "claims/*.md", "claims/*.txt"]',
            '"share/legalizer-dsl/docs" = ["docs/CLOSURE.md"]',
        ):
            with self.subTest(key=key):
                self.assertIn(key, text)



class FingerprintManifestTests(unittest.TestCase):
    def test_every_manifest_file_exists(self):
        for module in (
            *fingerprint.hand_evaluator_paths(),
            *fingerprint.codegen_paths(),
        ):
            self.assertTrue(module.is_file(), module)

    def test_hand_manifest_covers_conformance_import_closure(self):
        names = {
            module.relative_to(paths.PACKAGE_DIR).as_posix()
            for module in fingerprint.hand_evaluator_paths()
        }
        for required in (
            "conformance.py",
            "model.py",
            "parser.py",
            "interp.py",
            "checker/analysis.py",
            "diagnostics.py",
        ):
            self.assertIn(required, names)

    def test_codegen_manifest_is_separate_from_hand_evaluator(self):
        hand = set(fingerprint.HAND_EVALUATOR_FILES)
        codegen = set(fingerprint.CODEGEN_FILES)
        self.assertIn("codegen.py", codegen)
        self.assertNotIn("codegen.py", hand)


if __name__ == "__main__":
    unittest.main()
