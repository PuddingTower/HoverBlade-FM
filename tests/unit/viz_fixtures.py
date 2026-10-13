"""Synthetic :mod:`hoverblade_fm.models` payloads for the visualisation tests.

Deliberately self-contained: these builders only use the shared data contracts, so the
visual layer can be tested before the geometry and aero modules exist.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from hoverblade_fm.models import (
    DEFAULT_R_STATIONS,
    AirfoilPoints,
    ConstraintReport,
    Design,
    Performance,
    Provenance,
    RadialLoad,
    Section,
)

RHO = 1.225
MU = 1.81e-5
A_SOUND = 340.3

N_PER_SURFACE = 60
RADIUS_M = 0.85
BLADE_COUNT = 2
RPM = 1150.0
TE_MIN_M = 0.0015
THRUST_N = 220.0


def _chord_schedule(r_R: np.ndarray) -> np.ndarray:
    return RADIUS_M * (0.185 - 0.105 * r_R)


def blunt_airfoil(
    te_over_chord: float = 0.012,
    tc_over_chord: float = 0.14,
    camber_over_chord: float = 0.035,
    n_per_surface: int = N_PER_SURFACE,
) -> AirfoilPoints:
    """Closed contour with a hard blunt trailing edge, in non-dimensional chord coordinates."""
    i = np.arange(n_per_surface, dtype=float)
    x = 0.5 * (1.0 - np.cos(math.pi * i / (n_per_surface - 1)))
    thickness = te_over_chord + (tc_over_chord - te_over_chord) * np.sin(math.pi * x**0.75) ** 0.8
    camber = camber_over_chord * 4.0 * x * (1.0 - x)
    lower_x = x[::-1]
    lower_y = camber - 0.5 * thickness
    upper_y = camber + 0.5 * thickness
    return AirfoilPoints(
        x=np.concatenate([lower_x, x]),
        y=np.concatenate([lower_y[::-1], upper_y]),
        n_per_surface=n_per_surface,
    )


def make_design(te_scale: float = 1.35, *, sections: int | None = None) -> Design:
    """A physically plausible 10-station blade whose trailing edges clear ``TE_MIN_M``."""
    if sections is not None and sections != len(DEFAULT_R_STATIONS):
        stations = tuple(np.linspace(0.1, 0.97, sections, dtype=float))
    else:
        stations = DEFAULT_R_STATIONS
    r_R = np.array(stations, dtype=float)
    omega = RPM * 2.0 * math.pi / 60.0
    chords = _chord_schedule(r_R)
    built: list[Section] = []
    for index, (station, chord) in enumerate(zip(r_R, chords, strict=True)):
        r_m = station * RADIUS_M
        tip_speed = omega * r_m
        reynolds = RHO * tip_speed * chord / MU
        te_m = te_scale * TE_MIN_M * (1.0 + 0.35 * station)
        t_c = 0.16 - 0.075 * station
        built.append(
            Section(
                station_id=index,
                r_R=float(station),
                r_m=float(r_m),
                chord_m=float(chord),
                twist_deg=float(16.0 - 12.5 * station),
                te_thickness_m=float(te_m),
                max_thickness_m=float(t_c * chord),
                thickness_to_chord=float(t_c),
                camber_m=float((0.045 - 0.02 * station) * chord),
                le_radius_m=float(0.018 * chord),
                te_included_angle_deg=float(10.5 + 6.0 * station),
                reynolds=float(reynolds),
                mach=float(tip_speed / A_SOUND),
                cl_design=float(0.62 - 0.22 * station),
                cd_design=float(0.0085 + 0.004 * station),
                loading_n_m=float(THRUST_N / (BLADE_COUNT * RADIUS_M) * (1.15 - 0.55 * station)),
                alpha_effective_deg=float(4.4 - 2.6 * station),
                points=blunt_airfoil(te_m / chord, t_c, 0.045 - 0.02 * station),
                airfoil_file=f"sections/section_{index:02d}_r{station:.3f}.csv",
            )
        )
    return Design(
        sections=built,
        radius_m=RADIUS_M,
        blade_count=BLADE_COUNT,
        rpm=RPM,
        omega=omega,
        te_min_m=TE_MIN_M,
        design_vector=np.linspace(0.0, 1.0, 22),
        collective_trim_deg=3.4,
        label="fixture-blade",
    )


def make_radial(design: Design, n: int = 61) -> RadialLoad:
    r_R = np.linspace(0.05, 1.0, n)
    omega = design.omega
    chord = np.interp(r_R, design.r_R, design.chord_m)
    tangent = np.maximum(omega * r_R * design.radius_m, 1.0)
    v_i = 6.4 - 1.6 * r_R**2
    cl = np.clip(0.58 - 0.30 * r_R + 0.10 * np.sin(6.0 * r_R), 0.05, 1.0)
    cd = 0.0075 + 0.020 * cl**2
    aoa = 1.6 + 6.4 * cl
    inflow = np.degrees(np.arctan2(v_i, tangent))
    thrust_gradient = 0.5 * RHO * (tangent**2) * cl * chord * design.blade_count
    power_gradient = thrust_gradient * (v_i + 0.06 * tangent) + 9.0 + 40.0 * r_R**3
    return RadialLoad(
        r_R=r_R,
        thrust_gradient=thrust_gradient,
        torque_gradient=power_gradient / max(omega, 1e-6),
        power_gradient=power_gradient,
        induced_velocity=v_i,
        inflow_angle_deg=inflow,
        aoa_deg=aoa,
        cl=cl,
        cd=cd,
        mach=tangent / A_SOUND,
        reynolds=RHO * tangent * chord / MU,
        loss_factor=1.0 - 0.18 * r_R**3,
    )


def make_performance(
    design: Design | None = None, *, radial: bool = True, nan_fields: tuple[str, ...] = ()
) -> Performance:
    design = design or make_design()
    thrust = THRUST_N
    disk = math.pi * design.radius_m**2
    power_ideal = thrust**1.5 / math.sqrt(2.0 * RHO * disk)
    power_profile = 0.11 * power_ideal
    power_swirl = 0.05 * power_ideal
    power_induced = power_ideal * 1.04
    power_actual = power_induced + power_profile + power_swirl
    perf = Performance(
        thrust_target_n=thrust,
        thrust_calc_n=thrust * 1.0021,
        torque_nm=power_actual / design.omega,
        power_ideal_w=power_ideal,
        power_induced_w=power_induced,
        power_profile_w=power_profile,
        power_swirl_w=power_swirl,
        power_misc_w=0.0,
        power_actual_w=power_actual,
        figure_of_merit=power_ideal / power_actual,
        rpm=design.rpm,
        tip_mach=design.tip_speed / A_SOUND,
        solidity=design.solidity,
        blade_count=design.blade_count,
        tip_loss_factor=0.925,
        collective_trim_deg=design.collective_trim_deg,
        radial=make_radial(design) if radial else None,
    )
    for field in nan_fields:
        setattr(perf, field, float("nan"))
    return perf


def make_constraints(design: Design | None = None, *, violated: bool = False) -> ConstraintReport:
    design = design or make_design()
    margin = -0.0004 if violated else design.te_margin()
    return ConstraintReport(
        te_min_required_m=design.te_min_m,
        te_min_actual_m=design.te_min_m + margin,
        te_violation_m=max(0.0, -margin),
        te_included_angle_ok=not violated,
        thrust_error=0.0021 if not violated else 0.051,
        geometry_valid=True,
        geometry_issues=() if not violated else ("section 3 self-intersects near the leading edge",),
        smoothness_penalty=0.0031,
    )


def make_history(count: int = 40, *, fm_initial: float = 0.61) -> list[dict[str, Any]]:
    rng = np.random.default_rng(7)
    rows: list[dict[str, Any]] = []
    for step in range(count):
        progress = step / max(1, count - 1)
        noise = rng.normal(0.0, 0.012) * (1.0 - progress)
        rows.append(
            {
                "evaluation": 24 * (step + 1),
                "iteration": step,
                "figure_of_merit": fm_initial + 0.11 * progress + noise,
                "objective": -(fm_initial + 0.11 * progress) + 0.05 * (1.0 - progress) ** 2,
            }
        )
    rows[0]["fm_initial"] = fm_initial
    return rows


def make_provenance(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "program_version": "0.1.0",
        "git_commit": "8f3c1d2",
        "input_hash": "a1b2c3d4e5f60718",
        "random_seed": 1234,
        "timestamp": "2026-02-11T09:41:03Z",
        "optimization_settings": {"preset": "standard", "popsize": 48, "max_evaluations": 1200},
        "resolved_config": {
            "thrust_n": THRUST_N,
            "radius_m": RADIUS_M,
            "trailing_edge_thickness_m": TE_MIN_M,
            "blade_count": BLADE_COUNT,
            "rpm": RPM,
            "tip_mach": 0.547,
            "solidity_ref": 0.118,
            "air_density_kg_m3": RHO,
            "auto_reasons": [
                "[auto blade count] disk loading chose 2 blades",
                "[auto rotor speed] tip Mach limited to 0.55",
            ],
            "warnings": ["tip Mach is close to the transonic cushion"],
        },
        "python": "3.12.13",
        "numpy": "2.5.4",
        "scipy": "1.16.5",
    }
    base.update(overrides)
    return base


def make_provenance_object(**overrides: Any) -> Provenance:
    return Provenance(**make_provenance(**overrides))
