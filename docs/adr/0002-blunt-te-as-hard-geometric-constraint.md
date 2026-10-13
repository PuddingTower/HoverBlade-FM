# ADR-002: the blunt trailing edge is a hard geometric constraint, not a penalty

- **Status**: accepted
- **Date**: 2026-10-14
- **Task**: 计划.md T010, 第 5 节 (钝尾缘硬约束设计), 第 9.2 节
- **Relates to**: [ADR-001](0001-cst-plus-bemt-plus-cmaes.md),
  [blunt trailing edge guide](../blunt_trailing_edge.md), [quality gates](../quality_gates.md)

## Context

The user supplies a minimum trailing-edge thickness `trailing_edge_thickness_m`, and the plan states
the requirement as an absolute: 尾缘必须是钝尾缘，不允许尖尾缘 (第 5.1 节 forbids surface intersection
at `x/c = 1`, zero thickness, vanishing included angle, degenerate CAD edges, and any exported edge
below the user value).  Two facts make this a design decision rather than a check:

1. Textbook CST with class exponent `N2 = 1` **forces** a closed edge, so the parameterization as
   usually written cannot represent the constraint, let alone guarantee it.
2. The optimiser's objective (maximise FM, 品质因数) has an incentive to close the edge: a sharp edge is
   the thin-airfoil ideal and carries no base drag.  计划.md 第 22.1 节 lists exactly this hazard —
   "钝尾缘导致阻力过高 / FM 下降" and "Agent 自动修改破坏约束".

Blunt-edge aerodynamics is well documented: Standish & van Dam (2003,
[10.1115/1.1629103](https://doi.org/10.1115/1.1629103)) report increased maximum lift and
lift-curve slope together with a substantial base-drag penalty for cut-off airfoils; recent low-Re
work on rounded edges (Lee, Ramesh & Gopalarathnam 2025,
[10.1007/s00162-025-00747-z](https://doi.org/10.1007/s00162-025-00747-z)) shows the unsteady Kutta
condition detaching from the edge, i.e. a finite edge has its own physics; blunt-edge thickness studies
on very thick sections (Pei et al. 2023, [10.3390/wind3040025](https://doi.org/10.3390/wind3040025)) and
CFD at low Reynolds number ([arXiv:2409.13922](https://arxiv.org/abs/2409.13922)) confirm the drag cost
grows with the edge.  "Valid-by-construction" parameterization is an established idea in the generative
design literature too (Bézier-GAN latent shapes, Chen, Chiu & Fuge 2020,
[10.2514/1.J059317](https://doi.org/10.2514/1.J059317); manifold-constrained adjoint optimisation,
Chen et al. 2025, [arXiv:2507.23443](https://arxiv.org/abs/2507.23443)) — the argument is that a
constraint the search cannot violate is worth more than a constraint it is merely discouraged from
violating.

## Options considered

### Option A — penalty only (`λ_te · TE_violation²` in the objective)

- *Pros*: one line in the objective, no geometry change; keeps the parameterization textbook-clean.
- *Cons*: violations remain reachable — during early CMA-ES generations, after a restart, or whenever a
  cheaper infeasible shape wins; the reported optimum is then unbuildable.  Penalty tuning is
  problem-dependent, and a future contributor changing `λ_te` silently changes a safety property.
  An autonomous agent editing the objective is precisely the scenario the plan's risk table calls out.

### Option B — post-hoc repair (optimise freely, then thicken the edge before export)

- *Pros*: unconstrained search; simple to implement.
- *Cons*: the exported geometry is not the geometry that was evaluated, so the reported FM, thrust
  balance and polar data are all slightly wrong; repair changes camber and edge angle unpredictably, and
  repeated repair pushes designs off the smooth spanwise distribution.

### Option C — hard constraint by construction: explicit gap variable + projection at decode

- *Pros*: a sharp edge is unreachable for any input vector; the search space stays smooth because the
  projection is monotone; the constraint is expressed once, in the geometry layer, and cannot be
  defeated by an objective edit; per-station bounds on `t_TE/c` and on the included angle come free.
- *Cons*: one extra design variable per station and a slightly less "pure" CST; the feasible set is a
  clipped region, so the optimiser can sit on the clamp (which is physically meaningful, not a bug);
  the aero model must then charge for the blunt edge, or the clamp would be a free win.

### Option D — replace the parameterization (e.g. NACA 4-digit with a thickness floor, or B-spline with
  constrained end points)

- *Pros*: 4-digit families make the floor trivial to enforce.
- *Cons*: the shape authority is too low for hover sections (camber, edge radius, aft loading), and the
  plan explicitly recommends CST with an explicit `te_gap` (第 4.2, 9.2 节).

## Decision

**Option C.**  The trailing-edge gap is an explicit degree of freedom in the CST decode,

```text
y_u(x) = C(x)·S_u(x) + x·y_te_upper
y_l(x) = C(x)·S_l(x) + x·y_te_lower
te_gap = y_te_upper − y_te_lower ,  required: te_gap · chord >= trailing_edge_thickness_m
```

and the decoded gap is **clamped** so the requirement holds by construction:
`te_gap_used = max(te_gap_raw, trailing_edge_thickness_m / chord)`, additionally bounded by
`AirfoilBounds.te_over_chord_min/max` (0.002 / 0.08) and the included-angle window (6°–45°) from
第 5.4 节.  The upper and lower surfaces therefore never coincide at `x/c = 1`, and the trailing edge
sits at `x/c = 1` by construction.

The penalty term from Option A stays in the objective, but it is reclassified as a *reporting* device:
`TE_violation_m` should be exactly zero at convergence, and a non-zero value means a bug.  The invariant
is then re-verified at three independent layers (unit tests on decoded sections, the CI assertion against
the exported `sections.csv`, and the manufacturing check / `--strict`), see
[quality_gates.md](../quality_gates.md).

Finally, the blunt edge is charged for: the analytic polar applies a trailing-edge thickness drag
correction (T043), so FM reflects the cost of the constraint instead of hiding it.

## Consequences

**Positive**

- The defining product promise — "give three numbers, get a manufacturable blade" — holds for every run,
  including runs with a broken input, an interrupted optimisation, or `--no-optimize`.
- Constraint violations cannot be introduced by an objective edit, a new optimiser, or a future
  generative warm start (which would produce arbitrary shapes).
- CI can assert on an artefact rather than trust a code path, which is what makes agent-driven
  development (计划.md 第 15 节) acceptable.
- Exported numbers stay honest because the blunt-edge drag term is inside the model rather than a
  footnote.

**Negative / accepted costs**

- Achievable FM is lower than a sharp-edge ideal; a user asking for an aggressive thickness (e.g. 3.5 mm
  on a 0.55 m rotor) gets a blade whose aft sections are dominated by the clamp.  The plan's feasibility
  warning (`trailing_edge_thickness_m / c_ref > 0.08`, T018) reports this instead of hiding it.
- Optimiser trajectories can pile onto the clamp, which slightly wastes search effort; the `custom`
  preset and bounded design ranges mitigate it.
- CAD lofting must handle a finite-thickness edge (tip cap and edge rounding), which is why the outermost
  station stays at `r/R = 0.97` (第 9.1 节).
- The invariant constrains every future upgrade: a generative model may not emit sections below the
  floor; it would be used only as a warm start inside the same clamped decoder (T089).

**Reversible?** No, and deliberately so.  Relaxing the hard clamp to a penalty would make sharp-edged
designs reachable again, invalidating the exported `sections.csv` guarantee, the manufacturing report and
the CI gate.  The *value* of `trailing_edge_thickness_m` is user input and fully adjustable; the
*mechanism* (construction + projection + layered checks + a drag penalty) is the project's contract.
