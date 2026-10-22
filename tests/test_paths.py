# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Tests for the central data-location resolver in ``legispec.paths``."""

from __future__ import annotations

import json
import pathlib
import sysconfig
import tempfile
import unittest

from legispec import fingerprint, paths

REPO = pathlib.Path(__file__).resolve().parents[1]


class DataRootTests(unittest.TestCase):
    def test_explicit_env_wins_over_a_repo_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            override = root / "shared-data"
            (repo / "rules").mkdir(parents=True)
            resolved = paths.data_root(
                repo_root=repo, env={"LEGISPEC_DATA": str(override)}
            )
        self.assertEqual(resolved, override)

    def test_root_alias_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            override = root / "legacy-data"
            resolved = paths.data_root(
                repo_root=root / "repo", env={"LEGISPEC_ROOT": str(override)}
            )
        self.assertEqual(resolved, override)

    def test_data_env_takes_precedence_over_root_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            primary = root / "primary"
            legacy = root / "legacy"
            resolved = paths.data_root(
                repo_root=root / "repo",
                env={
                    "LEGISPEC_DATA": str(primary),
                    "LEGISPEC_ROOT": str(legacy),
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
        expected = pathlib.Path(sysconfig.get_path("data")) / "share" / "legispec"
        self.assertEqual(resolved, expected)
        self.assertEqual(resolved.parts[-2:], ("share", "legispec"))


class OutRootTests(unittest.TestCase):
    def test_out_env_override_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            data = root / "data"
            custom = root / "custom-out"
            resolved = paths.out_root(
                data_root=data, env={"LEGISPEC_OUT": str(custom)}
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
            '"share/legispec/rules" = ["rules/*.lspec"]',
            '"share/legispec/witness" = ["witness/*.witness"]',
            '"share/legispec/capabilities" = ["capabilities/*.json"]',
            '"share/legispec/claims" = ["claims/*.json", "claims/*.md", "claims/*.txt"]',
            '"share/legispec/docs" = ["docs/CLOSURE.md", "docs/COMPATIBILITY.md", "docs/CORRECTNESS.md", "docs/COVERAGE.md", "docs/EVIDENCE.md", "docs/INTEGRATION.md"]',
        ):
            with self.subTest(key=key):
                self.assertIn(key, text)



class RuleExtensionTests(unittest.TestCase):
    """The canonical rule suffix is a repo-wide invariant, not a convention."""

    def test_rules_directory_uses_only_the_canonical_suffix(self):
        rules = sorted(path for path in (REPO / "rules").iterdir() if path.is_file())
        suffixes = {path.suffix for path in rules}
        self.assertEqual(suffixes, {paths.RULE_SUFFIX})
        self.assertEqual(
            [path.name for path in paths.iter_rule_files(REPO / "rules")],
            [path.name for path in rules],
        )

    def test_noncanonical_suffix_is_never_discovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "toycpu.lspec").write_text("", encoding="utf-8")
            (root / "legacy.rules").write_text("", encoding="utf-8")
            discovered = paths.iter_rule_files(root)
        self.assertEqual([path.name for path in discovered], ["toycpu.lspec"])

    def test_claims_and_generated_evidence_reference_lspec(self):
        claims = json.loads((REPO / "claims" / "claims.json").read_text(encoding="utf-8-sig"))
        for claim in claims["claims"]:
            with self.subTest(claim=claim["id"]):
                self.assertTrue(claim["rules"].endswith(paths.RULE_SUFFIX), claim["rules"])
        manifest = json.loads(
            (REPO / "claims" / "generated-evidence.json").read_text(encoding="utf-8-sig")
        )
        for name in manifest["hand_only"]:
            with self.subTest(name=name):
                self.assertTrue(name.endswith(paths.RULE_SUFFIX), name)


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
