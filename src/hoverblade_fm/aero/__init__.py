"""Hover aerodynamics: momentum theory, analytic polars, blade-element-momentum solver."""

from hoverblade_fm.aero.bemt import (
    BemtResult,
    BemtSettings,
    SpanInterp,
    prandtl_loss,
    solve_hover,
    trim_collective,
)
from hoverblade_fm.aero.differentiable import (
    ReducedBlade,
    adam_maximise_fm,
    fm_gradient,
    reduced_blade_from_design,
    solve_differentiable,
    solve_finite_difference,
)
from hoverblade_fm.aero.differentiable import (
    solve_core as solve_reduced,
)
from hoverblade_fm.aero.external import XfoilPolar, prepare_cfd_case, xfoil_executable
from hoverblade_fm.aero.fm import (
    figure_of_merit,
    ideal_induced_velocity,
    ideal_power,
    power_coefficient,
    thrust_coefficient,
)
from hoverblade_fm.aero.hover import (
    attach_design_point,
    constraint_report,
    evaluate_design,
    hover_power_split,
    performance_from_result,
)
from hoverblade_fm.aero.polar import (
    CL_SLOPE_IDEAL,
    AnalyticPolar,
    LinearPolar,
    PolarPoint,
    PolarSettings,
    PolarSource,
    PolarTable,
    sweep_polar,
)
from hoverblade_fm.aero.polar_cache import Bucketing, CachingPolar

__all__ = [
    "Bucketing",
    "CL_SLOPE_IDEAL",
    "CachingPolar",
    "ReducedBlade",
    "XfoilPolar",
    "AnalyticPolar",
    "BemtResult",
    "BemtSettings",
    "LinearPolar",
    "PolarPoint",
    "PolarSettings",
    "PolarSource",
    "PolarTable",
    "SpanInterp",
    "adam_maximise_fm",
    "attach_design_point",
    "constraint_report",
    "evaluate_design",
    "figure_of_merit",
    "fm_gradient",
    "hover_power_split",
    "prepare_cfd_case",
    "reduced_blade_from_design",
    "ideal_induced_velocity",
    "ideal_power",
    "performance_from_result",
    "power_coefficient",
    "prandtl_loss",
    "solve_differentiable",
    "solve_finite_difference",
    "solve_hover",
    "solve_reduced",
    "sweep_polar",
    "thrust_coefficient",
    "trim_collective",
    "xfoil_executable",
]
