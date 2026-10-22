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
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legalizer_dsl import codegen
from legalizer_dsl.checker.capability import required_capabilities
from legalizer_dsl.parser import parse, parse_file

HERE = pathlib.Path(__file__).resolve().parents[1]
RULES = HERE / "rules" / "toycpu.lspec"
REL = "rules/toycpu.lspec"


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
            self.assertEqual(manifest["generator_version"], "0.2.0")
            self.assertEqual(
                manifest["dsl_spec_version"], codegen.DSL_SPEC_VERSION
            )
            self.assertEqual(manifest["llvm_commit"], "unknown")
            self.assertEqual(manifest["namespace"], "ToyCPU")
            self.assertEqual(manifest["namespace_source"], "explicit")
            self.assertEqual(manifest["output_stem"], "toycpu_rules_gen")
            self.assertEqual(
                manifest["header_guard"],
                "TOYCPU_TOYCPU_RULES_GEN_H",
            )
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
                self.assertEqual(manifest[f"{key}_sha256"], _sha(data))

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
        self.assertEqual(manifest["namespace_source"], "explicit")
        self.assertIn(
            "#ifndef PROBENS_TOYCPU_RULES_GEN_H", header
        )
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

    def test_namespace_is_derived_from_rules_stem(self):
        rules = HERE / "rules" / "aarch64-vector.lspec"
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "out"
            self.assertEqual(
                codegen.main(["--rules", str(rules), "--out-dir", str(out)]),
                0,
            )
            manifest = json.loads(
                (out / codegen.GENERATED_MANIFEST).read_text(encoding="utf-8")
            )
        self.assertEqual(manifest["namespace"], "AArch64Vector")
        self.assertEqual(manifest["namespace_source"], "derived")

    def test_unknown_namespace_family_requires_explicit_namespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules = pathlib.Path(tmp) / "mystery-target.lspec"
            rules.write_text(
                "ruleset G_X { legal_for {s32}; }\n", encoding="utf-8"
            )
            out = pathlib.Path(tmp) / "out"
            self.assertEqual(
                codegen.main(["--rules", str(rules), "--out-dir", str(out)]),
                2,
            )
            self.assertFalse(out.exists())
            self.assertEqual(
                codegen.main(
                    [
                        "--rules",
                        str(rules),
                        "--out-dir",
                        str(out),
                        "--namespace",
                        "MysteryTarget",
                    ]
                ),
                0,
            )

    def test_two_slices_in_one_namespace_have_distinct_header_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            slices = (
                HERE / "rules" / "toycpu.lspec",
                HERE / "rules" / "aarch64-vector.lspec",
            )
            headers = []
            for rules in slices:
                self.assertEqual(
                    codegen.main(
                        [
                            "--rules",
                            str(rules),
                            "--out-dir",
                            str(out),
                            "--namespace",
                            "SharedNs",
                        ]
                    ),
                    0,
                )
                stem = codegen._derive_output_stem(rules)
                headers.append(out / f"{stem}.h")
            guards = []
            for header in headers:
                text = header.read_text(encoding="utf-8")
                match = __import__("re").search(
                    r"#ifndef ([A-Z0-9_]+)", text
                )
                self.assertIsNotNone(match)
                guards.append(match.group(1))
            self.assertEqual(len(set(guards)), 2)
            combined = "\n".join(f'#include "{h.name}"' for h in headers)
            self.assertEqual(combined.count("#include"), 2)

    def test_check_mode_accepts_fresh_output_and_never_rewrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "out"
            argv = [
                "--rules",
                str(RULES),
                "--out-dir",
                str(out),
            ]
            self.assertEqual(codegen.main(argv), 0)
            before = {
                path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                for path in out.iterdir()
            }
            self.assertEqual(codegen.main(argv + ["--check"]), 0)
            after = {
                path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                for path in out.iterdir()
            }
            self.assertEqual(before, after)

    def test_check_mode_detects_tampering_and_does_not_repair_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "out"
            argv = [
                "--rules",
                str(RULES),
                "--out-dir",
                str(out),
            ]
            self.assertEqual(codegen.main(argv), 0)
            header = out / codegen.GENERATED_HEADER
            tampered = header.read_text(encoding="utf-8") + "// tampered\n"
            header.write_text(tampered, encoding="utf-8", newline="\n")
            self.assertEqual(codegen.main(argv + ["--check"]), 1)
            self.assertEqual(
                header.read_text(encoding="utf-8"), tampered
            )

    def test_check_mode_reports_missing_output_without_creating_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "missing"
            self.assertEqual(
                codegen.main(
                    ["--rules", str(RULES), "--out-dir", str(out), "--check"]
                ),
                1,
            )
            self.assertFalse(out.exists())

    def test_cli_forwards_provenance_pins(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "out"
            self.assertEqual(
                codegen.main(
                    [
                        "--rules",
                        str(RULES),
                        "--out-dir",
                        str(out),
                        "--llvm-commit",
                        "deadbeef",
                        "--dsl-spec-version",
                        "9.9",
                    ]
                ),
                0,
            )
            manifest = json.loads(
                (out / codegen.GENERATED_MANIFEST).read_text(encoding="utf-8")
            )
        self.assertEqual(manifest["llvm_commit"], "deadbeef")
        self.assertEqual(manifest["dsl_spec_version"], "9.9")

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
                rules_rel="probe.lspec",
                rules_sha="0" * 64,
            )
            source = (out / codegen.GENERATED_SOURCE).read_text(
                encoding="utf-8"
            )
        self.assertIn("LegalityPredicate([=](const LegalityQuery &Q)", source)
        self.assertIn("getElementType()", source)
        self.assertIn("moreElementsIf", source)
        self.assertIn("fewerElementsIf", source)



class WiringContractTests(unittest.TestCase):
    def _emit(self, out, wiring, *, namespace='ToyCPU', stem=None):
        info = parse_file(RULES)
        return codegen.emit_wiring(
            info,
            out_dir=out,
            wiring_dir=wiring,
            namespace=namespace,
            output_stem=stem or codegen._derive_output_stem(RULES),
            llvm_commit='deadbeef',
        )

    def test_wiring_is_self_contained_and_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            out = root / 'generated'
            wiring = root / 'wiring'
            self._emit(out, wiring)
            first = {
                p.name: p.read_bytes()
                for p in sorted(wiring.iterdir())
                if p.is_file()
            }
            self.assertEqual(
                sorted(first),
                ['legalizer-rules.cmake', 'wiring.cpp.inc', 'wiring.md'],
            )

            cmake = first['legalizer-rules.cmake'].decode('utf-8')
            inc = first['wiring.cpp.inc'].decode('utf-8')
            self.assertIn('legalizer_dsl_toycpu_rules_gen', cmake)
            self.assertIn('LEGALIZER_DSL_GENERATED_DIR', cmake)
            self.assertIn('LEGALIZER_DSL_HEADER_GUARD', cmake)
            self.assertIn('TOYCPU_TOYCPU_RULES_GEN_H', cmake)
            self.assertIn('#include "toycpu_rules_gen.h"', inc)
            self.assertIn('LEGALIZER_DSL_APPLY_RULES', inc)
            self.assertIn(
                'applyGeneratedLegalizerRulesForConsumer_toycpu_rules_gen', inc
            )
            self.assertIn('lookupGeneratedOpcode_toycpu_rules_gen', inc)
            self.assertIn('llvm::TargetOpcode::G_ADD', inc)
            self.assertNotIn('Opcode = llvm::TargetOpcode::G_ADD, true', inc)

            self._emit(out, wiring)
            second = {
                p.name: p.read_bytes()
                for p in sorted(wiring.iterdir())
                if p.is_file()
            }
            self.assertEqual(first, second)

    def test_check_wiring_detects_tamper_and_missing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            out = root / 'generated'
            wiring = root / 'wiring'
            info = parse_file(RULES)
            self._emit(out, wiring)
            kwargs = dict(
                out_dir=out,
                wiring_dir=wiring,
                namespace='ToyCPU',
                output_stem=codegen._derive_output_stem(RULES),
                llvm_commit='deadbeef',
            )
            self.assertEqual(codegen.check_wiring(info, **kwargs), [])

            inc = wiring / 'wiring.cpp.inc'
            inc.write_text(inc.read_text(encoding='utf-8') + '// edited\n', encoding='utf-8')
            problems = codegen.check_wiring(info, **kwargs)
            self.assertTrue(any('content differs' in p for p in problems), problems)

            (wiring / 'legalizer-rules.cmake').unlink()
            problems = codegen.check_wiring(info, **kwargs)
            self.assertTrue(any('missing' in p for p in problems), problems)

    def test_two_slices_have_distinct_wiring_symbols(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            out = root / 'generated'
            texts = []
            for rules in (
                RULES,
                HERE / 'rules' / 'aarch64-vector.lspec',
            ):
                info = parse_file(rules)
                stem = codegen._derive_output_stem(rules)
                wiring = root / f'wiring-{stem}'
                codegen.emit_wiring(
                    info,
                    out_dir=out,
                    wiring_dir=wiring,
                    namespace='SharedNs',
                    output_stem=stem,
                    llvm_commit='deadbeef',
                )
                texts.append((wiring / 'wiring.cpp.inc').read_text(encoding='utf-8'))
            self.assertNotEqual(
                texts[0].split('LEGALIZER_DSL_APPLY_RULES')[-1],
                texts[1].split('LEGALIZER_DSL_APPLY_RULES')[-1],
            )
            self.assertIn('applyGeneratedLegalizerRulesForConsumer_toycpu_rules_gen', texts[0])
            self.assertIn(
                'applyGeneratedLegalizerRulesForConsumer_aarch64_vector_rules_gen',
                texts[1],
            )

    def test_cli_check_mode_covers_wiring(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            out = root / 'generated'
            wiring = root / 'wiring'
            argv = [
                '--rules', str(RULES),
                '--out-dir', str(out),
                '--emit-wiring', str(wiring),
                '--llvm-commit', 'deadbeef',
            ]
            self.assertEqual(codegen.main(argv), 0)
            self.assertEqual(codegen.main(argv + ['--check']), 0)
            inc = wiring / 'wiring.cpp.inc'
            inc.write_text(inc.read_text(encoding='utf-8') + '// edited\n', encoding='utf-8')
            self.assertEqual(codegen.main(argv + ['--check']), 1)


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
        rules = HERE / "rules" / "aarch64-concat-vectors.lspec"
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


class SourceLabelTests(unittest.TestCase):
    def test_repo_rules_keep_a_repository_relative_label_from_any_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "out"
            previous = pathlib.Path.cwd()
            try:
                os.chdir(tmp)
                rc = codegen.main(
                    ["--rules", str(RULES), "--out-dir", str(out)]
                )
            finally:
                os.chdir(previous)
            self.assertEqual(rc, 0)
            manifest = json.loads(
                (out / codegen.GENERATED_MANIFEST).read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["source"], REL)
            self.assertFalse(pathlib.Path(manifest["source"]).is_absolute())
            header = (out / codegen.GENERATED_HEADER).read_text(encoding="utf-8")
            self.assertIn(f"// Source: {REL}", header)
            self.assertNotIn(str(HERE), header)

    def test_external_rules_get_a_stable_label_instead_of_an_absolute_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            rules = root / "probe.lspec"
            rules.write_text("ruleset G_X { legal_for {s32}; }\n", encoding="utf-8")
            out = root / "out"
            self.assertEqual(
                codegen.main(
                    [
                        "--rules",
                        str(rules),
                        "--out-dir",
                        str(out),
                        "--namespace",
                        "Probe",
                    ]
                ),
                0,
            )
            manifest = json.loads(
                (out / codegen.GENERATED_MANIFEST).read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["source"], "external/probe.lspec")


if __name__ == "__main__":
    unittest.main()
