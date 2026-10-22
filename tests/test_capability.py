# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0.
# See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0
#
# ===----------------------------------------------------------------------===##

"""Phase 6: capability manifest checks."""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from legispec.checker.capability import (
    CapabilityManifestError,
    load_manifest,
    run,
)
from legispec.diagnostics import Severity
from legispec.parser import parse

SOURCE = """
domain { types { s32 }; q0 { (G_CUSTOM, [s32]); } }
ruleset G_CUSTOM { custom; }
ruleset G_LIB { libcall; }
ruleset G_LOW { lower; }
"""


def _manifest():
    return {
        "version": 1,
        "custom": {
            "symbol": "ToyCPULegalizerInfo::legalizeCustom",
            "opcodes": ["G_CUSTOM"],
        },
        "libcall": {"opcodes": ["G_LIB"]},
        "lower": {"opcodes": ["G_LOW"]},
    }


def _codes(diagnostics):
    return {d.code for d in diagnostics}


class CapabilityTests(unittest.TestCase):
    def test_exact_manifest_is_clean(self):
        self.assertEqual(run(parse(SOURCE), _manifest()), [])

    def test_missing_declaration_is_c001(self):
        manifest = _manifest()
        manifest["custom"]["opcodes"] = []
        diagnostics = run(parse(SOURCE), manifest)
        self.assertIn("C001", _codes(diagnostics))
        self.assertTrue(any(d.severity is Severity.ERROR for d in diagnostics))

    def test_stale_declaration_is_c002(self):
        manifest = _manifest()
        manifest["libcall"]["opcodes"].append("G_NOT_IN_DSL")
        diagnostics = run(parse(SOURCE), manifest)
        self.assertIn("C002", _codes(diagnostics))

    def test_missing_custom_case_is_c003(self):
        source = "void ToyCPULegalizerInfo::legalizeCustom() {}\n"
        diagnostics = run(parse(SOURCE), _manifest(), custom_source=source)
        self.assertIn("C003", _codes(diagnostics))

    def test_extra_custom_case_is_c004_warning(self):
        source = (
            "void ToyCPULegalizerInfo::legalizeCustom() {\n"
            "  switch (MI.getOpcode()) {\n"
            "  case TargetOpcode::G_CUSTOM: return true;\n"
            "  case TargetOpcode::G_EXTRA: return true;\n"
            "  }\n"
            "}\n"
        )
        diagnostics = run(parse(SOURCE), _manifest(), custom_source=source)
        self.assertIn("C004", _codes(diagnostics))
        self.assertTrue(
            all(
                d.severity is Severity.WARN
                for d in diagnostics
                if d.code == "C004"
            )
        )

    def test_missing_provider_symbol_is_c005(self):
        manifest = _manifest()
        manifest["custom"]["symbol"] = "MissingProvider"
        source = "void ToyCPULegalizerInfo::legalizeCustom() {}\n"
        diagnostics = run(parse(SOURCE), manifest, custom_source=source)
        self.assertIn("C005", _codes(diagnostics))

    def test_bad_manifest_version_is_rejected(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "capabilities.json"
            path.write_text(json.dumps({"version": 2}), encoding="utf-8")
            with self.assertRaises(CapabilityManifestError):
                load_manifest(path)


if __name__ == "__main__":
    unittest.main()
