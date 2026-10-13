"""Automatic design-parameter inference (T014, T015, T016, T018).

The user supplies only thrust, radius and trailing-edge thickness, so everything else has
to be *derived and explained*.  Each helper here returns the value plus a human-readable
reason string, and every reason is collected into ``config.provenance_reasons`` so a run
can be audited later (plan section 3.2).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

RHO_SL = 1.225  # kg/m^3, ISA sea level
MU_SL = 1.81e-5  # Pa.s
A_SOUND_SL = 340.29  # m/s
GRAVITY = 9.80665


@dataclass(slots=True)
class AutoValue:
    """An inferred quantity together with the reasoning that produced it."""

    value: float | int
    reason: str


def atmosphere(altitude_m: float = 0.0) -> tuple[float, float, float]:
    """ISA density, dynamic viscosity and sound speed at ``altitude_m``.

    A simple ISA layer model is used up to 11 km: it keeps the default sea-level values
    exactly when altitude is 0 while still supporting the advanced override.
    """
    if altitude_m <= 11000.0:
        t = 288.15 - 0.0065 * altitude_m
        p = 101325.0 * (t / 288.15) ** (GRAVITY * 0.0289644 / (8.3144598 * 0.0065))
        rho = p / (287.052 * t)
        a = math.sqrt(1.4 * 287.052 * t)
        # Sutherland's law
        mu = 1.458e-6 * t**1.5 / (t + 110.4)
        return float(rho), float(mu), float(a)
    rho = 0.0
    return rho, MU_SL, A_SOUND_SL


def auto_blade_count(
    radius_m: float, thrust_n: float, rho: float, candidates: tuple[int, ...] = (2, 3, 4)
) -> AutoValue:
    """Pick the blade count from radius and disk loading (T015).

    Physical reasoning: a small, lightly loaded rotor is best served by few blades (less
    profile power, cleaner wake), while a large or heavily disk-loaded machine needs more
    blades so the per-blade loading and the required chord stay structurally sane.  The
    bands below are the documented default policy; ``blade_count`` can always be overridden.
    """
    disk_area = math.pi * radius_m**2
    disk_loading = thrust_n / disk_area
    if radius_m <= 0.5:
        pick = 2
    elif radius_m <= 1.2:
        pick = 2 if disk_loading < 120.0 else 3
    elif radius_m <= 2.5:
        pick = 3 if disk_loading < 220.0 else 4
    else:
        pick = 4
    pick = int(min(max(pick, min(candidates)), max(candidates)))
    reason = (
        f"radius {radius_m:.2f} m, disk loading {disk_loading:.1f} N/m^2, "
        f"thrust per blade {thrust_n / pick:.0f} N -> {pick} blade(s) (policy bands 0.5/1.2/2.5 m "
        f"and 120/220 N/m^2, candidates {candidates})"
    )
    return AutoValue(pick, reason)


def auto_rotor_speed(
    radius_m: float,
    thrust_n: float,
    blade_count: int,
    rho: float,
    a_sound: float,
    tip_mach_limit: float = 0.65,
    tip_speed_max: float = 235.0,
    ct_min: float = 0.005,
) -> AutoValue:
    """Rotor speed from the tip-Mach limit, retuned so hover thrust coefficient is sane (T016).

    Start from the acoustic/compressibility cap ``V_tip = min(M_limit*a, tip_speed_max)``.
    If that leaves the rotor so lightly loaded (CT too small) that it would need a tiny
    chord or fly at very low incidence, slow it down until CT reaches ``ct_min``; hover
    figure of merit is best at moderate-to-high CT for a fixed profile-power level.
    """
    disk_area = math.pi * radius_m**2
    v_tip = min(tip_mach_limit * a_sound, tip_speed_max)
    omega = v_tip / radius_m
    ct = thrust_n / (rho * disk_area * v_tip**2)
    adjusted = False
    if ct < ct_min:
        scale = math.sqrt(ct / ct_min)
        v_tip_new = v_tip * scale
        omega = v_tip_new / radius_m
        ct = thrust_n / (rho * disk_area * v_tip_new**2)
        adjusted = True
    rpm = omega * 30.0 / math.pi
    tip_mach = omega * radius_m / a_sound
    reason = (
        f"initial tip speed {min(tip_mach_limit * a_sound, tip_speed_max):.1f} m/s "
        f"(M_limit={tip_mach_limit:g}) -> CT={thrust_n / (rho * disk_area * min(tip_mach_limit * a_sound, tip_speed_max) ** 2):.5f}; "
        + (f"lowered speed to reach CT>={ct_min:.3f} -> " if adjusted else "CT already within hover band -> ")
        + f"Omega={omega:.2f} rad/s, {rpm:.1f} RPM, tip Mach {tip_mach:.3f} (limit {tip_mach_limit:g})"
    )
    return AutoValue(omega, reason)


def solidity_estimate(radius_m: float, blade_count: int, chord_scale: float = 0.09) -> float:
    """Reference solidity ``sigma = N * c_ref / (pi * R)`` for the initial blade."""
    _ = radius_m
    return blade_count * chord_scale / math.pi


def feasibility_warnings(
    radius_m: float,
    thrust_n: float,
    te_min_m: float,
    rho: float,
    a_sound: float,
    blade_count: int,
    omega: float,
) -> list[str]:
    """Warn (never fail) when the three inputs push the design outside a credible envelope (T018)."""
    out: list[str] = []
    disk_area = math.pi * radius_m**2
    disk_loading = thrust_n / disk_area
    if disk_loading > 900.0:
        out.append(
            f"disk loading {disk_loading:.0f} N/m^2 exceeds the ~900 N/m^2 hover-design "
            "comfort band; expect low figure of merit or an infeasible thrust match"
        )
    if disk_loading < 5.0:
        out.append(
            f"disk loading {disk_loading:.1f} N/m^2 is very light; the rotor may be oversized for the thrust"
        )
    v_tip = omega * radius_m
    ct = thrust_n / (rho * disk_area * v_tip**2)
    if ct > 0.03:
        out.append(
            f"thrust coefficient CT={ct:.4f} is above the practical hover ceiling (~0.03); sections will stall"
        )
    if ct < 0.0015:
        out.append(
            f"thrust coefficient CT={ct:.4f} is extremely low; chords will collapse toward the structural minimum"
        )
    # trailing edge must remain expressible: t_te/c between 0.2% and 8% (plan section 5.4)
    c_ref = 0.09 * radius_m
    te_ratio = te_min_m / c_ref
    if te_ratio > 0.08:
        out.append(
            f"required trailing-edge thickness {te_min_m * 1000:.2f} mm is {100 * te_ratio:.2f}% of the "
            f"reference chord {c_ref * 1000:.0f} mm (> 8%): the blunt-TE constraint will dominate the design"
        )
    if te_ratio < 0.0005:
        out.append(
            "required trailing-edge thickness is below 0.05% of chord; numerical TE reconstruction may be "
            "ill-conditioned, consider a larger value for manufacturability"
        )
    power_ideal = thrust_n**1.5 / math.sqrt(2.0 * rho * disk_area)
    mass_equiv = thrust_n / GRAVITY
    out_hint = f"ideal (momentum) power {power_ideal / 1000:.2f} kW for {mass_equiv:.1f} kg equivalent mass"
    _ = out_hint
    return out
