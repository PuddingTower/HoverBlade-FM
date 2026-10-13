"""Optimisation pipeline: global search, local refinement, repair, selection (T052, T053, T054, T056)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from hoverblade_fm.aero.bemt import BemtSettings
from hoverblade_fm.aero.polar import AnalyticPolar, PolarSource
from hoverblade_fm.config.schema import ResolvedConfig, SolverSettings
from hoverblade_fm.geometry.sections import build_design
from hoverblade_fm.models import Design, OptimizationResult
from hoverblade_fm.optimize.objective import Evaluation, Objective
from hoverblade_fm.optimize.search import cma_search, refine_local, refinement_indices
from hoverblade_fm.optimize.selection import repair_design, select_best
from hoverblade_fm.optimize.vector import DesignVectorSpec, decode, initial_vector, random_vector


@dataclass
class RunReport:
    """Verbose record of one optimisation campaign for the run outputs."""

    phases: list[dict[str, object]] = field(default_factory=list)
    wall_time_s: float = 0.0
    strategy: str = "cma-es+slsqp"
    seeds: list[int] = field(default_factory=list)


def evaluate_vector(
    cfg: ResolvedConfig,
    spec: DesignVectorSpec,
    z: np.ndarray,
    polar: PolarSource,
    settings: BemtSettings | None,
    *,
    label: str = "candidate",
) -> Evaluation:
    """Evaluate one design vector at the *production* solver fidelity."""
    from hoverblade_fm.aero.hover import evaluate_design

    z = np.asarray(z, dtype=float)
    plan = decode(spec, z, cfg)
    design = build_design(plan, cfg, design_vector=z, label=label)
    trimmed, performance, constraints = evaluate_design(design, cfg, polar=polar, settings=settings)
    fm = float(np.clip(performance.figure_of_merit, 0.0, 1.0))
    err = float(performance.thrust_error)
    te_violation = float(constraints.te_violation_m) / max(cfg.te_min_m, 1e-9)
    p_thrust = cfg.optimization.lambda_thrust * err**2
    p_te = cfg.optimization.lambda_te * te_violation**2
    return Evaluation(
        z=z,
        plan=plan,
        design=trimmed,
        performance=performance,
        constraints=constraints,
        objective=-fm + p_thrust + p_te + cfg.optimization.lambda_smooth * constraints.smoothness_penalty,
        figure_of_merit=fm,
        penalty_thrust=p_thrust,
        penalty_te=p_te,
        penalty_geometry=0.0,
        penalty_smoothness=constraints.smoothness_penalty,
        wall_time_s=0.0,
        feasible=bool(constraints.feasible),
    )


def evaluate_initial(
    cfg: ResolvedConfig, spec: DesignVectorSpec, polar: PolarSource, settings: BemtSettings | None
) -> Evaluation:
    """The guaranteed-feasible physics-prior design that every campaign is compared against."""
    return evaluate_vector(cfg, spec, initial_vector(spec, cfg), polar, settings, label="initial")


def run_optimization(
    cfg: ResolvedConfig,
    *,
    polar: PolarSource | None = None,
    settings: BemtSettings | None = None,
    seed: int | None = None,
    search_solver: SolverSettings | None = None,
    log: object = None,
) -> OptimizationResult:
    """Run the full campaign and return the best feasible design plus the history."""
    opts = cfg.optimization
    seed = opts.seed if seed is None else int(seed)
    polar = polar or AnalyticPolar()
    spec = DesignVectorSpec(cfg)
    objective = Objective(
        cfg=cfg,
        spec=spec,
        polar=polar,
        settings=settings,
        search_solver=search_solver,
        use_adaptive_penalty=opts.adaptive_penalty,
    )
    report = RunReport(strategy="cma-es" + ("+slsqp" if opts.local_refine else ""), seeds=[seed])
    t_start = time.perf_counter()

    initial = evaluate_initial(cfg, spec, polar, settings)
    report.phases.append(
        {
            "phase": "initial",
            "figure_of_merit": initial.figure_of_merit,
            "objective": initial.objective,
            "feasible": initial.feasible,
            "thrust_error": initial.performance.thrust_error,
        }
    )
    _emit(log, f"initial design: FM={initial.figure_of_merit:.4f}, feasible={initial.feasible}")

    if opts.max_evaluations > 0:
        x0 = initial_vector(spec, cfg)
        global_best_z = x0
        global_best_value = objective(x0).objective
        for restart in range(opts.restarts):
            restart_seed = seed + 97 * restart
            start = (
                x0 if restart == 0 else random_vector(spec, np.random.default_rng(restart_seed), radius=0.2)
            )
            phase = cma_search(
                objective.value,
                start,
                popsize=opts.popsize,
                max_evaluations=max(40, opts.max_evaluations // opts.restarts),
                seed=restart_seed,
            )
            if phase.best_value < global_best_value:
                global_best_value, global_best_z = phase.best_value, phase.best_z
            report.phases.append(
                {
                    "phase": "global",
                    "restart": restart,
                    "algorithm": phase.algorithm,
                    "evaluations": phase.evaluations,
                    "objective": phase.best_value,
                    "converged": phase.converged,
                    "note": phase.note,
                }
            )
            _emit(
                log,
                f"global phase {restart + 1}/{opts.restarts}: objective={phase.best_value:.5f} ({phase.evaluations} evals)",
            )
        best_z = global_best_z
    else:
        best_z = initial.z

    if opts.local_refine:
        shape_start = spec.n_chord_cp + spec.n_twist_cp
        idx = refinement_indices(
            spec.n_chord_cp, spec.n_twist_cp, shape_start, spec.n_shape_cp, fields=(0, 2, 4)
        )
        idx = [i for i in idx if i < spec.dim]
        local = refine_local(
            objective.value,
            best_z,
            idx,
            max_iterations=max(5, opts.local_max_iterations // 4),
        )
        if objective.best is not None and local.best_value < objective.best.objective:
            best_z = local.best_z
        report.phases.append(
            {
                "phase": "local",
                "algorithm": local.algorithm,
                "objective": local.best_value,
                "converged": local.converged,
                "note": local.note,
            }
        )
        _emit(log, f"local phase: objective={local.best_value:.5f}")

    # finalists are re-ranked at production fidelity so the reported answer never depends on
    # the cheaper solver the search ran with
    finalists_z: list[np.ndarray] = [initial.z, best_z]
    finalists_z.extend(evaluation.z for evaluation in objective.top(6))
    evaluations: list[Evaluation] = []
    seen: list[np.ndarray] = []
    for z in finalists_z:
        if any(np.allclose(z, prev, atol=1e-9) for prev in seen):
            continue
        seen.append(z)
        evaluations.append(evaluate_vector(cfg, spec, z, polar, settings))
    production_best = select_best(evaluations)
    assert production_best is not None
    report.phases.append(
        {
            "phase": "selection",
            "candidates": len(evaluations),
            "figure_of_merit": production_best.figure_of_merit,
            "feasible": production_best.feasible,
        }
    )

    final = production_best
    if not final.feasible:
        repaired = repair_design(final, cfg)
        better = select_best([production_best, repaired]) or repaired
        final = better
        report.phases.append(
            {"phase": "repair", "feasible": final.feasible, "figure_of_merit": final.figure_of_merit}
        )

    if initial.feasible and (not final.feasible or final.figure_of_merit < initial.figure_of_merit):
        final = initial
        final.design.label = "initial"
        report.phases.append(
            {
                "phase": "fallback",
                "note": "kept initial design: the search produced no better feasible design at production fidelity",
            }
        )
    _emit(log, f"selected design: FM={final.figure_of_merit:.4f}, feasible={final.feasible}")

    report.wall_time_s = time.perf_counter() - t_start
    result = OptimizationResult(
        design=final.design,
        performance=final.performance,
        constraints=final.constraints,
        initial_performance=initial.performance,
        initial_constraints=initial.constraints,
        history=objective.history,
        evaluations=objective.evaluations,
        strategy=report.strategy + (f"+{opts.restarts}restart" if opts.restarts > 1 else ""),
        seed=seed,
        report={"phases": report.phases, "wall_time_s": report.wall_time_s, "seeds": report.seeds},
    )
    result.design.label = "optimised"
    return result


def design_to_vector(design: Design, cfg: ResolvedConfig) -> np.ndarray:
    """Recover the vector of a design when it was produced by the decoder."""
    if design.design_vector is not None:
        return np.asarray(design.design_vector, dtype=float)
    spec = DesignVectorSpec(cfg)
    return initial_vector(spec, cfg)


def _emit(log: object, message: str) -> None:
    emit = getattr(log, "info", None)
    if callable(emit):
        emit(message)
