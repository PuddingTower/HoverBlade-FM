"""Lofting tests: contour ordering, station count, root blend, tip cap (T071-T074)."""

from __future__ import annotations

import numpy as np
import pytest
from cad_fixtures import make_design

from hoverblade_fm.cad.loft import (
    DEFAULT_HUB_RADIUS_OVER_R,
    ROOT_BLEND_STATIONS,
    TIP_BLEND_STATIONS,
    LoftedBlade,
    loft_blade,
    loft_section_properties,
    resample_contour,
)


@pytest.fixture(scope="module")
def design():
    return make_design()


def test_resample_contour_shape_and_radial_column(design):
    section = design.sections[4]
    contour = resample_contour(section, 121)
    assert contour.shape == (121, 3)
    assert np.allclose(contour[:, 1], section.r_m)
    assert np.isfinite(contour).all()


def test_resample_preserves_blunt_te_gap(design):
    for section in design.sections:
        contour = resample_contour(section, 161)
        # the blunt facet is the closing segment between the two parametric end points
        facet = float(np.hypot(contour[-1, 0] - contour[0, 0], contour[-1, 2] - contour[0, 2]))
        assert facet == pytest.approx(section.te_thickness_m, rel=1e-9, abs=1e-15)
        # plan 3.5: the section is rotated about 0.25c, so the z-only gap shrinks by cos(twist)
        z_gap = contour[-1, 2] - contour[0, 2]
        assert z_gap == pytest.approx(
            section.te_thickness_m * np.cos(np.deg2rad(section.twist_deg)), rel=1e-6
        )
        assert facet >= design.te_min_m - 1e-12
        # the first and last points are the two trailing-edge corners (maximum chordwise x)
        xs = np.sort(contour[:, 0])
        assert np.allclose(np.sort([contour[0, 0], contour[-1, 0]]), xs[-2:], atol=1e-9)


def test_resample_ordering_is_lower_te_to_upper_te(design):
    contour = resample_contour(design.sections[0], 161)
    # leading edge is the minimum-x point and sits near the middle of the array
    le_index = int(np.argmin(contour[:, 0]))
    assert 0.35 * len(contour) < le_index < 0.65 * len(contour)
    # lower surface (before the LE) lies below the upper surface at the same station
    lower = contour[:le_index]
    upper = contour[le_index:]
    assert np.percentile(lower[:, 2], 50) < np.percentile(upper[:, 2], 50)


def test_identical_parametric_ordering_across_stations(design):
    contours = [resample_contour(s, 161) for s in design.sections]
    le_fraction = [int(np.argmin(c[:, 0])) / (c.shape[0] - 1) for c in contours]
    # arc-length resampling shifts the LE by less than a contour step between stations, which
    # is what guarantees that stitched station pairs cannot twist into a self-intersection
    assert max(le_fraction) - min(le_fraction) < 0.05
    assert all(0.4 < f < 0.6 for f in le_fraction)


def test_every_loft_station_stays_blunt(design):
    loft = loft_blade(design, points_per_section=81)
    gaps = np.array([loft.te_gap_at(i) for i in range(loft.n_stations)])
    assert np.all(gaps >= design.te_min_m - 1e-12)
    assert gaps.min() == pytest.approx(design.min_te_thickness(), rel=1e-6)


def test_resample_rejects_too_few_points(design):
    with pytest.raises(ValueError):
        resample_contour(design.sections[0], 4)


def test_loft_station_layout(design):
    loft = loft_blade(design, points_per_section=81)
    assert isinstance(loft, LoftedBlade)
    assert loft.n_stations == 10 + ROOT_BLEND_STATIONS + TIP_BLEND_STATIONS
    assert np.all(np.diff(loft.station_radii) > 0.0)
    assert loft.hub_radius_m == pytest.approx(DEFAULT_HUB_RADIUS_OVER_R * design.radius_m)
    assert loft.tip_radius_m == pytest.approx(1.02 * design.radius_m)
    assert loft.station_radii[0] == pytest.approx(loft.hub_radius_m)
    assert loft.station_radii[-1] == pytest.approx(loft.tip_radius_m)
    assert loft.is_design_station.count(True) == 10
    assert all(st.shape == (81, 3) for st in loft.stations)


def test_loft_keeps_design_sections_exactly(design):
    loft = loft_blade(design, points_per_section=81)
    expected = np.asarray(design.r_R, dtype=float) * design.radius_m
    got = loft.station_radii[np.asarray(loft.is_design_station, dtype=bool)]
    assert np.allclose(got, expected, atol=1e-12)
    for k, section in zip(loft.design_indices, design.sections, strict=True):
        assert np.allclose(loft.stations[k], resample_contour(section, 81), atol=1e-12)


def test_root_blend_gives_blunt_rounded_root(design):
    loft = loft_blade(design, points_per_section=81)
    inner_index = int(np.flatnonzero(np.asarray(loft.is_design_station, dtype=bool))[0])
    chords = np.array([loft.chord_at(i) for i in range(inner_index + 1)])
    gaps = np.array([loft.te_gap_at(i) for i in range(inner_index + 1)])
    assert np.all(np.diff(chords) > 0.0)  # chord grows toward the first airfoil station
    assert np.all(gaps >= design.te_min_m - 1e-12)  # the blunt-TE constraint survives the root
    assert loft.chord_at(0) < chords[-1]
    thickness = np.array([np.ptp(loft.stations[i][:, 2]) for i in range(inner_index + 1)])
    assert thickness[0] > thickness[-1]  # root blank is relatively thicker (rounded)


def test_tip_cap_closes_the_surface(design):
    loft = loft_blade(design, points_per_section=81)
    outer = int(np.flatnonzero(np.asarray(loft.is_design_station, dtype=bool))[-1])
    chords = np.array([loft.chord_at(i) for i in range(outer, loft.n_stations)])
    assert np.all(np.diff(chords) < 0.0)
    assert chords[-1] < 0.5 * chords[0]
    assert loft.tip_radius_m > design.radius_m * 0.97


def test_spanwise_densification_keeps_every_design_station(design):
    loft = loft_blade(design, n_spanwise=24, points_per_section=61)
    assert loft.n_stations == 24
    assert loft.is_design_station.count(True) == 10
    assert np.all(np.diff(loft.station_radii) > 0.0)


def test_sections_mm_are_metres_times_1000(design):
    loft = loft_blade(design, points_per_section=61)
    assert np.allclose(loft.sections_mm[3], loft.stations[3] * 1000.0)
    assert loft.radii_mm[3] == pytest.approx(loft.station_radii[3] * 1000.0)


def test_loft_section_properties_report_vectors(design):
    loft = loft_blade(design, points_per_section=61)
    props = loft_section_properties(loft)
    assert set(props) == {"chord_m", "thickness_m", "te_gap_m"}
    assert props["chord_m"].shape == (loft.n_stations,)
    assert np.all(props["te_gap_m"] > 0.0)


def test_loft_rejects_mismatched_contours():
    with pytest.raises(ValueError):
        LoftedBlade(
            stations=[np.zeros((9, 3)), np.zeros((10, 3))],
            station_radii=[0.1, 0.2],
            n_per_section=9,
            hub_radius_m=0.05,
            tip_radius_m=0.25,
            radius_m=0.25,
        )
