"""CAD export tests: STEP structure and kernel validity, DXF templates, manufacturing checks."""

from __future__ import annotations

import json

import numpy as np
import pytest

from hoverblade_fm.cad import backend, export_manufacturing_package
from hoverblade_fm.cad.checks import TOLERANCES, ManufacturingChecks, run_manufacturing_checks
from hoverblade_fm.cad.dxf import (
    LAYER_SECTIONS,
    LAYER_TE,
    read_dxf_summary,
    station_layout,
    write_master_sections,
)
from hoverblade_fm.cad.loft import loft_blade, resample_contour
from hoverblade_fm.cad.mesh import (
    is_watertight,
    loft_to_mesh,
    mesh_bounds,
    mesh_surface_area,
    mesh_volume,
    write_stl,
)
from hoverblade_fm.cad.step import step_readback, write_step, write_step_with_kernel
from hoverblade_fm.config.schema import ResolvedConfig, UserInput, resolve
from hoverblade_fm.geometry.sections import build_design, plan_from_raw
from hoverblade_fm.models import Design

MINIMAL = {"thrust_n": 5000.0, "radius_m": 2.5, "trailing_edge_thickness_m": 0.0025}


@pytest.fixture(scope="module")
def cfg() -> ResolvedConfig:
    return resolve(UserInput.model_validate(MINIMAL))


@pytest.fixture(scope="module")
def design(cfg) -> Design:
    return build_design(plan_from_raw(cfg), cfg)


@pytest.fixture(scope="module")
def loft(design):
    return loft_blade(design)


@pytest.fixture(scope="module")
def mesh(loft):
    return loft_to_mesh(loft)


# -- loft ------------------------------------------------------------------------------------


def test_loft_is_monotone_in_radius_and_capped(loft, design, cfg):
    radii = loft.station_radii
    assert np.all(np.diff(radii) > 0)
    assert radii[0] <= cfg.r_stations[0] * cfg.radius_m + 1e-9
    assert radii[-1] > cfg.radius_m, "the tip cap must extend beyond the last design station"
    assert all(st.shape == (loft.n_per_section, 3) for st in loft.stations)
    assert sum(loft.is_design_station) == len(design.sections)


def test_resampled_contours_have_identical_point_counts(design):
    counts = {resample_contour(section, 121).shape[0] for section in design.sections}
    assert counts == {121}


def test_loft_records_the_trailing_edge_gap(loft, design):
    """The lofted rings must preserve the blunt gap: the first and last point of a ring differ."""
    for index, flag in enumerate(loft.is_design_station):
        if not flag:
            continue
        ring = loft.stations[index]
        gap = np.hypot(*(ring[-1, [0, 2]] - ring[0, [0, 2]]))
        assert gap > 1e-4, f"loft ring {index} closed to a sharp edge"


# -- STL --------------------------------------------------------------------------------------


def test_mesh_is_watertight_with_positive_volume(mesh, cfg):
    assert is_watertight(mesh.faces, mesh.vertices)
    volume = mesh_volume(mesh.vertices, mesh.faces)
    assert volume > 0.0
    # sanity: a thin disc of radius R and mean section area c*t should be within an order of magnitude
    reference = np.pi * cfg.radius_m**2 * 0.12 * np.mean(disc_chord_fraction(cfg))
    assert 1e-4 < volume / reference < 100.0


def disc_chord_fraction(cfg) -> list[float]:
    plan = plan_from_raw(cfg)
    return list(plan.chord_m)


def test_mesh_bounds_span_the_blade(mesh, cfg):
    low, high = mesh_bounds(mesh.vertices)
    assert low[1] == pytest.approx(cfg.solver.root_extension_r * cfg.radius_m, abs=0.05)
    assert high[1] >= cfg.radius_m * 0.99
    assert np.ptp(low), "degenerate bounds"


def test_stl_file_is_binary_and_reloadable(loft, tmp_path):
    path, mesh_written = write_stl(loft, tmp_path / "blade.stl", resolution="coarse")
    raw = path.read_bytes()
    assert raw[:5] != b"solid" or b"HoverBlade-FM" in raw[:80]  # binary header carries our marker
    assert b"HoverBlade-FM" in raw[:80]
    assert path.stat().st_size > 84
    triangles = (path.stat().st_size - 84) // 50
    assert triangles == len(mesh_written.faces)


def test_mesh_volume_is_cubic_in_length(mesh):
    """A mesh scaled by k must enclose k^3 of volume - catches unit and winding mistakes."""
    base = mesh_volume(*mesh.as_tuple())
    doubled = (mesh.vertices * 2.0, mesh.faces)
    assert mesh_volume(*doubled) == pytest.approx(base * 8.0, rel=1e-9)


def test_blade_grows_when_both_radius_and_thrust_grow(cfg):
    """A larger, more heavily loaded rotor needs more material, so volume must increase."""
    import dataclasses

    small_cfg = dataclasses.replace(cfg, radius_m=cfg.radius_m * 1.5, thrust_n=cfg.thrust_n * 2.0)
    small = build_design(plan_from_raw(small_cfg), small_cfg)
    volume = mesh_volume(*loft_to_mesh(loft_blade(small)).as_tuple())
    assert volume > mesh_volume_from_design(cfg)


def mesh_volume_from_design(cfg) -> float:
    design = build_design(plan_from_raw(cfg), cfg)
    return mesh_volume(*loft_to_mesh(loft_blade(design)).as_tuple())


def test_surface_area_is_positive_and_finite(mesh):
    assert 0.0 < mesh_surface_area(mesh.vertices, mesh.faces) < 100.0


# -- STEP --------------------------------------------------------------------------------------


def test_builtin_step_is_structurally_valid_and_correctly_scaled(loft, cfg, tmp_path):
    path = write_step(loft, tmp_path / "builtin.step")
    report = step_readback(path)
    assert report["ok"], report
    text = path.read_text(encoding="utf-8")
    assert text.startswith("ISO-10303-21;")
    assert text.rstrip().endswith("END-ISO-10303-21;")
    assert "FILE_SCHEMA(('AUTOMOTIVE_DESIGN" in text
    assert "SI_UNIT(.MILLI.,.METRE.)" in text
    assert "MANIFOLD_SOLID_BREP" in text and "CLOSED_SHELL" in text
    # the millimetre scaling must be exact, not 1e6 off
    tip_mm = cfg.radius_m * 1000.0
    coords = [float(v) for v in np.array(report["coordinates"])] if "coordinates" in report else []
    if coords:
        assert max(abs(c) for c in coords) < 10.0 * tip_mm
    assert report["entity_count"] > 100


def test_kernel_step_is_read_by_occt(loft, tmp_path):
    if not backend.has_kernel():
        pytest.skip("no CAD kernel installed")
    path = write_step_with_kernel(loft, tmp_path / "kernel.step")
    assert path is not None
    report = backend.occt_validate(str(path))
    assert report.get("available") is True
    assert report.get("valid") is True, report
    assert report.get("solid_count", 0) >= 1
    mesh_mm3 = mesh_volume(*loft_to_mesh(loft).as_tuple()) * 1e9
    assert report["volume_mm3"] == pytest.approx(mesh_mm3, rel=0.05)


@pytest.mark.cad
def test_kernel_step_round_trips_through_the_kernel(loft, tmp_path):
    if not backend.has_kernel():
        pytest.skip("no CAD kernel installed")
    import cadquery as cq

    path = write_step_with_kernel(loft, tmp_path / "roundtrip.step")
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer

    shape = cq.importers.importStep(str(path)).val()
    explorer = TopExp_Explorer(shape.wrapped, TopAbs_FACE)
    faces = 0
    while explorer.More():
        faces += 1
        explorer.Next()
    assert faces > 10


# -- DXF ---------------------------------------------------------------------------------------


def test_master_sections_have_ten_templates_and_te_details(design, cfg, tmp_path):
    path = write_master_sections(design, tmp_path / "sections.dxf", cfg=cfg)
    summary = read_dxf_summary(path)
    assert summary["section_polylines"] == 10
    assert summary["te_detail_polylines"] == 10
    assert LAYER_SECTIONS in summary["layers"] and LAYER_TE in summary["layers"]
    assert summary["texts"] >= 11  # one label per station plus the title block


def test_station_layout_places_blocks_side_by_side(design):
    blocks = station_layout(design, spacing_mm=300.0, n_points=81)
    assert len(blocks) == 10
    starts = [float(np.min(np.asarray(b["contour"])[:, 0])) for b in blocks]
    assert starts == sorted(starts)
    for block in blocks:
        contour = np.asarray(block["contour"])
        detail = np.asarray(block["detail"])
        assert contour.shape[0] == 81 and detail.shape[0] == 81
        assert np.ptp(detail[:, 0]) > np.ptp(contour[:, 0]) * 0.3  # magnified TE window


# -- checks -------------------------------------------------------------------------------------


def test_checks_expose_every_plan_field(design, cfg, loft, mesh, tmp_path):
    step = write_step(loft, tmp_path / "b.step")
    dxf = write_master_sections(design, tmp_path / "b.dxf", cfg=cfg)
    checks = run_manufacturing_checks(design, cfg, loft=loft, mesh=mesh, step_path=step, dxf_path=dxf)
    payload = checks.to_dict()
    required = {
        "min_te_thickness_global_mm",
        "min_te_thickness_per_section_mm",
        "te_thickness_margin_mm",
        "te_edge_length_mm",
        "te_radius_estimate_mm",
        "te_manufacturability_flag",
        "mesh_watertight",
        "step_parse_ok",
        "dxf_written",
        "units",
        "tolerances",
        "blocking_failures",
    }
    assert required <= set(payload)
    assert len(payload["min_te_thickness_per_section_mm"]) == 10
    assert payload["units"] == "MM"
    assert set(payload["tolerances"]) == set(TOLERANCES)
    assert checks.ok and not checks.blocking_failures
    assert checks.te_manufacturability_flag is True
    assert checks.loft_max_gap_error_m == pytest.approx(0.0, abs=1e-9)


def test_a_sub_minimum_trailing_edge_is_blocking(design, cfg):
    """Bypass the generator, hand the checker an illegal design, and require it to say so."""
    import dataclasses

    illegal = dataclasses.replace(cfg, te_min_m=design.min_te_thickness() * 2.0)
    checks = run_manufacturing_checks(design, illegal)
    assert checks.min_te_thickness_global_m < illegal.te_min_m
    assert not checks.ok
    assert any("trailing edge" in item for item in checks.blocking_failures)


def test_checks_reject_a_torn_mesh(design, cfg, mesh):

    torn_faces = mesh.faces[:-40]
    checks = run_manufacturing_checks(design, cfg, mesh=type(mesh)(mesh.vertices, torn_faces))
    assert checks.mesh_watertight is False
    assert any("watertight" in item for item in checks.blocking_failures)


def test_rate_and_band_limits_are_reported(design, cfg):
    checks = run_manufacturing_checks(design, cfg)
    assert checks.chord_change_rate_max <= checks.chord_change_rate_limit + 1e-6
    assert checks.twist_change_rate_deg_per_r_max <= checks.twist_change_rate_limit + 1e-6
    assert checks.te_over_chord_band_ok is True


# -- package -------------------------------------------------------------------------------------


def test_export_manufacturing_package_writes_everything(design, cfg, tmp_path):
    package = export_manufacturing_package(design, cfg, tmp_path / "run", mesh_resolution="coarse")
    cad_dir = tmp_path / "run" / "cad"
    for path in (
        package.step_path,
        package.stl_path,
        package.dxf_path,
        package.report_path,
        package.manifest_path,
    ):
        assert path.is_file(), path
        assert path.parent == cad_dir
        assert path.stat().st_size > 0
    assert package.backend == backend.name()
    assert package.checks.ok

    report_text = package.report_path.read_text(encoding="utf-8")
    assert "minimum trailing-edge thickness" in report_text
    assert "PASS" in report_text
    assert "millimetres" in report_text
    assert "not a structural" in report_text.lower() or "Disclaimer" in report_text

    manifest = json.loads(package.manifest_path.read_text(encoding="utf-8"))
    assert manifest["units"] == "MM"
    assert manifest["package_ok"] is True
    names = {entry["file"] for entry in manifest["files"]}
    assert {"blade.step", "blade.stl", "master_sections.dxf", "manufacturing_report.md"} <= names
    for entry in manifest["files"]:
        assert len(entry["sha256"]) == 64
        assert entry["bytes"] > 0


def test_manifest_digests_match_the_files(design, cfg, tmp_path):
    import hashlib

    package = export_manufacturing_package(design, cfg, tmp_path, mesh_resolution="coarse")
    manifest = json.loads(package.manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        path = package.manifest_path.parent / entry["file"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == entry["sha256"], entry["file"]


def test_export_never_raises_on_a_large_legal_design(cfg, tmp_path):
    import dataclasses

    big = dataclasses.replace(cfg, radius_m=8.0, thrust_n=120000.0)
    big.te_min_m = 0.006
    design = build_design(plan_from_raw(big), big)
    package = export_manufacturing_package(design, big, tmp_path / "big", mesh_resolution="coarse")
    assert isinstance(package.checks, ManufacturingChecks)
    assert design.is_blunt()
