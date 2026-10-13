"""Blunt trailing-edge measurement, projection and checks (T022, T023, T024, T029).

Two families of criteria from the plan are kept deliberately separate:

* the **hard** constraint ``t_TE >= trailing_edge_thickness_m`` (section 5.1/5.3), which is
  enforced by projecting ``te_gap`` at decode time so no code path can violate it; and
* the **band** criteria of section 5.4, ``0.002 <= t_TE/c <= 0.08`` and the advisory
  ``6 deg <= included angle <= 45 deg``.  The t/c band is blocking; the angle band is
  advisory because a blunt (flat or capped) trailing edge is by construction close to
  parallel-surfaced, and a genuinely round cap has a locally 180 deg wedge.  When the angle
  falls outside the advisory band the plan's alternative criterion (the t/c band) decides.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from hoverblade_fm.config.schema import AirfoilBounds, ResolvedConfig
from hoverblade_fm.geometry.cst import SectionParameters, surface_offsets
from hoverblade_fm.models import AirfoilPoints

TE_EPS = 1e-4


#: Relative guard band applied when projecting onto the minimum thickness, so a design never
#: sits exactly on the boundary where floating-point round-off could tip it below.
TE_GUARD = 1.0 + 1e-9


def clamp_te_gap(te_gap_norm: float, chord_m: float, te_min_m: float) -> float:
    """Hard projection: the geometric gap can only ever meet or exceed the user minimum."""
    required = te_min_m * TE_GUARD / max(chord_m, 1e-9)
    return float(max(te_gap_norm, required))


def te_required_gap(chord_m: float, te_min_m: float) -> float:
    return te_min_m * TE_GUARD / max(chord_m, 1e-9)


def enforce_blunt(
    params: SectionParameters, chord_m: float, cfg: ResolvedConfig
) -> tuple[SectionParameters, bool]:
    """Return parameters whose TE gap satisfies the hard constraint, plus whether clamping fired."""
    required = te_required_gap(chord_m, cfg.te_min_m)
    if params.te_gap >= required:
        return params, False
    patched = SectionParameters(**{**params_dict(params), "te_gap": required})
    return patched, True


def params_dict(params: SectionParameters) -> dict[str, float]:
    from dataclasses import asdict

    return asdict(params)


def te_thickness_m(points: AirfoilPoints, chord_m: float) -> float:
    return points.te_thickness * chord_m


def te_thickness_at(points: AirfoilPoints, chord_m: float, x_over_c: float) -> float:
    """Thickness measured slightly ahead of the TE (the 0.995 / 0.990 manufacturing checks)."""
    return points.trailing_edge_gap_at(x_over_c) * chord_m


def _surface_tangent_at_te(points: AirfoilPoints, x_query: float) -> tuple[float, float]:
    """Return ``(slope_upper, slope_lower)`` in chord units at ``x_query`` (LE->TE convention)."""
    x_lower, y_lower, x_upper, y_upper = points.surface_pairs()
    d = max(1e-3, 1.0 - x_query)
    pts_u = (x_upper >= x_query - d) & (x_upper <= x_query + 1e-9 + d)
    pts_l = (x_lower >= x_query - d) & (x_lower <= x_query + 1e-9 + d)
    slopes: list[float] = []
    for xs, ys, mask in ((x_upper, y_upper, pts_u), (x_lower, y_lower, pts_l)):
        xm, ym = xs[mask], ys[mask]
        if xm.size >= 3:
            coef = np.polyfit(xm, ym, 2)
            slopes.append(float(np.polyval(np.polyder(coef), x_query)))
        elif xm.size == 2:
            slopes.append(float((ym[1] - ym[0]) / max(xm[1] - xm[0], 1e-12)))
        else:
            slopes.append(0.0)
    return slopes[0], slopes[1]


def analytic_tangent_at_te(params: SectionParameters, x_query: float = 1.0 - TE_EPS) -> tuple[float, float]:
    """Slopes from the analytic parameterisation; robust for the optimiser's hot loop."""
    xs = np.array([x_query - 5e-4, x_query, min(1.0, x_query + 5e-4)])
    upper, lower = surface_offsets(xs, params)
    du = (upper[2] - upper[0]) / max(xs[2] - xs[0], 1e-12)
    dl = (lower[2] - lower[0]) / max(xs[2] - xs[0], 1e-12)
    return float(du), float(dl)


def te_included_angle_deg(params: SectionParameters) -> float:
    """Wedge angle between the upper and lower surface tangents at the trailing edge.

    The upper surface slopes down and the lower surface slopes up toward a converging (sharp)
    edge, so the opening angle is ``atan(slope_lower) - atan(slope_upper)``; a blunt gap makes
    that value small, and a round cap makes the local surface turn through a much larger angle.
    """
    du, dl = analytic_tangent_at_te(params)
    return float(np.rad2deg(np.arctan(dl) - np.arctan(du)))


def te_included_angle_from_points(points: AirfoilPoints) -> float:
    du, dl = _surface_tangent_at_te(points, 1.0 - 3e-3)
    return float(np.rad2deg(np.arctan(dl) - np.arctan(du)))


def te_radius_estimate_m(points: AirfoilPoints, chord_m: float) -> float:
    """Radius of a circle through the last upper point, the TE mid point and the last lower point."""
    x_lower, y_lower, x_upper, y_upper = points.surface_pairs()
    k = max(2, min(6, points.n_per_surface // 12))
    from hoverblade_fm.geometry.cst import circle_radius

    xs = np.concatenate((x_upper[-k:][::-1], [1.0], x_lower[-k:])) * chord_m
    ys = np.concatenate((y_upper[-k:][::-1], [points.te_midline], y_lower[-k:])) * chord_m
    return circle_radius(xs, ys)


def te_edge_length_m(points: AirfoilPoints, chord_m: float) -> float:
    """Arc length of the trailing-edge closure, i.e. what a cutter/mould must reproduce."""
    x = points.x
    y = points.y
    near = x >= 1.0 - 2e-3
    idx = np.where(near)[0]
    if idx.size < 2:
        return abs(points.te_thickness) * chord_m
    seg = np.diff(np.column_stack((x[idx], y[idx])), axis=0)
    return float(np.sum(np.hypot(seg[:, 0], seg[:, 1])) * chord_m)


@dataclass(slots=True)
class TrailingEdgeCheck:
    """Per-station trailing-edge verdict, ready to be serialised into the run outputs."""

    station_id: int
    chord_m: float
    te_thickness_m: float
    te_required_m: float
    te_margin_m: float
    te_over_chord: float
    included_angle_deg: float
    te_radius_m: float
    te_edge_length_m: float
    te_at_0p995_m: float
    te_at_0p990_m: float
    hard_ok: bool
    tc_band_ok: bool
    angle_band_advisory_ok: bool

    @property
    def ok(self) -> bool:
        return bool(self.hard_ok and self.tc_band_ok)

    def to_dict(self) -> dict[str, float | bool | int]:
        from dataclasses import asdict

        out = dict(asdict(self))
        out["ok"] = self.ok
        return out


def check_trailing_edge(
    points: AirfoilPoints,
    chord_m: float,
    cfg: ResolvedConfig,
    station_id: int,
    params: SectionParameters | None = None,
    bounds: AirfoilBounds | None = None,
) -> TrailingEdgeCheck:
    bounds = bounds or cfg.airfoil
    te_m = te_thickness_m(points, chord_m)
    required = cfg.te_min_m
    te_c = te_m / chord_m if chord_m > 0 else float("nan")
    angle = te_included_angle_deg(params) if params is not None else te_included_angle_from_points(points)
    hard_ok = te_m >= required - 1e-12
    tc_ok = bounds.te_over_chord_min - 1e-9 <= te_c <= bounds.te_over_chord_max + 1e-9
    angle_ok = bounds.te_included_angle_min_deg <= angle <= bounds.te_included_angle_max_deg
    return TrailingEdgeCheck(
        station_id=station_id,
        chord_m=chord_m,
        te_thickness_m=te_m,
        te_required_m=required,
        te_margin_m=te_m - required,
        te_over_chord=te_c,
        included_angle_deg=angle,
        te_radius_m=te_radius_estimate_m(points, chord_m),
        te_edge_length_m=te_edge_length_m(points, chord_m),
        te_at_0p995_m=te_thickness_at(points, chord_m, 0.995),
        te_at_0p990_m=te_thickness_at(points, chord_m, 0.990),
        hard_ok=hard_ok,
        tc_band_ok=tc_ok,
        angle_band_advisory_ok=angle_ok,
    )


def minimum_required_chord(cfg: ResolvedConfig) -> float:
    """Chord below which the required TE thickness breaks the advisory t/c ceiling of 8%."""
    return cfg.te_min_m / cfg.airfoil.te_over_chord_max


__all__ = [
    "TrailingEdgeCheck",
    "analytic_tangent_at_te",
    "check_trailing_edge",
    "clamp_te_gap",
    "enforce_blunt",
    "minimum_required_chord",
    "te_edge_length_m",
    "te_included_angle_deg",
    "te_included_angle_from_points",
    "te_radius_estimate_m",
    "te_required_gap",
    "te_thickness_at",
    "te_thickness_m",
]
