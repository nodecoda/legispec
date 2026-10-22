#!/usr/bin/env python3
# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0.
# See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0
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
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
RULE_NAME = "toycpu.lspec"

CANONICAL_SCRIPTS = (
    "legispec",
    "legispec-claims",
    "legispec-constructs",
    "legispec-closure",
    "legispec-diff-oracles",
)


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
    # A stale build/lib tree can make bdist_wheel copy packages that no longer
    # exist in src/.  Start from a clean tree so the smoke proves the current
    # source, not a previous checkout.
    build_dir = REPO / "build"
    if build_dir.exists():
        shutil.rmtree(build_dir)
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
    """Locate the installed ``share/legispec`` tree.

    ``pip install --target`` places data-files in ``<target>/share`` on some
    schemes and beside the target on others, so accept either layout.
    """
    candidates = (
        temp_dir / "share" / "legispec",
        site_dir.parent / "share" / "legispec",
        site_dir / "share" / "legispec",
    )
    for candidate in candidates:
        if (candidate / "rules" / RULE_NAME).is_file():
            return candidate
    for base in (temp_dir, site_dir.parent):
        for path in sorted(base.rglob(RULE_NAME)):
            if path.parent.name == "rules" and path.parent.parent.name == "legispec":
                return path.parent.parent
    return None


def _installed_env(site_dir: pathlib.Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(site_dir)
    env.pop("LEGISPEC_ROOT", None)
    env.pop("LEGISPEC_OUT", None)
    env.pop("LEGISPEC_DATA", None)
    return env


def run_strict_check(
    site_dir: pathlib.Path, data_root: pathlib.Path, cwd: pathlib.Path
) -> int:
    rules = data_root / "rules" / RULE_NAME
    code = (
        "import sys; "
        "from legispec.cli import main; "
        f"sys.exit(main(['--rules', {str(rules)!r}, 'check', '--strict']))"
    )
    return run([sys.executable, "-c", code], cwd=cwd, env=_installed_env(site_dir))


def check_entry_points(site_dir: pathlib.Path) -> int:
    """Assert the wheel installs exactly the canonical commands."""
    manifests = sorted(site_dir.rglob("entry_points.txt"))
    if not manifests:
        print("error: no entry_points.txt in the installed wheel", file=sys.stderr)
        return 1
    text = manifests[0].read_text(encoding="utf-8")
    missing = [name for name in CANONICAL_SCRIPTS if f"{name} =" not in text]
    if missing:
        print(f"error: missing console scripts: {', '.join(missing)}", file=sys.stderr)
        return 1
    print(f"entry points: {len(CANONICAL_SCRIPTS)} canonical", flush=True)
    return 0


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
            tempfile.mkdtemp(prefix="legispec-wheel-smoke-")
        )
        cleanup = False
    else:
        _temp = tempfile.TemporaryDirectory(prefix="legispec-wheel-smoke-")
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
                f"error: no share/legispec with rules/{RULE_NAME} found under "
                f"{temp_dir}",
                file=sys.stderr,
            )
            return 2
        print(f"data: {data_root}", flush=True)
        checks = [
            ("console entry points", check_entry_points(site_dir), 0),
            ("strict check", run_strict_check(site_dir, data_root, temp_dir), 0),
            (
                "constructs check",
                run_module(site_dir, temp_dir, "legispec.constructs", "check"),
                0,
            ),
            (
                "closure check",
                run_module(
                    site_dir,
                    temp_dir,
                    "legispec.closure",
                    "check",
                    "--skip-evidence",
                    "--skip-tests",
                ),
                0,
            ),
            (
                "claims verify without evidence (expected clean failure)",
                run_module(site_dir, temp_dir, "legispec.claims", "verify"),
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
