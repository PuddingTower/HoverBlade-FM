"""Turn a planform plus per-station shape parameters into a validated ``Design`` (T020, T021, T031).

:func:`plan_from_raw` is the single funnel through which every design - user supplied,
optimiser candidate, or repaired final answer - must pass, because that is where the
blunt-trailing-edge hard clamp is applied.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from hoverblade_fm.config.schema import ResolvedConfig
from hoverblade_fm.geometry.cst import SectionParameters, build_airfoil, le_radius
from hoverblade_fm.geometry.spanwise import (
    DEFAULT_CHORD_CP_R,
    chord_distribution,
    twist_distribution,
)
from hoverblade_fm.geometry.trailing_edge import enforce_blunt, te_included_angle_deg
from hoverblade_fm.models import Design, Section, stations_as_array

DEFAULT_TOLERANCE_MM = 1e-9
CD0_ESTIMATE = 0.0085


@dataclass(slots=True)
class BladePlan:
    """Decoded, constraint-satisfied description of a blade."""

    chord_m: np.ndarray
    twist_deg: np.ndarray
    params: list[SectionParameters]
    te_clamped: list[bool] = field(default_factory=list)
    r_R: np.ndarray = field(default_factory=lambda: stations_as_array(None))
    collective_trim_deg: float = 0.0
    chord_control_points: np.ndarray | None = None
    twist_control_points: np.ndarray | None = None
    te_clamp_deficit: float = 0.0

    @property
    def max_te_clamp_deficit_m(self) -> float:
        """How far the requested design was below the required thickness, before clamping."""
        return float(self.te_clamp_deficit)

    def with_collective(self, delta_deg: float) -> BladePlan:
        """Return a plan whose twist is shifted by ``delta_deg`` (hover thrust trim)."""
        shifted = [
            SectionParameters(**{k: getattr(p, k) for k in p.__dataclass_fields__}) for p in self.params
        ]
        return BladePlan(
            chord_m=self.chord_m.copy(),
            twist_deg=self.twist_deg + delta_deg,
            params=shifted,
            te_clamped=list(self.te_clamped),
            r_R=self.r_R.copy(),
            collective_trim_deg=self.collective_trim_deg + delta_deg,
            chord_control_points=None
            if self.chord_control_points is None
            else self.chord_control_points.copy(),
            twist_control_points=None
            if self.twist_control_points is None
            else self.twist_control_points.copy(),
            te_clamp_deficit=self.te_clamp_deficit,
        )


def plan_from_raw(
    cfg: ResolvedConfig,
    chord_cp: np.ndarray | list[float] | None = None,
    twist_cp: np.ndarray | list[float] | None = None,
    section_params: list[SectionParameters] | None = None,
    chord_cp_r: np.ndarray | None = None,
    twist_cp_r: np.ndarray | None = None,
    collective_trim_deg: float = 0.0,
) -> BladePlan:
    """Build a plan and apply the blunt-TE hard constraint station by station."""
    r = stations_as_array(cfg.r_stations)
    if chord_cp is None or twist_cp is None:
        from hoverblade_fm.geometry.spanwise import reference_planform

        chord_cp, twist_cp = reference_planform(cfg)
    chord = chord_distribution(np.asarray(chord_cp, dtype=float), cfg, chord_cp_r)
    twist = twist_distribution(np.asarray(twist_cp, dtype=float), cfg, twist_cp_r)

    if section_params is None:
        base = SectionParameters()
        section_params = [
            SectionParameters(
                t_over_c=base.t_over_c,
                x_t=base.x_t,
                camber=base.camber,
                x_camber=base.x_camber,
                te_gap=max(base.te_gap, cfg.te_min_m / c),
                le_factor=base.le_factor,
            )
            for c in chord
        ]
    if len(section_params) != r.size:
        raise ValueError(f"expected {r.size} station parameter sets, got {len(section_params)}")

    clamped: list[bool] = []
    deficit = 0.0
    final_params: list[SectionParameters] = []
    for c, p in zip(chord, section_params, strict=True):
        patched, did_clamp = enforce_blunt(p, c, cfg)
        clamped.append(did_clamp)
        if did_clamp:
            deficit = max(deficit, (cfg.te_min_m / max(c, 1e-9)) - p.te_gap)
        final_params.append(patched)

    plan = BladePlan(
        chord_m=chord,
        twist_deg=twist,
        params=final_params,
        te_clamped=clamped,
        r_R=r,
        collective_trim_deg=collective_trim_deg,
        chord_control_points=np.asarray(chord_cp, dtype=float),
        twist_control_points=np.asarray(twist_cp, dtype=float),
        te_clamp_deficit=float(deficit),
    )
    return plan


def estimated_zero_lift_angle_deg(params: SectionParameters) -> float:
    """Zero-lift angle of the section, kept consistent with :class:`hoverblade_fm.aero.polar.AnalyticPolar`."""
    return float(-22.0 * params.camber)


def build_design(
    plan: BladePlan,
    cfg: ResolvedConfig,
    *,
    label: str = "design",
    design_vector: np.ndarray | None = None,
    n_per_surface: int | None = None,
) -> Design:
    """Evaluate all ten sections: coordinates, physical thicknesses and design-point estimates."""
    n = n_per_surface or cfg.solver.n_points_per_surface
    radius = cfg.radius_m
    sections: list[Section] = []
    for i, (r_R, chord, twist, params) in enumerate(
        zip(plan.r_R, plan.chord_m, plan.twist_deg, plan.params, strict=True)
    ):
        points = build_airfoil(params, n)
        r_m = r_R * radius
        v_local = cfg.omega * r_m
        alpha0 = estimated_zero_lift_angle_deg(params)
        alpha_eff = twist - alpha0
        cl = float(np.clip(2.0 * math.pi * math.radians(alpha_eff) * 0.85, -1.5, 1.6))
        sections.append(
            Section(
                station_id=i,
                r_R=float(r_R),
                r_m=float(r_m),
                chord_m=float(chord),
                twist_deg=float(twist),
                te_thickness_m=float(points.te_thickness * chord),
                max_thickness_m=float(points.max_thickness * chord),
                thickness_to_chord=float(points.max_thickness),
                camber_m=float(points.max_camber * chord),
                le_radius_m=float(le_radius(points) * chord),
                te_included_angle_deg=float(te_included_angle_deg(params)),
                reynolds=float(cfg.rho * v_local * chord / cfg.mu),
                mach=float(v_local / cfg.a_sound),
                cl_design=cl,
                cd_design=float(CD0_ESTIMATE + 0.05 * max(cl, 0.0) ** 2),
                loading_n_m=float(0.5 * cfg.rho * v_local**2 * chord * cl),
                alpha_effective_deg=float(alpha_eff),
                points=points,
                airfoil_file=f"sections/section_{i:02d}_r{r_R:.3f}.csv",
            )
        )
    design = Design(
        sections=sections,
        radius_m=radius,
        blade_count=cfg.blade_count,
        rpm=cfg.rpm,
        omega=cfg.omega,
        te_min_m=cfg.te_min_m,
        design_vector=design_vector,
        collective_trim_deg=plan.collective_trim_deg,
        label=label,
    )
    if not design.is_blunt():  # invariant guard: the clamp above must make this unreachable
        raise AssertionError(
            f"internal error: a design escaped the blunt trailing-edge projection "
            f"(min {design.min_te_thickness() * 1000:.6f} mm < required {design.te_min_m * 1000:.6f} mm)"
        )
    return design


def default_section_parameters(
    cfg: ResolvedConfig, chord: np.ndarray | None = None
) -> list[SectionParameters]:
    """Hover-oriented default shape family: moderately thick, cambered, thick-LE sections."""
    r = stations_as_array(cfg.r_stations)
    c = (
        chord_distribution(np.zeros(len(DEFAULT_CHORD_CP_R)) + 0.1 * cfg.radius_m, cfg)
        if chord is None
        else np.asarray(chord)
    )
    out: list[SectionParameters] = []
    for r_R, ch in zip(r, c, strict=True):
        taper = 1.0 - 0.35 * r_R
        te_gap_required = cfg.te_min_m / max(ch, 1e-9)
        out.append(
            SectionParameters(
                t_over_c=float(
                    np.clip(
                        0.18 * taper, cfg.airfoil.thickness_to_chord_min, cfg.airfoil.thickness_to_chord_max
                    )
                ),
                x_t=0.30,
                camber=float(np.clip(0.045 - 0.02 * r_R, 0.0, cfg.airfoil.camber_max_over_chord)),
                x_camber=float(0.40 - 0.08 * r_R),
                te_gap=float(max(0.010 * taper, te_gap_required)),
                le_factor=1.0 + 0.1 * (1.0 - r_R),
            )
        )
    return out


__all__ = [
    "DEFAULT_TOLERANCE_MM",
    "BladePlan",
    "build_design",
    "default_section_parameters",
    "estimated_zero_lift_angle_deg",
    "plan_from_raw",
]
