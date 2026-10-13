# ADR-001: CST parameterization + hover BEMT + CMA-ES/SLSQP as the mainline

- **Status**: accepted
- **Date**: 2026-10-14
- **Task**: 计划.md T010 (建立 ADR 决策记录), 第 6.1 / 7.5 / 11 节
- **Relates to**: [ADR-002](0002-blunt-te-as-hard-geometric-constraint.md),
  [ADR-003](0003-cad-kernel-optional-with-pure-python-fallback.md),
  [literature review](../literature_review.md)

## Context

Three numbers — `thrust_n`, `radius_m`, `trailing_edge_thickness_m` — have to become a ten-section
blade, a hover power figure, and manufacturing geometry.  That means choosing, at the same time,

1. a **geometry parameterization** the optimiser can move (and that cannot produce illegal shapes),
2. an **aerodynamic model** that scores a candidate in milliseconds rather than CPU-hours,
3. an **optimiser** that works on a non-convex, partly discontinuous objective without gradients.

The candidate technologies named in 计划.md 第 6.3 节 are (A) constrained CST + differentiable
hover BEMT, (B) global search + local gradient refinement, (C) multi-fidelity optimisation, and
(D) generative-model augmentation.  Constraints: no closed-source core dependency (第 23 节 gate 8),
a single-package `pip`/`uv` install, results reproducible from a seed, and CI that runs the whole
pipeline in minutes.

## Options considered

### Option 1 — analytic/empirical mapping (NACA 4-digit / PARSEC style)

- *Pros*: closed-form, cheap, trivially testable; NACA 4-digit thickness is a single parameter.
- *Cons*: no trailing-edge gap degree of freedom at all; a 4-digit family cannot express cambered,
  low-Reynolds hover sections with a controlled edge; optimising inside it is really grid search.

### Option 2 — CST (class function / shape function transformation) with an explicit gap term

- *Pros*: small coefficient count with full-shape authority; the class function controls leading-edge
  roundness and trailing-edge closure analytically (Kulfan 2010,
  [10.1017/S0001924000003614](https://doi.org/10.1017/S0001924000003614)); adding `x·y_te` offsets
  makes the edge gap a first-class variable; standard in aerodynamic shape optimisation, so results
  are comparable with literature; coefficients span smoothly, which is what a lofting stage needs.
- *Cons*: Bernstein coefficients are not intuitive, so bounds on `t/c`, camber and edge angle must be
  derived and enforced; shape functions of high order cause wiggles (mitigated by a fixed, low order
  plus a spanwise smoothness penalty).

### Option 3 — free-form B-spline / CAD-interoperable parameterization

- *Pros*: arbitrary shapes, natural G2 continuity, direct CAD linkage.
- *Cons*: knot placement becomes a design variable; many more degrees of freedom at ten stations;
  self-intersection risk grows; harder to guarantee "valid by construction".

### Option 4 — generative latent space (VAE / GAN / diffusion) as the parameterization

- *Pros*: shapes live on a learned manifold of realistic airfoils; strong warm-start behaviour for
  inverse design (Chen et al. 2022, [10.1115/1.4052846](https://doi.org/10.1115/1.4052846);
  Bézier-GAN, Chen, Chiu & Fuge 2020, [10.2514/1.J059317](https://doi.org/10.2514/1.J059317);
  Airfoil Diffusion, Graves & Farimani 2024, [arXiv:2408.15898](https://arxiv.org/abs/2408.15898);
  diffusion manifold as an equality constraint in adjoint ASO, Chen et al. 2025,
  [arXiv:2507.23443](https://arxiv.org/abs/2507.23443)).
- *Cons*: needs a training corpus and a GPU-scale workflow for anything serious; constraint
  satisfaction is statistical, not guaranteed — exactly the wrong property for a hard geometric
  invariant; results are not reproducible across model versions, which breaks the provenance story.

### Aerodynamic model: momentum theory / BEMT vs. panel vs. CFD

- **Actuator disk / pure momentum theory**: gives `P_ideal` and an FM ceiling, but ignores chord,
  twist, airfoil and losses — cannot optimise a blade.
- **Hover BEMT with tip/root loss factors + airfoil polars** (Glauert form): cheap (µs–ms per
  evaluation), differentiable-ish in structure, and the accepted conceptual-design tool — Ruh & Hwang
  2023 show a robust BEM formulation computing optimal rotor designs quickly
  ([10.2514/1.J062611](https://doi.org/10.2514/1.J062611)), Sheen et al. 2023 use tip/hub loss factors
  to represent 3-D effects in hover ([10.2514/1.C036528](https://doi.org/10.2514/1.C036528)), and
  Tai et al. 2025 remove the low-inflow numerical singularity for UAM/AAM rotors
  ([10.3390/aerospace12080728](https://doi.org/10.3390/aerospace12080728)).  The open-source unified
  momentum model of Liew, Heck & Howland 2024
  ([10.1038/s41467-024-50756-5](https://doi.org/10.1038/s41467-024-50756-5)) is the principled
  replacement for the empirical corrections we are forced to use in 0.1.0.
- **Panel / XFOIL-class boundary-layer coupling** (Drela 1989,
  [10.1007/978-3-642-84010-4_1](https://doi.org/10.1007/978-3-642-84010-4_1)): much better polars at
  low Reynolds number, but an external process, awkward around blunt edges, and a licensing/install
  burden we refuse in the core (第 23 节 gate 8).
- **RANS/LES CFD**: the truth, at 10¹–10⁴ CPU-hours per design.  Only usable as a posterior check of a
  handful of candidates (Erhard & Alonso 2024, [10.2514/6.2024-2506](https://doi.org/10.2514/6.2024-2506);
  Cornelius et al. 2025, [10.3390/aerospace12080650](https://doi.org/10.3390/aerospace12080650)).

### Optimiser: CMA-ES (+SLSQP) vs. Bayesian optimisation vs. gradient/adjoint

- **CMA-ES**: derivative-free, handles discontinuities from feasibility projections and geometry
  checks, population-based (robust to the multi-modal hover objective), a mature BSD-licensed `cma`
  package, and well documented (Hansen 2016, [arXiv:1604.00772](https://arxiv.org/abs/1604.00772)).
  Rotor design work already uses the global-then-local pattern (Wang et al. 2023,
  [arXiv:2302.08728](https://arxiv.org/abs/2302.08728)).
- **Bayesian / surrogate optimisation**: fewer evaluations, best when one evaluation is expensive;
  poor fit for ~30-dimensional coupled geometry where the cheap model already runs thousands of times,
  and it adds GP infrastructure to the core.
- **Gradient / adjoint (differentiable)**: highest sample efficiency at high dimension, and the right
  long-term direction (Ho & Farhat 2023, [10.1016/j.jcp.2023.112191](https://doi.org/10.1016/j.jcp.2023.112191);
  adjoint with diffusion-manifold constraints, [arXiv:2507.23443](https://arxiv.org/abs/2507.23443)),
  but gradients through a loss-iteration loop and a geometric projection are a research project, not a
  v0.1 deliverable.

## Decision

Adopt **constrained CST parameterization with an explicit trailing-edge gap + hover BEMT with Prandtl
tip/root losses and an analytic polar + CMA-ES global search with an SLSQP local refinement**, exactly
as 计划.md 第 6.1 / 7.5 节 proposes:

```text
三输入 → 参数自动推断 → 约束化几何生成器(CST + te_gap) → 10 截面
      → 悬停 BEMT 气动评估 → FM 计算 → CMA-ES + SLSQP → 最优叶片 → 放样/CAD/报告
```

Concretely:

1. CST with `C(x) = x^0.5 (1-x)^1` class functions, order-5 shape functions per surface, plus
   explicit `te_gap` and `te_midline` terms; decode clamps the gap (ADR-002).
2. Chord and twist from spanwise control points with bounded spanwise rates; thickness/camber/edge
   bounds from `AirfoilBounds`.
3. Hover-only BEMT, `forward_speed = climb_rate = 0`, Prandtl tip loss, root correction, relaxation on
   the induction iteration, thrust-matching via collective trim; analytic polar with Reynolds, Mach and
   blunt-edge drag terms so a thick edge is paid for (Standish & van Dam 2003).
4. Objective `-FM + λ_thrust·thrust_error² + λ_te·TE_violation² + λ_smooth·smoothness + λ_geom·invalid`,
   `λ` values 100 / 1000 / 1 / 1000 as prescribed in 第 11.2 节; the `λ_te` term is reporting only, the
   clamp is the guarantee.
5. CMA-ES (presets 24/320, 48/1500, 64/3000 with restarts) then SLSQP through SciPy
   ([10.1038/s41592-019-0686-2](https://doi.org/10.1038/s41592-019-0686-2)) for the local polish, then a
   constraint-repair pass (第 11.3 节).
6. Everything optional — XFOIL, CFD, surrogates, generative warm start — sits behind the polar and
   search interfaces, never inside the core path.

## Consequences

**Positive**

- One evaluation is sub-millisecond, so thousands of evaluations in CI on two laptops are feasible;
  the whole `preset fast` run fits in the smoke-test budget.
- Feasibility by construction: the trailing-edge invariant does not depend on the optimiser
  cooperating.
- Pure permissive-dependency core (`numpy`, `scipy`, `pydantic`, `matplotlib`, `cma`); installable with
  `uv sync`, no external solvers, no GPU.
- Deterministic given `--seed`; `provenance.json` pins version, input hash and seed.
- The same objective and model are reused by every later upgrade, so upgrades are comparable.

**Negative / accepted costs**

- BEMT is a low-fidelity model: it cannot see dynamic stall, 3-D tip flow, or wake detail, so FM is an
  *optimistic conceptual-design estimate*.  Mitigation: a high-fidelity check interface (第 12/第 22 节
  risk table) and the disclaimer (`docs/disclaimer.md`).
- The analytic polar is calibrated, not measured; off-nominal sections extrapolate.  Mitigation: polar
  files are exported per station so a reviewer can inspect the used polar, and the interface is
  replaceable (T039, T082-T084).
- Derivative-free search needs hundreds of evaluations; a 30-dimensional space is at the upper edge of
  what CMA-ES does comfortably with a small budget.  Mitigation: low-order CST, bounded spanwise rates,
  SLSQP polish, restarts in the `full` preset.
- Penalty weights need tuning when new constraints appear; adaptive penalty is available
  (`adaptive_penalty`).
- Choosing BEMT now means later work if a CFD-validated FM is required; the fidelity ladder
  (第 6.3 节 C) is explicitly deferred to v0.2/v0.3.

**Reversible?** Partially, cheaply: the parameterization and the optimiser are behind `geometry/` and
`optimize/` interfaces, and the polar is behind `aero/polar.py`, so swapping in XFOIL, a learned
surrogate, a JAX-differentiable BEMT or a Bayesian outer loop does not touch the CLI, the schema or the
output files.  What is **not** reversible without invalidating published results is the trailing-edge
invariant and the FM definition — those are pinned by ADR-002 and the disclaimer.
