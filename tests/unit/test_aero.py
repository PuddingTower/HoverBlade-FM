"""Hover aerodynamics tests: FM formulas, analytic polar corrections and the BEMT solution (T033-T045)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from hoverblade_fm.aero.bemt import BemtSettings, SpanInterp, prandtl_loss, solve_hover, trim_collective
from hoverblade_fm.aero.fm import (
    figure_of_merit,
    ideal_induced_velocity,
    ideal_power,
    power_coefficient,
    thrust_coefficient,
)
from hoverblade_fm.aero.hover import evaluate_design, hover_power_split
from hoverblade_fm.aero.polar import AnalyticPolar, LinearPolar, sweep_polar
from hoverblade_fm.config.schema import UserInput, resolve
from hoverblade_fm.geometry.sections import build_design, plan_from_raw

MINIMAL = {"thrust_n": 5000.0, "radius_m": 2.5, "trailing_edge_thickness_m": 0.0025}


@pytest.fixture(scope="module")
def cfg():
    return resolve(UserInput.model_validate(MINIMAL))


@pytest.fixture(scope="module")
def design(cfg):
    return build_design(plan_from_raw(cfg), cfg)


# -- formulas (T034, T035) ------------------------------------------------------------------


def test_ideal_power_and_induced_velocity_match_textbook(cfg):
    area = math.pi * cfg.radius_m**2
    assert cfg.disk_area == pytest.approx(area)
    assert ideal_power(5000.0, 1.225, area) == pytest.approx(5000.0**1.5 / math.sqrt(2 * 1.225 * area))
    assert ideal_induced_velocity(5000.0, 1.225, area) == pytest.approx(
        math.sqrt(5000.0 / (2 * 1.225 * area))
    )


def test_figure_of_merit_definition():
    assert figure_of_merit(100.0, 100.0) == pytest.approx(1.0)
    assert figure_of_merit(100.0, 200.0) == pytest.approx(0.5)
    assert figure_of_merit(100.0, 0.0) == 0.0
    assert figure_of_merit(100.0, -5.0) == 0.0
    assert figure_of_merit(100.0, float("nan")) == 0.0


def test_dimensionless_coefficients(cfg):
    ct = thrust_coefficient(cfg.thrust_n, cfg.rho, cfg.disk_area, cfg.tip_speed)
    assert 0.001 < ct < 0.03
    cp = power_coefficient(cfg.power_ideal_w, cfg.rho, cfg.disk_area, cfg.omega, cfg.radius_m)
    assert cp > 0.0


# -- analytic polar (T040 - T043) ------------------------------------------------------------


def test_polar_is_dependency_free_and_vectorised():
    polar = AnalyticPolar()
    alpha = np.linspace(-8.0, 16.0, 49)
    point = polar(alpha, 1.0e6, 0.3, 0.12, 0.01, 0.03)
    assert point.cl.shape == alpha.shape and point.cd.shape == alpha.shape
    assert np.all(np.diff(point.alpha_deg) > 0)
    assert np.all(point.cd > 0.0)


def test_lift_curve_slope_and_zero_lift_shift_with_camber():
    polar = AnalyticPolar()
    flat = polar(np.array([-2.0, 0.0, 2.0, 4.0]), 2.0e6, 0.2, 0.12, 0.008, 0.0)
    cambered = polar(np.array([-2.0, 0.0, 2.0, 4.0]), 2.0e6, 0.2, 0.12, 0.008, 0.05)
    slope = float(np.polyfit(np.radians([-2.0, 0.0, 2.0]), flat.cl[:3], 1)[0])
    assert 4.5 < slope < 7.0  # around 2*pi with the viscous efficiency factor
    # a symmetric section unloads at zero geometric incidence, a cambered one does not
    assert flat.cl[1] == pytest.approx(0.0, abs=1e-12)
    assert cambered.cl[1] > 0.0


def test_blunt_trailing_edge_increases_drag_monotonically():
    polar = AnalyticPolar()
    gaps = np.array([0.002, 0.005, 0.010, 0.020, 0.040])
    point = polar(np.full(gaps.size, 4.0), 2.0e6, 0.25, 0.12, gaps, 0.03)
    assert np.all(np.diff(point.cd) > 0.0)
    assert point.cd[0] < 0.02 and point.cd[-1] > point.cd[0] + 0.03
    assert np.allclose(point.cl, point.cl[0], rtol=1e-9)  # TE drag term must not change lift


def test_reynolds_number_affects_drag_and_max_lift():
    polar = AnalyticPolar()
    low = polar(np.array([6.0]), 1.0e5, 0.2, 0.12, 0.008, 0.03)
    high = polar(np.array([6.0]), 6.0e6, 0.2, 0.12, 0.008, 0.03)
    assert low.cd[0] > high.cd[0]
    cl_max_low = polar.cl_max(np.array([1.0e5]), np.array([0.12]), np.array([0.2]))
    cl_max_high = polar.cl_max(np.array([6.0e6]), np.array([0.12]), np.array([0.2]))
    assert cl_max_low < cl_max_high


def test_mach_number_degrades_performance():
    polar = AnalyticPolar()
    sub = polar(np.array([4.0]), 3.0e6, 0.30, 0.12, 0.008, 0.03)
    trans = polar(np.array([4.0]), 3.0e6, 0.86, 0.12, 0.008, 0.03)
    assert trans.cd[0] > sub.cd[0] + 0.02  # wave drag onset
    # Prandtl-Glauert steepens the lift slope while compressibility caps the maximum lift
    assert polar.lift_curve_slope(np.array([0.85]))[0] > polar.lift_curve_slope(np.array([0.2]))[0]
    assert polar.cl_max(np.array([3e6]), np.array([0.12]), np.array([0.86])) < polar.cl_max(
        np.array([3e6]), np.array([0.12]), np.array([0.2])
    )


def test_stall_saturates_lift_smoothly():
    """The analytic polar saturates lift instead of dropping it: monotone and bounded, which is
    what the optimiser needs, with the post-stall penalty carried by drag."""
    polar = AnalyticPolar()
    alpha = np.linspace(-10.0, 25.0, 200)
    point = polar(alpha, 2.0e6, 0.3, 0.15, 0.01, 0.03)
    assert point.cl.max() < 1.7
    assert np.all(np.diff(point.cl) > 0.0)  # monotone, no post-stall reversal
    assert np.max(np.diff(point.cl)) < 0.05  # and no discontinuity
    assert point.cd[0] == pytest.approx(point.cd[40], rel=1e-9)  # attached range: lift-independent cd
    assert point.cd[-1] > 1.5 * point.cd[0]  # deep post-stall drag rise


def test_polar_sweep_table_is_usable():
    table = sweep_polar(
        AnalyticPolar(), reynolds=5.0e5, mach=0.35, t_over_c=0.12, te_over_c=0.01, camber=0.03
    )
    cl, cd, cm = table.sample(np.array([0.0, 4.0, 8.0]))
    assert cl[0] < cl[1] < cl[2]
    # induced drag belongs to the momentum balance, so attached-flow cd is lift-independent
    assert cd[0] == pytest.approx(cd[2], rel=1e-6)
    assert np.all(np.isfinite(cm))
    deep = sweep_polar(AnalyticPolar(), alpha_max=20.0).sample(np.array([18.0]))[1]
    assert deep > cd[2]


# -- BEMT (T036 - T038, T044, T045) ----------------------------------------------------------


def test_solver_converges_and_returns_positive_power(cfg, design):
    result = solve_hover(design, cfg)
    assert result.converged
    assert result.thrust_n > 0 and result.torque_nm > 0 and result.power_total_w > 0
    assert result.iterations < cfg.solver.n_itmax
    assert 0.0 < result.figure_of_merit <= 1.0


def test_power_split_is_additive(cfg, design):
    _, performance, _ = evaluate_design(design, cfg)
    total = performance.power_actual_w
    split = hover_power_split(performance)
    assert total > 0
    assert sum(split.values()) == pytest.approx(1.0, rel=1e-6)
    assert performance.power_induced_w > 0 and performance.power_profile_w > 0


def test_prandtl_tip_loss_vanishes_at_the_tip_and_recovers_inboard():
    x = np.array([0.2, 0.5, 0.8, 0.95, 0.9999])
    sin_phi = np.full(x.size, math.sin(math.radians(8.0)))
    loss = prandtl_loss(x, sin_phi, blade_count=4, settings=BemtSettings())
    assert loss[-1] < loss[-2] < 1.0
    assert loss[1] > 0.95
    assert np.all(loss <= 1.0 + 1e-12) and np.all(loss > 0.0)


def test_tip_loss_reduces_total_thrust(cfg, design):
    with_loss = solve_hover(design, cfg, settings=BemtSettings(tip_loss=True, root_loss=True))
    without = solve_hover(design, cfg, settings=BemtSettings(tip_loss=False, root_loss=False))
    assert with_loss.thrust_n < without.thrust_n


def test_root_loss_reduces_inboard_loading(cfg, design):
    load = solve_hover(design, cfg, settings=BemtSettings(root_loss=True)).radial
    bare = solve_hover(design, cfg, settings=BemtSettings(root_loss=False, tip_loss=False)).radial
    inner = load.r_R < 0.25
    assert np.mean(load.loss_factor[inner]) < np.mean(bare.loss_factor[inner])


def test_span_interpolation_recovers_the_stations(cfg, design):
    interp = SpanInterp(design, cfg)
    x = design.r_R
    assert np.allclose(interp("chord", x), design.chord_m, rtol=1e-9)
    assert np.allclose(interp("twist", x), design.twist_deg, rtol=1e-9)


def test_thrust_trim_matches_the_target(cfg, design):
    delta, result = trim_collective(design, cfg)
    assert abs(result.thrust_n - cfg.thrust_n) / cfg.thrust_n <= cfg.solver.thrust_tolerance + 1e-3
    assert -10.0 <= delta <= 18.0
    more = solve_hover(design, cfg, collective_trim_deg=delta + 3.0)
    assert more.thrust_n > result.thrust_n  # thrust is monotone in collective


def test_momentum_theory_limit_without_profile_drag(cfg, design):
    """T045: an inviscid, loss-free rotor must approach the momentum-theory power bound."""
    _, ideal = trim_collective(
        design, cfg, polar=LinearPolar(cd=0.0), settings=BemtSettings(tip_loss=False, root_loss=False)
    )
    assert abs(ideal.thrust_n - cfg.thrust_n) / cfg.thrust_n < 0.02
    assert ideal.figure_of_merit > 0.9
    assert ideal.power_profile_w == pytest.approx(0.0, abs=1e-9)
    assert ideal.power_total_w >= ideal.power_ideal_w * 0.999


def test_tip_losses_make_the_inviscid_case_worse_not_better(cfg, design):
    _, lossless = trim_collective(
        design, cfg, polar=LinearPolar(cd=0.0), settings=BemtSettings(tip_loss=False, root_loss=False)
    )
    _, lossy = trim_collective(design, cfg, polar=LinearPolar(cd=0.0), settings=BemtSettings())
    assert lossy.figure_of_merit < lossless.figure_of_merit


def test_adding_profile_drag_never_raises_fm(cfg, design):
    _, clean = trim_collective(design, cfg, polar=LinearPolar(cd=0.0))
    _, dirty = trim_collective(design, cfg, polar=LinearPolar(cd=0.03))
    assert dirty.figure_of_merit < clean.figure_of_merit
    assert dirty.power_profile_w > clean.power_profile_w


def test_misc_power_interface_is_reserved(cfg, design):
    import dataclasses

    variant = dataclasses.replace(cfg, solver=cfg.solver.model_copy(update={"misc_power_w": 5000.0}))
    result = solve_hover(design, variant)
    assert result.power_misc_w == 5000.0
    assert result.power_total_w > 0


def test_radial_load_arrays_have_matching_shapes(cfg, design):
    load = solve_hover(design, cfg).radial
    n = load.r_R.size
    for name in (
        "thrust_gradient",
        "torque_gradient",
        "power_gradient",
        "induced_velocity",
        "cl",
        "cd",
        "mach",
        "reynolds",
    ):
        assert getattr(load, name).shape == (n,), name
    assert np.all(load.thrust_gradient > -1e-6)
    assert np.all(load.reynolds > 1e3)
    assert load.loss_factor[0] < 0.9 and load.loss_factor[-1] < 0.2
