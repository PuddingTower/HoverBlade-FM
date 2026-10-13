"""Synthetic :class:`~hoverblade_fm.models.Design` builder for CAD tests.

Deliberately independent of ``geometry``/``aero``/``optimize`` (they are developed in
parallel): the blunt-trailing-edge airfoil here is a closed-form NACA-type thickness plus a
linear TE ramp, which is enough to exercise every CAD code path.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from hoverblade_fm.models import DEFAULT_R_STATIONS, AirfoilPoints, Design, Section

NACA_THICKNESS = (0.2969, -0.1260, -0.3516, 0.2843, -0.1015)


def thickness_shape(xi: np.ndarray, t_over_c: float) -> np.ndarray:
    """NACA-style thickness distribution, forced to exactly zero at ``xi = 1``."""
    xi = np.clip(np.asarray(xi, dtype=float), 0.0, 1.0)
    raw = 5.0 * t_over_c * sum(a * xi ** (i / 2.0) for i, a in enumerate(NACA_THICKNESS))
    tail = 5.0 * t_over_c * sum(a for a in NACA_THICKNESS)
    return raw - xi * tail


def blunt_airfoil(
    *,
    te_gap: float = 0.012,
    thickness_to_chord: float = 0.14,
    camber_to_chord: float = 0.03,
    n_per_surface: int = 80,
) -> AirfoilPoints:
    """Unit-chord contour ordered lower TE -> lower surface -> LE -> upper surface -> upper TE.

    ``y_upper(1) - y_lower(1)`` equals ``te_gap`` exactly, i.e. the trailing edge is blunt by
    construction and never closes to a point.
    """
    xi = np.linspace(0.0, 1.0, n_per_surface)
    half = 0.5 * thickness_shape(xi, thickness_to_chord)
    yc = 4.0 * camber_to_chord * xi * (1.0 - xi)
    y_upper = yc + half + 0.5 * te_gap * xi
    y_lower = yc - half - 0.5 * te_gap * xi
    x = np.concatenate((xi[::-1], xi))
    y = np.concatenate((y_lower[::-1], y_upper))
    return AirfoilPoints(x=x, y=y, n_per_surface=n_per_surface)


def make_section(
    station_id: int,
    *,
    r_R: float,
    radius_m: float,
    chord_m: float,
    twist_deg: float,
    te_gap: float,
    thickness_to_chord: float,
    camber_to_chord: float = 0.03,
    n_per_surface: int = 80,
) -> Section:
    points = blunt_airfoil(
        te_gap=te_gap,
        thickness_to_chord=thickness_to_chord,
        camber_to_chord=camber_to_chord,
        n_per_surface=n_per_surface,
    )
    return Section(
        station_id=station_id,
        r_R=r_R,
        r_m=r_R * radius_m,
        chord_m=chord_m,
        twist_deg=twist_deg,
        te_thickness_m=points.te_thickness * chord_m,
        max_thickness_m=points.max_thickness * chord_m,
        thickness_to_chord=points.max_thickness,
        camber_m=points.max_camber * chord_m,
        le_radius_m=0.006 * chord_m,
        te_included_angle_deg=_te_angle(points),
        reynolds=4.0e5,
        mach=0.35,
        cl_design=0.6,
        cd_design=0.012,
        loading_n_m=120.0,
        alpha_effective_deg=4.0,
        points=points,
        airfoil_file=f"cst_{station_id:02d}",
    )


def _te_angle(points: AirfoilPoints) -> float:
    """Included angle between the two surface tangents at the trailing edge [deg]."""
    x_lower, y_lower, x_upper, y_upper = points.surface_pairs()
    du = np.array([x_upper[-1] - x_upper[-3], y_upper[-1] - y_upper[-3]])
    dl = np.array([x_lower[-1] - x_lower[-3], y_lower[-1] - y_lower[-3]])
    cosang = float(np.dot(du, dl) / (np.linalg.norm(du) * np.linalg.norm(dl) + 1e-30))
    return float(np.rad2deg(np.arccos(np.clip(cosang, -1.0, 1.0))))


def make_design(
    *,
    radius_m: float = 0.80,
    te_min_m: float = 0.0015,
    blade_count: int = 2,
    rpm: float = 1600.0,
    te_multiplier: float = 1.0,
    chord_base_over_r: float = 0.14,
    twist_root_deg: float = 12.0,
    twist_tip_deg: float = 4.0,
    n_per_surface: int = 80,
) -> Design:
    """A smooth, manufacturable 10-station hover blade used across the CAD tests."""
    r_R = np.asarray(DEFAULT_R_STATIONS, dtype=float)
    # gentle taper (chord) and washout (twist); quadratic-ish, monotone
    chord = chord_base_over_r * radius_m * (1.0 - 0.45 * (r_R - r_R[0]) / (r_R[-1] - r_R[0]))
    twist = twist_root_deg + (twist_tip_deg - twist_root_deg) * (r_R - r_R[0]) / (r_R[-1] - r_R[0])
    te_gap_nd = te_multiplier * te_min_m / chord
    te_gap_nd = np.maximum(te_gap_nd, 0.002)
    t_over_c = np.interp(r_R, [0.1, 0.5, 0.97], [0.19, 0.14, 0.11])
    sections = [
        make_section(
            i,
            r_R=float(r_R[i]),
            radius_m=radius_m,
            chord_m=float(chord[i]),
            twist_deg=float(twist[i]),
            te_gap=float(te_gap_nd[i]),
            thickness_to_chord=float(t_over_c[i]),
            n_per_surface=n_per_surface,
        )
        for i in range(len(r_R))
    ]
    omega = 2.0 * np.pi * rpm / 60.0
    return Design(
        sections=sections,
        radius_m=radius_m,
        blade_count=blade_count,
        rpm=rpm,
        omega=omega,
        te_min_m=te_min_m,
        collective_trim_deg=3.0,
        label="fixture",
    )


def violating_design(design: Design) -> Design:
    """Hand-edited copy of ``design`` whose sections fall below the blunt-TE minimum."""
    sections = []
    for s in design.sections:
        pts = s.points
        target_gap_nd = 0.6 * design.te_min_m / s.chord_m
        shift = 0.5 * max(pts.te_thickness - target_gap_nd, 0.0)
        y = pts.y.copy()
        y[0] += shift
        y[-1] -= shift
        broken = replace(pts, x=pts.x.copy(), y=y)
        sections.append(replace(s, points=broken, te_thickness_m=broken.te_thickness * s.chord_m))
    return Design(
        sections=sections,
        radius_m=design.radius_m,
        blade_count=design.blade_count,
        rpm=design.rpm,
        omega=design.omega,
        te_min_m=design.te_min_m,
        label="fixture-violating",
    )
