# Release checklist

This document is the operational companion to the release gate matrix in
`docs/CLOSURE.md` and the governance rules in `GOVERNANCE.md`.

Release policy: source-first GitHub release, optional PyPI publication after
TestPyPI validation. A failed gate blocks the tag; do not "fix" a gate by
weakening a check.

## 0. One-time repository setup

Replace every `<org>` placeholder before the first public push:

```text
README.md
README.zh-CN.md (if it contains repository links)
pyproject.toml
SECURITY.md
.github/CODEOWNERS
.github/ISSUE_TEMPLATE/config.yml
```

Then configure the GitHub repository:

- Description, topics, website, and social preview.
- `main` ruleset: require a pull request, dismiss stale approvals, block force
  pushes, require linear history if the project uses rebase/squash merges.
- Required checks: the four `test (...)` matrix jobs, `codegen contract`,
  `mutation controls`, `corpus coverage ratchet`, `release hygiene`, and `dco`.
- Enable secret scanning, push protection, Dependabot alerts, and private
  vulnerability reporting.
- Protect the `v*` tag pattern and the `pypi` environment (if PyPI is enabled).
- Enable the repository `dco` check (`.github/workflows/dco.yml`) or install
  the GitHub DCO app; the workflow checks every non-merge PR commit.

## 1. Pre-flight

```bash
git status --short
git ls-files | grep -E '^(AGENTS\.md|Status|\.codex/|out/)' && exit 1
python scripts/check_release_hygiene.py
python -m unittest discover -s tests
python scripts/check_all_rules.py
python scripts/check_codegen_contract.py
python scripts/check_skips.py
```

Expected:

- `git status --short` is empty.
- No local agent configuration, `Status`, `.codex/`, or `out/` is tracked.
- 400 tests pass (`OK (skipped=1)` is expected without local evidence).
- The hygiene gate reports every tracked file clean.

## 2. Evidence gate

```bash
export LEGISPEC_LLVM_SRC=/path/to/llvm-project
export LEGISPEC_LLVM_BUILD=/path/to/llvm-build
export LEGISPEC_TOYCPU_BUILD=/path/to/llvm-build-toycpu
python scripts/refresh_evidence.py --all --require-all
legispec-claims verify --verify-corpus
legispec-closure check
```

Expected:

```text
claims=43 proven=43 failed=0
claims=43/43 hand=27 generated=16 rules-files=22
hand-queries=321627 constructs=63 tests=400 problems=0
```

If only the evaluator fingerprint changed, `refresh_evidence.py` must be run
once for the whole batch; do not update `claims.json` by hand. If rules or
oracles changed, rebuild them first and record the LLVM commit.

## 3. Package gate

```bash
python -m pip install -e ".[dev]"
python -m build
twine check dist/*
python scripts/wheel_smoke.py
```

Inspect the sdist and wheel:

```bash
python -m zipfile -l dist/legispec-1.0.0-py3-none-any.whl
tar -tf dist/legispec-1.0.0.tar.gz
```

Expected contents include `rules/`, `witness/`, `capabilities/`, `claims/`,
the installed `docs/` subset, `LICENSE.TXT`, `NOTICE`, `README.md`, and
`CHANGELOG.md`. Install the wheel into a clean virtual environment and run:

```bash
legispec --help
legispec --rules rules/toycpu.lspec check --strict
```

## 4. Tag and GitHub Release

Use a signed tag when a signing key is configured; otherwise use an annotated tag
and publish checksums.

```bash
git tag -s v1.0.0 -m "LegiSpec 1.0.0"
git push origin main
git push origin v1.0.0
```

The `release` workflow builds the sdist/wheel, checks metadata, runs the wheel
smoke test, verifies claims and closure when the evidence environment is
available, writes `SHA256SUMS`, and creates the GitHub Release.

Release assets:

- `legispec-1.0.0.tar.gz`
- `legispec-1.0.0-py3-none-any.whl`
- `SHA256SUMS`
- `out/closure.json`
- `claims/claim-ledger.md`
- a small evidence summary

Do **not** upload the whole `out/` directory (hundreds of MB) or the oracle
binaries. Document how to rebuild them from `docs/EVIDENCE.md`.

## 5. PyPI (optional, second step)

1. Publish `1.0.0` to TestPyPI with Trusted Publishing.
2. Install from TestPyPI in a clean venv and run the smoke commands.
3. Create a protected `pypi` environment and publish through
   `pypa/gh-action-pypi-publish` with OIDC. Do not store a long-lived token.
4. Verify the PyPI page renders `README.md`, `LICENSE.TXT`, and `NOTICE`.

## 6. Post-release

- Verify the Release assets and `SHA256SUMS`.
- Confirm the Release notes match the claim/test/integration numbers.
- Watch CI, issues, security advisories, and Dependabot for 48 hours.
- Archive old branches as `archive/<name>` tags before deleting them.
- For a bad release, yank the PyPI version and mark the GitHub Release as a
  pre-release; do not rewrite the tag or the evidence ledger.
