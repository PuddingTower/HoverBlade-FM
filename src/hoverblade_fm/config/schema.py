"""Input schema, validation and resolved design configuration (T011, T012, T013, T014).

``UserInput`` is intentionally tiny: three numbers.  ``ResolvedConfig`` is everything the
solver needs after the automatic strategies in :mod:`hoverblade_fm.config.defaults` have
filled in the gaps.  Advanced overrides are accepted but never required.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from hoverblade_fm.config.defaults import (
    A_SOUND_SL,
    MU_SL,
    AutoValue,
    atmosphere,
    auto_blade_count,
    auto_rotor_speed,
    feasibility_warnings,
    solidity_estimate,
)
from hoverblade_fm.config.units import omega_to_rpm, rpm_to_omega, to_metres, to_newtons
from hoverblade_fm.models import DEFAULT_R_STATIONS, SECTION_COUNT

#: Non-dimensional chord bounds as a fraction of radius (plan section 11.1).
CHORD_MIN_OVER_R = 0.030
CHORD_MAX_OVER_R = 0.30
#: Geometric twist bounds in degrees.
TWIST_MIN_DEG = -4.0
TWIST_MAX_DEG = 28.0


class InputValidationError(ValueError):
    """Raised when the three user inputs (or an override) are not physically usable."""


class UnitOverrides(BaseModel):
    """Optional unit system for the three raw inputs.  SI by default."""

    model_config = ConfigDict(extra="forbid")

    thrust: str = "N"
    length: str = "m"


class AirfoilBounds(BaseModel):
    """Section-level shape limits that keep a design manufacturable (plan sections 5.4, 9.4)."""

    model_config = ConfigDict(extra="forbid")

    thickness_to_chord_min: float = Field(default=0.06, gt=0.0, lt=0.5)
    thickness_to_chord_max: float = Field(default=0.24, gt=0.0, lt=0.6)
    te_over_chord_min: float = Field(default=0.002, gt=0.0)
    te_over_chord_max: float = Field(default=0.08, lt=0.5)
    te_included_angle_min_deg: float = Field(default=6.0, gt=0.0)
    te_included_angle_max_deg: float = Field(default=45.0, lt=180.0)
    camber_max_over_chord: float = Field(default=0.07, gt=0.0, lt=0.2)
    le_radius_min_over_chord: float = Field(default=0.0015, gt=0.0)
    upper_cst_order: int = Field(default=5, ge=2, le=8)
    lower_cst_order: int = Field(default=5, ge=2, le=8)


class SolverSettings(BaseModel):
    """Numerical settings of the hover BEMT solver."""

    model_config = ConfigDict(extra="forbid")

    n_radial: int = Field(default=161, ge=41, le=1001)
    n_points_per_surface: int = Field(default=80, ge=24, le=400)
    n_itmax: int = Field(default=200, ge=10, le=2000)
    tolerance: float = Field(default=1e-7, gt=0.0, lt=1e-2)
    relaxation: float = Field(default=0.35, gt=0.0, lt=1.0)
    root_extension_r: float = Field(default=0.04, ge=0.0, lt=0.10, description="inner integration limit")
    thrust_tolerance: float = Field(default=0.005, gt=0.0, lt=0.1)
    swirl_recovery: float = Field(
        default=0.0, ge=0.0, le=1.0, description="fraction of swirl kinetic energy counted as loss"
    )
    misc_power_w: float = Field(default=0.0, ge=0.0, description="P_misc interface, 0 in MVP")


class OptimizationSettings(BaseModel):
    """Optimiser configuration (plan sections 11.2, 11.3)."""

    model_config = ConfigDict(extra="forbid")

    preset: str = Field(default="standard")
    seed: int = 0
    popsize: int = Field(default=64, ge=8, le=512)
    max_evaluations: int = Field(default=3000, ge=40, le=200_000)
    local_refine: bool = True
    local_max_iterations: int = Field(default=200, ge=0, le=2000)
    restarts: int = Field(default=1, ge=1, le=12)
    lambda_thrust: float = Field(default=100.0, ge=0.0)
    lambda_te: float = Field(default=1000.0, ge=0.0)
    lambda_smooth: float = Field(default=1.0, ge=0.0)
    lambda_geometry: float = Field(default=1000.0, ge=0.0)
    adaptive_penalty: bool = True

    @model_validator(mode="after")
    def _apply_preset(self) -> OptimizationSettings:
        presets = {
            "fast": {"popsize": 24, "max_evaluations": 320, "local_refine": False, "restarts": 1},
            "standard": {"popsize": 48, "max_evaluations": 1500, "local_refine": True, "restarts": 1},
            "full": {"popsize": 64, "max_evaluations": 3000, "local_refine": True, "restarts": 3},
            "custom": {},
        }
        if self.preset not in presets:
            raise ValueError(f"unknown optimisation preset '{self.preset}', expected {sorted(presets)}")
        for key, value in presets[self.preset].items():
            setattr(self, key, value)
        return self


class UserInput(BaseModel):
    """The three required numbers, plus optional advanced overrides (plan section 20.1)."""

    model_config = ConfigDict(extra="forbid")

    thrust_n: float | None = Field(default=None, gt=0)
    radius_m: float | None = Field(default=None, gt=0)
    trailing_edge_thickness_m: float | None = Field(default=None, gt=0)

    # -- optional raw-unit variants -----------------------------------------------------
    thrust: float | None = None
    radius: float | None = None
    trailing_edge_thickness: float | None = None
    units: UnitOverrides = Field(default_factory=UnitOverrides)

    # -- advanced overrides --------------------------------------------------------------
    air_density_kg_m3: float | None = Field(default=None, gt=0)
    dynamic_viscosity_pa_s: float | None = Field(default=None, gt=0)
    sound_speed_m_s: float | None = Field(default=None, gt=0)
    altitude_m: float | None = Field(default=None, ge=0, le=11000)
    blade_count: int | None = Field(default=None, ge=1, le=8)
    rpm: float | None = Field(default=None, gt=0)
    tip_mach_limit: float | None = Field(default=None, gt=0.1, le=0.95)
    r_stations: list[float] | None = None
    airfoil: AirfoilBounds = Field(default_factory=AirfoilBounds)
    solver: SolverSettings = Field(default_factory=SolverSettings)
    optimization: OptimizationSettings = Field(default_factory=OptimizationSettings)

    @model_validator(mode="after")
    def _require_three(self) -> UserInput:
        has_si = (
            self.thrust_n is not None
            and self.radius_m is not None
            and self.trailing_edge_thickness_m is not None
        )
        has_raw = (
            self.thrust is not None and self.radius is not None and self.trailing_edge_thickness is not None
        )
        if not (has_si or has_raw):
            raise ValueError(
                "required inputs are thrust_n [N], radius_m [m] and trailing_edge_thickness_m [m] "
                "(or the unit-aware trio thrust/radius/trailing_edge_thickness plus units)"
            )
        return self

    # -- loading -----------------------------------------------------------
    @classmethod
    def from_json(cls, path: str | Path) -> UserInput:
        text = Path(path).read_text(encoding="utf-8")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InputValidationError(f"{path}: invalid JSON ({exc})") from exc
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserInput:
        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            raise InputValidationError(_pretty_validation(exc, source=None)) from exc

    def input_hash(self) -> str:
        payload = json.dumps(self.raw_values(), sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]

    def raw_values(self) -> dict[str, Any]:
        return {
            "thrust_n": self.thrust_n if self.thrust_n is not None else self.thrust,
            "radius_m": self.radius_m if self.radius_m is not None else self.radius,
            "trailing_edge_thickness_m": (
                self.trailing_edge_thickness_m
                if self.trailing_edge_thickness_m is not None
                else self.trailing_edge_thickness
            ),
            "units": self.units.model_dump(),
        }


def _pretty_validation(exc: ValidationError, source: str | None) -> str:
    lines = [f"invalid input{f' in {source}' if source else ''}:"]
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        lines.append(f"  - {loc}: {err['msg']}")
    return "\n".join(lines)


@dataclass(slots=True)
class ResolvedConfig:
    """Fully specified, SI-unit design configuration handed to every downstream module."""

    thrust_n: float
    radius_m: float
    te_min_m: float
    rho: float
    mu: float
    a_sound: float
    blade_count: int
    omega: float
    rpm: float
    tip_mach: float
    tip_mach_limit: float
    r_stations: list[float]
    solidity_ref: float
    airfoil: AirfoilBounds
    solver: SolverSettings
    optimization: OptimizationSettings
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    input_hash: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    # -- derived quantities --------------------------------------------------------------
    @property
    def disk_area(self) -> float:
        return math.pi * self.radius_m**2

    @property
    def tip_speed(self) -> float:
        return self.omega * self.radius_m

    @property
    def chord_min_m(self) -> float:
        return CHORD_MIN_OVER_R * self.radius_m

    @property
    def chord_max_m(self) -> float:
        return CHORD_MAX_OVER_R * self.radius_m

    @property
    def power_ideal_w(self) -> float:
        return self.thrust_n**1.5 / math.sqrt(2.0 * self.rho * self.disk_area)

    def to_dict(self) -> dict[str, Any]:
        return {
            "thrust_n": self.thrust_n,
            "radius_m": self.radius_m,
            "trailing_edge_thickness_m": self.te_min_m,
            "air_density_kg_m3": self.rho,
            "dynamic_viscosity_pa_s": self.mu,
            "sound_speed_m_s": self.a_sound,
            "blade_count": self.blade_count,
            "omega_rad_s": self.omega,
            "rpm": self.rpm,
            "tip_mach": self.tip_mach,
            "tip_mach_limit": self.tip_mach_limit,
            "tip_speed_m_s": self.tip_speed,
            "disk_area_m2": self.disk_area,
            "disk_loading_n_m2": self.thrust_n / self.disk_area,
            "thrust_coefficient": self.thrust_n / (self.rho * self.disk_area * self.tip_speed**2),
            "reynolds_per_m_per_rad_s": self.rho * self.tip_speed * 1.0 / self.mu,
            "solidity_ref": self.solidity_ref,
            "r_stations": self.r_stations,
            "power_ideal_w": self.power_ideal_w,
            "airfoil": self.airfoil.model_dump(),
            "solver": self.solver.model_dump(),
            "optimization": self.optimization.model_dump(),
            "auto_reasons": self.reasons,
            "warnings": self.warnings,
            "input_hash": self.input_hash,
            "raw_input": self.raw,
        }


def resolve(user_input: UserInput) -> ResolvedConfig:
    """Turn the three raw inputs into a complete, self-consistent configuration."""
    units = user_input.units
    if user_input.thrust_n is not None:
        thrust = user_input.thrust_n
    else:
        assert user_input.thrust is not None
        thrust = to_newtons(user_input.thrust, units.thrust)
    if user_input.radius_m is not None:
        radius = user_input.radius_m
    else:
        assert user_input.radius is not None
        radius = to_metres(user_input.radius, units.length)
    if user_input.trailing_edge_thickness_m is not None:
        te_min = user_input.trailing_edge_thickness_m
    else:
        assert user_input.trailing_edge_thickness is not None
        te_min = to_metres(user_input.trailing_edge_thickness, units.length)

    if te_min >= 0.5 * radius:
        raise InputValidationError(
            f"trailing-edge thickness {te_min} m is unphysical for a rotor of radius {radius} m"
        )
    if radius > 30.0:
        raise InputValidationError(f"radius {radius} m is outside the supported range (<= 30 m)")

    reasons: list[str] = []

    if user_input.air_density_kg_m3 is not None:
        rho = user_input.air_density_kg_m3
        mu = user_input.dynamic_viscosity_pa_s or MU_SL
        a_sound = user_input.sound_speed_m_s or A_SOUND_SL
        reasons.append(f"atmosphere overridden: rho={rho} kg/m^3, mu={mu} Pa.s, a={a_sound} m/s")
    else:
        alt = user_input.altitude_m or 0.0
        rho, mu, a_sound = atmosphere(alt)
        if user_input.dynamic_viscosity_pa_s is not None:
            mu = user_input.dynamic_viscosity_pa_s
        if user_input.sound_speed_m_s is not None:
            a_sound = user_input.sound_speed_m_s
        reasons.append(
            f"ISA atmosphere at altitude {alt:g} m: rho={rho:.5f} kg/m^3, mu={mu:.4e} Pa.s, a={a_sound:.2f} m/s"
        )

    tip_mach_limit = user_input.tip_mach_limit if user_input.tip_mach_limit is not None else 0.65

    if user_input.blade_count is not None:
        blades = user_input.blade_count
        reasons.append(f"blade count overridden by user: {blades}")
    else:
        auto_blades = auto_blade_count(radius, thrust, rho)
        blades = int(auto_blades.value)
        reasons.append(f"[auto blade count] {auto_blades.reason}")

    if user_input.rpm is not None:
        omega = rpm_to_omega(user_input.rpm)
        reasons.append(f"rotor speed overridden by user: {user_input.rpm:.1f} RPM")
    else:
        auto_omega: AutoValue = auto_rotor_speed(radius, thrust, blades, rho, a_sound, tip_mach_limit)
        omega = float(auto_omega.value)
        reasons.append(f"[auto rotor speed] {auto_omega.reason}")

    tip_mach = omega * radius / a_sound
    if tip_mach > tip_mach_limit + 1e-9:
        raise InputValidationError(
            f"resolved tip Mach {tip_mach:.3f} exceeds the limit {tip_mach_limit:.3f}; "
            "reduce rpm / blade loading or raise tip_mach_limit"
        )

    r_stations = (
        list(user_input.r_stations) if user_input.r_stations is not None else list(DEFAULT_R_STATIONS)
    )
    if len(r_stations) != SECTION_COUNT:
        raise InputValidationError(
            f"exactly {SECTION_COUNT} radial stations are required, got {len(r_stations)}"
        )
    if any(not (0.0 < s < 1.0) for s in r_stations) or any(
        b <= a for a, b in zip(r_stations[:-1], r_stations[1:], strict=True)
    ):
        raise InputValidationError("radial stations must be strictly increasing and inside (0, 1)")
    if max(r_stations) >= 1.0:
        raise InputValidationError(
            "the outermost station must stay below r/R = 1 to leave room for the tip cap"
        )

    solidity_ref = solidity_estimate(radius, blades)

    cfg = ResolvedConfig(
        thrust_n=thrust,
        radius_m=radius,
        te_min_m=te_min,
        rho=rho,
        mu=mu,
        a_sound=a_sound,
        blade_count=blades,
        omega=omega,
        rpm=omega_to_rpm(omega),
        tip_mach=tip_mach,
        tip_mach_limit=tip_mach_limit,
        r_stations=r_stations,
        solidity_ref=solidity_ref,
        airfoil=user_input.airfoil,
        solver=user_input.solver,
        optimization=user_input.optimization,
        reasons=reasons,
        input_hash=user_input.input_hash(),
        raw=user_input.raw_values(),
    )
    cfg.warnings = feasibility_warnings(radius, thrust, te_min, rho, a_sound, blades, omega)
    return cfg


def load_input(
    path: str | Path | None,
    thrust_n: float | None = None,
    radius_m: float | None = None,
    te_min_m: float | None = None,
    overrides: dict[str, Any] | None = None,
) -> UserInput:
    """Load from JSON file and/or CLI scalars (T012).  CLI values win."""
    data: dict[str, Any] = {}
    if path is not None:
        try:
            data.update(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            raise InputValidationError(f"{path}: unreadable input JSON ({exc})") from exc
    if thrust_n is not None:
        data["thrust_n"] = thrust_n
    if radius_m is not None:
        data["radius_m"] = radius_m
    if te_min_m is not None:
        data["trailing_edge_thickness_m"] = te_min_m
    if overrides:
        data.update(overrides)
    try:
        return UserInput.model_validate(data)
    except ValidationError as exc:
        raise InputValidationError(_pretty_validation(exc, str(path) if path else "command line")) from exc
