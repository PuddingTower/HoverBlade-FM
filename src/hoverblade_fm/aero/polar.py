"""Airfoil polar sources (T039-T043).

``PolarSource`` is the replaceable interface demanded by the plan: the analytic model below
is the default (no external dependency, deterministic, CI-safe), a table/source or XFOIL
adapter can be dropped in later, and :class:`LinearPolar` exists purely so the momentum
theory limit can be verified without any profile drag at all.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

CL_SLOPE_IDEAL = 2.0 * math.pi


@dataclass(frozen=True)
class PolarPoint:
    alpha_deg: np.ndarray
    cl: np.ndarray
    cd: np.ndarray
    cm: np.ndarray


class PolarSource:
    """Interface: evaluate ``(cl, cd, cm)`` for arrays of angles and local conditions."""

    def __call__(
        self,
        alpha_deg: np.ndarray | float,
        reynolds: np.ndarray | float,
        mach: np.ndarray | float,
        t_over_c: np.ndarray | float,
        te_over_c: np.ndarray | float,
        camber: np.ndarray | float,
    ) -> PolarPoint:
        raise NotImplementedError


@dataclass(frozen=True)
class PolarSettings:
    """Coefficients of the analytic (Level 1) polar.  All documented in docs/user_guide.md."""

    slope_efficiency: float = 0.93
    alpha0_base_deg: float = 0.0
    camber_alpha_slope_deg: float = 22.0
    cl_max_ref: float = 1.45
    re_reference: float = 1.5e6
    re_low_limit: float = 2.0e4
    cd_form_factor_slope: float = 2.0
    te_drag_slope: float = 1.0
    te_thickness_interaction: float = 0.4
    mach_critical_base: float = 0.86
    mach_thickness_penalty: float = 0.60
    mach_lift_penalty: float = 0.20
    wave_drag_factor: float = 8.0
    wave_drag_cap: float = 0.15
    stall_onset_fraction: float = 0.85
    stall_drag_factor: float = 2.4
    cm_base: float = -0.05
    cm_camber_factor: float = 0.6
    profile_drag_scale: float = 1.0


def _as_array(value: np.ndarray | float, like: np.ndarray | None = None) -> np.ndarray:
    arr = np.asarray(value, dtype=float)
    if arr.ndim == 0:
        arr = np.full_like(np.asarray(like if like is not None else [0.0], dtype=float), float(arr))
    return arr


class AnalyticPolar(PolarSource):
    """Empirical, differentiable-enough polar: tanh stall, ITTV friction, blunt-TE base drag, wave drag."""

    def __init__(self, settings: PolarSettings | None = None) -> None:
        self.settings = settings or PolarSettings()

    def zero_lift_angle_deg(self, camber: np.ndarray | float) -> np.ndarray:
        s = self.settings
        return s.alpha0_base_deg - s.camber_alpha_slope_deg * _as_array(camber)

    def cl_max(self, reynolds: np.ndarray, t_over_c: np.ndarray, mach: np.ndarray) -> np.ndarray:
        s = self.settings
        re = np.maximum(_as_array(reynolds, t_over_c), s.re_low_limit)
        f_re = np.clip((re / s.re_reference) ** 0.15, 0.68, 1.03)
        f_thick = np.clip(1.0 + 0.9 * (t_over_c - 0.12), 0.82, 1.12)
        f_mach = np.clip(1.0 - 0.35 * np.maximum(mach - 0.55, 0.0) ** 1.5, 0.70, 1.0)
        return s.cl_max_ref * f_re * f_thick * f_mach

    def lift_curve_slope(self, mach: np.ndarray) -> np.ndarray:
        """Prandtl-Glauert corrected lift-curve slope per radian."""
        s = self.settings
        beta = np.sqrt(np.maximum(1.0 - np.clip(mach, 0.0, 0.95) ** 2, 0.16))
        return CL_SLOPE_IDEAL * s.slope_efficiency / beta

    def friction_drag(self, reynolds: np.ndarray, t_over_c: np.ndarray) -> np.ndarray:
        s = self.settings
        re = np.maximum(_as_array(reynolds, t_over_c), s.re_low_limit)
        cf = 0.075 / np.maximum(np.log10(re) - 2.0, 0.6) ** 2
        ff = 1.0 + s.cd_form_factor_slope * t_over_c
        return s.profile_drag_scale * 2.0 * cf * ff

    def trailing_edge_drag(self, te_over_c: np.ndarray, t_over_c: np.ndarray) -> np.ndarray:
        """Leading-order base drag of a flat/blunt trailing edge: ``cd ~ (t_TE/c) (1 + 0.4 t/c)``.

        For a symmetric wedge the base-pressure integral reduces to ``cd ≈ 2 sin(theta/2) ≈ theta``,
        and the wedge angle of a blunt TE of thickness ``t_TE`` over chord ``c`` is ``t_TE/c``; hence a
        slope of 1.0 with a mild interaction with aft thickness.
        """
        s = self.settings
        return s.te_drag_slope * te_over_c * (1.0 + s.te_thickness_interaction * t_over_c)

    def wave_drag(self, mach: np.ndarray, cl: np.ndarray, t_over_c: np.ndarray) -> np.ndarray:
        s = self.settings
        m_crit = s.mach_critical_base - s.mach_thickness_penalty * t_over_c - s.mach_lift_penalty * np.abs(cl)
        excess = np.maximum(mach - m_crit, 0.0)
        return np.minimum(s.wave_drag_factor * excess**2, s.wave_drag_cap)

    def stall_drag(self, cl: np.ndarray, cl_max: np.ndarray) -> np.ndarray:
        s = self.settings
        onset = s.stall_onset_fraction * cl_max
        ratio = np.maximum(np.abs(cl) - onset, 0.0) / np.maximum(0.35 * cl_max, 1e-6)
        return s.stall_drag_factor * ratio**2

    def __call__(
        self,
        alpha_deg: np.ndarray | float,
        reynolds: np.ndarray | float,
        mach: np.ndarray | float,
        t_over_c: np.ndarray | float,
        te_over_c: np.ndarray | float,
        camber: np.ndarray | float,
    ) -> PolarPoint:
        s = self.settings
        base = _as_array(camber)
        a = _as_array(alpha_deg, base)
        re = _as_array(reynolds, base)
        m = _as_array(mach, base)
        tc = _as_array(t_over_c, base)
        tec = _as_array(te_over_c, base)

        a0 = self.zero_lift_angle_deg(base)
        slope = self.lift_curve_slope(m)
        cl_lin = slope * np.radians(a - a0)
        cl_peak = self.cl_max(re, tc, m)
        cl = cl_peak * np.tanh(cl_lin / np.maximum(cl_peak, 1e-6))
        cd = (
            self.friction_drag(re, tc)
            + self.trailing_edge_drag(tec, tc)
            + self.wave_drag(m, cl, tc)
            + self.stall_drag(cl, cl_peak)
        )
        cm = np.full_like(a, s.cm_base) - s.cm_camber_factor * base
        return PolarPoint(alpha_deg=a, cl=cl, cd=cd, cm=cm)


class LinearPolar(PolarSource):
    """Inviscid or constant-drag polar used to verify the momentum-theory limit (T045)."""

    def __init__(
        self, cd: float = 0.0, slope: float = CL_SLOPE_IDEAL, camber_slope_deg: float = 22.0
    ) -> None:
        self.cd = cd
        self.slope = slope
        self.camber_slope_deg = camber_slope_deg

    def zero_lift_angle_deg(self, camber: np.ndarray | float) -> np.ndarray:
        return -self.camber_slope_deg * _as_array(camber)

    def __call__(
        self,
        alpha_deg: np.ndarray | float,
        reynolds: np.ndarray | float,
        mach: np.ndarray | float,
        t_over_c: np.ndarray | float,
        te_over_c: np.ndarray | float,
        camber: np.ndarray | float,
    ) -> PolarPoint:
        base = _as_array(camber)
        a = _as_array(alpha_deg, base)
        a0 = self.zero_lift_angle_deg(base)
        cl = self.slope * np.radians(a - a0)
        return PolarPoint(alpha_deg=a, cl=cl, cd=np.full_like(a, self.cd), cm=np.zeros_like(a))

    @property
    def settings(self) -> PolarSettings:  # pragma: no cover - interface parity
        return PolarSettings()


@dataclass
class PolarTable:
    """Cached polar samples for one station (T083), keyed by (mach, reynolds) bucket."""

    alpha_deg: np.ndarray
    cl: np.ndarray
    cd: np.ndarray
    cm: np.ndarray
    meta: dict[str, float] = field(default_factory=dict)

    def sample(self, alpha_deg: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        return (
            np.interp(alpha_deg, self.alpha_deg, self.cl),
            np.interp(alpha_deg, self.alpha_deg, self.cd),
            np.interp(alpha_deg, self.alpha_deg, self.cm),
        )


def sweep_polar(
    polar: PolarSource,
    *,
    alpha_min: float = -12.0,
    alpha_max: float = 20.0,
    step: float = 0.5,
    reynolds: float = 5.0e5,
    mach: float = 0.35,
    t_over_c: float = 0.12,
    te_over_c: float = 0.01,
    camber: float = 0.03,
) -> PolarTable:
    """Discretise a polar source into a table, used for the ``polars/`` outputs."""
    alpha = np.arange(alpha_min, alpha_max + 1e-9, step)
    point = polar(alpha, reynolds, mach, t_over_c, te_over_c, camber)
    return PolarTable(
        alpha_deg=alpha,
        cl=np.asarray(point.cl),
        cd=np.asarray(point.cd),
        cm=np.asarray(point.cm),
        meta={
            "reynolds": reynolds,
            "mach": mach,
            "t_over_c": t_over_c,
            "te_over_c": te_over_c,
            "camber": camber,
        },
    )


__all__ = [
    "CL_SLOPE_IDEAL",
    "AnalyticPolar",
    "LinearPolar",
    "PolarPoint",
    "PolarSettings",
    "PolarSource",
    "PolarTable",
    "sweep_polar",
]
