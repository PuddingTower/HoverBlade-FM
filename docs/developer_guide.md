# Developer guide

Architecture, extension points, testing and the development loop for HoverBlade-FM
(计划.md T092).  Read [blunt_trailing_edge.md](blunt_trailing_edge.md) before touching geometry
or the objective — the trailing-edge invariant is the one thing that may not regress.

---

## 1. Development loop

```bash
uv sync --extra dev          # interpreter + deps from uv.lock, nothing global installed
uv run pre-commit install    # optional: mirror the CI gates locally

uv run ruff check .          # lint
uv run ruff format .         # format (CI checks `--check`)
uv run mypy src              # types
uv run pytest -q             # all tests
uv run pytest -q -k trailing # the constraint tests only
uv run pytest -q --cov       # coverage

uv run hoverblade-fm generate examples/input_5000N.json --out output/dev --preset fast
uv run hoverblade-fm summary output/dev --json
uv run hoverblade-fm check-manufacturing output/dev --strict
```

`make` targets wrap the same commands (`make lint typecheck test smoke example docs`).

Useful knobs while developing:

| Knob | Effect |
|---|---|
| `--preset fast` | 24 × 320 evaluations, no local refinement — the interactive loop |
| `--no-optimize` | skip CMA-ES/SLSQP entirely; exercises geometry + aero + IO without a search |
| `--no-cad` / `--no-viz` | isolate the stage you are working on |
| `--seed N` | determinism; `HOVERBLADE_FM_SEED` sets the default |
| `HOVERBLADE_FM_OFFLINE=1` | no network for the literature helper |
| `-m "not slow and not cad"` | restrict pytest to the fast, kernel-free subset (markers are declared in `pyproject.toml`) |

## 2. Package layout

```text
src/hoverblade_fm/
├── models.py          shared dataclasses: Section, Design, Performance, ConstraintReport,
│                      OptimizationResult, Provenance, AirfoilPoints, SECTION_CSV_COLUMNS
├── cli.py             argparse surface, exit codes 0/2/3/4
├── pipeline.py        end-to-end orchestration: resolve -> optimise -> export
├── config/            schema.py (Pydantic, extra="forbid"), defaults.py (automatic strategies),
│                      units.py
├── geometry/          CST decode, explicit TE gap, hard clamp, spanwise distributions,
│                      section construction, geometric validity checks
├── aero/              hover BEMT loop, losses, analytic polar + Re/Mach/blunt-TE corrections,
│                      FM and power bookkeeping
├── optimize/          design-vector encode/decode, penalty objective, CMA-ES driver, SLSQP
│                      refinement, solution selection, history recording
├── io/                writers (CSV/JSON/provenance), readers, summary
├── viz/               matplotlib plots, single-page report.html, interactive/blade.html
├── cad/               section splines, loft, tip/root caps, STEP/STL/DXF, manufacturing checks
├── utils/             logging and small helpers
└── data/              packaged data (empty in 0.1.0, included in the wheel by hatch)
```

The dependency direction is one-way: `cli/pipeline` → `optimize` → `aero` → `geometry` →
`config` → `models`.  `io`, `viz`, `cad` sit at the far end and may read everything; nothing
they expose is imported by the solver path, and `cad` degrades to a pure-Python fallback when no
kernel is installed.

**Data contracts, not dictionaries.**  Modules exchange the dataclasses in `models.py`; a run's
serialisable form is produced by `io/`, so the on-disk schema and the in-memory schema stay
separable.  NumPy arrays carry point sets, Pydantic is used only at the user-facing validation
boundary (`config/schema.py`).

## 3. Where the invariants live

| Invariant | Enforced in | Re-checked by |
|---|---|---|
| `te_thickness_m >= trailing_edge_thickness_m` at all ten stations | trailing-edge clamp during CST decode (`geometry/`) | unit tests, CI `sections.csv` assertion, `check-manufacturing --strict` |
| exactly ten sections, outermost `r/R < 1` | `config/schema.resolve()` + `models.Design.__post_init__` | unit tests, CI artefact count |
| hover only (`forward_speed = climb_rate = 0`) | aero model construction | code review (no forward-flight input exists) |
| upper and lower surfaces never coincide at `x/c = 1` | trailing-edge construction | coordinate export tests |
| `FM = P_ideal / P_actual`, `P_ideal = T^1.5/sqrt(2 rho A)` | `aero/fm.py` | analytic-limit unit test (零阻力极限) |
| run reproducibility | seed threaded into CMA-ES | `provenance.json` (version, input hash, seed) |

If you change one of these, you change an ADR — see `docs/adr/`.

## 4. Extending

**New automatic strategy (e.g. an altitude-aware blade count).**  Add a pure function to
`config/defaults.py` returning an `AutoValue(value, reason)`; wire it into `resolve()` so the
reason string lands in `ResolvedConfig.reasons` and therefore in `provenance.json`.  Never fail
silently: a suspicious input becomes a warning in `feasibility_warnings`, a hard limit stays a
raise of `InputValidationError` (exit 2).

**New polar source.**  `aero/polar.py` is the seam.  A level-2 (built-in database), level-3
(XFOIL) or level-4 (CFD) backend must implement the same interface — Cl, Cd, Cm at `(alpha, Re,
M, geometry)` — and be selectable through config without touching the BEMT loop.  The analytic
model must remain the default so CI, the Docker image and a laptop without XFOIL behave
identically.

**New optimiser.**  `optimize/search.py` consumes the objective in `optimize/objective.py`
(`-FM + penalties`) and produces an `OptimizationResult` with a history.  Whatever you add:
keep decode-time feasibility (the projection, not the penalty, guarantees the edge), record every
evaluation into `optimization_history.csv`, and honour `--seed`.

**New output file.**  Add the writer in `io/` or `viz/`, extend `SECTION_CSV_COLUMNS` in
`models.py` if it is a column change, document it in [outputs.md](outputs.md), and update the
regression expectations — the output schema is a compatibility surface (计划.md 第 21.3 节).

**New CAD backend.**  `cad/backend.py` is the abstraction point (see ADR-003).  A backend must
either produce the artefacts or report unavailability so the pipeline falls back rather than
failing the run; `--no-cad` remains the escape hatch for users without the extra.

## 5. Testing strategy

```text
tests/unit/         fast, no optional dependencies; one file per module
tests/integration/  pipeline-level: resolve -> optimise (tiny budget) -> export -> read back
tests/golden/       sections_summary.csv, performance_summary.json, fm_baseline.json
```

Test requirements from 计划.md 第 21 节, and what they mean in practice:

* **unit** — input validation, unit conversion, CST coordinate generation, trailing-edge
  constraint, the fixed section count, the BEMT ideal limit, the FM formula, optimisation-history
  writing, CAD file generation (marked `cad`).
* **smoke** — `hoverblade-fm generate examples/input_5000N.json --out output/smoke`: exit 0, ten
  sections, `report.html`, `blade.step`, plausible FM, no constraint violation.
* **regression** — golden files: a new version must not lower FM materially, must not change the
  output schema silently, and must not reintroduce a sharp edge.
* **geometry focus** — the trailing-edge assertion is written per station, plus the
  `te_thickness_m > 1e-6` degenerate-edge guard (第 21.4 节).
* **aero focus** — zero-drag limit approaches the momentum-theory FM ceiling; increasing
  trailing-edge thickness must not *reduce* drag below non-physical values; thrust-matching error
  must be computable (第 21.5 节).

Markers (`pyproject.toml`): `slow` for full-budget optimisation runs, `cad` for kernel-dependent
tests.  Opt-in live-network test: `test_literature_search.py::test_live_search_smoke` runs only
with `HOVERBLADE_FM_LIVE_LITERATURE=1`, so the default suite is hermetic.

Write the failing test first for constraint work: a geometry change that cannot be covered by an
assertion on decoded sections is a change that will regress.

## 6. Conventions

* Python >= 3.11; `from __future__ import annotations`; type hints on public functions.
* Ruff config lives in `pyproject.toml` (`line-length = 110`, rules `E,F,I,UP,B,SIM`); do not add
  per-file ignores without a comment explaining why.
* Mypy runs over `src` only (`ignore_missing_imports = true` for `cma`, `cadquery`, `ezdxf`).
* Arrays are vectorised; loops over the ten stations are fine, loops over thousands of evaluations
  are not.
* No `print` in the library: use the shared logger in `utils/`; the CLI owns stdout (and `--json`
  must be the only thing on stdout in that mode).
* No hard-coded absolute paths; artefacts are resolved relative to `--out`.
* English docstrings; Chinese term in parentheses on first mention of a key concept
  (Figure of Merit 品质因数, blunt trailing edge 钝尾缘, hover 悬停).
* Deterministic behaviour by default: any randomness must be seedable and recorded.

## 7. Reproducing a reported number

```bash
uv run hoverblade-fm generate examples/input_5000N.json --out output/repro \
  --preset standard --seed 0
uv run python -c "import json;print(json.load(open('output/repro/provenance.json')))"
```

Check `program_version`, `input_hash`, `random_seed` and `optimization_settings` against the
report you are reproducing.  If `git_commit` is `unknown`, the run came from an sdist or a Docker
image — note it, since the wheel contents are the same but the provenance is weaker.

## 8. Release checklist

Maintainers only; see `CONTRIBUTING.md` §8 and `.github/workflows/release.yml`.

1. `CHANGELOG.md`: move `[Unreleased]` items under a dated `[X.Y.Z]` heading.
2. Bump `version` in `pyproject.toml` and `CITATION.cff` in the same commit; commit `uv.lock`.
3. Full local gate: `make lint typecheck test smoke example docs dist`.
4. Tag `vX.Y.Z` and push the tag → Release workflow builds, tests the wheel in a clean venv,
   regenerates the example output, and publishes the GitHub Release with wheel + sdist + the
   zipped example run (计划.md T097, T100).
5. Replace the placeholder DOI in `CITATION.cff` once the archived concept DOI exists.
