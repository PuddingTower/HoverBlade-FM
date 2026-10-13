"""Phase 7 tests: airfoil library, polar cache, external adapters, surrogate, gradients, generator."""

from __future__ import annotations

import json

import numpy as np
import pytest

from hoverblade_fm.aero.differentiable import (
    ReducedBlade,
    adam_maximise_fm,
    backend_name,
    fm_gradient,
    reduced_blade_from_design,
    solve_finite_difference,
)
from hoverblade_fm.aero.external import CfdCase, XfoilPolar, prepare_cfd_case, xfoil_executable
from hoverblade_fm.aero.polar import AnalyticPolar, sweep_polar
from hoverblade_fm.aero.polar_cache import Bucketing, CachingPolar
from hoverblade_fm.config.schema import UserInput, resolve
from hoverblade_fm.geometry import generative as gen
from hoverblade_fm.geometry import library as lib
from hoverblade_fm.geometry.cst import build_airfoil
from hoverblade_fm.geometry.sections import build_design, plan_from_raw
from hoverblade_fm.optimize.surrogate import (
    GaussianProcess,
    bayesian_optimise,
    expected_improvement,
    surrogate_prescreen,
)
from hoverblade_fm.optimize.vector import DesignVectorSpec, decode, initial_vector

MINIMAL = {"thrust_n": 5000.0, "radius_m": 2.5, "trailing_edge_thickness_m": 0.0025}


@pytest.fixture(scope="module")
def cfg():
    return resolve(UserInput.model_validate(MINIMAL))


@pytest.fixture(scope="module")
def design(cfg):
    return build_design(plan_from_raw(cfg), cfg)


# -- T082 built-in library ---------------------------------------------------------------------


def test_library_lists_sections_and_reports_provenance():
    names = lib.names()
    assert len(names) >= 8
    summary = lib.library_summary()
    assert summary["total"] == len(names)
    assert "no third-party coordinate table" in summary["provenance"]


def test_naca_generation_matches_theory():
    for code, t in (("0012", 0.12), ("0018", 0.18)):
        points = lib.naca_four_digit(code)
        assert points.max_thickness == pytest.approx(t, rel=0.01)
        assert points.thickness_location == pytest.approx(0.30, abs=0.03)
        assert points.te_thickness < 0.005  # the NACA edge is essentially sharp, before blunting


def test_blunting_thickens_a_sharp_edge():
    raw = lib.naca_four_digit("0012")
    blunt = lib.load("NACA0012", te_over_c=0.012)
    assert blunt.te_thickness > 4.0 * raw.te_thickness


def test_library_always_blunts_the_trailing_edge():
    for name in lib.names():
        for requested in (0.002, 0.006, 0.020):
            points = lib.load(name, te_over_c=requested)
            assert points.te_thickness >= requested - 1e-12
            assert points.te_thickness > 0.0
            assert np.all(np.isfinite(points.x)) and np.all(np.isfinite(points.y))


def test_loading_cannot_sharpen_an_edge():
    points = lib.load("NACA0012", te_over_c=1e-9)
    assert points.te_thickness > 0.0


def test_library_sections_have_positive_leading_edge_radius():
    from hoverblade_fm.geometry.cst import le_radius

    for name in ("NACA0012", "NACA4415", "HVR-T14"):
        assert le_radius(lib.load(name, te_over_c=0.008)) > 0.0


def test_unknown_airfoil_raises_with_the_list():
    with pytest.raises(KeyError):
        lib.load("NOT-ASECTION")


def test_library_shape_parameters_round_trip():
    params = lib.section_parameters_for("HVR-T14")
    points = build_airfoil(params)
    assert points.max_thickness == pytest.approx(params.t_over_c, rel=0.03)


# -- T083 polar caching -------------------------------------------------------------------------


def test_caching_polar_matches_the_source_and_serves_hits(cfg):
    inner = AnalyticPolar()
    cached = CachingPolar(inner=inner)
    alpha = np.linspace(-6.0, 14.0, 41)
    args = (alpha, 8.0e5, 0.35, 0.12, 0.01, 0.03)
    first = cached(*args)
    second = cached(*args)
    assert np.allclose(first.cl, second.cl, rtol=1e-12)
    assert np.allclose(first.cd, second.cd, rtol=1e-12)
    reference = inner(*args)
    assert np.allclose(reference.cl, second.cl, rtol=1e-6)
    assert cached.misses > 0 and cached.hits > 0
    assert 0.0 < cached.hit_rate <= 1.0


def test_polar_cache_round_trips_through_disk(tmp_path):
    path = tmp_path / "polar_cache.json"
    writer = CachingPolar(inner=AnalyticPolar())
    alpha = np.linspace(0.0, 10.0, 12)
    writer(alpha, 1.0e6, 0.3, 0.12, 0.01, 0.03)
    before = len(writer.cache)
    writer.save(path)
    reader = CachingPolar(inner=AnalyticPolar(), disk_path=path)
    assert len(reader.cache) == before
    point = reader(alpha, 1.0e6, 0.3, 0.12, 0.01, 0.03)
    assert np.isfinite(point.cl).all() and np.all(point.cd > 0.0)
    assert reader.hits >= alpha.size  # every query lands in a bucket restored from disk


def test_bucketing_controls_granularity(cfg):
    fine = CachingPolar(inner=AnalyticPolar(), bucketing=Bucketing(alpha_deg=0.1))
    coarse = CachingPolar(inner=AnalyticPolar(), bucketing=Bucketing(alpha_deg=5.0))
    alpha = np.array([2.0, 2.05, 2.1])
    fine(alpha, 1e6, 0.3, 0.12, 0.01, 0.03)
    coarse(alpha, 1e6, 0.3, 0.12, 0.01, 0.03)
    assert len(fine.cache) >= len(coarse.cache)


# -- T084 XFOIL adapter -------------------------------------------------------------------------


def test_xfoil_adapter_degrades_without_the_binary(cfg, design):
    adapter = XfoilPolar(reynolds=6.0e5)
    if xfoil_executable() is None:
        status = adapter.run(design.sections[4].points)
        assert status["ok"] is False
        assert "not found" in status["reason"]
        point = adapter(np.array([4.0]), 6.0e5, 0.3, 0.12, 0.01, 0.03)
        assert np.isfinite(point.cl[0])  # falls back to the analytic polar
    else:  # pragma: no cover - only on a machine with XFOIL installed
        status = adapter.run(design.sections[4].points, reynolds=6.0e5)
        assert status["ok"] is True, status


def test_xfoil_geometry_file_is_upper_then_lower_with_a_single_nose(design):
    from hoverblade_fm.aero.external import _xfoil_geometry_file

    text = _xfoil_geometry_file(design.sections[3].points)
    rows = [line.split() for line in text.splitlines()[2:] if line.strip() and not line.startswith("END")]
    xs = [float(row[0]) for row in rows]
    assert xs[0] == pytest.approx(1.0)
    assert min(xs) == pytest.approx(0.0)
    assert xs.count(0.0) == 1  # XFOIL rejects duplicated nose points


def test_polar_sweep_table_serialisable():
    table = sweep_polar(AnalyticPolar(), reynolds=4e5, mach=0.3, t_over_c=0.12, te_over_c=0.01, camber=0.03)
    assert table.alpha_deg.size > 20
    assert all(np.isfinite(arr).all() for arr in (table.cl, table.cd, table.cm))


# -- T090 CFD case preparation ---------------------------------------------------------------------


def test_prepare_cfd_case_writes_surfaces_and_a_template(design, cfg, tmp_path):
    case = prepare_cfd_case(design, tmp_path / "cfd", mesher="su2")
    assert isinstance(case, CfdCase)
    surfaces = [p for p in case.geometry_files if p.suffix == ".sur"]
    assert len(surfaces) == 10
    assert (tmp_path / "cfd" / "rotor.cfg").is_file()
    assert case.command[0] == "su2_CFD"
    assert any("CFD executable" in note or "would use" in note for note in case.notes)
    payload = case.to_dict()
    assert json.dumps(payload)  # must be serialisable for provenance
    assert len(payload["geometry_files"]) == 11


def test_openfoam_template_is_also_produced(design, tmp_path):
    case = prepare_cfd_case(design, tmp_path / "cfd2", mesher="openfoam")
    assert case.command[0] == "blockMesh"
    block = tmp_path / "cfd2" / "rotor.blockedMesh"
    assert block.is_file()
    assert json.loads(block.read_text(encoding="utf-8"))["rotor"]["blade_count"] == design.blade_count


# -- T085 / T086 surrogate and Bayesian optimisation ------------------------------------------------


def test_gp_fits_training_points_and_predicts():
    rng = np.random.default_rng(0)
    X = rng.uniform(-1, 1, size=(40, 3))
    y = np.sum(X**2, axis=1)
    model = GaussianProcess.fit(X, y)
    mu, sigma = model.predict(X, return_std=True)
    assert np.max(np.abs(mu - y)) < 0.15 * np.std(y)
    assert np.all(sigma >= 0.0)


def test_expected_improvement_favours_unexplored_promising_regions():
    rng = np.random.default_rng(1)
    X = rng.uniform(-1, 1, size=(24, 2))
    y = np.sum((X - 0.5) ** 2, axis=1)
    model = GaussianProcess.fit(X, y)
    best = float(np.max(-y))
    near_optimum = np.array([[0.5, 0.5], [-0.9, -0.9]])
    scores = expected_improvement(near_optimum, model, best)
    assert scores.shape == (2,)
    assert np.isfinite(scores).all()


def test_bayesian_optimiser_finds_a_minimum():
    target = np.array([0.25, -0.5, 0.1])
    cost = lambda z: float(np.sum((np.asarray(z) - target) ** 2))  # noqa: E731
    result = bayesian_optimise(
        cost, (np.full(3, -1.0), np.full(3, 1.0)), np.zeros(3), max_evaluations=90, seed=2
    )
    assert result.best_value < 0.05
    assert result.evaluations >= 90 - 30
    assert len(result.history) == result.evaluations


def test_surrogate_prescreen_keeps_the_best_candidates():
    rng = np.random.default_rng(3)
    X = rng.uniform(-1, 1, size=(30, 4))
    y = np.sum((X - 0.4) ** 2, axis=1)
    candidates = rng.uniform(-1, 1, size=(20, 4))
    truth = np.sum((candidates - 0.4) ** 2, axis=1)
    kept = surrogate_prescreen(X, y, candidates, keep=5)
    assert len(kept) == 5
    kept_ids = [int(np.argmin(np.linalg.norm(candidates - k, axis=1))) for k in kept]
    assert min(truth[kept_ids]) <= np.sort(truth)[4] + 1e-9  # at least one genuinely good candidate survives


# -- T087 differentiable solver -----------------------------------------------------------------


def test_reduced_blade_sampling_matches_the_design(cfg, design):
    blade = reduced_blade_from_design(design, cfg)
    assert isinstance(blade, ReducedBlade)
    assert blade.r_over_R[0] >= cfg.solver.root_extension_r - 1e-9
    assert blade.r_over_R[-1] == pytest.approx(1.0)
    assert np.all(blade.te_over_c > 0.0)


def test_reduced_solver_matches_thrust_and_stays_physical(cfg, design):
    result = solve_finite_difference(reduced_blade_from_design(design, cfg), iterations=30, trim_steps=12)
    assert abs(result["thrust_error"]) < 0.02, "the inner trim must actually match the target thrust"
    assert 0.0 < result["figure_of_merit"] <= 1.0
    assert result["power_w"] > 0.0


def test_reduced_and_production_fm_agree_within_a_tenth(cfg, design):
    from hoverblade_fm.aero.bemt import solve_hover

    reduced = solve_finite_difference(reduced_blade_from_design(design, cfg), iterations=40, trim_steps=14)
    production = solve_hover(design, cfg)
    assert abs(reduced["figure_of_merit"] - production.figure_of_merit) < 0.10


def test_gradient_points_toward_higher_fm(cfg):
    spec = DesignVectorSpec(cfg)
    base = initial_vector(spec, cfg)

    def build(z: np.ndarray) -> ReducedBlade:
        return reduced_blade_from_design(build_design(decode(spec, z, cfg), cfg), cfg)

    value, gradient = fm_gradient(build, base, iterations=20)
    assert gradient.shape == base.shape
    assert np.count_nonzero(gradient) > 0
    step = 0.02
    improved = np.clip(base + step * gradient / max(np.max(np.abs(gradient)), 1e-9), -1.0, 1.0)
    assert solve_finite_difference(build(improved), iterations=20)["figure_of_merit"] >= value - 1e-3


def test_adam_ascent_improves_fm_without_breaking_the_bound(cfg):
    spec = DesignVectorSpec(cfg)
    base = initial_vector(spec, cfg)

    def build(z: np.ndarray) -> ReducedBlade:
        return reduced_blade_from_design(build_design(decode(spec, z, cfg), cfg), cfg)

    best_x, best_fm = adam_maximise_fm(build, base, steps=4, learning_rate=0.05, iterations=20)
    assert best_fm <= 1.0 + 1e-9
    assert np.all(np.abs(best_x) <= 1.0)
    assert backend_name() in ("jax-autodiff", "numpy-central-difference")


# -- T089 conditional generator -----------------------------------------------------------------


def test_generator_dataset_is_all_legal(cfg):
    spec = DesignVectorSpec(cfg)
    matrices, planforms, scores = gen.dataset(cfg, spec, samples=24, seed=5)
    assert matrices.shape == (24, 10 * len(gen.SHAPE_FIELDS)) or matrices.shape[1] == 10 * len(
        gen.SHAPE_FIELDS
    )
    assert planforms.shape[0] == matrices.shape[0]
    assert np.all(scores > 0.0) and np.all(scores <= 1.0)


def test_latent_model_trains_and_reports_variance(cfg):
    spec = DesignVectorSpec(cfg)
    model, info = gen.train(cfg, spec, samples=24, n_components=4, seed=5)
    assert model.n_components == 4
    assert len(info["explained_variance"]) == 4
    assert sum(info["explained_variance"]) <= 1.000001
    assert info["fm_max"] >= info["fm_min"] > 0.0


def test_generated_designs_satisfy_the_hard_constraint(cfg):
    spec = DesignVectorSpec(cfg)
    model, _info = gen.train(cfg, spec, samples=24, n_components=4, seed=5)
    conditions = gen.Conditions(
        thrust_n=cfg.thrust_n,
        radius_m=cfg.radius_m,
        te_min_m=cfg.te_min_m,
        target_fm=0.55,
        tip_mach=cfg.tip_mach,
        blade_count=cfg.blade_count,
    )
    designs = gen.sample(model, spec, cfg, conditions, count=3, seed=7)
    report = gen.legality_report(designs, cfg)
    assert report["all_blunt"] is True
    assert report["all_ten_sections"] is True
    assert report["min_te_mm"] >= report["required_te_mm"] - 1e-9
    for design in designs:
        for section in design.sections:
            assert section.te_thickness_m >= cfg.te_min_m - 1e-15


def test_latent_model_serialises(cfg):
    spec = DesignVectorSpec(cfg)
    model, _info = gen.train(cfg, spec, samples=20, n_components=3, seed=9)
    payload = model.to_dict()
    assert json.dumps(payload)
    restored = gen.LatentModel.from_dict(payload)
    conditions = gen.Conditions(thrust_n=cfg.thrust_n, radius_m=cfg.radius_m, te_min_m=cfg.te_min_m)
    assert np.allclose(restored.latent_for(conditions), model.latent_for(conditions), atol=1e-9)
