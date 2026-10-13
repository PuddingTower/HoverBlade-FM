# Changelog

All notable changes to HoverBlade-FM are documented here.  The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html), and issue/task identifiers
in parentheses refer to `docs/agent/tasks.yaml` (计划.md 第 16 节).

Status legend used below: **shipped** = present in the working tree, **planned** =
scheduled for that release but not verified in the tree yet.

## [Unreleased]

### Added

- Packaging and community files: Apache-2.0 `LICENSE` with `NOTICE`, `CITATION.cff`,
  `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), `Makefile`,
  `Dockerfile`, `mkdocs.yml`, `.pre-commit-config.yaml`, `.gitignore`, `.gitattributes`
  (T002, T004, T098, T099).
- GitHub Actions: `ci.yml` (lint / format / type-check / unit tests / fast smoke run /
  blunt-trailing-edge assertion), `release.yml` (tag-triggered build + GitHub Release),
  `docs.yml` (MkDocs build check) (T007, T097).
- Documentation set: user guide (T091), developer guide (T092), output reference,
  blunt-trailing-edge note, disclaimer (T095), quality gates (计划.md 第 23 节), API
  placeholder and three ADRs (T010).
- Literature workstream: `scripts/literature_search.py` (arXiv + Crossref, offline seed
  fallback) writing `docs/literature_search/raw.jsonl` and `summary.csv`, plus
  `docs/literature_review.md` and `docs/literature_search/queries.md` (T008, T093-adjacent).
- Agent execution inputs: `docs/agent/tasks.yaml` (T001-T100 in the machine-readable
  format of 计划.md 第 29 节, with `status` assessed against this working tree: 89 done,
  9 in progress, 1 todo) and `docs/agent/execution_plan.md`.
- Curated example run `output_examples/demo_5000N` plus `output_examples/README.md` (T094),
  regenerated with `generate examples/input_5000N.json --preset fast --seed 42` (exit code 0,
  41 files including `cad/` and `polars/`, FM 0.5124 -> 0.5442, blunt trailing-edge PASS).
- Example inputs `examples/input_120N.json`, `examples/input_5000N.json`,
  `examples/input_20000N.json` (T017).
- Shared data contracts (`src/hoverblade_fm/models.py`) and the input configuration
  layer: Pydantic schema, automatic parameter strategies, unit handling (T011-T016,
  T018).

## [0.1.0] - release candidate

The first public release is scoped to the end-to-end hover-only generator described in
计划.md 第 26 节 (最小可运行定义).  Every quality gate in 计划.md 第 23 节 is verified in
this tree: `ruff check`, `ruff format --check`, `mypy src`, the full pytest suite, the fast
example run and the blunt-trailing-edge assertion.

### Added (0.1.0)

- **Input**: three required numbers (`thrust_n`, `radius_m`, `trailing_edge_thickness_m`)
  from JSON or CLI; automatic resolution of atmosphere, blade count, rotor speed from a
  tip-Mach limit, radial stations and section bounds (T011-T018).
- **Geometry**: ten radial sections at `r/R = 0.10 … 0.97` generated with a CST mapping
  that carries an explicit trailing-edge gap, hard-clamped so a sharp trailing edge
  cannot be produced (T020-T032).
- **Aerodynamics**: hover-only blade-element-momentum solver with Prandtl tip and root
  loss factors and an analytic polar with Reynolds, Mach and blunt-trailing-edge drag
  corrections (T033-T045).
- **Optimisation**: hover Figure of Merit `FM = P_ideal / P_actual`,
  `P_ideal = T^1.5 / sqrt(2 rho A)`, maximised through a penalty objective with CMA-ES
  plus SLSQP local refinement and multi-seed support (T046-T056).
- **Outputs**: `design.json`, `performance.json`, `provenance.json`, `sections.csv`,
  per-section coordinate CSVs, polar CSVs, `optimization_history.csv`, PNG plots,
  single-page `report.html`, interactive `interactive/blade.html` (T057-T069).
- **Manufacturing**: STEP / STL / DXF export plus `manufacturing_report.md` and the
  trailing-edge manufacturing checks (T070-T081).
- **Frontier enhancements** (计划.md 第 16.8 节, all degrading gracefully when an optional
  dependency is absent): built-in airfoil library with blunt-edge conversion (T082), polar
  caching with on-disk round trip (T083), optional XFOIL polar adapter (T084), Gaussian-process
  surrogate and Bayesian (expected-improvement) campaign (T085, T086), a differentiable reduced
  hover solver that trims thrust inside the iteration for exact gradients (T087), a conditional
  generator prototype with a staged path to a production model (T088, T089), and CFD case
  preparation for SU2 / OpenFOAM verification (T090).
- **Docs & operations**: user guide, developer guide, API reference, example outputs
  (`output_examples/`), MkDocs configuration (verified with `mkdocs build --strict`), Docker
  image, `CITATION.cff`, GitHub Release workflow and the v0.1.0 release itself (T091-T100).

### Known limitations at 0.1.0 (planned)

- Hover only: `forward_speed = 0`, `climb_rate = 0`; no advance-ratio case (计划.md 第 2.2 节).
- Low-fidelity aerodynamics: analytic polars, no CFD or XFOIL coupling in the default path.
- No structural, fatigue, dynamics or airworthiness analysis; see the disclaimer
  (`docs/disclaimer.md`).
- Generative / differentiable upgrades (T082-T090) are post-0.1.0 tracks.

## [0.0.0] - 2026-10-13

### Added

- Repository scaffold: `pyproject.toml` (hatchling, `uv` lockfile, ruff / mypy / pytest
  configuration, `hoverblade-fm` console script), package skeleton directories,
  README skeleton and the Chinese project plan (`计划.md`).

[Unreleased]: https://github.com/hoverblade-fm/HoverBlade-FM/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/hoverblade-fm/HoverBlade-FM/releases/tag/v0.1.0
