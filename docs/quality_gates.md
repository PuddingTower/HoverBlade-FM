# Quality gates (质量门槛)

计划.md 第 23 节 lists ten conditions that must hold **for every merge**.  This page maps each
one to the command or CI step that proves it, so "passed the gates" is a checkable statement
rather than an opinion.

Run everything locally with:

```bash
make install lint typecheck test smoke example docs
```

| # | Gate (计划.md 第 23 节) | Command | CI job / step |
|---|---|---|---|
| 1 | `ruff` passes | `uv run ruff check .` | CI *quality* → "Ruff lint" |
| 1b | formatting is canonical | `uv run ruff format --check .` | CI *quality* → "Ruff format check" |
| 2 | `mypy` passes | `uv run mypy src` | CI *quality* → "Mypy" |
| 3 | `pytest` passes | `uv run pytest -q` | CI *quality* → "Pytest (unit + integration, coverage)" |
| 4 | the example runs | `uv run hoverblade-fm generate examples/input_5000N.json --out output/ci_demo --preset fast` | CI *smoke* → "Fast example run" + "Required artefacts exist" |
| 5 | **trailing-edge constraint test passes** | unit test in `tests/unit/test_geometry.py` **and** the `sections.csv` assertion | CI *smoke* → "Blunt trailing-edge assertion on sections.csv"; `make check-te` |
| 6 | output files complete | existence checks on `design.json`, `performance.json`, `provenance.json`, `sections.csv`, ten `sections/*.csv`, `report.html` | CI *smoke* → "Required artefacts exist"; CAD artefacts in CI *cad-extra* |
| 7 | documentation updated | reviewed in the PR; `docs.yml` builds the site with `--strict` so broken links fail | Docs workflow → "Build" |
| 8 | no closed-source core dependency | dependency review at review time; runtime core = NumPy / SciPy / Matplotlib / Pydantic / CMA-ES; CAD kernel is an optional extra with a pure-Python fallback | CI *quality* syncs `--extra dev` only; the `cad` extra lives in a separate, non-blocking job |
| 9 | no secrets | `pre-commit` hook `detect-private-key`, plus reviewer check; `.gitignore` excludes `.env*` | local hooks; manual review |
| 10 | release build passes | `uv build` plus a wheel smoke test | Release workflow → "Build sdist and wheel", "Smoke-test the built wheel in a clean venv" |

## The hard gate: no sharp trailing edge

Gate 5 is the project's defining invariant (计划.md 第 5.1 节), so it is asserted three
independently-written ways:

1. **Unit level** — a generated design whose sections are decoded from an arbitrary design
   vector must satisfy `te_thickness_m >= trailing_edge_thickness_m` at every station
   (`tests/unit/test_geometry.py`).
2. **Artefact level (CI)** — after a real `generate` run, `output/ci_demo/sections.csv` is read
   back from disk and checked:

   ```python
   te = [float(row["te_thickness_m"]) for row in rows]
   assert len(rows) == 10
   assert min(te) >= required - 1e-12
   assert min(te) > 1e-6  # no degenerate edge
   ```

   The check reads the *file*, not the in-memory object, so an export bug cannot hide a
   violation.
3. **Release level** — the same assertion runs against the wheel installed in a clean venv
   before a GitHub Release can be created.

An agent or human change that breaks any of the three cannot merge.

### Known discrepancy in the current development tree

On the committed example (`output_examples/demo_5000N`, regenerated with `--preset fast --seed 0`) the three
layers do **not** agree, and this is a real finding rather than a CI artefact:

* `sections.csv` reports `te_thickness_m = 0.0025` exactly, so the CI artefact gate and the wheel gate pass;
* `check-manufacturing` measures the thinnest edge from the exported contour coordinates as
  `2.49984 mm`, i.e. a margin of `-1.6e-4 mm` against the requested `2.5 mm`, and therefore reports
  `te_manufacturability_flag: false`; with `--strict` the command exits **3**;
* the same check reports `te_included_angle_band_ok: false` (several outboard stations fall below the 6°
  floor of 计划.md 第 5.4 节).

Both effects are sub-millimetre rounding or aft-section quality issues rather than a genuinely sharp edge,
but they must be reconciled before release (tasks `T079`, `T024` in `docs/agent/tasks.yaml`): either the clamp
keeps a small positive margin, or the coordinate export keeps enough precision that a measurement of the
written contour reproduces the requested thickness.

## Additional checks carried by CI

| Check | Where | Why |
|---|---|---|
| `FM` within a plausible range and thrust match within 2 % | CI *smoke* → "FM and thrust consistency" | guards against an aero or trim bug producing a physically wrong "good" design |
| Literature helper runs with no network | CI *quality* → "Literature retrieval runs offline" | the documentation build must not depend on arXiv / Crossref availability |
| CAD artefacts (`blade.step`, `blade.stl`, `master_sections.dxf`, `manufacturing_report.md`) | CI *cad-extra* (non-blocking, `continue-on-error`) | the `cad` extra is optional and pinned to Python < 3.13, so its failure must not block core merges |
| `uv.lock` matches `pyproject.toml` | every `uv sync --frozen` / `uv sync` in CI | dependency drift is caught at the install step |

## Coverage

计划.md 第 24.2 节 asks for "at least 80 % coverage of the core modules".  Coverage is produced
by `uv run pytest -q --cov` (CI artefact `coverage.xml`) and reported in the PR; it is a review
target rather than an enforced numeric threshold in CI, so that a legitimately small new module
cannot be merged untested only because the project total still looks fine.

## Review checklist for agents

Before marking a task `done` in `docs/agent/tasks.yaml`:

- [ ] every `dod` item of that task has an artefact or test name attached to it;
- [ ] the gates table above is green locally;
- [ ] CI on the branch is green (or the failure is explained in the PR);
- [ ] docs touched if behaviour or output schema changed;
- [ ] `CHANGELOG.md` entry added;
- [ ] no new runtime dependency outside the permissive core set;
- [ ] a run's `provenance.json` reproduces with the recorded seed.
