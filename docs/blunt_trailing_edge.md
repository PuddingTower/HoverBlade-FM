# Blunt trailing edge (钝尾缘): the hard geometric constraint

**Rule: no sharp trailing edge, ever.**  Every one of the ten sections must satisfy

```text
t_TE(section) = y_upper(x_te) - y_lower(x_te)  >=  trailing_edge_thickness_m
```

with `x_te = 1.000` in chord coordinates, and the exported geometry must not contain a
degenerate edge.  This is 计划.md 第 5 节; the plan's risk register (第 22.1 节) names "an agent
modification that breaks the constraint" as a project risk and prescribes exactly this page's
answer: put the constraint in the parameterization, then re-check it in tests and in CI.

---

## 1. Why it is hard, and why it is a hard constraint

An optimiser maximising the hover figure of merit (FM, 品质因数) has no reason to keep the
edge thick — a closed (sharp) edge is the classical thin-airfoil ideal, and a penalty term
only makes violations *expensive*, never *impossible*.  Three consequences drive the design:

1. **CST in its textbook form closes the edge.** The class function
   `C(x) = x^N1 (1-x)^N2` with `N2 = 1` forces both surfaces to the same point at `x = 1`,
   so the trailing-edge gap is structurally zero and cannot be raised by the shape
   coefficients alone.
2. **Manufacturing does not want the sharp edge either.** A 0.1 mm nominal edge on a moulded
   or machined blade is a defect: it is where the layup meets, it is fragile, and CMM
   inspection cannot resolve it.  The user's `trailing_edge_thickness_m` is that floor.
3. **Aerodynamically the blunt edge is not free.** Cutting the edge off raises base drag, and
   it changes the pressure recovery near `x/c → 1`; the classic blunt-edge study by Standish
   and van Dam (2003, [DOI 10.1115/1.1629103](https://doi.org/10.1115/1.1629103)) reports the
   trade as higher maximum lift and lift-curve slope against a real profile-drag penalty.
   Low-Reynolds work on rounded edges — Lee, Ramesh and Gopalarathnam (2025,
   [DOI 10.1007/s00162-025-00747-z](https://doi.org/10.1007/s00162-025-00747-z)) — shows the
   unsteady Kutta condition detaching from the edge, which is why the edge must be *modelled*
   rather than patched after the fact.

So the constraint has to be geometric (unbreakable) and simultaneously visible to the aero
model (honest).  Anything less produces an optimiser that exploits a hole in the model.

## 2. How the geometry enforces it

### 2.1 Explicit trailing-edge gap in the parameterization

Each section is built with a CST mapping whose surface equations carry an offset term:

```text
y_u(x) = C(x) · S_u(x) + x · y_te_upper
y_l(x) = C(x) · S_l(x) + x · y_te_lower

te_gap = y_te_upper - y_te_lower          (non-dimensional, = t_TE / c)
```

Because the class function vanishes at `x = 1` while the linear terms do not, the gap is a
first-class degree of freedom: `y_u(1) - y_l(1) = te_gap` exactly.  Setting the midline
`(y_te_upper + y_te_lower)/2` also lets the edge be placed on or off the camber line without
disturbing the rest of the profile.

### 2.2 Hard clamp at decode time

The design vector carries a *non-negative* gap parameter which is **projected** onto the
feasible set before the contour is evaluated:

```text
te_gap_used = max(te_gap_raw, trailing_edge_thickness_m / chord)
```

`config/…airfoil.te_over_chord_min` (default `0.002`) and `te_over_chord_max` (`0.08`)
bound the ratio in addition to the physical floor, and `trailing_edge_included_angle_min_deg`
/ `max_deg` (6° / 45°) express the plan's angle window (第 5.4 节).  Because the clamp happens
*after* the sampling distribution and *before* the geometry evaluation:

* a sharp edge cannot be produced by any value of the design vector;
* the search space stays smooth (the projection is monotone), so CMA-ES is not fed a cliff;
* the penalty term `lambda_te · TE_violation²` in the objective is a **belt-and-braces**
  reporting device — a converged design normally has `te_violation_m == 0` exactly.

### 2.3 What "blunt" looks like in the data

`sections.csv` exposes `te_thickness_m`, `te_included_angle_deg` and `max_thickness_m` per
station; `design.json` exposes `te_margin = min(te_thickness_m) - trailing_edge_thickness_m`.
`plots/trailing_edge_zoom.png` magnifies `x/c = 0.9 … 1.0` so the gap is visible without
measuring it, and `sections/section_NN_*.csv` retains the raw contour where
`y_over_c[last] - y_over_c[first]` is the gap.

## 3. Checks, at four levels

| Level | Check | Where |
|---|---|---|
| 1. construction | projection of `te_gap` onto `t_TE >= trailing_edge_thickness_m` | `geometry/` (decode path) |
| 2. unit tests | every station of every generated design meets the minimum; a requested zero-thickness edge is rejected; included angle inside bounds; no crossing surfaces | `tests/unit/test_geometry.py`, `tests/unit/test_literature_search.py` (helper tests) |
| 3. CI artefact assertion | read the produced `sections.csv`, assert 10 rows, `min(te_thickness_m) >= required` and `min(te_thickness_m) > 1e-6` | `.github/workflows/ci.yml` step *Blunt trailing-edge assertion*, `Makefile` target `check-te`, `release.yml` wheel smoke test |
| 4. manufacturing | `min_te_thickness_global`, per-section minimums, `te_edge_length`, `te_radius_estimate`, `te_manufacturability_flag`; `check-manufacturing --strict` exits 3 on failure | `cad/` report, `hoverblade-fm check-manufacturing` |

The plan's own geometric test sketch (第 21.4 节) is implemented verbatim in spirit:

```python
def test_trailing_edge_is_blunt():
    for section in sections:
        assert section.te_thickness_m >= user_input.trailing_edge_thickness_m
        assert section.te_thickness_m > 1e-6
```

## 4. Aero consequence: the drag must pay for the bluntness

An honest FM has to charge for the edge.  The analytic polar used by the hover BEMT solver
applies corrections to a baseline section drag, among them a blunt-edge term that grows with
`t_TE / c` (plus Reynolds and Mach terms), so the optimiser cannot buy FM by pretending a
thick edge is free.  This is 计划.md 第 10.2 节 item 3 and T043.  The `polars/` CSVs record the
per-station `te_drag_delta_cd`, so the penalty is inspectable rather than assumed.

If the requested `trailing_edge_thickness_m` is a large fraction of the local chord, the plan
emits a feasibility warning (第 18.2 节, `trailing_edge_thickness_m / c_ref > 0.08`) and the
design may end up with a wide zero-margin band — which is reported, not hidden.

## 5. Deliberate rejections

| Temptation | Why it was rejected |
|---|---|
| Penalty-only enforcement of the edge | penalties bound the *probability* of violation, not the possibility; the plan's own risk table (第 22.1 节) flags agent-generated violations, so the constraint lives in the geometry |
| Post-hoc geometry repair ("thicken the edge after optimisation") | silently changes the aerodynamic shape that was evaluated and invalidates the reported FM |
| Allowing a zero or 1e-9 m gap for "infeasible" inputs | a degenerate edge is exactly what the constraint forbids; such inputs are rejected with exit code 2, or reported infeasible with exit code 3 |
| A single global trailing-edge thickness | the plan's per-section floor and the local chord both matter; the clamp uses `t_TE / c` bounds per station |
| Treating a blunt edge as loss-free | contradicts the blunt-edge literature (Standish & van Dam 2003; Lee et al. 2025) and inflates FM |

## 6. Related reading

* [ADR-002 — blunt trailing edge as a hard geometric constraint](adr/0002-blunt-te-as-hard-geometric-constraint.md)
* [ADR-001 — CST + BEMT + CMA-ES as the mainline](adr/0001-cst-plus-bemt-plus-cmaes.md)
* [Quality gates](quality_gates.md) — how the assertion is wired into CI
* [Literature review](literature_review.md) — the trailing-edge and blunt-edge drag sources
* 计划.md 第 5 节 (钝尾缘硬约束设计), 第 9.2 节 (翼型参数化), 第 10.2 节 (修正项), 第 13.3 节 (制造检查)
