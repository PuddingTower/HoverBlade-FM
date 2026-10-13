# HoverBlade-FM

**Hover-only rotor end-to-end generator** (悬停旋翼端到端生成器) with a **blunt trailing edge
hard constraint** (钝尾缘硬约束) and **Figure of Merit maximisation** (品质因数最大化).

You give it three numbers:

| Input | Field | Unit |
|---|---|---|
| Target thrust (per rotor) | `thrust_n` | N |
| Rotor radius | `radius_m` | m |
| Minimum trailing-edge thickness | `trailing_edge_thickness_m` | m |

It resolves everything else automatically (ISA atmosphere, blade count, rotor speed from a
tip-Mach limit), generates **10 radial sections** at `r/R = 0.10 … 0.97` parameterised with
CST that carries an explicit trailing-edge gap, evaluates hover performance with a
blade-element-momentum solver, maximises

```text
FM = P_ideal / P_actual ,   P_ideal = T^1.5 / sqrt(2 rho A) ,   A = pi R^2
```

with CMA-ES plus an SLSQP refinement, and writes data, plots, an HTML report, an interactive
`blade.html` and STEP / STL / DXF manufacturing files with a manufacturing report.

## Quick start

```bash
uv sync
uv run hoverblade-fm generate examples/input_5000N.json --out output/demo
```

or without an input file:

```bash
uv run hoverblade-fm generate --thrust-n 5000 --radius-m 2.5 \
  --trailing-edge-thickness-m 0.0025 --out output/demo
```

## What the tool guarantees

* **Ten sections, always.** `r/R = 0.10, 0.20, … 0.90, 0.97`; the last station stays below
  1.0 so a tip cap can be lofted beyond it.
* **No sharp trailing edge, ever.** The trailing-edge gap is an explicit CST degree of
  freedom, hard-clamped at decode time so `te_thickness_m >= trailing_edge_thickness_m`;
  the CI assertion step re-checks the exported `sections.csv`.
* **Hover only.** `forward_speed = 0`, `climb_rate = 0`.
* **Open source, no closed-source core dependency.** NumPy / SciPy / Matplotlib / Pydantic /
  CMA-ES; the analytic polar needs no external solver.  CAD export is an optional extra with
  a pure-Python fallback.
* **Reproducible.** Every run writes `provenance.json` (program version, input hash, seed,
  resolved configuration).

## Where to read next

| I want to… | Go to |
|---|---|
| run the tool, understand flags, outputs, exit codes | [User guide](user_guide.md) |
| know exactly what each produced file contains | [Output reference](outputs.md) |
| understand the trailing-edge rule and its checks | [Blunt trailing edge](blunt_trailing_edge.md) |
| hack on the code | [Developer guide](developer_guide.md) + [API reference](api/index.md) |
| know why the stack looks like this | [ADR-001](adr/0001-cst-plus-bemt-plus-cmaes.md), [ADR-002](adr/0002-blunt-te-as-hard-geometric-constraint.md), [ADR-003](adr/0003-cad-kernel-optional-with-pure-python-fallback.md) |
| check what has to pass before a merge | [Quality gates](quality_gates.md) |
| see what the literature says | [Literature review](literature_review.md) |
| drive the build as an agent | [Agent execution plan](agent/execution_plan.md) + [`tasks.yaml`](agent/tasks.yaml) |
| know what this tool is *not* | [Disclaimer](disclaimer.md) |

## Repository map

```text
src/hoverblade_fm/   config, geometry, aero, optimize, io, viz, cad, cli, pipeline
examples/            input_120N.json, input_5000N.json, input_20000N.json
docs/                this site, ADRs, literature material, agent task list
scripts/             literature_search.py (standard library only)
tests/               unit/, integration/, golden/
output_examples/     curated sample run (committed; regenerated at release time)
.github/workflows/   ci.yml, docs.yml, release.yml
```

## Licence and citation

Apache-2.0 (code), CC-BY-4.0 (documentation), CC0-1.0 (example data) — see `LICENSE`,
`NOTICE` and `CITATION.cff`.  Please cite the repository *and* the works listed in
[literature_review.md](literature_review.md).

HoverBlade-FM outputs aerodynamic shapes and manufacturing geometry.  It is not a
flight-safety, structural-strength or airworthiness certification tool
([disclaimer](disclaimer.md)).
