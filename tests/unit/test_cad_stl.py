"""Mesh / binary STL tests: winding, watertightness, volume scaling, TE round-trip (T076)."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest
from cad_fixtures import make_design

from hoverblade_fm.cad.loft import LoftedBlade, loft_blade
from hoverblade_fm.cad.mesh import (
    MESH_RESOLUTIONS,
    TriMesh,
    design_to_mesh,
    is_watertight,
    loft_to_mesh,
    loft_to_triangles,
    mesh_bounds,
    mesh_normals,
    mesh_surface_area,
    mesh_to_binary_stl,
    mesh_volume,
    read_binary_stl_records,
    resolution_profile,
    stl_to_mesh,
    subdivide_mesh,
    te_gap_from_mesh,
)


@pytest.fixture(scope="module")
def design():
    return make_design()


@pytest.fixture(scope="module")
def loft(design):
    return loft_blade(design, points_per_section=81)


@pytest.fixture(scope="module")
def prism():
    """Two identical square contours, one unit of radius apart: an exact 4 m^3 prism."""
    square = np.array([[1.0, 0.0, -1.0], [-1.0, 0.0, -1.0], [-1.0, 0.0, 1.0], [1.0, 0.0, 1.0]])
    stations = [square, square.copy()]
    stations[0][:, 1] = 0.0
    stations[1][:, 1] = 1.0
    return LoftedBlade(
        stations=stations,
        station_radii=[0.0, 1.0],
        n_per_section=4,
        hub_radius_m=0.0,
        tip_radius_m=1.0,
        radius_m=1.0,
        is_design_station=[True, True],
    )


def test_prism_volume_is_positive_and_exact(prism):
    vertices, faces = loft_to_triangles(prism.stations, prism.n_per_section)
    assert is_watertight(faces, vertices)
    # cross-section 2 m x 2 m over 1 m of radius, caps flat at both ends
    assert mesh_volume(vertices, faces) == pytest.approx(4.0, rel=1e-12)


def test_reversed_winding_flips_the_volume_sign(prism):
    vertices, faces = loft_to_triangles(prism.stations, prism.n_per_section)
    volume = mesh_volume(vertices, faces)
    assert mesh_volume(vertices, faces[:, ::-1].copy()) == pytest.approx(-volume, rel=1e-12)


def test_blade_mesh_is_watertight_and_outward(loft):
    mesh = loft_to_mesh(loft, resolution="standard")
    assert isinstance(mesh, TriMesh)
    assert is_watertight(mesh.faces, mesh.vertices)
    assert mesh_volume(mesh.vertices, mesh.faces) > 0.0
    assert mesh.faces.shape[0] == 2 * (loft.n_stations - 1) * loft.n_per_section + 2 * loft.n_per_section
    assert mesh.vertices.shape[0] == loft.n_stations * loft.n_per_section + 2


def test_mesh_bounds_match_the_loft_extent(loft):
    mesh = loft_to_mesh(loft, resolution="coarse")
    low, high = mesh_bounds(mesh.vertices)
    radii = np.array(loft.station_radii)
    assert low[1] == pytest.approx(radii.min(), rel=1e-12)
    assert high[1] == pytest.approx(radii.max(), rel=1e-12)
    widest = max(loft.chord_at(i) for i in range(loft.n_stations))
    assert high[0] - low[0] == pytest.approx(widest, rel=1e-6)


def test_volume_scales_with_chord_squared(design):
    _, fine = design_to_mesh(
        make_design(radius_m=design.radius_m, chord_base_over_r=0.20), resolution="coarse"
    )
    _, coarse = design_to_mesh(
        make_design(radius_m=design.radius_m, chord_base_over_r=0.10), resolution="coarse"
    )
    ratio = mesh_volume(*fine.as_tuple()) / mesh_volume(*coarse.as_tuple())
    assert ratio == pytest.approx(4.0, rel=0.06)


def test_volume_scales_cubically_with_radius(design):
    # self-similar pair: chord, span *and* the absolute TE minimum all scale with the radius
    _, small = design_to_mesh(make_design(radius_m=0.5, te_min_m=0.00075), resolution="coarse")
    _, big = design_to_mesh(make_design(radius_m=1.0, te_min_m=0.0015), resolution="coarse")
    ratio = mesh_volume(*big.as_tuple()) / mesh_volume(*small.as_tuple())
    assert ratio == pytest.approx(8.0, rel=0.06)


def test_te_gap_measured_from_mesh_vertices(loft, design):
    mesh = loft_to_mesh(loft, resolution="coarse")
    design_radii = np.asarray(design.r_R, dtype=float) * design.radius_m
    gaps = te_gap_from_mesh(mesh.vertices, design_radii, radius_tol=1e-12)
    assert np.allclose(gaps, np.asarray(design.te_thickness_m), rtol=5e-3, atol=1e-9)
    assert np.all(gaps >= design.te_min_m * 0.99)


def test_subdivision_keeps_watertightness_and_volume(loft):
    base = loft_to_mesh(loft, resolution="coarse", subdivision=0)
    refined = subdivide_mesh(base, 1)
    assert is_watertight(refined.faces, refined.vertices)
    assert refined.faces.shape[0] == 4 * base.faces.shape[0]
    assert mesh_volume(*refined.as_tuple()) == pytest.approx(mesh_volume(*base.as_tuple()), rel=1e-9)
    gaps = te_gap_from_mesh(refined.vertices, [loft.station_radii[5]], radius_tol=1e-12)
    assert gaps[0] == pytest.approx(loft.te_gap_at(5), rel=5e-3)


def test_resolution_profiles_and_rejection():
    assert set(MESH_RESOLUTIONS) == {"coarse", "standard", "fine", "ultra"}
    assert (
        resolution_profile("coarse")["points_per_section"] < resolution_profile("fine")["points_per_section"]
    )
    with pytest.raises(ValueError):
        resolution_profile("medium")


def test_binary_stl_round_trip_and_te_gap(tmp_path: Path, design):
    loft, mesh = design_to_mesh(design, resolution="coarse")
    stl_path = mesh_to_binary_stl(mesh.vertices, mesh.faces, tmp_path / "cad" / "blade.stl")
    assert stl_path.exists()
    raw = stl_path.read_bytes()
    (count,) = struct.unpack_from("<I", raw, 80)
    assert count == mesh.faces.shape[0]
    assert len(raw) == 84 + 50 * count
    assert raw[:31].startswith(b"HoverBlade-FM")

    back = stl_to_mesh(stl_path)
    assert is_watertight(back.faces, back.vertices)
    # STL is millimetres: the volume must come back scaled by 1e9
    vol_mm3 = mesh_volume(back.vertices, back.faces)
    assert vol_mm3 == pytest.approx(mesh_volume(mesh.vertices, mesh.faces) * 1e9, rel=1e-3)

    radii_mm = np.asarray(design.r_R, dtype=float) * design.radius_m * 1000.0
    gaps_mm = te_gap_from_mesh(back.vertices, radii_mm, radius_tol=0.05)
    expected_mm = np.asarray(design.te_thickness_m) * 1000.0
    assert np.allclose(gaps_mm, expected_mm, rtol=5e-3, atol=2e-3)
    assert np.all(gaps_mm >= design.te_min_m * 1000.0 - 2e-3)


def test_stl_normals_agree_with_winding(tmp_path: Path, loft):
    mesh = loft_to_mesh(loft, resolution="coarse")
    stl_path = mesh_to_binary_stl(mesh.vertices, mesh.faces, tmp_path / "blade.stl")
    stored, triangles = read_binary_stl_records(stl_path)
    count = triangles.shape[0]
    computed = mesh_normals(
        triangles.reshape(count * 3, 3), np.arange(count * 3, dtype=np.int64).reshape(count, 3)
    )
    dots = np.einsum("ij,ij->i", stored, computed)
    assert np.all(dots > 0.99)
    assert np.allclose(np.linalg.norm(stored, axis=1), 1.0, atol=1e-4)


def test_stl_unit_conversion(tmp_path: Path, loft):
    mesh = loft_to_mesh(loft, resolution="coarse")
    mm = mesh_to_binary_stl(mesh.vertices, mesh.faces, tmp_path / "mm.stl")
    m = mesh_to_binary_stl(mesh.vertices, mesh.faces, tmp_path / "m.stl", target_units="M")
    lo_mm, hi_mm = mesh_bounds(stl_to_mesh(mm).vertices)
    lo_m, hi_m = mesh_bounds(stl_to_mesh(m).vertices)
    assert hi_mm[1] == pytest.approx(hi_m[1] * 1000.0, rel=1e-6)


def test_open_patch_is_not_watertight(loft):
    mesh = loft_to_mesh(loft, resolution="coarse")
    punctured = mesh.faces[:-3]
    assert not is_watertight(punctured, mesh.vertices)


def test_surface_area_is_positive_and_grows_with_refinement(loft):
    coarse = loft_to_mesh(loft, resolution="coarse")
    fine = loft_to_mesh(loft, resolution="fine")
    assert mesh_surface_area(*coarse.as_tuple()) > 0.0
    assert mesh_surface_area(*fine.as_tuple()) > mesh_surface_area(*coarse.as_tuple()) * 0.9
