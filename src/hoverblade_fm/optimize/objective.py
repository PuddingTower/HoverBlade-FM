"""Penalty objective: maximise hover FM subject to thrust match and the blunt-TE constraint (T047, T048)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace

import numpy as np

from hoverblade_fm.aero.bemt import BemtSettings
from hoverblade_fm.aero.hover import evaluate_design
from hoverblade_fm.aero.polar import AnalyticPolar, PolarSource
from hoverblade_fm.config.schema import ResolvedConfig, SolverSettings
from hoverblade_fm.geometry.sections import BladePlan, build_design
from hoverblade_fm.geometry.validate import geometry_penalty
from hoverblade_fm.models import ConstraintReport, Design, Performance
from hoverblade_fm.optimize.vector import DesignVectorSpec, decode


@dataclass
class Evaluation:
    """One objective evaluation, everything the reporting layers need."""

    z: np.ndarray
    plan: BladePlan
    design: Design
    performance: Performance
    constraints: ConstraintReport
    objective: float
    figure_of_merit: float
    penalty_thrust: float
    penalty_te: float
    penalty_geometry: float
    penalty_smoothness: float
    wall_time_s: float
    feasible: bool

    def to_history_row(self) -> dict[str, float | bool]:
        return {
            "objective": self.objective,
            "figure_of_merit": self.figure_of_merit,
            "thrust_n": self.performance.thrust_calc_n,
            "thrust_error": self.performance.thrust_error,
            "te_min_m": self.constraints.te_min_actual_m,
            "te_margin_m": self.constraints.te_min_actual_m - self.constraints.te_min_required_m,
            "geometry_valid": self.constraints.geometry_valid,
            "smoothness_penalty": self.constraints.smoothness_penalty,
            "penalty_thrust": self.penalty_thrust,
            "penalty_te": self.penalty_te,
            "penalty_geometry": self.penalty_geometry,
            "feasible": self.feasible,
            "wall_time_s": self.wall_time_s,
        }


@dataclass
class Objective:
    """``-FM + penalties`` with optional adaptive penalty scaling and a cheaper search solver."""

    cfg: ResolvedConfig
    spec: DesignVectorSpec
    polar: PolarSource = field(default_factory=AnalyticPolar)
    settings: BemtSettings | None = None
    trim: bool = True
    search_solver: SolverSettings | None = None
    use_adaptive_penalty: bool = True
    infeasible_window: int = 60

    def __post_init__(self) -> None:
        self.evaluations = 0
        self.history: list[dict[str, float | bool]] = []
        self._seen: dict[tuple[float, ...], Evaluation] = {}
        self.best: Evaluation | None = None
        lam = self.cfg.optimization
        self.lambda_thrust = lam.lambda_thrust
        self.lambda_te = lam.lambda_te
        self.lambda_smooth = lam.lambda_smooth
        self.lambda_geometry = lam.lambda_geometry
        self._cfg_search = self.cfg
        if self.search_solver is not None:
            self._cfg_search = replace(self.cfg, solver=self.search_solver)
        self._recent_feasible: list[bool] = []

    def _cfg_for(self) -> ResolvedConfig:
        return self._cfg_search

    def feasible_fraction(self) -> float:
        if not self._recent_feasible:
            return 0.0
        return sum(1 for f in self._recent_feasible if f) / len(self._recent_feasible)

    def _adapt(self) -> None:
        """Grow the geometry/TE penalties when the population keeps producing infeasible designs."""
        if not self.use_adaptive_penalty:
            return
        if len(self._recent_feasible) >= self.infeasible_window:
            fraction = self.feasible_fraction()
            if fraction < 0.25:
                self.lambda_geometry = min(self.lambda_geometry * 1.5, 1e6)
                self.lambda_te = min(self.lambda_te * 1.5, 1e6)
            self._recent_feasible = []

    def __call__(self, z: np.ndarray) -> Evaluation:
        t0 = time.perf_counter()
        cfg = self._cfg_for()
        plan = decode(self.spec, z, cfg)
        design = build_design(plan, cfg, design_vector=np.asarray(z, dtype=float))
        trimmed, performance, constraints = evaluate_design(
            design, cfg, polar=self.polar, settings=self.settings, trim=self.trim
        )
        fm = float(np.clip(performance.figure_of_merit, 0.0, 1.0))
        err = float(performance.thrust_error)
        te_violation = float(constraints.te_violation_m) / max(cfg.te_min_m, 1e-9)
        geom = geometry_penalty(trimmed, cfg)
        smooth = float(constraints.smoothness_penalty)
        p_thrust = self.lambda_thrust * err**2
        p_te = self.lambda_te * te_violation**2
        p_geom = self.lambda_geometry * min(geom, 10.0)
        p_smooth = self.lambda_smooth * smooth
        objective = -fm + p_thrust + p_te + p_geom + p_smooth

        self.evaluations += 1
        feasible = bool(constraints.feasible and abs(err) < 2.0 * cfg.solver.thrust_tolerance)
        self._recent_feasible.append(feasible)
        if len(self._recent_feasible) >= self.infeasible_window:
            self._adapt()
        row = Evaluation(
            z=np.asarray(z, dtype=float),
            plan=plan,
            design=trimmed,
            performance=performance,
            constraints=constraints,
            objective=objective,
            figure_of_merit=fm,
            penalty_thrust=p_thrust,
            penalty_te=p_te,
            penalty_geometry=p_geom,
            penalty_smoothness=p_smooth,
            wall_time_s=time.perf_counter() - t0,
            feasible=feasible,
        )
        self.history.append({"evaluation": self.evaluations, **row.to_history_row()})
        self.register(row)
        if self.best is None or objective < self.best.objective:
            self.best = row
        return row

    def value(self, z: np.ndarray) -> float:
        return self(z).objective

    def top(self, k: int = 6) -> list[Evaluation]:
        """The ``k`` best distinct evaluations seen so far, best first.

        The search runs on a cheaper radial resolution, so its ranking is only a proxy.  Keeping
        several finalists and re-scoring each at production resolution is what stops a campaign
        from discarding a genuine improvement because the coarse optimum happened not to transfer.
        """
        ranked = sorted(self._seen.values(), key=lambda item: item.objective)
        return ranked[: max(1, int(k))]

    def register(self, evaluation: Evaluation) -> None:
        """Index an evaluation by its vector so duplicates collapse when finalists are chosen."""
        key = tuple(np.round(np.asarray(evaluation.z, dtype=float), 9).tolist())
        existing = self._seen.get(key)
        if existing is None or evaluation.objective < existing.objective:
            self._seen[key] = evaluation


def momentum_feasibility_guard(performance: Performance) -> str | None:
    """Warn when the reported hover power is below what momentum theory allows."""
    if performance.power_actual_w < performance.power_ideal_w * 0.999:
        return (
            f"computed power {performance.power_actual_w:.1f} W is below the momentum-theory ideal "
            f"{performance.power_ideal_w:.1f} W (FM would exceed 1); the design is not physically attainable"
        )
    return None


__all__ = ["Evaluation", "Objective", "momentum_feasibility_guard"]
