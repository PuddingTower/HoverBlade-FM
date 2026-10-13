"""Design-point hover evaluation: trim to target thrust, then report performance and constraints."""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from hoverblade_fm.aero.bemt import BemtResult, BemtSettings, SpanInterp, solve_hover, trim_collective
from hoverblade_fm.aero.polar import AnalyticPolar, PolarSource
from hoverblade_fm.config.schema import ResolvedConfig
from hoverblade_fm.geometry.cst import le_radius
from hoverblade_fm.geometry.trailing_edge import check_trailing_edge
from hoverblade_fm.geometry.validate import validate_design
from hoverblade_fm.models import ConstraintReport, Design, Performance, Section


def attach_design_point(design: Design, result: BemtResult, cfg: ResolvedConfig) -> Design:
    """Copy the converged local flow quantities of the ten stations back into the sections."""
    load = result.radial
    interp = SpanInterp(design, cfg, result.collective_trim_deg)
    updated: list[Section] = []
    for section in design.sections:
        x = np.array([section.r_R])
        cl = float(np.interp(section.r_R, load.r_R, load.cl))
        cd = float(np.interp(section.r_R, load.r_R, load.cd))
        mach = float(np.interp(section.r_R, load.r_R, load.mach))
        re = float(np.interp(section.r_R, load.r_R, load.reynolds))
        alpha = float(np.interp(section.r_R, load.r_R, load.aoa_deg))
        dT = float(np.interp(section.r_R, load.r_R, load.thrust_gradient))
        chord = float(interp("chord", x)[0])
        updated.append(
            replace(
                section,
                chord_m=chord,
                twist_deg=float(interp("twist", x)[0]),
                te_thickness_m=section.points.te_thickness * chord,
                max_thickness_m=section.points.max_thickness * chord,
                camber_m=section.points.max_camber * chord,
                le_radius_m=le_radius(section.points) * chord,
                reynolds=re,
                mach=mach,
                cl_design=cl,
                cd_design=cd,
                alpha_effective_deg=alpha,
                loading_n_m=dT,
            )
        )
    return replace(
        design,
        sections=updated,
        collective_trim_deg=result.collective_trim_deg,
    )


def evaluate_design(
    design: Design,
    cfg: ResolvedConfig,
    *,
    polar: PolarSource | None = None,
    settings: BemtSettings | None = None,
    trim: bool = True,
) -> tuple[Design, Performance, ConstraintReport]:
    """Full hover evaluation of a design, returning the trimmed design and its verdict."""
    polar = polar or AnalyticPolar()
    if trim:
        _, result = trim_collective(design, cfg, polar=polar, settings=settings)
    else:
        result = solve_hover(design, cfg, polar=polar, collective_trim_deg=0.0, settings=settings)
    trimmed = attach_design_point(design, result, cfg)
    performance = performance_from_result(result, cfg, trimmed)
    constraints = constraint_report(trimmed, cfg, performance, result, settings=settings)
    return trimmed, performance, constraints


def performance_from_result(result: BemtResult, cfg: ResolvedConfig, design: Design) -> Performance:
    return Performance(
        thrust_target_n=cfg.thrust_n,
        thrust_calc_n=result.thrust_n,
        torque_nm=result.torque_nm,
        power_ideal_w=result.power_ideal_w,
        power_induced_w=result.power_induced_w,
        power_profile_w=result.power_profile_w,
        power_swirl_w=result.power_swirl_w,
        power_misc_w=result.power_misc_w,
        power_actual_w=result.power_total_w,
        figure_of_merit=result.figure_of_merit,
        rpm=cfg.rpm,
        tip_mach=cfg.tip_mach,
        solidity=design.solidity,
        blade_count=design.blade_count,
        tip_loss_factor=result.tip_loss_factor,
        collective_trim_deg=result.collective_trim_deg,
        radial=result.radial,
    )


def constraint_report(
    design: Design,
    cfg: ResolvedConfig,
    performance: Performance,
    result: BemtResult,
    settings: BemtSettings | None = None,
) -> ConstraintReport:
    """Aggregate the trailing-edge, geometry and thrust-match verdicts of a design."""
    issues = validate_design(design, cfg)
    te_checks = [check_trailing_edge(s.points, s.chord_m, cfg, s.station_id) for s in design.sections]
    te_violation = max((max(c.te_required_m - c.te_thickness_m, 0.0) for c in te_checks), default=0.0)
    tc_band_ok = all(c.tc_band_ok for c in te_checks)
    from hoverblade_fm.geometry.spanwise import spanwise_penalty

    smooth = spanwise_penalty(design.r_R, design.chord_m, design.twist_deg, cfg.radius_m)
    return ConstraintReport(
        te_min_required_m=cfg.te_min_m,
        te_min_actual_m=design.min_te_thickness(),
        te_violation_m=float(te_violation),
        te_included_angle_ok=bool(tc_band_ok),
        thrust_error=float(performance.thrust_error),
        geometry_valid=len(issues) == 0,
        geometry_issues=tuple(issues),
        smoothness_penalty=float(smooth),
    )


def hover_power_split(performance: Performance) -> dict[str, float]:
    """Fractional contribution of each power term, handy for reports and convergence plots."""
    total = performance.power_actual_w or 1.0
    return {
        "induced": performance.power_induced_w / total,
        "profile": performance.power_profile_w / total,
        "swirl": performance.power_swirl_w / total,
        "misc": performance.power_misc_w / total,
    }


__all__ = [
    "attach_design_point",
    "constraint_report",
    "evaluate_design",
    "hover_power_split",
    "performance_from_result",
]
