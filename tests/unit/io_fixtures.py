"""Local synthetic fixtures for the IO tests (self-contained; imports nothing from geometry/aero)."""

from __future__ import annotations

from typing import Any

import numpy as np

from hoverblade_fm.config.schema import AirfoilBounds, OptimizationSettings, ResolvedConfig, SolverSettings
from hoverblade_fm.models import (
    DEFAULT_R_STATIONS,
    AirfoilPoints,
    ConstraintReport,
    Design,
    OptimizationResult,
    Performance,
    RadialLoad,
    Section,
)

RADIUS_M = 0.5
TE_MIN_M = 0.002
N_PER_SURFACE = 24


def blunt_te_points(
    te_over_c: float = 0.02,
    thickness_over_c: float = 0.10,
    camber_over_c: float = 0.02,
    n_per_surface: int = N_PER_SURFACE,
) -> AirfoilPoints:
    """NACA-4D-style symmetric thickness + parabolic camber with a blunt TE of exact gap *te_over_c*."""
    x = np.linspace(0.0, 1.0, n_per_surface)
    yt = (
        5.0
        * thickness_over_c
        * (0.2969 * np.sqrt(x) - 0.1260 * x - 0.3516 * x**2 + 0.2843 * x**3 - 0.1036 * x**4)
    )
    half = 0.5 * te_over_c + np.maximum(yt, 0.0)
    camber = camber_over_c * 4.0 * x * (1.0 - x)
    lower = camber - half
    upper = camber + half
    # Closed contour: lower surface TE->LE, then upper surface LE->TE (models.AirfoilPoints convention).
    return AirfoilPoints(
        x=np.concatenate((x[::-1], x)),
        y=np.concatenate((lower[::-1], upper)),
        n_per_surface=n_per_surface,
    )


def make_sections(te_over_c_scale: float = 1.0) -> list[Section]:
    """Ten synthetic sections at the default stations, all blunt when *te_over_c_scale* >= 1."""
    sections: list[Section] = []
    for i, r_R in enumerate(DEFAULT_R_STATIONS):
        chord = 0.18 - 0.012 * i
        points = blunt_te_points(te_over_c=0.02 * te_over_c_scale + 0.004 * i)
        sections.append(
            Section(
                station_id=i,
                r_R=float(r_R),
                r_m=float(r_R) * RADIUS_M,
                chord_m=chord,
                twist_deg=16.0 - 1.3 * i,
                te_thickness_m=points.te_thickness * chord,
                max_thickness_m=points.max_thickness * chord,
                thickness_to_chord=points.max_thickness,
                camber_m=points.max_camber * chord,
                le_radius_m=0.015 * chord,
                te_included_angle_deg=12.0 + 0.5 * i,
                reynolds=3.0e5 + 1.0e4 * i,
                mach=0.25 + 0.02 * i,
                cl_design=0.60 - 0.02 * i,
                cd_design=0.010 + 0.0005 * i,
                loading_n_m=20.0 - 1.2 * i,
                alpha_effective_deg=4.0 - 0.2 * i,
                airfoil_file=f"sections/section_{i:02d}_r{r_R:.3f}.csv",
                points=points,
            )
        )
    return sections


def make_design(te_over_c_scale: float = 1.0) -> Design:
    return Design(
        sections=make_sections(te_over_c_scale),
        radius_m=RADIUS_M,
        blade_count=2,
        rpm=1200.0,
        omega=40.0 * np.pi,
        te_min_m=TE_MIN_M,
        design_vector=np.linspace(0.4, 0.7, 22),
        collective_trim_deg=1.5,
        label="fixture",
    )


def make_config() -> ResolvedConfig:
    return ResolvedConfig(
        thrust_n=120.0,
        radius_m=RADIUS_M,
        te_min_m=TE_MIN_M,
        rho=1.225,
        mu=1.81e-5,
        a_sound=340.3,
        blade_count=2,
        omega=40.0 * np.pi,
        rpm=1200.0,
        tip_mach=0.52,
        tip_mach_limit=0.65,
        r_stations=list(DEFAULT_R_STATIONS),
        solidity_ref=0.12,
        airfoil=AirfoilBounds(),
        solver=SolverSettings(),
        optimization=OptimizationSettings(preset="fast"),
        reasons=["fixture config"],
        warnings=[],
        input_hash="0123abcd4567ef89",
        raw={"thrust_n": 120.0, "radius_m": RADIUS_M, "trailing_edge_thickness_m": TE_MIN_M},
    )


def make_performance(with_radial: bool = True, nan_marker: bool = True) -> Performance:
    radial: RadialLoad | None = None
    if with_radial:
        r = np.linspace(0.04, 1.0, 25)
        power_gradient = 40.0 * np.sin(np.pi * r) + 20.0
        if nan_marker:
            power_gradient[5] = np.nan
        radial = RadialLoad(
            r_R=r,
            thrust_gradient=180.0 * np.sin(np.pi * r),
            torque_gradient=22.0 * np.cos(np.pi * r * 0.5),
            power_gradient=power_gradient,
            induced_velocity=4.0 + 1.5 * r,
            inflow_angle_deg=6.0 - 3.0 * r,
            aoa_deg=3.0 + 1.0 * r,
            cl=0.55 - 0.1 * r,
            cd=0.010 + 0.004 * r,
            mach=0.10 + 0.45 * r,
            reynolds=2.0e5 + 1.0e5 * r,
            loss_factor=0.9 + 0.1 * r,
        )
    return Performance(
        thrust_target_n=120.0,
        thrust_calc_n=120.5,
        torque_nm=15.0,
        power_ideal_w=132.4,
        power_induced_w=150.0,
        power_profile_w=55.0,
        power_swirl_w=12.0,
        power_misc_w=3.0,
        power_actual_w=220.0,
        figure_of_merit=132.4 / 220.0,
        rpm=1200.0,
        tip_mach=0.52,
        solidity=0.123,
        blade_count=2,
        tip_loss_factor=0.95,
        collective_trim_deg=1.5,
        radial=radial,
    )


def make_constraints() -> ConstraintReport:
    return ConstraintReport(
        te_min_required_m=TE_MIN_M,
        te_min_actual_m=0.0036,
        te_violation_m=0.0,
        te_included_angle_ok=True,
        thrust_error=(120.5 - 120.0) / 120.0,
        geometry_valid=True,
        geometry_issues=(),
        smoothness_penalty=0.0012,
    )


def make_history() -> list[dict[str, Any]]:
    return [
        {
            "evaluation": 1,
            "objective": 0.62,
            "figure_of_merit": 0.60,
            "thrust_n": 118.0,
            "thrust_error": -0.0167,
            "te_min_m": TE_MIN_M,
            "te_margin_m": 0.0016,
            "geometry_valid": True,
            "smoothness_penalty": 0.0,
            "feasible": True,
            "wall_time_s": 0.12,
        },
        {
            "evaluation": 2,
            "objective": 0.58,
            "figure_of_merit": 0.63,
            "geometry_valid": False,
            "feasible": False,
        },
        {
            "evaluation": 3,
            "objective": float("nan"),
            "figure_of_merit": 0.0,
        },
        {
            "evaluation": 4,
            "objective": 0.71,
            "figure_of_merit": 0.65,
            "thrust_n": 120.5,
            "feasible": True,
        },
    ]


def make_optimization_result() -> OptimizationResult:
    design = make_design()
    performance = make_performance()
    return OptimizationResult(
        design=design,
        performance=performance,
        constraints=make_constraints(),
        initial_performance=None,
        initial_constraints=None,
        history=make_history(),
        evaluations=42,
        strategy="cma-es+slsqp",
        seed=7,
    )
