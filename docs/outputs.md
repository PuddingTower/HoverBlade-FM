# Output reference

Every artefact a `generate` run writes under `--out DIR`, field by field.  The tree is sketched in
the user guide (§5); this page is the schema reference (计划.md 第 3.3 – 3.5 节, 第 12.3 节, 第 13.3 节).

Schemas below were read from a verified run in this working tree:

```bash
uv run hoverblade-fm generate examples/input_5000N.json --out output_examples/demo_5000N \
  --preset fast --seed 0          # exit code 0, blunt trailing edge PASS
```

That run is committed under `output_examples/demo_5000N/`.  Line
endings are pinned by `.gitattributes` (CSV/JSON = UTF-8, LF) so the regression tests can compare bytes.

**Status note (development tree).**  The plan's output contract (第 3.3 节) also includes `polars/`, the
`cad/` package (`blade.step`, `blade.stl`, `master_sections.dxf`, `manufacturing_report.md`,
`manifest.json`) and a top-level `report.html`.  All of them are produced by a normal
`hoverblade-fm generate` run: `polars/section_XX_polar.csv` is sampled per station at that station's
`Re` and `M`, and `cad/` is written by the builtin exporters (with a native kernel solid when
cadquery or build123d is installed).  `--no-cad`, `--no-viz` and a zero evaluation budget are the only
ways to omit those directories, and the run summary reports anything missing under `missing_optional`.
Items still marked **planned** in [§13](#13-contract-items-that-were-once-missing-now-produced) are part of the
contract but absent from the committed example run.

---

## 1. `design.json` — produced

Resolved design: what the three inputs became, plus the ten sections.

| Key | Content |
|---|---|
| `label` | design label, `design` for the exported optimum |
| `radius_m`, `blade_count`, `rpm`, `omega`, `tip_mach`, `tip_speed`, `solidity` | resolved operating point |
| `collective_trim_deg` | thrust-matching collective trim applied by the solver |
| `te_min_m` | the requested minimum trailing-edge thickness |
| `min_te_thickness_m`, `te_margin_m`, `blunt_te_ok` | trailing-edge margin (`min_te_thickness_m - te_min_m`) and the blunt-edge verdict for the whole design |
| `config` | the full resolved configuration (see below) |
| `stations` | summary block per station (`r_R`, `r_m`, chord, twist, thickness, Re, M, …) as a dict of arrays |
| `sections` | per-station objects: the `sections.csv` columns plus `te_thickness_to_chord`, `blunt_te_ok` and `shape_parameters` (the CST coefficients, thickness/camber/edge parameters that produced the section) |
| `design_vector` | the encoded design vector of the exported optimum |

Inside `config`:

```text
thrust_n, radius_m, trailing_edge_thickness_m,
air_density_kg_m3, dynamic_viscosity_pa_s, sound_speed_m_s,
blade_count, omega_rad_s, rpm, tip_mach, tip_mach_limit, tip_speed_m_s,
disk_area_m2, disk_loading_n_m2, thrust_coefficient, reynolds_per_m_per_rad_s,
solidity_ref, r_stations, power_ideal_w,
airfoil {…}, solver {…}, optimization {…},
auto_reasons […], warnings […], input_hash, raw_input {…}
```

`auto_reasons` is the audit trail of the automatic strategies (计划.md 第 3.2 节) — one sentence per
decision, e.g. `ISA atmosphere at altitude 0 m: rho=1.22500 kg/m^3, …` and
`[auto rotor speed] initial tip speed 221.2 m/s (M_limit=0.65) -> CT=0.00425; lowered speed to reach
CT>=0.005 -> …`.  `warnings` holds the feasibility remarks (disk loading, `CT`, trailing-edge-to-chord
ratio); they never abort a run.

## 2. `performance.json` — produced

Hover performance of the exported design (计划.md 第 20.3 节).

```text
thrust_target_n, thrust_calc_n, torque_nm,
power_ideal_w, power_induced_w, power_profile_w, power_swirl_w, power_misc_w, power_actual_w,
figure_of_merit, rpm, tip_mach, solidity, blade_count, tip_loss_factor, collective_trim_deg,
thrust_error, figure_power_w_per_n,
radial { r_R, thrust_gradient, torque_gradient, power_gradient, induced_velocity,
         inflow_angle_deg, aoa_deg, cl, cd, mach, reynolds, loss_factor }
```

* `figure_of_merit = power_ideal_w / power_actual_w` (FM, 品质因数), with
  `power_ideal_w = thrust^1.5 / sqrt(2 rho A)` and `A = pi R^2`.
  In the current implementation the `thrust` used for the ideal power is the **achieved** thrust
  (`thrust_calc_n`), not the target, so `figure_of_merit` and `power_ideal_w / power_actual_w` differ by a
  few tenths of a percent whenever the thrust trim leaves a residual error (0.28 % on the committed
  example).  Read `thrust_error` together with FM.
* `power_actual_w = power_induced_w + power_profile_w + power_swirl_w + power_misc_w`;
  `power_misc_w` is `0` in 0.1.0 and the interface is retained (计划.md 第 4.1 节).
* `thrust_error = (thrust_calc_n - thrust_target_n) / thrust_target_n`; a run is feasible while
  `|thrust_error| < 0.02`.
* `figure_power_w_per_n = power_actual_w / thrust_calc_n` — the `P/T` figure.
* `radial` is the spanwise distribution behind `plots/radial_performance.png`.

## 3. `provenance.json` — produced

Written for every run, including `--no-optimize` runs (计划.md T069).

```text
program_version, git_commit, input_hash, random_seed, timestamp,
optimization_settings {…}, resolved_config {…}, python, numpy, scipy,
figure_of_merit, blade_count, rpm, solidity, optimised
```

`git_commit` is `unknown` outside a git checkout — an sdist or Docker run cannot invent one, and the
value is recorded rather than faked.  Reproduce a number by fixing `program_version`, `random_seed` and
`input_hash`.

## 4. `constraints.json` — produced (addition to the plan's list)

The feasibility record of the exported design, kept as its own file so a manufacturing or CI check can
read it without re-parsing the design.

```text
te_min_required_m, te_min_actual_m, te_violation_m, te_included_angle_ok,
thrust_error, geometry_valid, geometry_issues [], smoothness_penalty, feasible
```

`feasible` is `geometry_valid and te_violation_m <= 1e-12 and |thrust_error| < 0.02 and
te_included_angle_ok`.  `te_violation_m` should be exactly `0`: the trailing-edge gap is clamped during
decode, so a non-zero value is a bug, not a design choice.

## 5. `optimization_summary.json` — produced (addition)

```text
best_objective, best_evaluation, evaluations, strategy, seed
```

`strategy` records which stages ran (`cma-es`, `cma-es+slsqp`); with `--no-optimize` the file reports the
single reference evaluation.

## 6. `sections.csv` — produced

Ten rows, one per station, ordered by `r_R` (计划.md 第 3.4 节).  Column order is defined once, in
`models.SECTION_CSV_COLUMNS`:

```text
station_id, r_R, r_m, chord_m, twist_deg, te_thickness_m, max_thickness_m,
thickness_to_chord, camber_m, le_radius_m, te_included_angle_deg,
reynolds, mach, cl_design, cd_design, loading_n_m, alpha_effective_deg, airfoil_file
```

| Column | Meaning | Unit |
|---|---|---|
| `station_id` | 0-based station index (0 = inboard) | – |
| `r_R` | non-dimensional radial position | – |
| `r_m` | radial position | m |
| `chord_m` | local chord | m |
| `twist_deg` | geometric twist about the 25 % chord reference | deg |
| `te_thickness_m` | **actual trailing-edge thickness**; must be `>= trailing_edge_thickness_m` | m |
| `max_thickness_m` | maximum profile thickness | m |
| `thickness_to_chord` | `max_thickness_m / chord_m` | – |
| `camber_m` | maximum camber | m |
| `reynolds` | section Reynolds number at the resolved operating point | – |
| `mach` | section Mach number | – |
| `cl_design`, `cd_design` | design-point lift and drag coefficients from the polar | – |
| `loading_n_m` | local line loading | N/m |
| `airfoil_file` | that station's contour CSV, relative to the run directory | – |

Three columns extend the plan's list, because the blunt-edge and lofting checks need them and it is cheaper
to publish them than to recompute them (第 5.4 节, 第 9.4 节):

| Column | Meaning |
|---|---|
| `le_radius_m` | leading-edge radius — a collapsed nose is a geometry failure |
| `te_included_angle_deg` | trailing-edge included angle, against the plan's 6°–45° window |
| `alpha_effective_deg` | effective angle of attack at the design point (twist + inflow − zero-lift) |

**CI's hard gate** reads `te_thickness_m` from this file and asserts exactly ten rows plus
`min(te_thickness_m) >= trailing_edge_thickness_m`
(`.github/workflows/ci.yml` step "Blunt trailing-edge assertion on sections.csv", `make check-te`,
`release.yml` wheel smoke test).

## 7. `sections/section_NN_rR.CCC.csv` + `sections/index.json` — produced

Ten files named `section_00_r0.100.csv` … `section_09_r0.970.csv`.  Columns:

```text
side, x_over_c, y_over_c, x_m, y_m
```

* `side` is `lower` or `upper`; each block runs leading edge → trailing edge.
* `x_over_c`/`y_over_c` are non-dimensional chord coordinates: `x = 0` at the leading edge, `x = 1` at the
  trailing edge, `y` positive up (计划.md 第 3.5 节).
* `x_m` is the chordwise physical coordinate and `y_m` the thickness/lift-direction coordinate of the
  twist-rotated section, both after scaling by `chord_m` and rotating about `0.25 c`.
* The internal closed contour runs lower-surface TE→LE then upper-surface LE→TE, so it is recovered by
  reversing the lower block and appending the upper block.  The leading-edge point appears once per block;
  **the trailing edge stays blunt** — the upper and lower TE points never coincide, and their separation is
  `te_thickness_m`.

`sections/index.json` is a machine-readable sidecar that repeats these conventions verbatim (columns, the
five-line convention note and per-station `station_id`, `r_R`, `r_m`, `chord_m`, `twist_deg`,
`te_thickness_m`, `max_thickness_m`, `thickness_to_chord`, `camber_m`, `le_radius_m`,
`te_included_angle_deg`, `n_per_surface`, `file`), so a downstream tool can read the contours without
parsing `sections.csv` first.

## 8. `optimization_history.csv` — produced

One row per objective evaluation (计划.md T061); the data behind `plots/fm_convergence.png`.  Written even
when a search is interrupted, so a partial run stays auditable.  Columns cover the evaluation index, stage,
FM, power, thrust and thrust error, the trailing-edge violation, the smoothness and geometry penalties, the
objective value and the feasibility flag.

## 9. `plots/*.png` — produced

| File | Content | Plan reference |
|---|---|---|
| `chord_twist.png` | chord and twist versus `r/R`, twin axes | 第 12.1 节 (1) |
| `airfoil_stack.png` | the ten sections overlaid | 第 12.1 节 (2) |
| `trailing_edge_zoom.png` | magnified aft region; the blunt gap must be visible | 第 12.1 节 (3) |
| `fm_convergence.png` | FM / objective versus evaluation count | 第 12.1 节 (4) |
| `radial_performance.png` | loading, induced velocity and power versus `r/R` | 第 12.1 节 (5) |
| `sections_preview.png` | quick per-section overview grid (addition, diagnostics) | – |

Naming differs from the two-line lists in 第 12.1 节 (`chord_vs_r.png` + `twist_vs_r.png`,
`radial_thrust_loading.png` + `radial_power_loading.png`): chord and twist share one twin-axis figure, and
the radial distributions share one figure, matching the tree in 第 3.3 节 (`chord_twist.png`,
`radial_performance.png`).  Both pieces of content are present; they are just consolidated.

## 10. `interactive/blade.html` and `report.html` — produced

* `report.html` — single page: input and resolved configuration, FM and power breakdown, the PNGs inlined,
  the ten-station table, trailing-edge margin, provenance.  No external asset requests.
* `interactive/blade.html` — rotatable blade view with per-section toggles, chord/twist readout, colour map
  over `te_thickness_m`, and a performance summary (计划.md 第 12.2 节).  Regenerate with
  `hoverblade-fm viz OUT_DIR --interactive-only`.

## 11. `cad/` — planned in the contract, produced when a CAD backend is available

| File | Content |
|---|---|
| `blade.step` | lofted solid: ten spline sections → radial loft → tip cap → root interface |
| `blade.stl` | tessellated shell at the requested `--mesh-resolution`; watertight by construction |
| `master_sections.dxf` | 2D master sections for layup templates, CNC profiles, CMM comparison |
| `manufacturing_report.md` | the check sheet in §12 |

Absent with `--no-cad`, and absent when no kernel is installed
([ADR-003](adr/0003-cad-kernel-optional-with-pure-python-fallback.md)).  `hoverblade-fm
check-manufacturing OUT_DIR --strict` exits 3 when a blocking item fails.

## 12. `manufacturing_report.md` contents — planned in the contract (计划.md 第 13.3 – 13.4 节)

```text
min_te_thickness_global          >= trailing_edge_thickness_m
min_te_thickness_per_section     per station
te_thickness_margin
te_edge_length
te_radius_estimate
te_manufacturability_flag
chord continuity / twist continuity   (rate of change between stations)
loft quality                          (no self-intersection, no degenerate edge)
STL watertightness
STEP readability                      (OpenCascade / FreeCAD import check)
units                                 stated explicitly (mm in DXF, m in STEP/STL)
default tolerances                    TE thickness ±0.10 mm or ±5 % (larger wins);
                                      chord ±0.50 mm or ±0.1 %; twist ±0.2°;
                                      station position ±0.20 mm; G1 continuity, G2 recommended
backend                               which CAD path produced these files
```

## 13. Contract items that were once missing, now produced

| Item | Contract | Verified status |
|---|---|---|
| `polars/section_XX_polar.csv` | 第 3.3 节 requires one polar file per station (Cl/Cd vs alpha at that station's `Re`, `M`) | produced: ten files, sampled from the same polar source the BEMT used |
| `cad/blade.step`, `cad/blade.stl`, `cad/master_sections.dxf`, `cad/manufacturing_report.md` | 第 13.2 - 13.3 节 | produced by the builtin writers; the committed `output_examples/demo_5000N/` run contains all four |
| `cad/manifest.json` | 第 16.7 节 T081 production package manifest | produced: file list with sizes, SHA-256 digests, roles and a `package_ok` flag |

No contract output is currently unproduced.  `--no-cad`, `--no-viz` and a zero evaluation budget are
the only switches that remove parts of the tree, and `hoverblade-fm summary` reports anything absent
under `missing_required` or `missing_optional`.
