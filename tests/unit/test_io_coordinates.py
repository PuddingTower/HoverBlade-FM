"""Tests for per-section coordinate files and the coordinate convention (plan T060, section 3.5)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from io_fixtures import make_design

from hoverblade_fm.io import read_section_coordinates, section_coordinate_name, write_section_coordinates
from hoverblade_fm.io.writers import SECTION_COORD_COLUMNS


@pytest.fixture()
def coord_paths(tmp_path: Path) -> list[Path]:
    return write_section_coordinates(make_design(), tmp_path)


def test_expected_file_names(coord_paths: list[Path]) -> None:
    design = make_design()
    expected = [section_coordinate_name(i, s.r_R) for i, s in enumerate(design.sections)]
    assert expected[0] == "section_00_r0.100.csv"
    assert expected[-1] == "section_09_r0.970.csv"
    names = [p.name for p in coord_paths if p.suffix == ".csv"]
    assert names == expected
    assert coord_paths[-1].name == "index.json"
    for path in coord_paths:
        assert path.parent.name == "sections"


def test_header_comments_and_table_columns(coord_paths: list[Path]) -> None:
    lines = coord_paths[0].read_text(encoding="utf-8").splitlines()
    assert all(line.startswith("#") for line in lines[:16])
    header_index = next(i for i, line in enumerate(lines) if not line.startswith("#"))
    assert lines[header_index] == ",".join(SECTION_COORD_COLUMNS)
    joined = "\n".join(lines)
    for key in (
        "station_id",
        "r_R",
        "r_m",
        "chord_m",
        "twist_deg",
        "te_thickness_m",
        "max_thickness_m",
        "thickness_to_chord",
        "camber_m",
        "le_radius_m",
        "te_included_angle_deg",
    ):
        assert f"# {key}:" in joined
    assert "# convention:" in joined
    assert "lower-surface TE->LE" in joined
    assert "25% chord" in joined


def test_surface_blocks_ordered_le_to_te(coord_paths: list[Path]) -> None:
    data = read_section_coordinates(coord_paths[0])
    n = data["meta"]["n_per_surface"]
    assert data["side"] == ["lower"] * n + ["upper"] * n
    x = np.asarray(data["x_over_c"], dtype=float)
    lower, upper = x[:n], x[n:]
    assert lower[0] == pytest.approx(0.0, abs=1e-9)
    assert lower[-1] == pytest.approx(1.0, abs=1e-9)
    assert np.all(np.diff(lower) >= 0.0)
    assert np.all(np.diff(upper) >= 0.0)
    assert upper[0] == pytest.approx(0.0, abs=1e-9)


def test_blunt_te_gap_survives_round_trip(coord_paths: list[Path]) -> None:
    design = make_design()
    for path, section in zip(coord_paths[:10], design.sections, strict=True):
        data = read_section_coordinates(path)
        n = data["meta"]["n_per_surface"]
        y = np.asarray(data["y_over_c"], dtype=float)
        gap = y[2 * n - 1] - y[n - 1]  # upper TE minus lower TE (last point of each block)
        assert gap == pytest.approx(section.points.te_thickness, abs=5e-7)
        assert gap > 0.0


def test_physical_columns_match_in_blade_frame(coord_paths: list[Path]) -> None:
    design = make_design()
    section = design.sections[3]
    path = coord_paths[3]
    data = read_section_coordinates(path)
    frame = section.points.in_blade_frame(section.chord_m, section.twist_deg, section.r_m)
    n = section.points.n_per_surface
    lower_x = frame[:n, 0][::-1]
    lower_z = frame[:n, 2][::-1]
    upper_x = frame[n:, 0]
    upper_z = frame[n:, 2]
    xm = np.asarray(data["x_m"], dtype=float)
    ym = np.asarray(data["y_m"], dtype=float)
    np.testing.assert_allclose(xm[:n], lower_x, atol=5e-8)
    np.testing.assert_allclose(ym[:n], lower_z, atol=5e-8)
    np.testing.assert_allclose(xm[n:], upper_x, atol=5e-8)
    np.testing.assert_allclose(ym[n:], upper_z, atol=5e-8)


def test_closed_contour_reconstruction(coord_paths: list[Path]) -> None:
    design = make_design()
    section = design.sections[0]
    data = read_section_coordinates(coord_paths[0])
    n = data["meta"]["n_per_surface"]
    x = np.asarray(data["x_over_c"], dtype=float)
    y = np.asarray(data["y_over_c"], dtype=float)
    contour_x = np.concatenate((x[:n][::-1], x[n:]))
    contour_y = np.concatenate((y[:n][::-1], y[n:]))
    np.testing.assert_allclose(contour_x, section.points.x, atol=5e-7)
    np.testing.assert_allclose(contour_y, section.points.y, atol=5e-7)


def test_index_json_describes_every_file(coord_paths: list[Path]) -> None:
    index = json.loads(coord_paths[-1].read_text(encoding="utf-8"))
    assert index["columns"] == list(SECTION_COORD_COLUMNS)
    assert len(index["convention"]) >= 4
    assert len(index["sections"]) == 10
    entry = index["sections"][0]
    assert entry["file"] == "section_00_r0.100.csv"
    assert entry["r_R"] == pytest.approx(0.1)
    assert entry["n_per_surface"] == 24
    for key in ("station_id", "chord_m", "twist_deg", "te_thickness_m", "camber_m", "le_radius_m"):
        assert key in entry


def test_output_is_deterministic(tmp_path: Path) -> None:
    design = make_design()
    first = write_section_coordinates(design, tmp_path / "a")
    second = write_section_coordinates(design, tmp_path / "b")
    assert [p.name for p in first] == [p.name for p in second]
    for path_a, path_b in zip(first, second, strict=True):
        assert path_a.read_bytes() == path_b.read_bytes()
