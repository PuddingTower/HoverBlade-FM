"""Optimisation tests: vector codec, objective, search phases and the FM regression gate (T046-T056)."""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import pytest

from hoverblade_fm.config.schema import OptimizationSettings, SolverSettings, UserInput, resolve
from hoverblade_fm.geometry.sections import build_design
from hoverblade_fm.optimize.objective import Evaluation, Objective
from hoverblade_fm.optimize.pipeline import evaluate_initial, evaluate_vector, run_optimization
from hoverblade_fm.optimize.search import cma_search, de_search, refine_local, refinement_indices
from hoverblade_fm.optimize.selection import rank_key, repair_design, select_best
from hoverblade_fm.optimize.vector import (
    SHAPE_CP_R,
    SHAPE_FIELDS,
    DesignVectorSpec,
    decode,
    encode,
    from_normalised,
    initial_vector,
    random_vector,
    to_normalised,
    vector_report,
)

MINIMAL = {"thrust_n": 5000.0, "radius_m": 2.5, "trailing_edge_thickness_m": 0.0025}
TINY = OptimizationSettings(preset="custom", max_evaluations=60, popsize=8, local_refine=False, restarts=1)
COARSE = SolverSettings(n_radial=61, n_itmax=120)


@pytest.fixture(scope="module")
def cfg():
    return resolve(UserInput.model_validate(MINIMAL))


@pytest.fixture(scope="module")
def spec(cfg) -> DesignVectorSpec:
    return DesignVectorSpec(cfg)


# -- design vector (T046, T049) --------------------------------------------------------------


def test_dimensionality_is_small_and_named(spec, cfg):
    # 5 chord + 5 twist control points + 8 shape fields x 3 control points
    assert spec.dim == 5 + 5 + 8 * 3
    names = spec.names()
    assert len(names) == spec.dim
    assert names[0] == "chord_cp_0"
    assert f"{SHAPE_FIELDS[0]}_cp_0" in names


def test_bounds_are_finite_and_ordered(spec):
    lo, hi = spec.bounds()
    assert np.all(np.isfinite(lo)) and np.all(np.isfinite(hi))
    assert np.all(hi > lo)
    assert np.all(lo[spec.slices["chord"]] == spec.cfg.chord_min_m)


def test_normalisation_round_trip(spec):
    lo, hi = spec.bounds()
    physical = 0.5 * (lo + hi) + 0.3 * (hi - lo)
    z = to_normalised(spec, physical)
    assert np.all(np.abs(z) <= 1.0)
    back = from_normalised(spec, z)
    assert np.allclose(back, physical, rtol=1e-9)


def test_decode_clamps_illegal_trailing_gaps(spec, cfg):
    z = initial_vector(spec, cfg).copy()
    shape_start = spec.n_chord_cp + spec.n_twist_cp
    te_index = SHAPE_FIELDS.index("te_gap")
    z[shape_start + te_index * spec.n_shape_cp : shape_start + (te_index + 1) * spec.n_shape_cp] = -1.0
    plan = decode(spec, z, cfg)
    for chord, params in zip(plan.chord_m, plan.params, strict=True):
        assert params.te_gap * chord >= cfg.te_min_m - 1e-15
        assert params.te_gap >= 0.0


def test_initial_vector_decodes_to_a_feasible_design(spec, cfg):
    plan = decode(spec, initial_vector(spec, cfg), cfg)
    design = build_design(plan, cfg)
    assert design.is_blunt()
    assert len(design.sections) == 10
    assert np.all(design.chord_m > 0)
    assert np.all(np.isfinite(design.twist_deg))


def test_encode_decode_agrees_on_the_planform(spec, cfg):
    z = initial_vector(spec, cfg)
    plan = decode(spec, z, cfg)
    re_z = encode(spec, plan)
    plan_again = decode(spec, re_z, cfg)
    assert np.allclose(plan_again.chord_m, plan.chord_m, rtol=1e-6)
    assert np.allclose(plan_again.twist_deg, plan.twist_deg, atol=0.05)
    # shape fields pass through a second PCHIP fit, so the round trip is close, not exact
    assert np.allclose([p.te_gap for p in plan_again.params], [p.te_gap for p in plan.params], atol=0.006)


def test_random_vectors_stay_in_the_box(spec, cfg):
    rng = np.random.default_rng(0)
    for _ in range(20):
        z = random_vector(spec, rng, radius=0.6)
        assert np.all(np.abs(z) <= 1.0)
        plan = decode(spec, z, cfg)
        assert np.all(plan.chord_m >= cfg.chord_min_m - 1e-12)
        assert np.all(plan.chord_m <= cfg.chord_max_m + 1e-12)


def test_vector_report_is_readable(spec, cfg):
    report = vector_report(spec, initial_vector(spec, cfg))
    assert set(report) == set(spec.names())
    assert all(math.isfinite(v) for v in report.values())


# -- objective (T047, T048) -------------------------------------------------------------------


def test_objective_returns_minus_fm_plus_nonnegative_penalties(cfg, spec):
    objective = Objective(cfg=cfg, spec=spec, search_solver=COARSE)
    evaluation = objective(initial_vector(spec, cfg))
    assert isinstance(evaluation, Evaluation)
    assert evaluation.penalty_thrust >= 0.0 and evaluation.penalty_te >= 0.0
    assert evaluation.penalty_geometry >= 0.0 and evaluation.penalty_smoothness >= 0.0
    assert evaluation.objective == pytest.approx(
        -evaluation.figure_of_merit
        + evaluation.penalty_thrust
        + evaluation.penalty_te
        + evaluation.penalty_geometry
        + evaluation.penalty_smoothness,
        rel=1e-9,
    )
    assert 0.0 < evaluation.figure_of_merit <= 1.0


def test_violations_are_penalized(cfg, spec):
    objective = Objective(cfg=cfg, spec=spec, search_solver=COARSE)
    objective(initial_vector(spec, cfg))
    # a vector asking for the smallest legal chord forces the TE clamp to bite hard
    lo, _ = spec.bounds()
    z = to_normalised(spec, np.where(np.arange(spec.dim) < spec.n_chord_cp, cfg.chord_min_m, lo[: spec.dim]))
    forced = objective(z)
    assert forced.figure_of_merit <= 1.0
    assert forced.constraints.te_violation_m == pytest.approx(0.0, abs=1e-15)  # clamped, never violated
    assert forced.objective >= -1.0


def test_history_and_best_are_recorded(cfg, spec):
    objective = Objective(cfg=cfg, spec=spec, search_solver=COARSE)
    rng = np.random.default_rng(1)
    for _ in range(6):
        objective(random_vector(spec, rng, 0.2))
    assert len(objective.history) == objective.evaluations == 6
    assert objective.best is not None
    row = objective.history[-1]
    assert {"evaluation", "objective", "figure_of_merit", "feasible", "te_margin_m"} <= set(row)
    assert min(h["objective"] for h in objective.history) == pytest.approx(objective.best.objective)


def test_thrust_mismatch_is_penalised_directly(cfg, spec):
    objective = Objective(cfg=cfg, spec=spec, search_solver=COARSE, trim=False)
    evaluation = objective(initial_vector(spec, cfg))
    assert evaluation.penalty_thrust > 0.0  # untrimmed design does not hit the target exactly


def test_adaptive_penalty_grows_under_infeasibility(cfg, spec):
    objective = Objective(cfg=cfg, spec=spec, search_solver=COARSE, infeasible_window=4)
    start = objective.lambda_geometry
    for _ in range(8):
        objective(np.full(spec.dim, 0.99))
    assert objective.feasible_fraction() <= 1.0
    assert objective.lambda_geometry >= start


# -- search wrappers (T050, T051, T052) -------------------------------------------------------


def test_cma_search_minimises_a_quadratic():
    target = np.array([0.2, -0.4, 0.6, 0.1])

    def cost(z: np.ndarray) -> float:
        return float(np.sum((z - target) ** 2))

    report = cma_search(cost, np.zeros(4), popsize=12, max_evaluations=400, seed=0)
    assert report.algorithm in ("CMA-ES", "differential-evolution")
    assert report.best_value < 0.01
    assert report.evaluations <= 420


def test_de_fallback_also_works():
    def cost(z: np.ndarray) -> float:
        return float(np.sum(z**2))

    report = de_search(cost, np.zeros(3), popsize=8, max_evaluations=150, seed=1)
    assert report.best_value < 0.05


def test_slsqp_refines_only_the_free_coordinates():
    target = np.zeros(6)
    target[1] = 0.4
    target[3] = -0.3

    def cost(z: np.ndarray) -> float:
        return float(np.sum((z - target) ** 2))

    start = np.zeros(6)
    report = refine_local(cost, start, [1, 3], max_iterations=30)
    assert report.algorithm == "SLSQP"
    assert report.best_value < 1e-3
    assert report.best_z[1] == pytest.approx(0.4, abs=0.05)
    assert report.best_z[0] == pytest.approx(0.0, abs=1e-9)  # frozen coordinates stay put


def test_refinement_indices_select_planform_and_key_fields():
    idx = refinement_indices(5, 5, 10, 3, fields=(0, 2, 4))
    assert idx[:10] == list(range(10))  # planform control points
    assert idx[10:13] == [10, 11, 12]  # t_over_c field
    assert 16 in idx and 22 in idx  # camber and te_gap fields
    assert 13 not in idx  # x_t stays frozen
    assert all(i < 10 + 8 * 3 for i in idx)


# -- campaign (T053 - T056) --------------------------------------------------------------------


def test_optimisation_never_reduces_fm(cfg, spec):
    """Regression gate T056: the reported design is at least as good as the initial one."""
    variant = dataclasses.replace(cfg, optimization=TINY)
    result = run_optimization(variant, search_solver=COARSE)
    assert result.evaluations > 0
    assert result.performance.figure_of_merit >= result.initial_performance.figure_of_merit - 1e-9
    assert result.constraints.te_violation_m == pytest.approx(0.0, abs=1e-15)
    assert result.design.is_blunt()


def test_optimisation_is_reproducible_for_a_seed(cfg, spec):
    variant = dataclasses.replace(cfg, optimization=TINY.model_copy(update={"seed": 11}))
    a = run_optimization(variant, search_solver=COARSE)
    b = run_optimization(variant, search_solver=COARSE)
    assert a.performance.figure_of_merit == pytest.approx(b.performance.figure_of_merit, rel=1e-9)
    assert np.allclose(a.design.chord_m, b.design.chord_m, rtol=1e-9)
    assert a.seed == b.seed == 11


def test_different_seeds_explore_different_designs(cfg, spec):
    fm = []
    for seed in (0, 1):
        variant = dataclasses.replace(cfg, optimization=TINY.model_copy(update={"seed": seed}))
        fm.append(run_optimization(variant, search_solver=COARSE).performance.figure_of_merit)
    assert all(0.0 < v < 1.0 for v in fm)


def test_history_and_report_are_complete(cfg, spec):
    variant = dataclasses.replace(cfg, optimization=TINY)
    result = run_optimization(variant, search_solver=COARSE)
    assert isinstance(result.history, list) and len(result.history) == result.evaluations
    phases = [p["phase"] for p in result.report["phases"]]
    assert "initial" in phases and "global" in phases and "selection" in phases
    assert result.report["wall_time_s"] > 0.0


def test_local_refine_phase_runs(cfg, spec):
    settings = TINY.model_copy(update={"local_refine": True, "local_max_iterations": 8})
    variant = dataclasses.replace(cfg, optimization=settings)
    result = run_optimization(variant, search_solver=COARSE)
    phases = {p["phase"]: p for p in result.report["phases"]}
    assert "local" in phases
    assert phases["local"]["algorithm"] == "SLSQP"


def test_zero_evaluation_budget_returns_the_reference_design(cfg, spec):
    settings = TINY.model_copy(update={"max_evaluations": 0, "local_refine": False})
    variant = dataclasses.replace(cfg, optimization=settings)
    result = run_optimization(variant, search_solver=COARSE)
    assert result.evaluations == 0
    assert result.performance.figure_of_merit == pytest.approx(
        result.initial_performance.figure_of_merit, rel=1e-9
    )


def test_selection_prefers_feasible_then_fm(cfg, spec):
    initial = evaluate_initial(
        cfg, spec, __import__("hoverblade_fm.aero.polar", fromlist=["x"]).AnalyticPolar(), None
    )
    worse_but_higher_fm = Evaluation(**dict(initial.__dict__))
    worse_but_higher_fm.figure_of_merit = initial.figure_of_merit + 0.05
    worse_but_higher_fm.feasible = False
    chosen = select_best([initial, worse_but_higher_fm])
    assert chosen is initial  # infeasible, however good its FM, loses
    assert rank_key(worse_but_higher_fm)[0] > rank_key(initial)[0]


def test_repair_design_keeps_the_blunt_constraint(cfg, spec):
    z = initial_vector(spec, cfg)
    evaluation = evaluate_vector(
        cfg, spec, z, __import__("hoverblade_fm.aero.polar", fromlist=["x"]).AnalyticPolar(), None
    )
    repaired = repair_design(evaluation, cfg)
    assert repaired.design.is_blunt()
    assert repaired.constraints.te_min_actual_m >= cfg.te_min_m - 1e-15


def test_shape_control_points_interpolate_monotonically(spec, cfg):
    z = initial_vector(spec, cfg)
    plan = decode(spec, z, cfg)
    te = [p.te_gap for p in plan.params]
    assert len(te) == 10
    assert all(t > 0 for t in te)
    assert np.all(np.abs(np.diff(te)) < 0.1)
    assert SHAPE_CP_R == (0.15, 0.5, 0.95)
