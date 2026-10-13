"""Unit handling and numeric guards (T013).

SI is the internal system everywhere in the package.  This module only converts *on the
way in*, so a user can supply millimetres or RPM without any unit bug reaching geometry.
"""

from __future__ import annotations

import math

SI_LENGTH_UNITS: dict[str, float] = {"m": 1.0, "mm": 1e-3, "cm": 1e-2, "in": 0.0254, "ft": 0.3048}
FORCE_UNITS: dict[str, float] = {"n": 1.0, "kn": 1000.0, "lbf": 4.4482216152605, "kgf": 9.80665}
ANGULAR_SPEED_UNITS: dict[str, float] = {"rad/s": 1.0, "rpm": math.pi / 30.0}


class UnitError(ValueError):
    """Raised for an unknown unit string."""


def to_metres(value: float, unit: str) -> float:
    key = unit.strip().lower()
    if key not in SI_LENGTH_UNITS:
        raise UnitError(f"unknown length unit '{unit}', expected one of {sorted(SI_LENGTH_UNITS)}")
    return value * SI_LENGTH_UNITS[key]


def to_newtons(value: float, unit: str) -> float:
    key = unit.strip().lower()
    if key not in FORCE_UNITS:
        raise UnitError(f"unknown force unit '{unit}', expected one of {sorted(FORCE_UNITS)}")
    return value * FORCE_UNITS[key]


def to_rad_per_sec(value: float, unit: str) -> float:
    key = unit.strip().lower()
    if key not in ANGULAR_SPEED_UNITS:
        raise UnitError(f"unknown angular speed unit '{unit}', expected one of {sorted(ANGULAR_SPEED_UNITS)}")
    return value * ANGULAR_SPEED_UNITS[key]


def rpm_to_omega(rpm: float) -> float:
    return rpm * math.pi / 30.0


def omega_to_rpm(omega: float) -> float:
    return omega * 30.0 / math.pi


def finite_positive(name: str, value: float) -> float:
    """Guard that a scalar is a finite, strictly positive number."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{name} must be a number, got {type(value).__name__}")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value}")
    if value <= 0.0:
        raise ValueError(f"{name} must be strictly positive, got {value}")
    return float(value)
