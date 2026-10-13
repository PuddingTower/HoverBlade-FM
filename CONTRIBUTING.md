# Contributing to HoverBlade-FM

Thanks for being here.  HoverBlade-FM is an Apache-2.0 hover-only rotor generator
(悬停旋翼端到端生成器): three inputs — thrust, radius, minimum trailing-edge thickness —
and it produces ten blade sections, hover performance with Figure of Merit (FM, 品质因数),
plots, a report and a manufacturing package.  This guide covers how to make a change land.

For behaviour rules see [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md); for what counts as done
see [docs/quality_gates.md](docs/quality_gates.md).

## 1. Set up a development environment

[`uv`](https://docs.astral.sh/uv/) is the only required tool.

```bash
git clone https://github.com/hoverblade-fm/HoverBlade-FM.git
cd HoverBlade-FM
make install          # uv sync --extra dev  (also: uv run pre-commit install)
make lint typecheck test
```

Nothing in this repository needs a closed-source dependency in the core path.  The CAD
extra (`cadquery`, which needs the OpenCascade kernel) is optional and is pinned to
Python < 3.13 in `pyproject.toml`; core code paths must degrade to the pure Python
fallback when it is missing (see `docs/adr/0003-cad-kernel-optional-with-pure-python-fallback.md`).

```bash
uv run hoverblade-fm generate examples/input_5000N.json --out output/demo --preset fast
```

## 2. Branch and commit conventions

Branch names follow 计划.md 第 15.3 节, one branch per atomic task:

```text
task/T023-blunt-te-hard-constraint
task/T008-literature-search-script
feat/<scope>-<slug>        # changes not tied to a numbered task
fix/<scope>-<slug>
docs/<slug>
```

Commit messages follow Conventional Commits (计划.md 第 15.4 节); scope is the module
(`config`, `geometry`, `aero`, `optimize`, `io`, `viz`, `cad`, `docs`, `ci`, `test`):

```text
feat(geometry): add CST airfoil with blunt TE hard clamp
test(aero): add hover momentum theory validation
docs(literature): add search script and summary template
ci: add example-run smoke test and blunt-TE gate
```

One logical change per commit; the commit that changes behaviour carries its test.

## 3. The one rule that cannot break

**No sharp trailing edge, ever.**  The trailing-edge gap is an explicit CST degree of
freedom and is clamped at decode time so `te_thickness_m >= trailing_edge_thickness_m`
for every one of the ten sections.  A change that can produce a thinner edge — even for
one station, even for an "infeasible" input — does not merge.  This is enforced in three
places, all of which must stay green:

1. unit tests in `tests/unit/` (geometry constraint coverage, T032);
2. the CI assertion step that reads the produced `sections.csv` and compares
   `min(te_thickness_m)` against the requested input;
3. `hoverblade-fm check-manufacturing output/<run>` with `--strict`.

## 4. Definition of done

Before opening a pull request, run the local gate (also documented in
`docs/quality_gates.md`, 计划.md 第 23 节):

```bash
make lint        # ruff check .
make format      # ruff format --check .
make typecheck   # mypy src
make test        # pytest -q
make smoke       # pytest -m "not slow and not cad"
make example     # fast end-to-end run into output/example
```

Additionally:

* every new behaviour has a test, every fixed bug has a regression test;
* user-visible changes update `docs/user_guide.md` / `docs/outputs.md` and `CHANGELOG.md`;
* an architectural decision (parameterization, solver, optimiser, CAD kernel, output
  schema) gets an ADR in `docs/adr/` before the code merges;
* outputs stay reproducible: a run is described by `provenance.json` (version, input
  hash, seed, resolved config), so pin `--seed` when you report numbers;
* no secrets, no absolute local paths, no binary artefacts committed to `output/`
  (large artefacts belong in Releases, 计划.md 第 22.2 节).

## 5. Pull requests

Use the template in `.github/PULL_REQUEST_TEMPLATE.md`.  Reference the task id, e.g.
`Closes #14 (T023)`.  If you are an agent working the machine-readable list, read
`docs/agent/tasks.yaml` and `docs/agent/execution_plan.md` first; the `dod` field is the
acceptance test, not the prose.

CI must be green before review.  If CI fails for an environmental reason (for instance
no network access for `scripts/literature_search.py`), say so in the PR body and link the
run — do not re-run until green to hide it.

## 6. Style

* Python >= 3.11, type hints on public functions, `from __future__ import annotations`.
* Ruff is the formatter and linter (`line-length = 110`); do not hand-format against it.
* NumPy arrays for point sets; Pydantic only at the user-facing validation boundary.
* Keep `scripts/literature_search.py` standard-library only — it must run on a checkout
  without dependencies installed.
* English for code, docstrings and docs, with the Chinese term in parentheses on first
  use for key concepts (Figure of Merit 品质因数, blunt trailing edge 钝尾缘, hover 悬停).

## 7. Reporting issues

Use the issue templates (`bug_report`, `feature_request`, `task_execution`).  A bug
report is actionable when it contains: the input JSON or the three flags, the full CLI
line, the `--out` directory's `provenance.json`, the exact command used to reproduce, and
the observed vs. expected behaviour.  For anything that looks like a trailing-edge or
geometry violation, attach `sections.csv` — that is the file CI asserts on.

## 8. Release process

Releases are cut from tags `vX.Y.Z` by `.github/workflows/release.yml`, which builds
sdist + wheel with `uv build`, runs the test suite and attaches artefacts to a GitHub
Release.  Only maintainers tag; the changelog entry and `CITATION.cff` version must be
updated in the same PR as the version bump (`pyproject.toml`).
