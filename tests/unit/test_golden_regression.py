"""Golden regression tests (plan section 21.3): output schema, FM floor, no sharp-edge regression."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest

from hoverblade_fm.config.schema import OptimizationSettings, SolverSettings, UserInput, resolve
from hoverblade_fm.geometry.sections import build_design, plan_from_raw
from hoverblade_fm.models import SECTION_COUNT
from hoverblade_fm.optimize.pipeline import run_optimization

GOLDEN = Path(__file__).resolve().parents[1] / "golden"
INPUT = {"thrust_n": 5000.0, "radius_m": 2.5, "trailing_edge_thickness_m": 0.0025}


@pytest.fixture(scope="module")
def baseline() -> dict:
    return json.loads((GOLDEN / "fm_baseline.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rerun() -> object:
    cfg = resolve(UserInput.model_validate(INPUT))
    cfg.optimization = OptimizationSettings(
        preset="custom",
        max_evaluations=120,
        popsize=12,
        local_refine=False,
        restarts=1,
        seed=42,
    )
    return run_optimization(cfg, search_solver=SolverSettings(n_radial=81, n_itmax=150))


def test_reference_geometry_reproduces_the_golden_sections(baseline):
    """The un-optimised design is fully deterministic, so it must reproduce station by station."""
    cfg = resolve(UserInput.model_validate(INPUT))
    design = build_design(plan_from_raw(cfg), cfg)
    rows = list(csv.DictReader((GOLDEN / "sections_summary.csv").read_text(encoding="utf-8").splitlines()))
    assert len(rows) == SECTION_COUNT
    for row, section in zip(rows, design.sections, strict=True):
        assert section.r_R == pytest.approx(float(row["r_R"]), abs=1e-12)
        assert section.te_thickness_m >= cfg.te_min_m - 1e-15
        assert float(row["te_thickness_m"]) >= cfg.te_min_m - 1e-15
    for row in rows:
        assert cfg.chord_min_m - 1e-12 <= float(row["chord_m"]) <= cfg.chord_max_m + 1e-12
        assert 0.02 <= float(row["thickness_to_chord"]) <= 0.30


def test_golden_section_distribution_is_reproduced(rerun):
    """Same seed, same solver resolution -> the campaign must land on the recorded planform."""
    rows = list(csv.DictReader((GOLDEN / "sections_summary.csv").read_text(encoding="utf-8").splitlines()))
    assert np.allclose([float(r["chord_m"]) for r in rows], rerun.design.chord_m, rtol=0.02, atol=1e-6), (
        "chord distribution moved by more than 2%: a change to the planform prior, design space or "
        "aero model is a breaking change for this baseline"
    )
    assert np.allclose([float(r["twist_deg"]) for r in rows], rerun.design.twist_deg, atol=1.5)


def test_optimised_fm_never_regresses_below_the_baseline(baseline, rerun):
    initial = rerun.initial_performance.figure_of_merit
    final = rerun.performance.figure_of_merit
    assert final >= initial - 1e-9, "the optimiser returned a worse design than the reference"
    assert final >= baseline["final_figure_of_merit"] * 0.97, (
        f"FM dropped from {baseline['final_figure_of_merit']:.4f} to {final:.4f}: "
        "a change to the aero model or design space must not silently lose hover performance"
    )
    assert initial == pytest.approx(baseline["initial_figure_of_merit"], rel=0.02)


def test_thrust_still_matched_and_te_still_blunt(baseline, rerun):
    assert abs(rerun.performance.thrust_error) < 0.02
    assert rerun.design.is_blunt()
    assert rerun.design.min_te_thickness() >= baseline["required_te_thickness_m"]
    assert rerun.constraints.te_violation_m == pytest.approx(0.0, abs=1e-15)


def test_performance_output_schema_is_stable():
    golden = json.loads((GOLDEN / "performance_summary.json").read_text(encoding="utf-8"))
    required = {
        "thrust_target_n",
        "thrust_calc_n",
        "torque_nm",
        "power_ideal_w",
        "power_induced_w",
        "power_profile_w",
        "power_swirl_w",
        "power_misc_w",
        "power_actual_w",
        "figure_of_merit",
        "rpm",
        "tip_mach",
        "solidity",
        "blade_count",
    }
    assert required <= set(golden)
    assert 0.0 < golden["figure_of_merit"] < 1.0


def test_no_sharp_trailing_edge_regression_across_the_design_space():
    """A CI-hard gate: sweep the design space and assert the blunt constraint always holds."""
    cfg = resolve(UserInput.model_validate(INPUT))
    rng = np.random.default_rng(2024)
    worst = float("inf")
    for _ in range(30):
        z = rng.uniform(-1.0, 1.0, size=34)
        from hoverblade_fm.optimize.vector import DesignVectorSpec, decode

        plan = decode(DesignVectorSpec(cfg), z, cfg)
        design = build_design(plan, cfg)
        worst = min(worst, design.min_te_thickness())
        assert design.is_blunt()
    assert worst >= cfg.te_min_m
