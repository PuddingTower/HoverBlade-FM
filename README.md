# HoverBlade-FM

> Hover-only rotor end-to-end generator with a **blunt trailing edge hard constraint** and
> **Figure of Merit (FM, 品质因数) maximisation**.

Give it three numbers — target thrust, rotor radius, minimum trailing-edge thickness — and it returns a
complete 10-section hover rotor blade: airfoil coordinates, chord and twist distributions, hover
blade-element-momentum (BEMT, 叶素动量) performance with FM, plots, an HTML report, an interactive blade
view and a manufacturing package (STEP / STL / DXF) with a manufacturing report.

```bash
uv sync
uv run hoverblade-fm generate --thrust-n 5000 --radius-m 2.5 \
  --trailing-edge-thickness-m 0.0025 --out output/run_001
```

or from an input file:

```bash
uv run hoverblade-fm generate examples/input_5000N.json --out output/run_001
```

```json
{ "thrust_n": 5000.0, "radius_m": 2.5, "trailing_edge_thickness_m": 0.0025 }
```

## What it guarantees

* **10 radial sections** at `r/R = 0.10 … 0.97` (the outer station stays below 1.0 so a tip cap can be
  lofted).
* **No sharp trailing edge, ever.** The trailing-edge gap (钝尾缘间隙) is an explicit CST degree of freedom
  and is projected onto `te_thickness_m >= trailing_edge_thickness_m` at decode time, so a sharp edge cannot
  be produced — a constraint by construction, re-checked by the test suite, by the CI assertion against the
  exported `sections.csv`, and by `check-manufacturing --strict`.
* **Hover only.** `forward_speed = 0`, `climb_rate = 0`.  FM is `P_ideal / P_actual` with
  `P_ideal = T^1.5 / sqrt(2 rho A)` and `A = pi R^2`.
* **Everything else is auto-resolved and explained.** ISA atmosphere, blade count, rotor speed from a tip-Mach
  limit (`M_tip <= 0.65` by default): each decision is written with its reason into `design.json` and
  `provenance.json`.
* **Optimised, not sampled.** CMA-ES global search plus SLSQP local refinement against a penalty objective
  (`-FM + thrust/te/smoothness/geometry penalties`), with the full evaluation history exported.
* **Open source, no closed-source core dependency.** NumPy / SciPy / Matplotlib / Pydantic / CMA-ES; the
  analytic airfoil polar (with Reynolds, Mach and blunt-edge drag corrections) needs no external solver.
  CAD export is an optional extra with a pure-Python fallback.
* **Reproducible.** Pin `--seed`; every run carries `provenance.json` (version, input hash, seed, resolved
  config).  Exit codes: `0` success, `2` invalid input, `3` infeasible design or failing strict manufacturing
  check, `4` internal error.

## Outputs

```text
output/run_001/
├── design.json            performance.json      provenance.json
├── sections.csv           sections/section_XX_r0.YYY.csv  (10 files, normalised + physical coords)
├── polars/                optimization_history.csv
├── plots/*.png            interactive/blade.html
├── cad/blade.step  blade.stl  master_sections.dxf  manufacturing_report.md
└── report.html
```

A real, committed run lives in [`output_examples/demo_5000N/`](output_examples/demo_5000N) (generated with
`--preset fast --seed 0`); its README states which artefacts the current build does not yet write.
Per-file and per-column documentation: [docs/outputs.md](docs/outputs.md).

## CLI

```text
hoverblade-fm generate [INPUT_JSON] [--thrust-n F] [--radius-m F] [--trailing-edge-thickness-m F]
                       --out DIR [--preset fast|standard|full|custom] [--seed N]
                       [--blade-count N] [--rpm F] [--tip-mach-limit F] [--rho F] [--mu F] [--a-sound F]
                       [--max-evaluations N] [--popsize N] [--no-optimize] [--no-cad] [--no-viz]
                       [--mesh-resolution coarse|standard|fine] [--quiet] [--json]
hoverblade-fm viz OUT_DIR [--interactive-only]
hoverblade-fm check-manufacturing OUT_DIR [--json] [--strict]
hoverblade-fm summary OUT_DIR [--json]
hoverblade-fm literature-search [--out docs/literature_search] [--limit N] [--offline] [--years 2022-2025]
hoverblade-fm version
```

Environment: `HOVERBLADE_FM_SEED` (default seed), `HOVERBLADE_FM_OFFLINE=1` (no network in the literature
helper).  Full flag semantics, presets and troubleshooting: [docs/user_guide.md](docs/user_guide.md).

## Development

```bash
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -q
uv run hoverblade-fm generate examples/input_5000N.json --out output/smoke --preset fast   # make smoke
```

`make install lint typecheck test smoke example docs dist` wraps the same commands.  Architecture, extension
points and the test strategy: [docs/developer_guide.md](docs/developer_guide.md).  Why the stack looks like
this: [docs/adr/](docs/adr/).  What must pass before a merge: [docs/quality_gates.md](docs/quality_gates.md).

Literature: [`scripts/literature_search.py`](scripts/literature_search.py) queries the arXiv Atom and Crossref
REST APIs with the检索词 of 计划.md 第 7.2 节, scores hits against the screening criteria of 第 7.4 节 and
writes [`docs/literature_search/raw.jsonl`](docs/literature_search/raw.jsonl) +
[`summary.csv`](docs/literature_search/summary.csv); the synthesis is
[docs/literature_review.md](docs/literature_review.md).  It runs standard-library-only and offline
(`--offline`), so CI never needs the network.

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) and
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).  Agents: work from
[`docs/agent/tasks.yaml`](docs/agent/tasks.yaml) following
[`docs/agent/execution_plan.md`](docs/agent/execution_plan.md).

## Disclaimer

This program produces aerodynamic shapes and manufacturing geometry.  It is **not** a flight-safety,
structural-strength or airworthiness certification tool; real use additionally requires structural, fatigue,
dynamics, manufacturing-inspection and test verification.  See [docs/disclaimer.md](docs/disclaimer.md)
(计划.md 第 28 节).

## License and citation

Code: Apache-2.0 ([LICENSE](LICENSE), third-party notices in [NOTICE](NOTICE)).  Documentation: CC-BY-4.0.
Example inputs and outputs: CC0-1.0.  If you use the tool, cite the repository and the works listed in
[docs/literature_review.md](docs/literature_review.md) — see [CITATION.cff](CITATION.cff).
Version 0.1.0; the first public release is tracked as task T100 in `docs/agent/tasks.yaml`.
