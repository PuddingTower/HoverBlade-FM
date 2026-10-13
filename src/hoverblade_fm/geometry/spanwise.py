"""Spanwise chord / twist distributions (T027, T028, T029) and their physics prior.

Control points are interpolated with monotone cubic (PCHIP) rather than a free B-spline so
that chord and twist cannot overshoot between stations - the CAD agent's loft then always
sees a monotone, non-crossing planform.  The optimiser moves the control points; the
reference planform below gives it a physically sensible starting blade.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.interpolate import PchipInterpolator

from hoverblade_fm.config.schema import ResolvedConfig
from hoverblade_fm.models import SECTION_COUNT

#: Radial positions of the planform control points (non-dimensional).
DEFAULT_CHORD_CP_R: tuple[float, ...] = (0.10, 0.30, 0.52, 0.74, 0.97)
DEFAULT_TWIST_CP_R: tuple[float, ...] = (0.12, 0.35, 0.60, 0.82, 0.97)

#: Advisory smoothness limits used by the geometric checks (T029).
MAX_DC_DR = 1.6
MAX_DTWIST_DR_DEG = 60.0
MAX_NEIGHBOUR_CHORD_JUMP = 0.45
MAX_NEIGHBOUR_TWIST_JUMP_DEG = 12.0


def _trapz(y: np.ndarray, x: np.ndarray) -> float:
    """Trapezoidal integral that works on both NumPy 1.x and 2.x."""
    fn = getattr(np, "trapezoid", None) or np.trapz  # type: ignore[attr-defined]
    return float(fn(y, x))


@dataclass(slots=True)
class Spanwise:
    """A monotone spanwise distribution with its control points and evaluation grid."""

    r_cp: np.ndarray
    values_cp: np.ndarray
    name: str = "distribution"

    def __post_init__(self) -> None:
        self.r_cp = np.asarray(self.r_cp, dtype=float)
        self.values_cp = np.asarray(self.values_cp, dtype=float)
        if self.r_cp.shape != self.values_cp.shape:
            raise ValueError(f"{self.name}: control point arrays must match in length")
        if self.r_cp.size < 2:
            raise ValueError(f"{self.name}: at least two control points are required")
        if np.any(np.diff(self.r_cp) <= 0):
            raise ValueError(f"{self.name}: control point radii must be strictly increasing")

    def evaluate(self, r: np.ndarray) -> np.ndarray:
        r = np.asarray(r, dtype=float)
        interp = PchipInterpolator(self.r_cp, self.values_cp, extrapolate=False)
        out = interp(np.clip(r, self.r_cp[0], self.r_cp[-1]))
        below = r < self.r_cp[0]
        above = r > self.r_cp[-1]
        if np.any(below):
            out[below] = self.values_cp[0]
        if np.any(above):
            out[above] = self.values_cp[-1]
        return np.asarray(out, dtype=float)

    def clipped(self, lo: float, hi: float) -> Spanwise:
        return Spanwise(self.r_cp, np.clip(self.values_cp, lo, hi), self.name)

    def derivative(self, r: np.ndarray, h: float = 1e-3) -> np.ndarray:
        r = np.asarray(r, dtype=float)
        return (self.evaluate(np.minimum(r + h, 1.0)) - self.evaluate(np.maximum(r - h, 0.0))) / (
            np.minimum(r + h, 1.0) - np.maximum(r - h, 0.0)
        )


def make_spanwise(
    values: Sequence[float] | np.ndarray,
    r_cp: Sequence[float] | np.ndarray | None,
    name: str,
) -> Spanwise:
    return Spanwise(
        np.asarray(r_cp if r_cp is not None else DEFAULT_CHORD_CP_R, dtype=float),
        np.asarray(values, dtype=float),
        name,
    )


def chord_distribution(
    chord_cp: np.ndarray | list[float], cfg: ResolvedConfig, r_cp: np.ndarray | None = None
) -> np.ndarray:
    """Chord at the 10 stations, projected onto the bounds and the manufacturability rate limit."""
    r = np.asarray(cfg.r_stations, dtype=float)
    span = make_spanwise(chord_cp, r_cp if r_cp is not None else DEFAULT_CHORD_CP_R, "chord")
    chord = span.evaluate(r)
    for _ in range(3):
        chord = limit_rate(r, chord, MAX_DC_DR * cfg.radius_m)
        chord = np.clip(chord, cfg.chord_min_m, cfg.chord_max_m)
    return chord


def twist_distribution(
    twist_cp: np.ndarray | list[float], cfg: ResolvedConfig, r_cp: np.ndarray | None = None
) -> np.ndarray:
    """Twist at the 10 stations, projected onto the bounds and the washout rate limit."""
    from hoverblade_fm.config.schema import TWIST_MAX_DEG, TWIST_MIN_DEG

    r = np.asarray(cfg.r_stations, dtype=float)
    span = make_spanwise(twist_cp, r_cp if r_cp is not None else DEFAULT_TWIST_CP_R, "twist")
    twist = span.evaluate(r)
    for _ in range(3):
        twist = limit_rate(r, twist, MAX_DTWIST_DR_DEG)
        twist = np.clip(twist, TWIST_MIN_DEG, TWIST_MAX_DEG)
    return twist


def smoothness_metrics(r_R: np.ndarray, values: np.ndarray) -> dict[str, float]:
    """Change-rate diagnostics of a discrete spanwise distribution (T029)."""
    r = np.asarray(r_R, dtype=float)
    v = np.asarray(values, dtype=float)
    dv = np.gradient(v, r)
    inner = slice(1, -1)
    return {
        "max_abs_dv_dr": float(np.max(np.abs(dv))) if dv.size else 0.0,
        "max_neighbour_jump": float(np.max(np.abs(np.diff(v)))) if v.size > 1 else 0.0,
        "max_neighbour_jump_interior": float(np.max(np.abs(np.diff(v[inner])))) if v.size > 3 else 0.0,
        "roughness": float(np.sum(dv[inner] ** 2)) if dv.size > 2 else 0.0,
    }


def spanwise_penalty(r_R: np.ndarray, chord_m: np.ndarray, twist_deg: np.ndarray, radius_m: float) -> float:
    """Quadratic penalty on chord/twist change rates, normalised by the advisory limits."""
    r = np.asarray(r_R, dtype=float)
    dc_dr = np.gradient(np.asarray(chord_m, dtype=float) / radius_m, r)
    dth_dr = np.gradient(np.asarray(twist_deg, dtype=float), r)
    over_c = np.maximum(np.abs(dc_dr) / MAX_DC_DR - 1.0, 0.0)
    over_t = np.maximum(np.abs(dth_dr) / MAX_DTWIST_DR_DEG - 1.0, 0.0)
    jump_c = np.maximum(
        np.abs(np.diff(np.asarray(chord_m, dtype=float) / radius_m)) / MAX_NEIGHBOUR_CHORD_JUMP - 1.0, 0.0
    )
    jump_t = np.maximum(
        np.abs(np.diff(np.asarray(twist_deg, dtype=float))) / MAX_NEIGHBOUR_TWIST_JUMP_DEG - 1.0, 0.0
    )
    return float(np.sum(over_c**2) + np.sum(over_t**2) + 0.5 * np.sum(jump_c**2) + 0.5 * np.sum(jump_t**2))


def control_points_from_values(
    values: np.ndarray | list[float],
    r_values: np.ndarray | list[float],
    r_cp: np.ndarray | list[float],
) -> np.ndarray:
    """Down-sample a station-wise distribution onto the control-point knots."""
    span = make_spanwise(values, r_values, "tmp")
    return span.evaluate(np.asarray(r_cp, dtype=float))


def limit_rate(x: np.ndarray, values: np.ndarray, max_rate: float) -> np.ndarray:
    """Enforce ``|dv/dx| <= max_rate`` on a discrete spanwise distribution (T029 repair).

    Used both to post-process the physics prior and as the optimiser's constraint-repair
    projection, so an otherwise good design never fails the manufacturability rate check.
    """
    v = np.asarray(values, dtype=float).copy()
    x = np.asarray(x, dtype=float)
    if v.size < 2:
        return v
    step = max_rate * np.diff(x)
    for _ in range(40):
        before = v.copy()
        for i in range(1, v.size):
            v[i] = min(v[i], v[i - 1] + step[i - 1])
        for i in range(v.size - 2, -1, -1):
            v[i] = min(v[i], v[i + 1] + step[i]) if v[i] > v[i + 1] else v[i]
        for i in range(v.size - 2, -1, -1):
            v[i] = max(v[i], v[i + 1] - step[i])
        if np.allclose(before, v, atol=1e-12):
            break
    return v


def reference_planform(
    cfg: ResolvedConfig,
    cl_target: float = 0.60,
    camber: float = 0.035,
    root_chord_cap: float = 1.45,
) -> tuple[np.ndarray, np.ndarray]:
    """Physics-based initial chord and twist (T049's seed).

    Momentum theory gives a uniform induced velocity in hover, so the spanwise loading is
    taken as elliptic-like, ``dT/dr ~ sqrt(1 - x^2)``, reduced toward the root by the
    loss-region factor ``x/(x + 0.18)``.  The loading is normalised to the required thrust,
    chord follows from ``dT/dr = 0.5 rho N c (Omega r)^2 cl_target``, and the twist from the
    angle of attack needed for that lift coefficient plus the induced inflow angle and the
    section's zero-lift angle, ``theta = alpha + phi + alpha_0``.
    """
    from hoverblade_fm.geometry.cst import SectionParameters
    from hoverblade_fm.geometry.sections import estimated_zero_lift_angle_deg

    rho, omega, radius = cfg.rho, cfg.omega, cfg.radius_m
    thrust = cfg.thrust_n
    v_i = math.sqrt(thrust / (2.0 * rho * cfg.disk_area))
    r = np.asarray(cfg.r_stations, dtype=float)
    x = np.clip(r, 1e-3, 0.999)

    fine = np.linspace(cfg.solver.root_extension_r, 1.0, 401)
    shape = np.sqrt(np.maximum(1.0 - fine**2, 0.0)) * fine / (fine + 0.18)
    norm = float(_trapz(shape, fine))
    # dT/dr integrates over r = R*x, so the normalised shape must be divided by the radius
    dT_dr_fine = thrust * shape / max(norm * radius, 1e-9)
    chord_fine = dT_dr_fine / (
        0.5 * rho * cfg.blade_count * (omega * radius * fine) ** 2 * max(cl_target, 0.15)
    )
    chord_fine = np.clip(chord_fine, cfg.chord_min_m, cfg.chord_max_m)
    chord = np.interp(x, fine, chord_fine)

    dT_dr = thrust * shape_func(x) / max(norm * radius, 1e-9)
    q_local = 0.5 * rho * (omega * radius * x) ** 2
    cl_local = np.clip(dT_dr / np.maximum(cfg.blade_count * chord * q_local, 1e-9), 0.05, 1.30)
    alpha = np.clip(cl_local / (2.0 * math.pi * 0.93), -0.30, 0.26)
    phi = np.arctan2(v_i, omega * radius * x)
    alpha0 = np.radians(estimated_zero_lift_angle_deg(SectionParameters(camber=camber)))
    twist = np.degrees(alpha + phi + alpha0)
    twist = np.clip(twist, -2.0, 26.0)

    chord = np.minimum(chord, root_chord_cap * float(np.median(chord)))
    chord = limit_rate(x, chord, MAX_DC_DR * radius)
    chord = np.clip(chord, cfg.chord_min_m, cfg.chord_max_m)
    twist = limit_rate(x, twist, MAX_DTWIST_DR_DEG)

    chord_span = make_spanwise(chord, x, "chord_raw").clipped(cfg.chord_min_m, cfg.chord_max_m)
    twist_span = make_spanwise(twist, x, "twist_raw")
    chord_cp = chord_span.evaluate(np.asarray(DEFAULT_CHORD_CP_R))
    twist_cp = twist_span.evaluate(np.asarray(DEFAULT_TWIST_CP_R))
    return chord_cp, twist_cp


def shape_func(x: np.ndarray) -> np.ndarray:
    """Elliptic-with-root-relief spanwise loading shape."""
    return np.sqrt(np.maximum(1.0 - np.asarray(x, dtype=float) ** 2, 0.0)) * x / (x + 0.18)


__all__ = [
    "DEFAULT_CHORD_CP_R",
    "DEFAULT_TWIST_CP_R",
    "MAX_DTWIST_DR_DEG",
    "MAX_DC_DR",
    "SECTION_COUNT",
    "Spanwise",
    "chord_distribution",
    "control_points_from_values",
    "limit_rate",
    "make_spanwise",
    "reference_planform",
    "smoothness_metrics",
    "spanwise_penalty",
    "twist_distribution",
]
