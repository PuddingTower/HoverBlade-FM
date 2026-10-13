"""Tests for the input schema, unit handling and automatic parameter strategies (T011-T018)."""

from __future__ import annotations

import json
import math

import pytest
from pydantic import ValidationError

from hoverblade_fm.config.defaults import atmosphere, auto_blade_count, auto_rotor_speed, feasibility_warnings
from hoverblade_fm.config.schema import (
    CHORD_MAX_OVER_R,
    InputValidationError,
    OptimizationSettings,
    UserInput,
    load_input,
    resolve,
)
from hoverblade_fm.config.units import UnitError, to_metres, to_newtons

MINIMAL = {"thrust_n": 5000.0, "radius_m": 2.5, "trailing_edge_thickness_m": 0.0025}


def test_three_inputs_are_enough():
    cfg = resolve(UserInput.model_validate(MINIMAL))
    assert cfg.thrust_n == 5000.0
    assert cfg.radius_m == 2.5
    assert cfg.te_min_m == 0.0025
    assert len(cfg.r_stations) == 10


def test_missing_input_is_rejected_with_a_clear_message():
    with pytest.raises(Exception) as excinfo:
        UserInput.model_validate({"thrust_n": 5000.0, "radius_m": 2.5})
    assert "trailing_edge_thickness_m" in str(excinfo.value)


@pytest.mark.parametrize("field", ["thrust_n", "radius_m", "trailing_edge_thickness_m"])
def test_non_positive_inputs_are_rejected(field):
    payload = dict(MINIMAL)
    payload[field] = -1.0
    with pytest.raises(ValidationError):
        UserInput.model_validate(payload)
    with pytest.raises(InputValidationError):
        load_input(None, **({field: -1.0} if field != "trailing_edge_thickness_m" else {"te_min_m": -1.0}))


def test_unknown_extra_key_is_rejected():
    payload = dict(MINIMAL)
    payload["typo_field"] = 1
    with pytest.raises(ValidationError):
        UserInput.model_validate(payload)


def test_unit_conversions():
    assert to_metres(2500.0, "mm") == pytest.approx(2.5)
    assert to_newtons(2.0, "kN") == pytest.approx(2000.0)
    assert to_metres(10.0, "in") == pytest.approx(0.254)
    with pytest.raises(UnitError):
        to_metres(1.0, "furlongs")


def test_unit_aware_inputs_are_converted_to_si():
    ui = UserInput(
        thrust=500.0,
        radius=82.0,
        trailing_edge_thickness=2.5,
        units={"thrust": "kgf", "length": "in"},
    )
    cfg = resolve(ui)
    assert cfg.thrust_n == pytest.approx(500 * 9.80665)
    assert cfg.radius_m == pytest.approx(82 * 0.0254)
    assert cfg.te_min_m == pytest.approx(2.5 * 0.0254)


def test_atmosphere_defaults_are_sea_level_isa():
    rho, mu, a = atmosphere(0.0)
    assert rho == pytest.approx(1.225, rel=1e-3)
    assert a == pytest.approx(340.3, rel=1e-3)
    assert mu == pytest.approx(1.81e-5, rel=2e-2)
    rho_alt, _, a_alt = atmosphere(3000.0)
    assert rho_alt < rho and a_alt < a


@pytest.mark.parametrize(
    ("radius", "thrust", "expected"),
    [(0.4, 30.0, 2), (0.55, 120.0, 3), (2.5, 5000.0, 4), (4.0, 20000.0, 4)],
)
def test_blade_count_policy(radius, thrust, expected):
    got = auto_blade_count(radius, thrust, 1.225)
    assert int(got.value) == expected
    assert "disk loading" in got.reason


def test_rotor_speed_respects_the_tip_mach_limit():
    cfg = resolve(UserInput.model_validate(MINIMAL))
    assert cfg.tip_mach <= cfg.tip_mach_limit + 1e-9
    assert cfg.rpm > 0
    assert "RPM" in " ".join(cfg.reasons)


def test_tip_mach_override_is_enforced():
    ui = UserInput.model_validate(dict(MINIMAL, tip_mach_limit=0.45))
    cfg = resolve(ui)
    assert cfg.tip_mach <= 0.45 + 1e-9


def test_explicit_rpm_above_the_mach_limit_is_rejected():
    ui = UserInput.model_validate(dict(MINIMAL, rpm=2000.0))
    with pytest.raises(InputValidationError):
        resolve(ui)


def test_station_count_is_fixed_at_ten():
    with pytest.raises(InputValidationError):
        resolve(UserInput.model_validate(dict(MINIMAL, r_stations=[0.1, 0.2, 0.3])))


def test_stations_must_increase_and_stay_below_the_tip():
    with pytest.raises(InputValidationError):
        resolve(
            UserInput.model_validate(
                dict(MINIMAL, r_stations=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
            )
        )
    with pytest.raises(InputValidationError):
        resolve(
            UserInput.model_validate(
                dict(MINIMAL, r_stations=[0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.05])
            )
        )


def test_chord_bounds_are_derived_from_radius():
    cfg = resolve(UserInput.model_validate(MINIMAL))
    assert cfg.chord_min_m < cfg.chord_max_m
    assert cfg.chord_max_m == pytest.approx(CHORD_MAX_OVER_R * 2.5)


def test_infeasible_inputs_warn():
    warnings = feasibility_warnings(0.3, 3000.0, 0.05, 1.225, 340.29, 2, 300.0)
    assert any("disk loading" in w.lower() for w in warnings)
    thick_te = feasibility_warnings(2.5, 5000.0, 0.4, 1.225, 340.29, 4, 80.0)
    assert any("trailing-edge" in w for w in thick_te)


def test_ideal_power_matches_the_formula():
    cfg = resolve(UserInput.model_validate(MINIMAL))
    expected = 5000.0**1.5 / math.sqrt(2.0 * 1.225 * math.pi * 2.5**2)
    assert cfg.power_ideal_w == pytest.approx(expected, rel=1e-4)


def test_input_hash_is_stable_and_input_sensitive():
    a = resolve(UserInput.model_validate(MINIMAL))
    b = resolve(UserInput.model_validate(MINIMAL))
    c = resolve(UserInput.model_validate(dict(MINIMAL, thrust_n=5001.0)))
    assert a.input_hash == b.input_hash
    assert a.input_hash != c.input_hash


def test_optimisation_presets_apply():
    for preset, evals in (("fast", 320), ("standard", 1500), ("full", 3000)):
        opts = OptimizationSettings(preset=preset)
        assert opts.max_evaluations == evals
    with pytest.raises(ValidationError):
        OptimizationSettings(preset="nope")


def test_json_input_round_trip(tmp_path):
    path = tmp_path / "input.json"
    path.write_text(json.dumps(MINIMAL), encoding="utf-8")
    cfg = resolve(load_input(path))
    assert cfg.radius_m == 2.5
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(InputValidationError):
        load_input(bad)


def test_cli_scalars_override_file_values(tmp_path):
    path = tmp_path / "input.json"
    path.write_text(json.dumps(MINIMAL), encoding="utf-8")
    ui = load_input(path, thrust_n=9000.0)
    cfg = resolve(ui)
    assert cfg.thrust_n == 9000.0
    assert cfg.radius_m == 2.5


def test_omega_speed_selection_slows_a_too_lightly_loaded_rotor():
    # a large radius with modest thrust would sit far below the hover CT band
    got = auto_rotor_speed(6.0, 3000.0, 4, 1.225, 340.29)
    tip_mach = float(got.value) * 6.0 / 340.29
    assert tip_mach <= 0.65 + 1e-9
    assert "CT" in got.reason
