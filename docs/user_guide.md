# User guide

Covers installation, the command-line interface, the input contract, what a run produces and
how to read it.  (计划.md T091.)

---

## 1. Install

[`uv`](https://docs.astral.sh/uv/) is the supported installer; Python 3.11 or 3.12 is required
(`requires-python = ">=3.11"`).

```bash
git clone https://github.com/hoverblade-fm/HoverBlade-FM.git
cd HoverBlade-FM
uv sync                          # runtime dependencies
uv sync --extra dev              # + pytest, ruff, mypy
uv sync --extra docs             # + mkdocs-material (optional)
uv sync --extra cad              # + cadquery / ezdxf (optional, Python < 3.13)
```

Then run either through `uv`:

```bash
uv run hoverblade-fm generate examples/input_5000N.json --out output/demo
```

or install the command globally:

```bash
uv tool install hoverblade-fm
hoverblade-fm version
```

Container:

```bash
docker build -t hoverblade-fm .
docker run --rm -v "$PWD/output:/app/output" hoverblade-fm \
  --thrust-n 5000 --radius-m 2.5 --trailing-edge-thickness-m 0.0025 --out output/demo
```

The image's entry point is pinned to `hoverblade-fm generate`; other subcommands need
`--entrypoint hoverblade-fm` (see `Dockerfile`).

---

## 2. Input contract

### 2.1 The three required numbers

```json
{
  "thrust_n": 5000.0,
  "radius_m": 2.5,
  "trailing_edge_thickness_m": 0.0025
}
```

| Field | Meaning | Unit |
|---|---|---|
| `thrust_n` | target thrust of one rotor (单副旋翼目标拉力) | N |
| `radius_m` | rotor radius | m |
| `trailing_edge_thickness_m` | minimum trailing-edge thickness; every section must be blunt (钝尾缘) | m |

The shipped examples are 120 N / 0.55 m (small), 5 000 N / 2.5 m (medium) and 20 000 N / 4.0 m
(large) as `examples/input_120N.json`, `examples/input_5000N.json`,
`examples/input_20000N.json`.

### 2.2 What the tool resolves for you

Nothing else is required.  Defaults (计划.md 第 3.2 节), each recorded with a reason string in
`provenance.json` / `design.json`:

| Parameter | Automatic strategy |
|---|---|
| Air density `rho` | ISA sea level, `1.225 kg/m^3` (or from `altitude_m` via the ISA layer model) |
| Dynamic viscosity `mu` | Sutherland at that altitude, `1.81e-5 Pa·s` at sea level |
| Speed of sound | `340.29 m/s` at sea level |
| Blade count | chosen from disk loading and per-blade thrust among 2 / 3 / 4 |
| Rotor speed | tip-Mach limited (`M_tip <= 0.65` by default, tip speed capped at 235 m/s), slowed down if the hover thrust coefficient `CT` would be implausibly small |
| Section count | fixed 10 |
| Radial stations | `r/R = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.97]` |
| Root region | `0 … 0.08 R` treated as a structural fairing, not an aerodynamic section |

### 2.3 Advanced overrides (optional, in the JSON)

Accepted keys on the input object: `air_density_kg_m3`, `dynamic_viscosity_pa_s`,
`sound_speed_m_s`, `altitude_m`, `blade_count`, `rpm`, `tip_mach_limit`, `r_stations`
(exactly 10, strictly increasing, inside `(0, 1)`), plus the nested blocks `airfoil`
(thickness / camber / `te_over_chord` / trailing-edge included-angle bounds, CST orders),
`solver` (radial resolution, iteration limits, tolerance, relaxation, thrust tolerance) and
`optimization` (`preset`, `seed`, `popsize`, `max_evaluations`, `local_refine`, `restarts`,
penalty weights `lambda_thrust` / `lambda_te` / `lambda_smooth` / `lambda_geometry`).

Unknown keys are rejected — the schema is `extra="forbid"`, so a typo fails loudly with exit
code 2 instead of being silently ignored.

A unit-aware alternative to the three SI fields also exists (`thrust`, `radius`,
`trailing_edge_thickness` plus a `units` block), but the three `_n` / `_m` fields are the
documented minimal contract.

---

## 3. CLI reference

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

### 3.1 `generate`

| Flag | Effect |
|---|---|
| `INPUT_JSON` | positional input file; the three scalar flags override it if both are given |
| `--thrust-n F` / `--radius-m F` / `--trailing-edge-thickness-m F` | supply the three required numbers directly |
| `--out DIR` | **required**; the run directory (created if missing) |
| `--preset fast\|standard\|full\|custom` | optimiser budget: `fast` = popsize 24 / 320 evals / no local refine, `standard` = 48 / 1500 / local refine, `full` = 64 / 3000 / local refine / 3 restarts, `custom` = whatever the JSON or the flags below say |
| `--seed N` | RNG seed for CMA-ES; deterministic for a fixed seed and version |
| `--blade-count N` | override the automatic blade count |
| `--rpm F` | override the automatically resolved rotor speed |
| `--tip-mach-limit F` | tip Mach cap used by the rotor-speed strategy (default 0.65) |
| `--rho F`, `--mu F`, `--a-sound F` | atmosphere overrides |
| `--max-evaluations N`, `--popsize N` | optimiser budget overrides (imply `custom` behaviour on top of a preset) |
| `--no-optimize` | skip CMA-ES/SLSQP; export and report the reference design only |
| `--no-cad` | skip STEP / STL / DXF and the manufacturing report |
| `--no-viz` | skip PNG plots, `report.html` and `interactive/blade.html` |
| `--mesh-resolution coarse\|standard\|fine` | tessellation quality of the STL mesh |
| `--quiet` | machine-friendly: no progress lines |
| `--json` | print the run summary as a single JSON object on stdout |

### 3.2 Other subcommands

* `viz OUT_DIR [--interactive-only]` — regenerate PNGs, `report.html` and
  `interactive/blade.html` from an existing run; `--interactive-only` rebuilds just
  `interactive/blade.html`.
* `check-manufacturing OUT_DIR [--json] [--strict]` — re-run the trailing-edge / lofting
  checks against the artefacts in a run directory; `--strict` turns a failing blocking check
  into exit code 3.
* `summary OUT_DIR [--json]` — print FM, power, thrust error and the ten stations; `--json`
  emits machine-readable output for scripting.
* `literature-search` — run the retrieval helper (see §6).
* `version` — print the installed program version (matches `provenance.json`).

### 3.3 Exit codes

| Code | Meaning |
|---|---|
| `0` | success |
| `2` | invalid input — malformed JSON, unknown key, non-physical value, unreadable file, bad flag combination |
| `3` | infeasible design, or (with `check-manufacturing --strict`) a failing manufacturing check |
| `4` | internal error — an unexpected exception; include `provenance.json` when reporting |

### 3.4 Environment overrides

| Variable | Effect |
|---|---|
| `HOVERBLADE_FM_SEED` | default value for `--seed` when the flag is not passed |
| `HOVERBLADE_FM_OFFLINE=1` | no outbound network calls: the literature helper falls back to its curated seed set, and CI runs hermetically |
| `HOVERBLADE_FM_INSECURE_TLS=1` | `literature-search` only: allow an unverified TLS retry for the two public metadata hosts, and log it.  Off by default |

---

## 4. Worked examples

```bash
# 1) minimal: an example file (medium hover rotor, 5 kN, 2.5 m radius, 2.5 mm blunt edge)
uv run hoverblade-fm generate examples/input_5000N.json --out output/demo

# 2) the three-flag form, no input file
uv run hoverblade-fm generate --thrust-n 5000 --radius-m 2.5 \
  --trailing-edge-thickness-m 0.0025 --out output/demo

# 3) quick look at a design: skip optimisation and CAD, keep plots
uv run hoverblade-fm generate examples/input_120N.json --out output/quick \
  --no-optimize --no-cad

# 4) deterministic full-budget run for regression comparison
uv run hoverblade-fm generate examples/input_20000N.json --out output/large \
  --preset full --seed 7

# 5) inspect / re-report a finished run
uv run hoverblade-fm summary output/demo
uv run hoverblade-fm check-manufacturing output/demo --strict
uv run hoverblade-fm viz output/demo --interactive-only

# 6) scripting: capture the summary as JSON
uv run hoverblade-fm generate examples/input_5000N.json --out output/ci --preset fast \
  --quiet --json | python -c "import json,sys; d=json.load(sys.stdin); print(d['figure_of_merit'])"
```

---

## 5. What a run produces

```text
output/demo/
├── design.json                 resolved configuration + the 10-section design
├── performance.json            T, Q, P_ideal, P_induced, P_profile, P_swirl, FM, ...
├── provenance.json             version, git commit, input hash, seed, timestamp, settings
├── sections.csv                one row per station (see docs/outputs.md)
├── sections/                   section_00_r0.100.csv … section_09_r0.970.csv (contour points)
├── polars/                     section_XX_polar.csv (Cl/Cd vs alpha at that station's Re, M)
├── optimization_history.csv    one row per evaluation (FM, penalties, feasibility)
├── plots/
│   ├── chord_twist.png         chord and twist vs r/R
│   ├── airfoil_stack.png       the 10 sections overlaid
│   ├── trailing_edge_zoom.png  magnified blunt trailing edge
│   ├── fm_convergence.png      FM vs evaluation count
│   └── radial_performance.png  loading / power distributions
├── interactive/blade.html      rotate the blade, toggle sections, colour by TE thickness
├── cad/
│   ├── blade.step              lofted solid (requires the `cad` extra)
│   ├── blade.stl               tessellated solid
│   ├── master_sections.dxf     2D master sections for layup / CNC
│   └── manufacturing_report.md TE checks, tolerances, unit statement
└── report.html                 single-page summary of everything above
```

With `--no-cad` the `cad/` tree is absent; with `--no-viz` the `plots/`, `interactive/` and
`report.html` items are absent; with `--no-optimize` `optimization_history.csv` records the
single reference evaluation.  Full field-by-field documentation: [outputs.md](outputs.md).

**Coordinate conventions** (计划.md 第 3.5 节): `x` runs from leading edge to trailing edge,
`y` is positive up, sections rotate about the `0.25 c` reference point, the trailing edge sits
at `x/c = 1`, and the upper and lower surfaces never coincide there — that non-zero gap *is*
`te_thickness_m`.

---

## 6. Reading the numbers

* **FM (品质因数)** is `P_ideal / P_actual` with `P_ideal = T^1.5 / sqrt(2 rho A)` and
  `P_actual = P_induced + P_profile + P_swirl + P_misc` (`P_misc = 0` in 0.1.0, the interface is
  kept).  A higher FM is better; FM above 1 would mean the model is wrong, and CI flags
  implausible values.
* **Thrust error** `(thrust_calc_n - thrust_target_n) / thrust_target_n` must stay small; the
  optimiser penalises it (`lambda_thrust`), and a run is only feasible when
  `|thrust_error| < 0.02`.
* **`te_margin`** in `design.json` is `min(te_thickness_m) - trailing_edge_thickness_m`; it is
  `>= 0` by construction.  A zero margin means the clamp, not the optimiser, set the edge.
* **`power_factor` (`P/T`, W/N)** in `performance.json` is the classic hover efficiency number
  if you prefer it to FM.

Never quote a performance figure without its seed, preset and version — `provenance.json`
carries all three.

---

## 7. The literature helper

```bash
uv run hoverblade-fm literature-search --offline            # curated set only, no network
uv run hoverblade-fm literature-search --limit 8 --years 2022-2025
python scripts/literature_search.py --out docs/literature_search --offline --no-merge
```

It queries the arXiv Atom API and the Crossref REST API with the English query list of
计划.md 第 7.2 节, dedupes by DOI then title, classifies each hit into the screening columns of
计划.md 第 7.4 节, and writes `docs/literature_search/raw.jsonl` plus `summary.csv`.  It is
standard-library only, rate-limits politely, never crashes on HTTP errors and falls back to a
bundled curated set when there is no network — so CI always produces non-empty output.  Query
list, column mapping and reproducibility notes: [literature_search/queries.md](literature_search/queries.md).

---

## 8. Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| exit 2, `required inputs are thrust_n …` | neither the JSON nor the three flags supplied all three numbers | pass the file or all three flags |
| exit 2, `unknown field` | a key the schema forbids (typo, or a field renamed) | compare against §2.3 |
| exit 2, `resolved tip Mach … exceeds the limit` | `--rpm` too high for `--radius-m` / `--tip-mach-limit` | lower `--rpm`, raise `--tip-mach-limit`, or drop the RPM override |
| exit 2, `exactly 10 radial stations are required` | `r_stations` length or ordering | give 10 strictly increasing values in `(0, 1)` |
| exit 3, infeasible design | disk loading or `CT` outside the plausible hover band, or `trailing_edge_thickness_m` too large for the chord | see the warnings in `design.json` / stdout; try a bigger radius, more blades, or a thinner edge |
| `cad/blade.step` missing | the `cad` extra is not installed (it needs Python < 3.13) | `uv sync --extra cad`, or run with `--no-cad` deliberately |
| `literature-search` writes only the curated set | no network, or `HOVERBLADE_FM_OFFLINE=1` | unset it; on a machine without a usable trust store set `HOVERBLADE_FM_INSECURE_TLS=1` and watch the log line |
| run looks different from a colleague's | different seed, preset, or program version | pin `--seed`, `--preset`, and compare `provenance.json` |

Warnings that do not stop a run are printed with the automatic-strategy reason strings; they
are also stored in `design.json` (`auto_reasons`, `warnings`), so an agent can act on them
without parsing stdout.
