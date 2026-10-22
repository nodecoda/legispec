#!/usr/bin/env python3
# ===----------------------------------------------------------------------===##
#
# Part of the Legalizer DSL project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Build a wheel, install it in isolation, and run a strict rule check.

The smoke test proves that a wheel carries the non-Python data files
(``rules/``, ``witness/`` and ``capabilities/``) and that ``paths.data_root``
finds them once the package is installed outside the source checkout.

Usage::

    python scripts/wheel_smoke.py
    python scripts/wheel_smoke.py --keep
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
RULE_NAME = "toycpu.lspec"


def run(
    command: list[str],
    *,
    cwd: pathlib.Path | None = None,
    env: dict[str, str] | None = None,
) -> int:
    printable = " ".join(str(part) for part in command)
    print(f"+ {printable}", flush=True)
    return subprocess.run(command, cwd=cwd, env=env).returncode


def build_wheel(dist_dir: pathlib.Path) -> pathlib.Path:
    dist_dir.mkdir(parents=True, exist_ok=True)
    if importlib.util.find_spec("build") is not None:
        command = [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--outdir",
            str(dist_dir),
        ]
    else:
        command = [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "-w",
            str(dist_dir),
        ]
    if run(command, cwd=REPO) != 0:
        raise RuntimeError("wheel build failed")
    wheels = sorted(dist_dir.glob("*.whl"))
    if not wheels:
        raise RuntimeError(f"no wheel produced in {dist_dir}")
    return wheels[-1]


def install_wheel(wheel: pathlib.Path, site_dir: pathlib.Path) -> None:
    site_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-deps",
        "--target",
        str(site_dir),
        str(wheel),
    ]
    if run(command) != 0:
        raise RuntimeError("wheel install failed")


def find_data_root(
    temp_dir: pathlib.Path, site_dir: pathlib.Path
) -> pathlib.Path | None:
    """Locate the installed ``share/legalizer-dsl`` tree.

    ``pip install --target`` places data-files in ``<target>/share`` on some
    schemes and beside the target on others, so accept either layout.
    """
    candidates = (
        temp_dir / "share" / "legalizer-dsl",
        site_dir.parent / "share" / "legalizer-dsl",
        site_dir / "share" / "legalizer-dsl",
    )
    for candidate in candidates:
        if (candidate / "rules" / RULE_NAME).is_file():
            return candidate
    for base in (temp_dir, site_dir.parent):
        for path in sorted(base.rglob(RULE_NAME)):
            if path.parent.name == "rules" and path.parent.parent.name == "legalizer-dsl":
                return path.parent.parent
    return None


def _installed_env(site_dir: pathlib.Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(site_dir)
    env.pop("LEGALIZER_DSL_ROOT", None)
    env.pop("LEGALIZER_DSL_OUT", None)
    env.pop("LEGALIZER_DSL_DATA", None)
    return env


def run_strict_check(
    site_dir: pathlib.Path, data_root: pathlib.Path, cwd: pathlib.Path
) -> int:
    rules = data_root / "rules" / RULE_NAME
    code = (
        "import sys; "
        "from legalizer_dsl.cli import main; "
        f"sys.exit(main(['--rules', {str(rules)!r}, 'check', '--strict']))"
    )
    return run([sys.executable, "-c", code], cwd=cwd, env=_installed_env(site_dir))


def run_module(
    site_dir: pathlib.Path, cwd: pathlib.Path, module: str, *args: str
) -> int:
    return run(
        [sys.executable, "-m", module, *args],
        cwd=cwd,
        env=_installed_env(site_dir),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep",
        action="store_true",
        help="keep the temporary build/install directory for debugging",
    )
    args = parser.parse_args(argv)

    if args.keep:
        temp_dir = pathlib.Path(
            tempfile.mkdtemp(prefix="legalizer-wheel-smoke-")
        )
        cleanup = False
    else:
        _temp = tempfile.TemporaryDirectory(prefix="legalizer-wheel-smoke-")
        temp_dir = pathlib.Path(_temp.name)
        cleanup = True

    try:
        dist_dir = temp_dir / "dist"
        site_dir = temp_dir / "site"
        wheel = build_wheel(dist_dir)
        print(f"wheel: {wheel}", flush=True)
        install_wheel(wheel, site_dir)
        data_root = find_data_root(temp_dir, site_dir)
        if data_root is None:
            print(
                f"error: no share/legalizer-dsl with rules/{RULE_NAME} found under "
                f"{temp_dir}",
                file=sys.stderr,
            )
            return 2
        print(f"data: {data_root}", flush=True)
        checks = [
            ("strict check", run_strict_check(site_dir, data_root, temp_dir), 0),
            (
                "constructs check",
                run_module(site_dir, temp_dir, "legalizer_dsl.constructs", "check"),
                0,
            ),
            (
                "closure check",
                run_module(
                    site_dir,
                    temp_dir,
                    "legalizer_dsl.closure",
                    "check",
                    "--skip-evidence",
                    "--skip-tests",
                ),
                0,
            ),
            (
                "claims verify without evidence (expected clean failure)",
                run_module(site_dir, temp_dir, "legalizer_dsl.claims", "verify"),
                1,
            ),
        ]
        failed = False
        for name, rc, expected in checks:
            print(f"{name} exit code: {rc} (expected {expected})", flush=True)
            if rc != expected:
                failed = True
        return 1 if failed else 0
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        if cleanup:
            _temp.cleanup()
        else:
            print(f"kept: {temp_dir}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
