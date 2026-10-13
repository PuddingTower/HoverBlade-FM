"""CAD and manufacturing checks (T078, T079) - the plan's section 5.5 and 13.3 requirements."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from hoverblade_fm.cad import backend
from hoverblade_fm.cad.dxf import read_dxf_summary
from hoverblade_fm.cad.loft import LoftedBlade, resample_contour
from hoverblade_fm.cad.mesh import TriMesh, is_watertight, mesh_volume
from hoverblade_fm.cad.step import step_readback
from hoverblade_fm.config.schema import ResolvedConfig
from hoverblade_fm.geometry.spanwise import MAX_DC_DR, MAX_DTWIST_DR_DEG, smoothness_metrics
from hoverblade_fm.geometry.trailing_edge import (
    check_trailing_edge,
    te_radius_estimate_m,
)
from hoverblade_fm.models import Design

#: Default manufacturing tolerances (plan section 13.4).
TOLERANCES: dict[str, str] = {
    "trailing_edge_thickness": "+/-0.10 mm or +/-5% of nominal, whichever is larger",
    "chord": "+/-0.50 mm or +/-0.1% of nominal, whichever is larger",
    "twist": "+/-0.2 deg",
    "station_position": "+/-0.20 mm",
    "surface_fairness": "G1 continuity required, G2 recommended",
    "units": "millimetres (declared in every file header)",
}

LOFT_ERROR_LIMIT_M = 1e-3


@dataclass
class ManufacturingChecks:
    """Verdicts a shop needs before cutting metal."""

    min_te_thickness_global_m: float
    min_te_thickness_per_section_m: list[float]
    te_thickness_margin_m: float
    te_edge_length_m: float
    te_edge_length_per_section_m: list[float]
    te_radius_estimate_m: float
    te_radius_per_section_m: list[float]
    te_manufacturability_flag: bool
    te_included_angle_deg_per_section: list[float]
    te_included_angle_band_ok: bool
    te_over_chord_band_ok: bool
    chord_change_rate_max: float
    chord_change_rate_limit: float
    twist_change_rate_deg_per_r_max: float
    twist_change_rate_limit: float
    surface_self_intersection_detected: bool
    loft_max_gap_error_m: float
    loft_error_limit_m: float
    mesh_watertight: bool
    mesh_volume_m3: float
    step_parse_ok: bool
    step_entities: int
    dxf_written: bool
    dxf_section_polylines: int
    units: str
    step_validator: str = "none"
    tolerances: dict[str, str] = field(default_factory=lambda: dict(TOLERANCES))
    blocking_failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.blocking_failures

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "min_te_thickness_global_mm": self.min_te_thickness_global_m * 1000.0,
            "min_te_thickness_per_section_mm": [v * 1000.0 for v in self.min_te_thickness_per_section_m],
            "te_thickness_margin_mm": self.te_thickness_margin_m * 1000.0,
            "te_edge_length_mm": self.te_edge_length_m * 1000.0,
            "te_edge_length_per_section_mm": [v * 1000.0 for v in self.te_edge_length_per_section_m],
            "te_radius_estimate_mm": self.te_radius_estimate_m * 1000.0,
            "te_radius_per_section_mm": [v * 1000.0 for v in self.te_radius_per_section_m],
            "te_manufacturability_flag": self.te_manufacturability_flag,
            "te_included_angle_deg_per_section": self.te_included_angle_deg_per_section,
            "te_included_angle_band_ok": self.te_included_angle_band_ok,
            "te_over_chord_band_ok": self.te_over_chord_band_ok,
            "chord_change_rate_max": self.chord_change_rate_max,
            "chord_change_rate_limit": self.chord_change_rate_limit,
            "twist_change_rate_deg_per_r_max": self.twist_change_rate_deg_per_r_max,
            "twist_change_rate_limit": self.twist_change_rate_limit,
            "surface_self_intersection_detected": self.surface_self_intersection_detected,
            "loft_max_gap_error_mm": self.loft_max_gap_error_m * 1000.0,
            "loft_error_limit_mm": self.loft_error_limit_m * 1000.0,
            "mesh_watertight": self.mesh_watertight,
            "mesh_volume_m3": self.mesh_volume_m3,
            "step_parse_ok": self.step_parse_ok,
            "step_entities": self.step_entities,
            "step_validator": self.step_validator,
            "dxf_written": self.dxf_written,
            "dxf_section_polylines": self.dxf_section_polylines,
            "units": self.units,
            "tolerances": self.tolerances,
            "blocking_failures": self.blocking_failures,
            "warnings": self.warnings,
            "ok": self.ok,
        }
        return payload


def _as_int(value: object) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _loft_gap_error_m(loft: LoftedBlade, design: Design) -> float:
    """Max distance between a design station and its lofted counterpart (sampling error)."""
    worst = 0.0
    flagged = [i for i, flag in enumerate(loft.is_design_station) if flag]
    for i, station_index in enumerate(flagged[: len(design.sections)]):
        contour = loft.stations[station_index]
        expected = resample_contour(design.sections[i], loft.n_per_section)
        worst = max(worst, float(np.max(np.linalg.norm(contour - expected, axis=1))))
    return worst


def _self_intersection_detected(loft: LoftedBlade) -> bool:
    """Detect a loft that turns inside out.

    Each ring is a closed contour in the chordwise/thickness plane; a self-intersecting loft
    shows up as an inversion of the polygon winding between neighbouring stations, or as a
    chordwise span that collapses to (near) zero inboard of the tip.
    """
    previous_sign: float | None = None
    for contour in loft.stations:
        x, z = contour[:, 0], contour[:, 2]
        signed_area = 0.5 * float(np.sum(x * np.roll(z, -1) - np.roll(x, -1) * z))
        if abs(signed_area) < 1e-12:
            return True
        sign = math.copysign(1.0, signed_area)
        if previous_sign is not None and sign != previous_sign:
            return True
        previous_sign = sign
    return False


def run_manufacturing_checks(
    design: Design,
    cfg: ResolvedConfig,
    *,
    loft: LoftedBlade | None = None,
    mesh: TriMesh | None = None,
    step_path: str | Path | None = None,
    dxf_path: str | Path | None = None,
) -> ManufacturingChecks:
    """Evaluate every plan 5.5 / 13.3 item and mark the ones that block production."""
    te_checks = [check_trailing_edge(s.points, s.chord_m, cfg, s.station_id) for s in design.sections]
    per_section_te = [c.te_thickness_m for c in te_checks]
    min_te = float(min(per_section_te)) if per_section_te else 0.0
    margin = min_te - cfg.te_min_m
    hard_ok = all(c.hard_ok for c in te_checks)
    tc_ok = all(c.tc_band_ok for c in te_checks)
    angle_ok = all(c.angle_band_advisory_ok for c in te_checks)
    radii = [te_radius_estimate_m(s.points, s.chord_m) for s in design.sections]
    edges = [c.te_edge_length_m for c in te_checks]

    chord_metrics = smoothness_metrics(design.r_R, design.chord_m / cfg.radius_m)
    twist_metrics = smoothness_metrics(design.r_R, design.twist_deg)

    blocking: list[str] = []
    warnings: list[str] = []
    if not hard_ok:
        blocking.append(f"trailing edge below the required {cfg.te_min_m * 1000:.3f} mm")
    if not tc_ok:
        blocking.append("t_TE/c outside the manufacturable band")
    if margin < 0.0:
        blocking.append("negative trailing-edge margin")

    loft_error = 0.0
    watertight = True if mesh is None else is_watertight(mesh.faces, mesh.vertices)
    volume = 0.0 if mesh is None else mesh_volume(mesh.vertices, mesh.faces)
    self_intersect = False
    if loft is not None:
        loft_error = _loft_gap_error_m(loft, design)
        self_intersect = _self_intersection_detected(loft)
        if loft_error > LOFT_ERROR_LIMIT_M:
            blocking.append(
                f"loft sampling error {loft_error * 1000:.3f} mm exceeds {LOFT_ERROR_LIMIT_M * 1000:.3f} mm"
            )
        if self_intersect:
            blocking.append("lofted surface self-intersection detected")
    if mesh is not None:
        if not watertight:
            blocking.append("STL mesh is not watertight")
        if volume <= 0.0:
            blocking.append("mesh volume is not positive (check face winding)")
    if chord_metrics["max_abs_dv_dr"] > MAX_DC_DR + 1e-6:
        blocking.append("chord change rate exceeds the manufacturable limit")
    if twist_metrics["max_abs_dv_dr"] > MAX_DTWIST_DR_DEG + 1e-6:
        blocking.append("twist change rate exceeds the manufacturable limit")

    step_ok = False
    entities = 0
    step_validator = "none"
    kernel_volume_mm3: float | None = None
    if step_path is not None and Path(step_path).is_file():
        if backend.occt_available():
            report = backend.occt_validate(str(step_path))
            step_validator = "occt"
            solid_count = _as_int(report.get("solid_count"))
            step_ok = bool(report.get("available")) and bool(report.get("valid")) and solid_count >= 1
            raw_volume = report.get("volume_mm3")
            kernel_volume_mm3 = float(raw_volume) if isinstance(raw_volume, (int, float)) else None
            if not step_ok:
                blocking.append(f"STEP rejected by the OCCT kernel: {report.get('error', report)}")
        readback = step_readback(step_path)
        entities = int(readback.get("entity_count", 0))
        if step_validator == "none":
            # without a kernel the structural parser is the only available validator
            step_ok = bool(readback.get("ok"))
            if not step_ok:
                blocking.append(
                    "STEP structural check failed: "
                    + ", ".join(
                        key
                        for key in ("parenthesis_balanced", "references_resolve", "ends_with_terminator")
                        if not readback.get(key)
                    )
                )
        elif mesh is not None and isinstance(kernel_volume_mm3, (int, float)):
            mesh_volume_mm3 = volume * 1e9
            if mesh_volume_mm3 > 0 and abs(kernel_volume_mm3 - mesh_volume_mm3) / mesh_volume_mm3 > 0.10:
                warnings.append(
                    f"STEP solid volume {kernel_volume_mm3 / 1e9:.6f} m^3 differs from the STL volume "
                    f"{mesh_volume_mm3 / 1e9:.6f} m^3 by more than 10%; the two representations of the "
                    "loft disagree and one should be regenerated"
                )
    dxf_summary = read_dxf_summary(dxf_path) if dxf_path is not None and Path(dxf_path).is_file() else {}
    dxf_written = bool(dxf_summary.get("polylines", 0))
    if dxf_path is not None and not dxf_written:
        blocking.append("DXF master sections contain no polylines")

    if not angle_ok:
        warnings.append(
            "some stations report a trailing-edge wedge angle outside the advisory "
            f"{cfg.airfoil.te_included_angle_min_deg:.0f}-{cfg.airfoil.te_included_angle_max_deg:.0f} deg band; "
            "the blocking criterion is t_TE/c within the manufacturable band"
        )
    if design.te_margin() < 1e-9:
        warnings.append(
            "the trailing edge sits exactly on the specified minimum; the shop should treat "
            "t_TE as a lower control limit, not a target"
        )

    return ManufacturingChecks(
        min_te_thickness_global_m=min_te,
        min_te_thickness_per_section_m=per_section_te,
        te_thickness_margin_m=margin,
        te_edge_length_m=float(np.sum(edges)) / max(len(edges), 1),
        te_edge_length_per_section_m=edges,
        te_radius_estimate_m=float(np.mean(radii)) if radii else 0.0,
        te_radius_per_section_m=radii,
        te_manufacturability_flag=hard_ok and tc_ok and not blocking,
        te_included_angle_deg_per_section=[round(c.included_angle_deg, 4) for c in te_checks],
        te_included_angle_band_ok=angle_ok,
        te_over_chord_band_ok=tc_ok,
        chord_change_rate_max=chord_metrics["max_abs_dv_dr"],
        chord_change_rate_limit=MAX_DC_DR,
        twist_change_rate_deg_per_r_max=twist_metrics["max_abs_dv_dr"],
        twist_change_rate_limit=MAX_DTWIST_DR_DEG,
        surface_self_intersection_detected=self_intersect,
        loft_max_gap_error_m=loft_error,
        loft_error_limit_m=LOFT_ERROR_LIMIT_M,
        mesh_watertight=watertight,
        mesh_volume_m3=volume,
        step_parse_ok=step_ok,
        step_entities=entities,
        step_validator=step_validator,
        dxf_written=dxf_written,
        dxf_section_polylines=_as_int(dxf_summary.get("section_polylines")),
        units="MM",
        blocking_failures=blocking,
        warnings=warnings,
    )


__all__ = ["LOFT_ERROR_LIMIT_M", "ManufacturingChecks", "TOLERANCES", "run_manufacturing_checks"]
