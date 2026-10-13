"""Geometry tests: CST construction, the blunt trailing-edge HARD constraint (T032), planform and checks."""

from __future__ import annotations

import numpy as np
import pytest

from hoverblade_fm.config.schema import UserInput, resolve
from hoverblade_fm.geometry.cst import SectionParameters, build_airfoil, le_radius
from hoverblade_fm.geometry.sections import build_design, plan_from_raw
from hoverblade_fm.geometry.spanwise import (
    DEFAULT_CHORD_CP_R,
    chord_distribution,
    limit_rate,
    reference_planform,
    smoothness_metrics,
    spanwise_penalty,
    twist_distribution,
)
from hoverblade_fm.geometry.trailing_edge import (
    check_trailing_edge,
    clamp_te_gap,
    enforce_blunt,
    te_edge_length_m,
    te_included_angle_deg,
    te_radius_estimate_m,
    te_thickness_at,
)
from hoverblade_fm.geometry.validate import (
    geometry_penalty,
    validate_airfoil_points,
    validate_design,
)
from hoverblade_fm.models import SECTION_COUNT, AirfoilPoints, Design

MINIMAL = {"thrust_n": 5000.0, "radius_m": 2.5, "trailing_edge_thickness_m": 0.0025}


@pytest.fixture(scope="module")
def cfg():
    return resolve(UserInput.model_validate(MINIMAL))


@pytest.fixture(scope="module")
def design(cfg) -> Design:
    return build_design(plan_from_raw(cfg), cfg, label="test")


# -- station grid (T020) -------------------------------------------------------------------


def test_exactly_ten_stations_by_default(cfg):
    plan = plan_from_raw(cfg)
    assert plan.r_R.size == SECTION_COUNT
    assert list(cfg.r_stations) == [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.97]
    assert max(cfg.r_stations) < 1.0


def test_design_builds_ten_sections_with_files(cfg, design):
    assert len(design.sections) == SECTION_COUNT
    assert all(s.airfoil_file.endswith(".csv") for s in design.sections)
    assert [s.station_id for s in design.sections] == list(range(SECTION_COUNT))


# -- CST construction (T021, T025, T026) ---------------------------------------------------


def test_cst_contour_is_closed_and_ordered():
    points = build_airfoil(SectionParameters())
    assert points.x.size == 2 * points.n_per_surface
    assert points.x[0] == pytest.approx(1.0) and points.x[-1] == pytest.approx(1.0)
    x_lower, y_lower, x_upper, y_upper = points.surface_pairs()
    assert x_lower[0] == pytest.approx(0.0) and x_lower[-1] == pytest.approx(1.0)
    assert x_upper[0] == pytest.approx(0.0) and x_upper[-1] == pytest.approx(1.0)
    assert np.all(np.diff(x_upper) > 0) and np.all(np.diff(x_lower) > 0)


def test_thickness_and_camber_controls_are_effective():
    thin = build_airfoil(SectionParameters(t_over_c=0.06))
    thick = build_airfoil(SectionParameters(t_over_c=0.24))
    assert thick.max_thickness > thin.max_thickness * 3.0
    flat = build_airfoil(SectionParameters(camber=0.0))
    cambered = build_airfoil(SectionParameters(camber=0.06))
    assert cambered.max_camber > flat.max_camber + 0.03
    assert cambered.max_camber == pytest.approx(0.06, abs=0.012)


def test_max_thickness_location_follows_x_t():
    for x_t in (0.22, 0.30, 0.40):
        points = build_airfoil(SectionParameters(x_t=x_t, te_gap=0.004))
        assert points.thickness_location == pytest.approx(x_t, abs=0.05)


def test_leading_edge_radius_is_positive_and_scales_with_thickness():
    small = le_radius(build_airfoil(SectionParameters(t_over_c=0.08)))
    large = le_radius(build_airfoil(SectionParameters(t_over_c=0.20)))
    assert 0 < small < large
    assert large > 2.0 * small


def test_realised_max_thickness_equals_the_parameter():
    for t_over_c in (0.06, 0.12, 0.24):
        for te_gap in (0.002, 0.01, 0.05):
            points = build_airfoil(SectionParameters(t_over_c=t_over_c, te_gap=te_gap))
            assert points.max_thickness == pytest.approx(t_over_c, rel=0.02)
            assert points.te_thickness == pytest.approx(te_gap, rel=1e-6, abs=1e-9)


def test_le_factor_blunts_the_nose_at_fixed_thickness():
    base = build_airfoil(SectionParameters(t_over_c=0.12, le_factor=0.9))
    blunt = build_airfoil(SectionParameters(t_over_c=0.12, le_factor=1.4))
    assert le_radius(blunt) > 1.2 * le_radius(base)
    assert blunt.max_thickness == pytest.approx(base.max_thickness, rel=0.02)


# -- THE hard constraint (T022, T023) ------------------------------------------------------


def test_te_gap_equals_the_parameter_exactly():
    for gap in (0.002, 0.01, 0.05):
        points = build_airfoil(SectionParameters(te_gap=gap))
        assert points.te_thickness == pytest.approx(gap, rel=1e-9, abs=1e-12)


def test_surfaces_never_coincide_at_the_trailing_edge():
    for gap in (0.001, 0.0025, 0.01):
        points = build_airfoil(SectionParameters(te_gap=gap))
        assert points.y[-1] - points.y[0] > 0.0
        assert points.te_thickness == pytest.approx(gap, abs=1e-12)


def test_clamp_te_gap_enforces_the_user_minimum():
    # 2.5 mm on a 175 mm chord is a gap of 0.0143
    assert clamp_te_gap(0.001, 0.175, 0.0025) == pytest.approx(0.0025 / 0.175)
    assert clamp_te_gap(0.05, 0.175, 0.0025) == pytest.approx(0.05)


def test_enforce_blunt_never_allows_a_sharp_edge(cfg):
    candidates = [
        SectionParameters(te_gap=0.0),
        SectionParameters(te_gap=1e-9),
        SectionParameters(te_gap=1e-6),
        SectionParameters(te_gap=cfg.te_min_m / 10.0),
    ]
    for chord in (cfg.chord_min_m, 0.05, cfg.chord_max_m):
        for params in candidates:
            patched, clamped = enforce_blunt(params, chord, cfg)
            assert clamped is True
            assert patched.te_gap * chord >= cfg.te_min_m - 1e-15


def test_every_station_of_every_design_meets_the_minimum(cfg):
    """The project's headline guarantee: no generated section may have a sharp trailing edge."""
    for te_min in (0.0008, 0.0025, 0.006, 0.012):
        variant = resolve(UserInput.model_validate(dict(MINIMAL, trailing_edge_thickness_m=te_min)))
        for seed in range(4):
            rng = np.random.default_rng(seed)
            chord_cp = rng.uniform(variant.chord_min_m, variant.chord_max_m, size=len(DEFAULT_CHORD_CP_R))
            params = [
                SectionParameters(
                    t_over_c=rng.uniform(0.06, 0.24),
                    camber=rng.uniform(0.0, 0.07),
                    te_gap=rng.uniform(0.0, 0.06),  # deliberately includes illegal values
                )
                for _ in range(SECTION_COUNT)
            ]
            design = build_design(plan_from_raw(variant, chord_cp=chord_cp, section_params=params), variant)
            assert design.is_blunt(), f"TE violation with te_min={te_min}, seed={seed}"
            assert design.te_margin() >= -1e-15
            for section in design.sections:
                assert section.te_thickness_m >= te_min - 1e-15
                assert section.te_thickness_m > 1e-6
                assert geometry_penalty(design, variant) < 1e-6


def test_a_zero_gap_request_is_rejected_before_it_reaches_geometry(cfg):
    with pytest.raises(ValueError):
        SectionParameters(te_gap=-0.001).validate()
    # te_gap == 0 is legal as an *input* only because the decoder clamps it upward
    plan = plan_from_raw(cfg, section_params=[SectionParameters(te_gap=0.0) for _ in range(SECTION_COUNT)])
    assert all(p.te_gap > 0 for p in plan.params)
    assert all(plan.te_clamped)


def test_trailing_edge_checks_report_the_plan_fields(cfg):
    design = build_design(plan_from_raw(cfg), cfg)
    section = design.sections[5]
    check = check_trailing_edge(section.points, section.chord_m, cfg, section.station_id)
    assert check.te_thickness_m >= cfg.te_min_m
    assert check.te_margin_m >= 0.0
    assert check.hard_ok is True
    assert check.te_over_chord > 0.0
    assert check.te_at_0p995_m > 0.0 and check.te_at_0p990_m > 0.0
    assert check.te_radius_m > 0.0
    assert check.te_edge_length_m > 0.0
    assert set(check.to_dict()) >= {"te_thickness_m", "te_margin_m", "te_over_chord", "hard_ok"}


def test_te_metrics_from_points_are_physical(cfg):
    points = build_airfoil(SectionParameters(te_gap=0.02))
    chord = 0.2
    assert te_edge_length_m(points, chord) >= 0.02 * chord * 0.5
    assert te_radius_estimate_m(points, chord) > 0.0
    assert te_thickness_at(points, chord, 0.995) > 0.0
    assert te_included_angle_deg(SectionParameters(te_gap=0.02)) >= 0.0


# -- planform (T027-T029) -------------------------------------------------------------------


def test_chord_and_twist_distributions_respect_bounds(cfg):
    chord = chord_distribution(np.full(len(DEFAULT_CHORD_CP_R), 0.2 * cfg.radius_m), cfg)
    assert chord.size == SECTION_COUNT
    assert np.all(chord >= cfg.chord_min_m - 1e-12)
    assert np.all(chord <= cfg.chord_max_m + 1e-12)
    twist = twist_distribution(np.array([30.0, 20.0, 10.0, 0.0, -10.0]), cfg)
    assert np.all(twist >= -4.0 - 1e-9) and np.all(twist <= 28.0 + 1e-9)


def test_reference_planform_is_feasible_and_tapered(cfg):
    chord_cp, twist_cp = reference_planform(cfg)
    plan = plan_from_raw(cfg, chord_cp=chord_cp, twist_cp=twist_cp)
    design = build_design(plan, cfg)
    assert validate_design(design, cfg) == []
    assert np.all(np.diff(plan.chord_m) <= 1e-9)  # non-increasing outward
    assert plan.chord_m[0] > plan.chord_m[-1]
    assert design.solidity < 0.2


@pytest.mark.parametrize("n_blades,n_expected", [(2, 10), (4, 10)], ids=["two", "four"])
def test_planform_works_for_other_blade_counts(cfg, n_blades, n_expected):
    import dataclasses

    variant = dataclasses.replace(cfg, blade_count=n_blades)
    plan = plan_from_raw(variant)
    assert plan.chord_m.size == n_expected
    assert np.all(plan.chord_m > 0)


def test_limit_rate_enforces_the_gradient_cap():
    x = np.linspace(0.1, 0.97, 10)
    values = np.array([30.0, 5.0, 4.0, 3.0, 2.0, 1.0, 0.5, 0.0, -0.5, -1.0])
    limited = limit_rate(x, values, 40.0)
    assert np.max(np.abs(np.diff(limited) / np.diff(x))) <= 40.0 + 1e-9
    assert limited.min() >= values.min() - 1e-9
    assert limited.max() <= values.max() + 1e-9
    # an already-compliant profile is untouched
    gentle = limit_rate(x, np.linspace(30.0, -1.0, 10), 40.0)
    assert np.allclose(gentle, np.linspace(30.0, -1.0, 10))


def test_smoothness_metrics_and_penalty(cfg, design):
    metrics = smoothness_metrics(design.r_R, design.chord_m)
    assert metrics["max_abs_dv_dr"] >= 0.0
    assert spanwise_penalty(design.r_R, design.chord_m, design.twist_deg, cfg.radius_m) >= 0.0
    wild = design.chord_m.copy()
    wild[3] *= 8.0
    assert spanwise_penalty(design.r_R, wild, design.twist_deg, cfg.radius_m) > 0.0


# -- validation (T030) ----------------------------------------------------------------------


def test_valid_design_reports_no_issues(cfg, design):
    assert validate_design(design, cfg) == []
    assert geometry_penalty(design, cfg) == pytest.approx(0.0)


def test_crossing_surfaces_are_detected(cfg):
    points = build_airfoil(SectionParameters(t_over_c=0.12, te_gap=0.004))
    x, y = points.x.copy(), points.y.copy()
    n = points.n_per_surface
    y[n + 5] = y[n + 5] - 1.0  # push the upper surface through the lower one
    issues = validate_airfoil_points(AirfoilPoints(x=x, y=y, n_per_surface=n))
    assert any("crossover" in msg for msg in issues)


def test_sharp_te_is_detected_when_bypassing_the_decoder(cfg):
    points = build_airfoil(SectionParameters(te_gap=1e-12))
    issues = validate_airfoil_points(points, cfg.airfoil)
    assert any("sharp trailing edge" in msg.lower() for msg in issues)


def test_validate_design_catches_a_violated_te(cfg):
    import dataclasses

    design = build_design(plan_from_raw(cfg), cfg)
    broken = dataclasses.replace(design, te_min_m=design.min_te_thickness() * 2.0)
    issues = validate_design(broken, dataclasses.replace(cfg, te_min_m=cfg.te_min_m * 2.0))
    assert any("trailing-edge thickness" in msg for msg in issues)
