"""Hover figure of merit and momentum-theory primitives (T034, T035)."""

from __future__ import annotations

import math


def ideal_induced_velocity(thrust_n: float, rho: float, disk_area: float) -> float:
    """Momentum-theory induced velocity in hover, ``v_i = sqrt(T / (2 rho A))``."""
    return math.sqrt(thrust_n / (2.0 * rho * disk_area))


def ideal_power(thrust_n: float, rho: float, disk_area: float) -> float:
    """Ideal (momentum) hover power ``P_ideal = T^1.5 / sqrt(2 rho A)``."""
    return thrust_n**1.5 / math.sqrt(2.0 * rho * disk_area)


def thrust_coefficient(thrust_n: float, rho: float, disk_area: float, tip_speed: float) -> float:
    return thrust_n / (rho * disk_area * tip_speed**2)


def power_coefficient(power_w: float, rho: float, area: float, omega: float, radius: float) -> float:
    """Rotor power coefficient ``C_P = P / (rho A (Omega R)^3)`` (``A = pi R^2``)."""
    return power_w / (rho * area * (omega * radius) ** 3)


def figure_of_merit(power_ideal_w: float, power_actual_w: float) -> float:
    """``FM = P_ideal / P_actual``; 0 when either side is not physical, capped at 1 by definition."""
    if power_actual_w <= 0.0 or power_ideal_w <= 0.0 or not math.isfinite(power_actual_w):
        return 0.0
    return power_ideal_w / power_actual_w


def inflow_angle_deg(axial: float, tangential: float) -> float:
    if abs(tangential) < 1e-12:
        return math.copysign(90.0, axial)
    return math.degrees(math.atan2(axial, tangential))


__all__ = [
    "figure_of_merit",
    "ideal_induced_velocity",
    "ideal_power",
    "inflow_angle_deg",
    "power_coefficient",
    "thrust_coefficient",
]
