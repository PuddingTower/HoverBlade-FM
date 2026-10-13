"""Hover blade-element-momentum solver (T036, T037, T038, T044).

Only hover is modelled: ``forward_speed = 0``, ``climb_rate = 0``.  The solver integrates the
classical Glauert momentum/blade-element balance over annuli with a double (tip and root)
Prandtl loss factor, and iterates the induced velocities to convergence.  Section lift and
drag come from a replaceable :class:`~hoverblade_fm.aero.polar.PolarSource`; the lifting-induced
term is deliberately excluded from the section drag because the induced power is carried by
the momentum balance - counting it in both places would double-penalise the blade.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.interpolate import PchipInterpolator

from hoverblade_fm.aero.fm import figure_of_merit, ideal_induced_velocity, ideal_power
from hoverblade_fm.aero.polar import AnalyticPolar, PolarSource
from hoverblade_fm.config.schema import ResolvedConfig
from hoverblade_fm.models import Design, RadialLoad


@dataclass
class BemtSettings:
    tip_loss: bool = True
    root_loss: bool = True
    allow_reverse_flow: bool = True


@dataclass
class BemtResult:
    """Converged hover solution for one blade design."""

    thrust_n: float
    torque_nm: float
    power_total_w: float
    power_ideal_w: float
    power_induced_w: float
    power_profile_w: float
    power_swirl_w: float
    power_misc_w: float
    figure_of_merit: float
    iterations: int
    residual: float
    converged: bool
    radial: RadialLoad
    collective_trim_deg: float = 0.0
    tip_loss_factor: float = 1.0

    @property
    def power_actual_w(self) -> float:
        return self.power_total_w


class SpanInterp:
    """Continuous spanwise description of the blade built from the ten stations."""

    def __init__(self, design: Design, cfg: ResolvedConfig, collective_trim_deg: float = 0.0) -> None:
        r = np.asarray([s.r_R for s in design.sections], dtype=float)
        self.r = r
        self.chord = np.asarray([s.chord_m for s in design.sections], dtype=float)
        self.twist = np.asarray([s.twist_deg for s in design.sections], dtype=float) + collective_trim_deg
        self.tc = np.asarray([s.thickness_to_chord for s in design.sections], dtype=float)
        self.te_c = np.asarray(
            [s.te_thickness_m / max(s.chord_m, 1e-9) for s in design.sections], dtype=float
        )
        self.camber = np.asarray([s.camber_m / max(s.chord_m, 1e-9) for s in design.sections], dtype=float)
        self._fns = {
            "chord": PchipInterpolator(r, self.chord, extrapolate=True),
            "twist": PchipInterpolator(r, self.twist, extrapolate=True),
            "tc": PchipInterpolator(r, self.tc, extrapolate=True),
            "te_c": PchipInterpolator(r, self.te_c, extrapolate=True),
            "camber": PchipInterpolator(r, self.camber, extrapolate=True),
        }

    def __call__(self, key: str, x: np.ndarray) -> np.ndarray:
        fn = self._fns[key]
        return np.asarray(fn(np.clip(x, self.r[0], self.r[-1])), dtype=float)

    def at(self, x: np.ndarray) -> dict[str, np.ndarray]:
        return {key: self(key, x) for key in self._fns}


def prandtl_loss(
    x: np.ndarray,
    sin_phi: np.ndarray,
    blade_count: int,
    settings: BemtSettings,
) -> np.ndarray:
    """Tip and root loss factors, multiplied together (double-Prandtl approximation)."""
    s = np.maximum(np.abs(sin_phi), 1e-6)
    factor = np.ones_like(x)
    if settings.tip_loss:
        mu_tip = 0.5 * blade_count * (1.0 - x) / (np.maximum(x, 1e-6) * s)
        factor = factor * (2.0 / math.pi) * np.arccos(np.clip(np.exp(-mu_tip), 0.0, 1.0))
    if settings.root_loss:
        mu_root = 0.5 * blade_count * x / (np.maximum(1.0 - x, 1e-6) * s)
        root = (2.0 / math.pi) * np.arccos(np.clip(np.exp(-mu_root), 0.0, 1.0))
        factor = factor * np.where(x < 0.35, root, 1.0)
    return np.clip(factor, 1e-6, 1.0)


def solve_hover(
    design: Design,
    cfg: ResolvedConfig,
    *,
    polar: PolarSource | None = None,
    collective_trim_deg: float = 0.0,
    settings: BemtSettings | None = None,
) -> BemtResult:
    """Iterate momentum and blade element until the induced velocities converge."""
    polar = polar or AnalyticPolar()
    settings = settings or BemtSettings()
    rho, mu, a_sound = cfg.rho, cfg.mu, cfg.a_sound
    omega, radius, blades = design.omega, design.radius_m, design.blade_count
    interp = SpanInterp(design, cfg, collective_trim_deg)

    n = cfg.solver.n_radial
    x = np.linspace(cfg.solver.root_extension_r, 1.0, n)
    x[0] = max(x[0], 1e-3)
    r = x * radius
    span = interp.at(x)
    chord = span["chord"]
    twist = np.radians(span["twist"])
    tc = span["tc"]
    te_c = span["te_c"]
    camber = span["camber"]

    v_i = ideal_induced_velocity(cfg.thrust_n, rho, cfg.disk_area)
    v = np.full(n, v_i * 1.05)
    u = np.full(n, v_i * 0.05)
    residual = math.inf
    iterations = 0
    cl = np.zeros(n)
    cd = np.zeros(n)
    phi = np.zeros(n)
    v_rel = np.zeros(n)
    mach = np.zeros(n)
    reynolds = np.zeros(n)
    dT_dr = np.zeros(n)
    dQ_dr = np.zeros(n)

    relax = cfg.solver.relaxation
    loss = np.ones(n)
    for _pass in range(1, cfg.solver.n_itmax + 1):
        tangential = omega * r + u
        tangential = np.where(np.abs(tangential) < 1e-9, 1e-9, tangential)
        phi = np.arctan2(v, tangential)
        v_rel = np.hypot(tangential, v)
        mach = v_rel / a_sound
        reynolds = rho * v_rel * chord / mu
        alpha = np.degrees(twist - phi) - _zero_lift_deg(polar, camber)
        point = polar(alpha, reynolds, mach, tc, te_c, camber)
        cl, cd = np.asarray(point.cl), np.asarray(point.cd)

        # Prandtl's loss factor reduces the *circulation* (blade element) near the tip and
        # root; the annular momentum balance below then needs no extra correction.
        loss = prandtl_loss(x, np.sin(phi), blades, settings)
        dT_dr = loss * 0.5 * rho * blades * chord * v_rel**2 * (cl * np.cos(phi) - cd * np.sin(phi))
        dQ_dr = loss * 0.5 * rho * blades * chord * v_rel**2 * (cl * np.sin(phi) + cd * np.cos(phi)) * r

        # Annular momentum in hover (no through-flow except the induced velocity):
        #   dT/dr = 4 pi rho v^2 r      (axial jump, far-wake velocity = 2v)
        #   dQ/dr = 4 pi rho v u r^2    (angular momentum, wake swirl = 2u)
        # Using (Omega r + u) as the mass-flux velocity here would inflate the flux by
        # Omega r / v ~ 20 and is the classic way to fake a figure of merit of 0.97.
        denom_v = 4.0 * math.pi * rho * np.maximum(r, 1e-6)
        denom_u = 4.0 * math.pi * rho * np.maximum(v * r**2, 1e-9)
        v_target = np.clip(np.sqrt(np.maximum(dT_dr, 0.0) / denom_v), 0.0, 0.9 * omega * r + 5.0)
        u_target = np.clip(dQ_dr / denom_u, -0.5 * omega * r, 0.9 * omega * r)

        v_new = (1.0 - relax) * v + relax * v_target
        u_new = (1.0 - relax) * u + relax * u_target
        residual = float(np.max(np.abs(v_new - v)) + np.max(np.abs(u_new - u)))
        v, u = v_new, u_new
        iterations = _pass
        if residual < cfg.solver.tolerance:
            break

    thrust = float(np.sum(0.5 * (dT_dr[1:] + dT_dr[:-1]) * np.diff(r)))
    torque = float(np.sum(0.5 * (dQ_dr[1:] + dQ_dr[:-1]) * np.diff(r)))
    power_total = torque * omega
    # P_ideal is reported for the required thrust (the plan's definition); FM uses the
    # achieved thrust so that the ratio stays physically <= 1 even when the trim overshoots.
    p_ideal = ideal_power(cfg.thrust_n, rho, cfg.disk_area)
    p_ideal_achieved = ideal_power(thrust, rho, cfg.disk_area) if thrust > 0 else 0.0

    # Parasitic (profile + TE + wave + stall) drag power, and the wake share by difference.
    profile_local = 0.5 * rho * blades * chord * v_rel**3 * cd
    power_profile = float(np.sum(0.5 * (profile_local[1:] + profile_local[:-1]) * np.diff(r)))
    power_profile = min(power_profile, power_total)
    power_induced = float(np.sum(0.5 * (v[1:] * dT_dr[1:] + v[:-1] * dT_dr[:-1]) * np.diff(r)))
    power_induced = min(max(power_induced, 0.0), max(power_total - power_profile, 0.0))
    power_swirl = max(power_total - power_profile - power_induced, 0.0)
    power_misc = cfg.solver.misc_power_w
    power_actual = power_profile + power_induced + power_swirl + power_misc

    radial = RadialLoad(
        r_R=x,
        thrust_gradient=dT_dr,
        torque_gradient=dQ_dr,
        power_gradient=dQ_dr * omega,
        induced_velocity=v,
        inflow_angle_deg=np.degrees(phi),
        aoa_deg=np.degrees(twist - phi),
        cl=cl,
        cd=cd,
        mach=mach,
        reynolds=reynolds,
        loss_factor=loss,
    )
    return BemtResult(
        thrust_n=thrust,
        torque_nm=torque,
        power_total_w=power_total,
        power_ideal_w=p_ideal,
        power_induced_w=power_induced,
        power_profile_w=power_profile,
        power_swirl_w=power_swirl,
        power_misc_w=power_misc,
        figure_of_merit=figure_of_merit(
            p_ideal_achieved, power_actual if power_actual > 0.0 else power_total
        ),
        iterations=iterations,
        residual=residual,
        converged=residual < max(cfg.solver.tolerance * 50.0, 1e-5),
        radial=radial,
        collective_trim_deg=collective_trim_deg,
        tip_loss_factor=float(loss[-3:].mean()),
    )


def _zero_lift_deg(polar: PolarSource, camber: np.ndarray) -> np.ndarray:
    """Zero-lift angle used to form the effective angle of attack, from the polar itself."""
    getter = getattr(polar, "zero_lift_angle_deg", None)
    if callable(getter):
        return np.asarray(getter(camber), dtype=float)
    return np.zeros_like(np.asarray(camber, dtype=float))


def trim_collective(
    design: Design,
    cfg: ResolvedConfig,
    *,
    polar: PolarSource | None = None,
    settings: BemtSettings | None = None,
    search: tuple[float, float] = (-10.0, 18.0),
    tolerance: float | None = None,
) -> tuple[float, BemtResult]:
    """Find the collective (parallel twist shift) that makes ``T = T_target`` (T044).

    Hover thrust control is a collective input, so the optimiser must compare designs *at
    matched thrust*; otherwise it would simply spin the rotor faster.  A bracketing secant
    with bisection fallback is used because thrust is monotone in collective.
    """
    tol = tolerance or cfg.solver.thrust_tolerance
    lo, hi = search

    def thrust_at(delta: float) -> float:
        return solve_hover(design, cfg, polar=polar, collective_trim_deg=delta, settings=settings).thrust_n

    t_lo, t_hi = thrust_at(lo), thrust_at(hi)
    target = cfg.thrust_n
    if t_lo > target or t_hi < target:
        # no collective in the search band can reach the target: pick the closest end
        delta = lo if abs(t_lo - target) < abs(t_hi - target) else hi
        return delta, solve_hover(design, cfg, polar=polar, collective_trim_deg=delta, settings=settings)

    delta = 0.0
    result = solve_hover(design, cfg, polar=polar, collective_trim_deg=delta, settings=settings)
    for _ in range(40):
        if abs(result.thrust_n - target) / target <= tol:
            return delta, result
        if result.thrust_n < target:
            lo, t_lo = delta, result.thrust_n
        else:
            hi, t_hi = delta, result.thrust_n
        try:
            candidate = delta + (target - result.thrust_n) * (hi - lo) / max(abs(t_hi - t_lo), 1e-9)
        except ZeroDivisionError:
            candidate = 0.5 * (lo + hi)
        if not (lo < candidate < hi) or not math.isfinite(candidate):
            candidate = 0.5 * (lo + hi)
        delta = candidate
        result = solve_hover(design, cfg, polar=polar, collective_trim_deg=delta, settings=settings)
    return delta, result
