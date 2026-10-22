#!/usr/bin/env python3
# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0.
# See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0
#
# ===----------------------------------------------------------------------===##

"""Release hygiene gate for the public repository.

This check catches the mistakes that are cheap to prevent and expensive to
publish: local agent configuration, build artifacts, absolute home/build
paths, and missing SPDX headers.  It scans only tracked files, so local caches
and evidence directories never need to be cleaned up first.

Exit codes: 0 = clean, 1 = hygiene problems, 2 = the check could not run.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# These must never be tracked.  They are either local agent state or build
# output; a public clone has no use for any of them.
FORBIDDEN_TRACKED = ("AGENTS.md", "Status", "out/", ".codex/", ".omx/")

# Tests intentionally construct Windows paths as fixtures.  They are not
# release documentation and never describe a real developer checkout.
ALLOW_PREFIXES = ("tests/",)

SOURCE_SUFFIXES = {".py", ".ps1", ".sh", ".cmake"}

ABSOLUTE_PATH_PATTERNS = (
    (re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]"), "local Windows path"),
    (re.compile(r"(?<![A-Za-z0-9])/(?:home|Users)/[^/\s]+/"), "local home path"),
)


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip())
    return [part for part in result.stdout.decode("utf-8").split("\0") if part]


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def main() -> int:
    try:
        files = tracked_files()
    except (OSError, RuntimeError) as exc:
        print(f"error: cannot list tracked files: {exc}", file=sys.stderr)
        return 2

    problems: list[str] = []
    for rel in files:
        if rel in FORBIDDEN_TRACKED or any(
            rel.startswith(prefix) for prefix in FORBIDDEN_TRACKED if prefix.endswith("/")
        ):
            problems.append(f"{rel}: local/build artifact must not be tracked")

        path = ROOT / rel
        if not path.is_file():
            continue
        text = read_text(path)
        if text is None:
            continue

        if path.suffix in SOURCE_SUFFIXES:
            header = "\n".join(text.splitlines()[:12])
            if "SPDX-License-Identifier: Apache-2.0" not in header:
                problems.append(f"{rel}: missing SPDX-License-Identifier header")

        if any(rel.startswith(prefix) for prefix in ALLOW_PREFIXES):
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            for pattern, label in ABSOLUTE_PATH_PATTERNS:
                match = pattern.search(line)
                if match:
                    problems.append(
                        f"{rel}:{line_number}: {label}: {match.group(0)}"
                    )

    if problems:
        print("release hygiene check failed:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print(f"release hygiene: {len(files)} tracked files clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
