# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Phase 6: code generation contract tests."""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legalizer_dsl import codegen
from legalizer_dsl.checker.capability import required_capabilities
from legalizer_dsl.parser import parse, parse_file

HERE = pathlib.Path(__file__).resolve().parents[1]
RULES = HERE / "rules" / "toycpu.ldsl"
REL = "rules/toycpu.ldsl"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class CodegenContractTests(unittest.TestCase):
    def _generate(self, out_dir: pathlib.Path):
        info = parse_file(RULES)
        rules_sha = _sha(RULES.read_bytes())
        manifest = codegen.generate(
            info,
            out_dir=out_dir,
            rules_rel=REL,
            rules_sha=rules_sha,
        )
        return info, manifest

    def test_manifest_matches_rule_counts_and_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            info, manifest = self._generate(out)

            self.assertEqual(manifest["version"], 1)
            self.assertEqual(manifest["source"], REL)
            self.assertEqual(manifest["rules_sha256"], _sha(RULES.read_bytes()))
            self.assertEqual(manifest["rulesets"], 47)
            self.assertEqual(manifest["opcodes"], 80)
            self.assertEqual(manifest["rules"], 89)
            self.assertEqual(
                manifest["capabilities_required"], required_capabilities(info)
            )

            for key, filename in (
                ("header", codegen.GENERATED_HEADER),
                ("source", codegen.GENERATED_SOURCE),
            ):
                data = (out / filename).read_bytes()
                self.assertEqual(manifest["generated"][key], filename)
                self.assertEqual(
                    manifest["generated"][f"{key}_sha256"], _sha(data)
                )

    def test_generation_is_deterministic_and_has_no_probe_output(self):
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            out_one = pathlib.Path(one)
            out_two = pathlib.Path(two)
            self._generate(out_one)
            self._generate(out_two)

            for filename in (
                codegen.GENERATED_HEADER,
                codegen.GENERATED_SOURCE,
                codegen.GENERATED_MANIFEST,
            ):
                self.assertEqual(
                    (out_one / filename).read_bytes(),
                    (out_two / filename).read_bytes(),
                    msg=filename,
                )

            source = (out_one / codegen.GENERATED_SOURCE).read_text(encoding="utf-8")
            self.assertNotIn("llvm::errs()", source)
            self.assertNotIn("v1s", source)

    def test_generation_does_not_mutate_the_ast(self):
        info = parse_file(RULES)
        before = [
            (ruleset.opcodes, [(str(rule), rule.action, rule.origin) for rule in ruleset.rules])
            for ruleset in info.rulesets
        ]
        with tempfile.TemporaryDirectory() as tmp:
            codegen.generate(
                info,
                out_dir=pathlib.Path(tmp),
                rules_rel=REL,
                rules_sha=_sha(RULES.read_bytes()),
            )
        after = [
            (ruleset.opcodes, [(str(rule), rule.action, rule.origin) for rule in ruleset.rules])
            for ruleset in info.rulesets
        ]
        self.assertEqual(before, after)

    def test_rule_order_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            self._generate(out)
            source = (out / codegen.GENERATED_SOURCE).read_text(encoding="utf-8")

        start = source.index("  // G_ADD")
        end = source.index("\n  // ", start + 1)
        block = source[start:end]
        self.assertLess(block.index(".legalFor"), block.index(".widenScalarIf"))
        self.assertLess(block.index(".widenScalarIf"), block.index(".narrowScalarIf"))

    def test_namespace_is_a_codegen_parameter(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            info = parse_file(RULES)
            manifest = codegen.generate(
                info,
                out_dir=out,
                rules_rel=REL,
                rules_sha=_sha(RULES.read_bytes()),
                namespace="ProbeNs",
            )
            header = (out / codegen.GENERATED_HEADER).read_text(encoding="utf-8")
            source = (out / codegen.GENERATED_SOURCE).read_text(encoding="utf-8")

        self.assertEqual(manifest["namespace"], "ProbeNs")
        self.assertIn("#ifndef PROBENS_RULES_GEN_H", header)
        self.assertIn("namespace ProbeNs {", header)
        self.assertNotIn("namespace ToyCPU", header + source)
        self.assertIn("} // namespace ProbeNs", source)

    def test_using_directives_are_function_scoped(self):
        # At global scope they would lose to llvm::any inside namespace llvm,
        # which breaks nested ``or`` predicates.
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            self._generate(out)
            source = (out / codegen.GENERATED_SOURCE).read_text(encoding="utf-8")
        body = source.index("void applyGeneratedLegalizerRules")
        self.assertGreater(source.index("using namespace LegalityPredicates;"), body)
        self.assertGreater(source.index("using namespace LegalizeMutations;"), body)
        self.assertNotIn("using namespace llvm::LegalityPredicates;", source)

    def test_invalid_namespace_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                codegen.main(["--namespace", "1bad", "--out-dir", tmp]), 2
            )

    def test_cpp_keyword_namespace_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                codegen.main(["--namespace", "int", "--out-dir", tmp]), 2
            )

    def test_cli_output_stem_controls_generated_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            self.assertEqual(
                codegen.main(
                    [
                        "--rules",
                        str(RULES),
                        "--out-dir",
                        str(out),
                        "--output-stem",
                        "custom_stem",
                    ]
                ),
                0,
            )
            header = out / "custom_stem.h"
            source = out / "custom_stem.cpp"
            self.assertTrue(header.is_file())
            self.assertTrue(source.is_file())
            manifest = json.loads(
                (out / codegen.GENERATED_MANIFEST).read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["generated"]["header"], "custom_stem.h")
            self.assertEqual(manifest["generated"]["source"], "custom_stem.cpp")
            self.assertIn(
                '#include "custom_stem.h"', source.read_text(encoding="utf-8")
            )
            self.assertFalse((out / codegen.GENERATED_HEADER).exists())
            self.assertFalse((out / codegen.GENERATED_SOURCE).exists())

    def test_each_llt_is_declared_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            self._generate(out)
            source = (out / codegen.GENERATED_SOURCE).read_text(encoding="utf-8")
        self.assertEqual(source.count("const LLT s32 ="), 1)
        self.assertEqual(source.count("const LLT v4s32 ="), 1)


    def test_vector_clamp_predicate_captures_element_llt(self):
        # The generated lambda compares against a local ``const LLT s8``.
        # A capture-less lambda cannot see it; LLVM's own builder uses [=].
        info = parse(
            "ruleset G_X { clamp_num_elements 0, v8s8, v16s8; }"
        )
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            codegen.generate(
                info,
                out_dir=out,
                rules_rel="probe.ldsl",
                rules_sha="0" * 64,
            )
            source = (out / codegen.GENERATED_SOURCE).read_text(
                encoding="utf-8"
            )
        self.assertIn("LegalityPredicate([=](const LegalityQuery &Q)", source)
        self.assertIn("getElementType()", source)
        self.assertIn("moreElementsIf", source)
        self.assertIn("fewerElementsIf", source)



class OutputStemSafetyTests(unittest.TestCase):
    def test_path_like_stems_are_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "out"
            for stem in ("../escape", "bad/name", "..", "C:\\evil", "", "1bad"):
                with self.subTest(stem=stem):
                    rc = codegen.main(
                        [
                            "--rules",
                            str(RULES),
                            "--out-dir",
                            str(out),
                            "--output-stem",
                            stem,
                        ]
                    )
                    self.assertNotEqual(rc, 0)
                    self.assertFalse(out.exists())
                    self.assertFalse((pathlib.Path(tmp) / "escape.h").exists())

    def test_valid_stem_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "out"
            self.assertEqual(
                codegen.main(
                    [
                        "--rules",
                        str(RULES),
                        "--out-dir",
                        str(out),
                        "--output-stem",
                        "_ok2",
                    ]
                ),
                0,
            )
            self.assertTrue((out / "_ok2.h").is_file())

    def test_default_stem_sanitizes_hyphenated_rules(self):
        rules = HERE / "rules" / "aarch64-concat-vectors.ldsl"
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "out"
            self.assertEqual(
                codegen.main(["--rules", str(rules), "--out-dir", str(out)]),
                0,
            )
            stem = "aarch64_concat_vectors_rules_gen"
            header = out / f"{stem}.h"
            source = out / f"{stem}.cpp"
            self.assertTrue(header.is_file())
            self.assertTrue(source.is_file())
            manifest = json.loads(
                (out / codegen.GENERATED_MANIFEST).read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["generated"]["header"], f"{stem}.h")
            self.assertEqual(manifest["generated"]["source"], f"{stem}.cpp")
            self.assertIn(
                f'#include "{stem}.h"', source.read_text(encoding="utf-8")
            )


if __name__ == "__main__":
    unittest.main()
