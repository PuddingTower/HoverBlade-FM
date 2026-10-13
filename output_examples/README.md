# Example outputs (示例输出, 计划.md T094)

One committed, fully reproducible run so the output contract can be inspected without executing
anything.  Everything here was produced by this repository's own code — nothing is hand-written or
placeholder.

## The command

```bash
uv run hoverblade-fm generate examples/input_5000N.json \
  --out output_examples/demo_5000N --preset fast --seed 0 --no-cad
```

* exit code `0`
* wall time ≈ 20 s on a desktop CPU (CMA-ES `fast` preset: population 24, 320 evaluation budget, 337
  evaluations used, no SLSQP polish)
* blunt trailing-edge assertion: **PASS** — `min(te_thickness_m) = 0.0025 m >= 0.0025 m` requested
* resolved operating point: 4 blades, 778.8 RPM, tip Mach 0.599 (limit 0.65), ISA sea level

The resolved configuration, the automatic-strategy reason strings and the seed are inside the run
(`design.json`, `provenance.json`), so the numbers in `performance.json` can be regenerated with the
command above.

## Contents of `demo_5000N/`

```text
design.json  performance.json  provenance.json  constraints.json  optimization_summary.json
sections.csv            10 rows x 18 columns
sections/               10 contour CSVs + index.json (coordinate conventions)
optimization_history.csv  one row per evaluation
plots/                  chord_twist, airfoil_stack, trailing_edge_zoom, fm_convergence,
                        radial_performance, sections_preview  (PNG)
interactive/blade.html  rotatable view, per-section toggle, TE-thickness colour map
report.html             single-page summary with inlined plots
```

Field-by-field meaning: [../docs/outputs.md](../docs/outputs.md).

## What is deliberately absent, and why

| Missing | Reason |
|---|---|
| `cad/blade.step`, `cad/blade.stl`, `cad/master_sections.dxf`, `cad/manufacturing_report.md` | produced with `--no-cad` here, because the CAD stage in the working tree currently fails part-way through the package: the DXF writer asks `ezdxf` for an `LWPOLYLINE` inside an R12 document and the run aborts with exit code 4.  STEP/STL themselves were observed to be written before that failure.  Tracked as tasks T077/T080 in [`docs/agent/tasks.yaml`](../docs/agent/tasks.yaml). |
| `polars/section_XX_polar.csv` | the per-station polar export of 计划.md 第 3.3 节 is not implemented yet (T039); the used design-point values are in `sections.csv` (`cl_design`, `cd_design`, `reynolds`, `mach`) |

Re-run **without** `--no-cad` to reproduce the CAD failure; that is the current state of the tree, not a
packaging accident of this example.

## An observation worth repeating for reviewers

`optimization_summary.json` reports `best_objective = -0.54740` (evaluation 325) while the exported design's
`figure_of_merit` is `0.51237`, equal to the initial reference design.  In this run the selection stage did not
carry the best-scanned candidate through to export, so the committed example demonstrates the *reference*
design plus a full search history rather than a demonstrably improved FM.  This is a real behaviour of the
working tree at the time of writing and is tracked under T055/T056; do not cite this example's FM as evidence
that the optimiser improves hover performance.

## Curating rules

* keep one example run per input case at most, regenerated at release time by `release.yml`
  (which also zips a `release_example/` bundle as a Release asset);
* do not commit large binaries here: STEP/STL and other artefacts belong to the GitHub Release
  (计划.md 第 22.2 节);
* regenerate with `--seed 0` so the committed artefacts and the numbers in the documentation agree.
