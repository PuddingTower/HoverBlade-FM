"""Geometry legality checks (plan section 9.4, task T030).

Every check returns human-readable issue strings so the same routine can be used as an
optimiser penalty term, as a manufacturing gate and as a CI assertion.
"""

from __future__ import annotations

import numpy as np

from hoverblade_fm.config.schema import TWIST_MAX_DEG, TWIST_MIN_DEG, ResolvedConfig
from hoverblade_fm.geometry.cst import le_radius
from hoverblade_fm.geometry.spanwise import (
    MAX_DC_DR,
    MAX_DTWIST_DR_DEG,
    MAX_NEIGHBOUR_CHORD_JUMP,
    MAX_NEIGHBOUR_TWIST_JUMP_DEG,
)
from hoverblade_fm.models import SECTION_COUNT, AirfoilPoints, Design

LE_GAP_TOLERANCE = 1e-9


def validate_airfoil_points(points: AirfoilPoints, bounds=None) -> list[str]:
    """Single-section geometric legality: closure, crossing, finiteness, thickness sanity."""
    issues: list[str] = []
    if not np.all(np.isfinite(points.x)) or not np.all(np.isfinite(points.y)):
        return ["non-finite coordinates"]
    x_lower, y_lower, x_upper, y_upper = points.surface_pairs()
    if np.any(np.diff(x_upper) <= 0) or np.any(np.diff(x_lower) <= 0):
        issues.append("chordwise stations are not strictly increasing on a surface")
    if not (abs(points.x[0] - 1.0) < 1e-9 and abs(points.x[-1] - 1.0) < 1e-9):
        issues.append("contour does not start and end at the trailing edge")
    if abs(points.x[len(points.y) // 2 - 1]) > 1e-6:
        issues.append("contour does not close at the leading edge")
    gap = points.te_thickness
    if gap <= LE_GAP_TOLERANCE:
        issues.append(f"sharp trailing edge detected (t_TE/c = {gap:.3e})")
    diff = y_upper - y_lower
    interior = slice(1, None)
    if np.any(diff[interior] < -1e-12):
        worst = int(np.argmin(diff[interior]) + 1)
        issues.append(f"surface crossover at x/c = {x_upper[worst]:.4f}")
    t = points.max_thickness
    if t <= 1e-6:
        issues.append("degenerate (zero) thickness")
    if not 0.05 <= points.thickness_location <= 0.85:
        issues.append(f"max thickness location x_t = {points.thickness_location:.3f} is implausible")
    if points.max_camber > 0.5 * t + 0.25:
        issues.append("camber exceeds twice the thickness envelope")
    if le_radius(points) <= 1e-6:
        issues.append("leading-edge radius collapsed to zero")
    if bounds is not None:
        if not bounds.thickness_to_chord_min - 1e-6 <= t <= bounds.thickness_to_chord_max + 1e-6:
            issues.append(
                f"t/c = {t:.4f} outside [{bounds.thickness_to_chord_min}, {bounds.thickness_to_chord_max}]"
            )
        if gap > bounds.te_over_chord_max + 1e-9:
            issues.append(f"t_TE/c = {gap:.4f} above the {bounds.te_over_chord_max} ceiling")
        if abs(points.max_camber) > bounds.camber_max_over_chord + 1e-6:
            issues.append(f"|camber|/c = {abs(points.max_camber):.4f} above {bounds.camber_max_over_chord}")
    return issues


def validate_section_geometry(
    points: AirfoilPoints,
    chord_m: float,
    twist_deg: float,
    cfg: ResolvedConfig,
) -> list[str]:
    issues: list[str] = []
    if not np.isfinite(chord_m) or chord_m <= 0.0:
        issues.append(f"non-positive chord {chord_m}")
    if chord_m < cfg.chord_min_m - 1e-12 or chord_m > cfg.chord_max_m + 1e-12:
        issues.append(f"chord {chord_m:.4f} m outside [{cfg.chord_min_m:.4f}, {cfg.chord_max_m:.4f}] m")
    if not TWIST_MIN_DEG - 1e-9 <= twist_deg <= TWIST_MAX_DEG + 1e-9:
        issues.append(f"twist {twist_deg:.2f} deg outside [{TWIST_MIN_DEG}, {TWIST_MAX_DEG}]")
    te_m = points.te_thickness * chord_m
    if te_m < cfg.te_min_m - 1e-12:
        issues.append(
            f"trailing-edge thickness {te_m * 1000:.4f} mm below the required {cfg.te_min_m * 1000:.4f} mm"
        )
    issues.extend(validate_airfoil_points(points, cfg.airfoil))
    return issues


def validate_design(design: Design, cfg: ResolvedConfig) -> list[str]:
    """Whole-blade legality, including the inter-station smoothness limits (T029)."""
    issues: list[str] = []
    if len(design.sections) != SECTION_COUNT:
        issues.append(f"expected {SECTION_COUNT} sections, found {len(design.sections)}")
    r = design.r_R
    if np.any(np.diff(r) <= 0):
        issues.append("radial stations are not strictly increasing")
    if design.blade_count != cfg.blade_count:
        issues.append(f"blade count {design.blade_count} differs from configuration {cfg.blade_count}")
    if abs(design.radius_m - cfg.radius_m) > 1e-9:
        issues.append("blade radius differs from configuration")

    for section in design.sections:
        # the twist bound applies to the design-space (untrimmed) twist; collective is an
        # operating point that the hover trim legitimately moves
        geometric_twist = section.twist_deg - design.collective_trim_deg
        station_issues = validate_section_geometry(section.points, section.chord_m, geometric_twist, cfg)
        issues.extend(
            f"station {section.station_id} (r/R={section.r_R:.3f}): {msg}" for msg in station_issues
        )

    chord = design.chord_m / cfg.radius_m
    twist = design.twist_deg - design.collective_trim_deg
    dc_dr = np.gradient(chord, r)
    dth_dr = np.gradient(twist, r)
    if np.max(np.abs(dc_dr)) > MAX_DC_DR + 1e-6:
        issues.append(f"chord rate |dc/d(r/R)| = {np.max(np.abs(dc_dr)):.3f} exceeds {MAX_DC_DR}")
    if np.max(np.abs(dth_dr)) > MAX_DTWIST_DR_DEG + 1e-6:
        issues.append(
            f"twist rate |dtheta/d(r/R)| = {np.max(np.abs(dth_dr)):.2f} deg exceeds {MAX_DTWIST_DR_DEG}"
        )
    if design.min_te_thickness() < cfg.te_min_m - 1e-12:
        issues.append(
            f"global minimum trailing-edge thickness {design.min_te_thickness() * 1000:.4f} mm "
            f"below the required {cfg.te_min_m * 1000:.4f} mm"
        )
    if not np.all(np.isfinite(chord)) or not np.all(np.isfinite(twist)):
        issues.append("non-finite planform values")
    return issues


def neighbour_jumps(design: Design, cfg: ResolvedConfig) -> dict[str, float]:
    """Maximum chord / twist jump between adjacent stations, normalised."""
    chord = design.chord_m / cfg.radius_m
    twist = design.twist_deg
    return {
        "max_chord_jump": float(np.max(np.abs(np.diff(chord)))) if chord.size > 1 else 0.0,
        "max_twist_jump_deg": float(np.max(np.abs(np.diff(twist)))) if twist.size > 1 else 0.0,
        "chord_jump_ratio": float(np.max(np.abs(np.diff(chord))) / MAX_NEIGHBOUR_CHORD_JUMP)
        if chord.size > 1
        else 0.0,
        "twist_jump_ratio": (
            float(np.max(np.abs(np.diff(twist))) / MAX_NEIGHBOUR_TWIST_JUMP_DEG) if twist.size > 1 else 0.0
        ),
    }


def geometry_penalty(design: Design, cfg: ResolvedConfig) -> float:
    """Continuous, non-negative measure of geometric violation used as an optimiser penalty."""
    total = 0.0
    for section in design.sections:
        gap_required = cfg.te_min_m / section.chord_m if section.chord_m > 0 else np.inf
        deficit = max(gap_required - section.points.te_thickness, 0.0)
        total += 100.0 * deficit**2
        x_lower, y_lower, x_upper, y_upper = section.points.surface_pairs()
        diff = y_upper - y_lower
        cross = np.maximum(-diff[1:], 0.0)
        total += 1e4 * float(np.sum(cross**2))
        total += 1e3 * float(max(section.points.max_thickness - cfg.airfoil.thickness_to_chord_max, 0.0) ** 2)
        total += 1e3 * float(max(cfg.airfoil.thickness_to_chord_min - section.points.max_thickness, 0.0) ** 2)
    chord = design.chord_m / cfg.radius_m
    twist = design.twist_deg - design.collective_trim_deg
    total += 1e3 * float(np.sum(np.maximum(twist - TWIST_MAX_DEG, 0.0) ** 2))
    total += 1e3 * float(np.sum(np.maximum(TWIST_MIN_DEG - twist, 0.0) ** 2))
    total += 1e3 * float(np.sum(np.maximum(chord - cfg.chord_max_m / cfg.radius_m, 0.0) ** 2))
    total += 1e3 * float(np.sum(np.maximum(cfg.chord_min_m / cfg.radius_m - chord, 0.0) ** 2))
    return float(total)
