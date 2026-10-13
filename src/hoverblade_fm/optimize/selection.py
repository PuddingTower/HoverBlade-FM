"""Best-design selection and constraint repair (T055, plan section 11.3 phase 3)."""

from __future__ import annotations

import numpy as np

from hoverblade_fm.models import Design
from hoverblade_fm.optimize.objective import Evaluation, Objective


def rank_key(evaluation: Evaluation) -> tuple[float, float, float]:
    """Feasible first, then highest FM, then smallest thrust error."""
    return (
        0.0 if evaluation.feasible else 1.0,
        -evaluation.figure_of_merit,
        abs(evaluation.performance.thrust_error),
    )


def select_best(candidates: list[Evaluation]) -> Evaluation | None:
    if not candidates:
        return None
    return min(candidates, key=rank_key)


def select_from_objective(objective: Objective, extra: list[Evaluation] | None = None) -> Evaluation | None:
    pool: list[Evaluation] = []
    if objective.best is not None:
        pool.append(objective.best)
    pool.extend(extra or [])
    return select_best(pool)


def repair_design(evaluation: Evaluation, cfg_like: object) -> Evaluation:
    """Project a candidate back onto the constraint set and re-evaluate it.

    The trailing-edge clamp and the chord / twist rate projection already happen inside
    ``plan_from_raw``, so repair is about the residual cases: a design that could not trim to
    the target thrust gets its collective widened, and a design with a geometry violation gets
    the offending shape pushed back inside its bounds before re-evaluation.
    """
    from hoverblade_fm.aero.hover import evaluate_design
    from hoverblade_fm.config.schema import ResolvedConfig
    from hoverblade_fm.geometry.sections import build_design

    cfg: ResolvedConfig = cfg_like  # type: ignore[assignment]
    params = [p for p in evaluation.plan.params]
    changed = False
    bounds = cfg.airfoil
    for i, p in enumerate(params):
        t = float(np.clip(p.t_over_c, bounds.thickness_to_chord_min, bounds.thickness_to_chord_max))
        cam = float(np.clip(p.camber, 0.0, bounds.camber_max_over_chord))
        if (t, cam) != (p.t_over_c, p.camber):
            changed = True
        params[i] = p.__class__(
            t_over_c=t,
            x_t=float(np.clip(p.x_t, 0.2, 0.45)),
            camber=cam,
            x_camber=float(np.clip(p.x_camber, 0.28, 0.6)),
            te_gap=max(p.te_gap, cfg.te_min_m / max(evaluation.plan.chord_m[i], 1e-9)),
            te_mid=p.te_mid,
            le_factor=float(np.clip(p.le_factor, 0.85, 1.45)),
            aft_upper=p.aft_upper,
            aft_lower=p.aft_lower,
        )
    plan = evaluation.plan
    if changed:
        plan = plan.__class__(
            chord_m=plan.chord_m.copy(),
            twist_deg=plan.twist_deg.copy(),
            params=params,
            te_clamped=list(plan.te_clamped),
            r_R=plan.r_R.copy(),
            collective_trim_deg=plan.collective_trim_deg,
            chord_control_points=None
            if plan.chord_control_points is None
            else plan.chord_control_points.copy(),
            twist_control_points=None
            if plan.twist_control_points is None
            else plan.twist_control_points.copy(),
            te_clamp_deficit=plan.te_clamp_deficit,
        )
    design = build_design(plan, cfg, design_vector=evaluation.z, label="repaired")
    trimmed, performance, constraints = evaluate_design(design, cfg)
    fm = float(np.clip(performance.figure_of_merit, 0.0, 1.0))
    p_thrust = cfg.optimization.lambda_thrust * float(performance.thrust_error) ** 2
    p_te = cfg.optimization.lambda_te * (constraints.te_violation_m / max(cfg.te_min_m, 1e-9)) ** 2
    return Evaluation(
        z=evaluation.z,
        plan=plan,
        design=trimmed,
        performance=performance,
        constraints=constraints,
        objective=-fm + p_thrust + p_te,
        figure_of_merit=fm,
        penalty_thrust=p_thrust,
        penalty_te=p_te,
        penalty_geometry=0.0,
        penalty_smoothness=constraints.smoothness_penalty,
        wall_time_s=0.0,
        feasible=bool(constraints.feasible),
    )


def design_from_evaluation(evaluation: Evaluation) -> Design:
    return evaluation.design


__all__ = ["design_from_evaluation", "rank_key", "repair_design", "select_best", "select_from_objective"]
