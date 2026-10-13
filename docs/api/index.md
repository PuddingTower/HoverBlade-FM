# API reference

Reference surface of the `hoverblade_fm` package (计划.md T093).  This page is maintained by
hand so that `mkdocs build --strict` works with only the `docs` extra installed; the docstring
source of truth is `src/hoverblade_fm/`, and anything not documented at module level is not part
of the supported API.

```python
import hoverblade_fm

hoverblade_fm.__version__  # "0.1.0"
```

Top-level exports: `SECTION_COUNT`, `DEFAULT_R_STATIONS`, `AirfoilPoints`, `Section`, `Design`,
`Performance`, `RadialLoad`, `ConstraintReport`, `OptimizationResult`, `Provenance`.

---

## `hoverblade_fm.models` — shared data contracts

Plain dataclasses (NumPy for point sets); every subsystem speaks them.

| Object | Purpose | Key members |
|---|---|---|
| `SECTION_COUNT = 10` | fixed section count | – |
| `DEFAULT_R_STATIONS` | `(0.10, 0.20, …, 0.90, 0.97)` | outermost station `< 1.0` so a tip cap can be lofted |
| `SECTION_CSV_COLUMNS` | column order of `sections.csv` | see [outputs.md](../outputs.md) §4 |
| `AirfoilPoints` | closed contour in chord coordinates (`x`: LE→TE, `y` up) | `te_thickness`, `te_midline`, `max_thickness`, `max_camber`, `surface_pairs()`, `trailing_edge_gap_at(x)`, `scale()`, `rotate()` (about `0.25 c`), `in_blade_frame()` |
| `Section` | one station: geometry + design point | `station_id`, `r_R`, `r_m`, `chord_m`, `twist_deg`, `te_thickness_m`, `max_thickness_m`, `thickness_to_chord`, `camber_m`, `le_radius_m`, `te_included_angle_deg`, `reynolds`, `mach`, `cl_design`, `cd_design`, `loading_n_m`, `alpha_effective_deg`, `points`, `airfoil_file`, `te_thickness_to_chord`, `to_row()` |
| `Design` | ten sections + operating point | `sections`, `radius_m`, `blade_count`, `rpm`, `omega`, `te_min_m`, `design_vector`, `collective_trim_deg`, `solidity`, `min_te_thickness()`, `te_margin()`, `is_blunt(tol)`, `contour_arrays()`; `__post_init__` enforces exactly ten sections sorted by `r_R` |
| `RadialLoad` | spanwise distributions from the solver | `r_R`, `thrust_gradient`, `torque_gradient`, `power_gradient`, `induced_velocity`, `inflow_angle_deg`, `aoa_deg`, `cl`, `cd`, `mach`, `reynolds`, `loss_factor` |
| `Performance` | hover performance record | thrust target/calc, `torque_nm`, `power_ideal_w`, `power_induced_w`, `power_profile_w`, `power_swirl_w`, `power_misc_w`, `power_actual_w`, `figure_of_merit`, `rpm`, `tip_mach`, `solidity`, `blade_count`, `tip_loss_factor`, `collective_trim_deg`, `radial`; properties `thrust_error`, `figure_power`; `to_dict()` |
| `ConstraintReport` | feasibility of a design | `te_min_required_m`, `te_min_actual_m`, `te_violation_m`, `te_included_angle_ok`, `thrust_error`, `geometry_valid`, `geometry_issues`, `smoothness_penalty`, property `feasible` (geometry valid **and** no TE violation **and** `|thrust_error| < 0.02` **and** TE angle ok) |
| `OptimizationResult` | best design + search history | `design`, `performance`, `constraints`, `initial_performance`, `initial_constraints`, `history`, `evaluations`, `strategy`, `seed` |
| `Provenance` | run provenance | program version, git commit, input hash, seed, timestamp, optimization settings, resolved config, interpreter/library versions |
| `stations_as_array(r_R)` | validate/normalize stations | raises unless exactly 10 |

## `hoverblade_fm.config` — input schema and automatic resolution

`hoverblade_fm.config.schema`

| Object | Purpose |
|---|---|
| `UserInput` | Pydantic model: `thrust_n`, `radius_m`, `trailing_edge_thickness_m` (or the unit-aware trio `thrust`, `radius`, `trailing_edge_thickness` + `units`), plus advanced overrides (`air_density_kg_m3`, `dynamic_viscosity_pa_s`, `sound_speed_m_s`, `altitude_m`, `blade_count`, `rpm`, `tip_mach_limit`, `r_stations`, `airfoil`, `solver`, `optimization`).  `extra="forbid"`.  Methods: `from_json()`, `from_dict()`, `input_hash()`, `raw_values()` |
| `AirfoilBounds` | `t/c` bounds, `te_over_chord_min/max` (0.002 / 0.08), TE included-angle bounds (6° / 45°), `camber_max_over_chord`, `le_radius_min_over_chord`, CST orders |
| `SolverSettings` | radial resolution, iteration limits, tolerance, relaxation, root extension, thrust tolerance, swirl recovery, `misc_power_w` |
| `OptimizationSettings` | `preset` (`fast`/`standard`/`full`/`custom` → popsize, evaluations, local refinement, restarts), `seed`, `popsize`, `max_evaluations`, `local_refine`, `restarts`, penalty weights `lambda_thrust` (100), `lambda_te` (1000), `lambda_smooth` (1), `lambda_geometry` (1000), `adaptive_penalty` |
| `ResolvedConfig` | everything downstream needs, SI units: `thrust_n`, `radius_m`, `te_min_m`, `rho`, `mu`, `a_sound`, `blade_count`, `omega`, `rpm`, `tip_mach`, `tip_mach_limit`, `r_stations`, `solidity_ref`, `airfoil`, `solver`, `optimization`, `reasons`, `warnings`, `input_hash`, `raw`; properties `disk_area`, `tip_speed`, `chord_min_m`, `chord_max_m`, `power_ideal_w`; `to_dict()` |
| `resolve(user_input)` | apply the automatic strategies; raises `InputValidationError` on a sharp-TE-impossible, out-of-range, or tip-Mach-violating configuration |
| `load_input(path, thrust_n, radius_m, te_min_m, overrides)` | JSON and/or CLI scalars; CLI wins |
| `InputValidationError` | the exception mapped to exit code 2 |

`hoverblade_fm.config.defaults`

| Function | Purpose |
|---|---|
| `atmosphere(altitude_m)` | ISA density / viscosity (Sutherland) / sound speed; `RHO_SL = 1.225`, `MU_SL = 1.81e-5`, `A_SOUND_SL = 340.29` |
| `auto_blade_count(radius_m, thrust_n, rho, candidates=(2,3,4))` | `AutoValue` with a disk-loading rationale |
| `auto_rotor_speed(radius_m, thrust_n, blade_count, rho, a_sound, tip_mach_limit=0.65, tip_speed_max=235, ct_min=0.005)` | `AutoValue` for `omega` from the tip-Mach cap, slowed if `CT` would be vanishingly small |
| `solidity_estimate(radius_m, blade_count)` | reference solidity of the initial blade |
| `feasibility_warnings(...)` | non-fatal remarks (disk loading, `CT`, TE-to-chord ratio) |
| `AutoValue` | `(value, reason)` pair; reasons are collected into `provenance` |

`hoverblade_fm.config.units` — `to_newtons`, `to_metres`, `rpm_to_omega`, `omega_to_rpm`.

## Solving and generating

The pipeline is driven by the CLI (`hoverblade-fm generate …`); the programmatic entry points
live in `hoverblade_fm.pipeline` and the stage packages:

| Package | Responsibility | Notes for callers |
|---|---|---|
| `geometry` | CST decode with an explicit trailing-edge gap, hard clamp, spanwise chord/twist interpolation, geometric validity | decode returns `AirfoilPoints`; the clamp is applied inside decode, so no caller can obtain a sharp edge from a valid design vector |
| `aero` | hover BEMT with Prandtl tip/root losses, analytic polar with Reynolds / Mach / blunt-TE drag terms, `P_ideal`, `P_actual`, FM | `fm.py` exposes the ideal-power and FM formulas used by both the solver and the tests |
| `optimize` | design-vector codec, penalty objective (`-FM + penalties`), CMA-ES global stage, SLSQP refinement, solution selection, history | needs `cma`; feasibility is decode-time, penalties are reporting |
| `io` | `design.json`, `performance.json`, `provenance.json`, `sections.csv`, section coordinate CSVs, polar CSVs, `optimization_history.csv`, readers, summary | the only place the on-disk schema is defined |
| `viz` | the five PNG plots, single-page `report.html`, `interactive/blade.html` | Matplotlib only; `--no-viz` skips it |
| `cad` | section splines, radial loft, tip/root caps, STEP / STL / DXF, manufacturing checks, `manufacturing_report.md` | optional kernel, pure-Python fallback (ADR-003); `--no-cad` skips it |
| `cli` | argparse surface, exit codes `0` success / `2` invalid input / `3` infeasible or strict-check failure / `4` internal error | `main(argv)` is importable for tests |

## `scripts/literature_search.py` (standard library only)

Not part of the installed package; importable by path for tests.

| Name | Purpose |
|---|---|
| `Record` | one literature hit with the screening columns of 计划.md 第 7.4 节 |
| `QUERIES`, `CHINESE_QUERIES` | the English query list (第 7.2 节 + extras) and the documented Chinese list |
| `SEED_DATA`, `seed_records()` | curated offline fallback set with hand-annotated screening columns |
| `arxiv_url`, `parse_arxiv_atom`, `crossref_url`, `parse_crossref_json` | transport-independent parsers (unit tested with fixtures) |
| `classify_method`, `classify_hover`, `classify_geometry`, `classify_open_source`, `classify_relevance`, `classify_borrow`, `auto_annotate` | keyword heuristics filling the 7.4 columns for live hits |
| `dedupe`, `in_year_window`, `sort_records` | DOI-then-title deduplication, year filtering, ordering |
| `SearchSession` | rate limiting, retries, circuit breaker |
| `write_raw_jsonl`, `write_summary_csv`, `read_existing_records` | outputs |
| `main(argv)` | CLI: `--out`, `--limit`, `--offline`, `--years`, `--include-seed`, `--no-merge`, `--quiet`; returns 0 / 2 / 4 |

---

## Phase 7 modules (planned-then-delivered enhancements)

### `hoverblade_fm.geometry.library` — built-in airfoil library (T082)

| Name | Purpose |
|---|---|
| `naca_four_digit(code, n_per_surface)` | section generated from the public-domain NACA four-digit formulae |
| `blunt_trailing_edge(points, te_over_c)` | converts any section (including a sharp NACA edge) into a blunt one |
| `load(name, te_over_c=None)`, `names()`, `available()` | registry access; loading can never return a sharp edge |
| `section_parameters_for(name)`, `library_summary()` | library entry expressed as CST parameters, and inventory/provenance for reports |

### `hoverblade_fm.aero.polar_cache` — polar caching (T083)

`CachingPolar` wraps any `PolarSource`; `Bucketing` fixes the grid the continuous local conditions
are snapped to, and `save`/`load` persist the table as JSON next to a run so a regression test never
re-invokes an external solver.

### `hoverblade_fm.aero.external` — optional external fidelity (T084, T090)

| Name | Purpose |
|---|---|
| `xfoil_executable()`, `XfoilPolar` | Level-3 polar: drives XFOIL when installed, falls back to the analytic polar and records why in `.status` |
| `prepare_cfd_case(design, out_dir, mesher=...)` | Level-4 preparation: writes one `.sur` per station plus an SU2 or OpenFOAM case template and the command it would run |

Neither adapter ever launches a CFD job; preparation and honest status reporting is the contract.

### `hoverblade_fm.optimize.surrogate` — surrogate and Bayesian search (T085, T086)

`GaussianProcess` (squared-exponential, Cholesky solve), `expected_improvement`, `bayesian_optimise`
and `surrogate_prescreen`.  Alternative to CMA-ES when evaluations are expensive; NumPy/SciPy only.

### `hoverblade_fm.aero.differentiable` — differentiable hover solver (T087)

`ReducedBlade`, `reduced_blade_from_design`, `solve_core(..., A=numpy|jax.numpy)`,
`solve_finite_difference`, `solve_differentiable`, `fm_gradient`, `adam_maximise_fm`, `backend_name`.
Thrust is trimmed inside the iteration and FM is referenced to the achieved thrust, so the surrogate
cannot be gamed into FM > 1.  JAX is optional; without it gradients come from central differences.

### `hoverblade_fm.geometry.generative` — conditional generator prototype (T089)

`Conditions`, `dataset`, `train`, `LatentModel` (PCA basis + ridge map), `sample`, `legality_report`.
Generated designs are decoded through the constrained decoder, so legality is inherited rather than
learned.  See [generative design feasibility](../generative_airfoil_feasibility.md) for the staged
path to a production model.
