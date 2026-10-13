"""End-to-end generation pipeline: three inputs in, complete run directory out (MVP definition, plan 26)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from hoverblade_fm.aero.bemt import BemtSettings
from hoverblade_fm.aero.hover import evaluate_design
from hoverblade_fm.aero.polar import AnalyticPolar, PolarSource, sweep_polar
from hoverblade_fm.config.schema import ResolvedConfig, SolverSettings
from hoverblade_fm.io.provenance import write_provenance
from hoverblade_fm.io.readers import read_section_coordinates
from hoverblade_fm.io.writers import write_polars, write_run_outputs
from hoverblade_fm.models import (
    SECTION_COUNT,
    AirfoilPoints,
    ConstraintReport,
    Design,
    OptimizationResult,
    Performance,
    Provenance,
    Section,
    stations_as_array,
)
from hoverblade_fm.optimize.pipeline import evaluate_vector, run_optimization
from hoverblade_fm.optimize.vector import DesignVectorSpec, initial_vector

__version__ = "0.1.0"


class NullLog:
    def info(self, message: str) -> None:  # pragma: no cover - no-op sink
        pass

    def warning(self, message: str) -> None:  # pragma: no cover - no-op sink
        pass


@dataclass
class RunOutcome:
    """Everything a caller (CLI, test, notebook) needs after one generation run."""

    config: ResolvedConfig
    design: Design
    performance: Performance
    constraints: ConstraintReport
    optimization: OptimizationResult | None
    paths: dict[str, Any]
    out_dir: Path = Path("output")
    provenance: Provenance | None = None
    manufacturing: dict[str, Any] = field(default_factory=dict)
    exit_code: int = 0
    notes: list[str] = field(default_factory=list)
    wall_time_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def generate(
    cfg: ResolvedConfig,
    out_dir: str | Path,
    *,
    optimize: bool = True,
    with_cad: bool = True,
    with_viz: bool = True,
    mesh_resolution: str = "standard",
    polar: PolarSource | None = None,
    settings: BemtSettings | None = None,
    seed: int | None = None,
    search_solver: SolverSettings | None = None,
    log: Any = None,
) -> RunOutcome:
    """Run the full chain and write the plan's output directory."""
    log = log if log is not None else NullLog()
    polar = polar or AnalyticPolar()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    notes: list[str] = []
    manufacturing: dict[str, object] = {}

    spec = DesignVectorSpec(cfg)
    optimization: OptimizationResult | None = None
    if optimize and cfg.optimization.max_evaluations > 0:
        log.info(
            f"optimising ({cfg.optimization.preset}, dim={spec.dim}, max_evaluations={cfg.optimization.max_evaluations})"
        )
        optimization = run_optimization(
            cfg,
            polar=polar,
            settings=settings,
            seed=seed,
            search_solver=search_solver,
            log=log,
        )
        design, performance, constraints = (
            optimization.design,
            optimization.performance,
            optimization.constraints,
        )
    else:
        log.info("evaluating the reference (physics-prior) design without optimisation")
        evaluation = evaluate_vector(cfg, spec, initial_vector(spec, cfg), polar, settings, label="reference")
        design, performance, constraints = evaluation.design, evaluation.performance, evaluation.constraints

    for warning in cfg.warnings:
        log.warning(warning)
    log.info(
        f"design: {len(design.sections)} sections, FM={performance.figure_of_merit:.4f}, "
        f"min t_TE={design.min_te_thickness() * 1000:.3f} mm (required {cfg.te_min_m * 1000:.3f} mm)"
    )

    history = optimization.history if optimization is not None else None
    paths: dict[str, Any] = write_run_outputs(
        design,
        performance,
        constraints,
        cfg,
        out_dir,
        history=history,
        optimization=optimization,
        seed=cfg.optimization.seed if seed is None else int(seed),
    )
    provenance = write_provenance(
        cfg,
        out_dir,
        seed=cfg.optimization.seed if seed is None else int(seed),
        extra={
            "figure_of_merit": performance.figure_of_merit,
            "blade_count": design.blade_count,
            "rpm": design.rpm,
            "solidity": performance.solidity,
            "optimised": optimization is not None,
            "program_version": __version__,
        },
    )
    paths["provenance"] = out_dir / "provenance.json"

    polars = _section_polars(design, cfg, polar)
    if polars:
        written = write_polars(polars, out_dir)
        if written:
            paths["polars"] = written

    if with_viz:
        try:
            from hoverblade_fm.viz import generate_all_visuals

            visual_paths = generate_all_visuals(
                design,
                performance,
                constraints,
                history,
                provenance.to_dict(),
                out_dir,
            )
            paths.update({k: v for k, v in visual_paths.items() if v is not None})
        except ImportError as exc:
            notes.append(f"visualisation skipped: {exc}")
    else:
        notes.append("visualisation disabled by flag")

    if with_cad:
        try:
            from hoverblade_fm.cad import export_manufacturing_package

            package = export_manufacturing_package(design, cfg, out_dir, mesh_resolution=mesh_resolution)
            paths["cad"] = {
                "step": package.step_path,
                "stl": package.stl_path,
                "dxf": package.dxf_path,
                "report": package.report_path,
                "manifest": package.manifest_path,
                "backend": package.backend,
            }
            manufacturing.update(package.checks.to_dict())
            manufacturing["backend"] = package.backend
            blocking = list(package.checks.blocking_failures)
            if blocking:
                notes.append("manufacturing checks reported blocking failures: " + "; ".join(blocking))
        except ImportError as exc:
            notes.append(f"CAD export skipped: {exc}")
    else:
        notes.append("CAD export disabled by flag")

    exit_code = 0
    if not constraints.feasible:
        exit_code = 3
        notes.append(
            "design violates constraints: "
            + "; ".join(constraints.geometry_issues[:3] or [f"thrust error {performance.thrust_error:.3%}"])
        )
    if design.min_te_thickness() < cfg.te_min_m - 1e-12:
        exit_code = 3
        notes.append("trailing-edge thickness below the required minimum (hard constraint violated)")

    return RunOutcome(
        config=cfg,
        design=design,
        performance=performance,
        constraints=constraints,
        optimization=optimization,
        paths=paths,
        out_dir=out_dir,
        provenance=provenance,
        exit_code=exit_code,
        manufacturing=manufacturing,
        notes=notes,
        wall_time_s=time.perf_counter() - t0,
    )


def _section_polars(design: Design, cfg: ResolvedConfig, polar: PolarSource) -> dict[int, np.ndarray]:
    """Sample each station's polar for the ``polars/`` output directory."""
    curves: dict[int, np.ndarray] = {}
    for section in design.sections:
        table = sweep_polar(
            polar,
            reynolds=max(section.reynolds, 1e4),
            mach=min(max(section.mach, 0.05), 0.9),
            t_over_c=section.thickness_to_chord,
            te_over_c=section.te_thickness_m / section.chord_m,
            camber=section.camber_m / section.chord_m,
        )
        curves[section.station_id] = np.column_stack((table.alpha_deg, table.cl, table.cd, table.cm))
    return curves


def design_from_run_dir(out_dir: str | Path, cfg: ResolvedConfig | None = None) -> Design:
    """Rebuild a ``Design`` from a run directory's ``design.json`` plus section coordinate files."""
    from hoverblade_fm.io.readers import read_design, required_te_thickness_m
    from hoverblade_fm.io.writers import section_coordinate_name

    out_dir = Path(out_dir)
    design_data = read_design(out_dir / "design.json")
    rows = design_data.get("sections", [])
    if len(rows) != SECTION_COUNT:
        raise ValueError(f"design.json lists {len(rows)} sections, expected {SECTION_COUNT}")
    radius = float(design_data.get("radius_m", cfg.radius_m if cfg else 1.0))
    te_min = float(
        design_data.get("te_min_m")
        or required_te_thickness_m(design_data)
        or (cfg.te_min_m if cfg else 0.001)
    )

    sections: list[Section] = []
    for i, row in enumerate(rows):
        station_id = int(row.get("station_id", i))
        r_R = float(row.get("r_R", stations_as_array(None)[i]))
        chord = float(row.get("chord_m", 0.1))
        csv_path = out_dir / "sections" / section_coordinate_name(station_id, r_R)
        points = _airfoil_from_records(read_section_coordinates(csv_path))
        sections.append(
            Section(
                station_id=station_id,
                r_R=r_R,
                r_m=float(row.get("r_m", r_R * radius)),
                chord_m=chord,
                twist_deg=float(row.get("twist_deg", 0.0)),
                te_thickness_m=float(row.get("te_thickness_m", points.te_thickness * chord)),
                max_thickness_m=float(row.get("max_thickness_m", points.max_thickness * chord)),
                thickness_to_chord=float(row.get("thickness_to_chord", points.max_thickness)),
                camber_m=float(row.get("camber_m", points.max_camber * chord)),
                le_radius_m=float(row.get("le_radius_m", 0.0)),
                te_included_angle_deg=float(row.get("te_included_angle_deg", 0.0)),
                reynolds=float(row.get("reynolds", 0.0)),
                mach=float(row.get("mach", 0.0)),
                cl_design=float(row.get("cl_design", 0.0)),
                cd_design=float(row.get("cd_design", 0.0)),
                loading_n_m=float(row.get("loading_n_m", 0.0)),
                alpha_effective_deg=float(row.get("alpha_effective_deg", 0.0)),
                points=points,
                airfoil_file=str(csv_path.relative_to(out_dir)),
            )
        )
    rpm = float(design_data.get("rpm", cfg.rpm if cfg else 0.0))
    return Design(
        sections=sections,
        radius_m=radius,
        blade_count=int(design_data.get("blade_count", cfg.blade_count if cfg else 2)),
        rpm=rpm,
        omega=float(design_data.get("omega", rpm * np.pi / 30.0)),
        te_min_m=te_min,
        collective_trim_deg=float(design_data.get("collective_trim_deg", 0.0)),
        design_vector=np.asarray(design_data["design_vector"], dtype=float)
        if design_data.get("design_vector")
        else None,
        label=str(design_data.get("label", "reloaded")),
    )


def _airfoil_from_records(coords: dict[str, Any]) -> AirfoilPoints:
    """Assemble the closed contour from the reader's lower-then-upper surface blocks."""
    x = np.asarray(coords["x_over_c"], dtype=float)
    y = np.asarray(coords["y_over_c"], dtype=float)
    side = np.asarray(coords["side"], dtype=object)
    is_lower = np.char.lower(np.asarray(side, dtype=str)) == "lower"
    lower_x, lower_y = x[is_lower], y[is_lower]
    upper_x, upper_y = x[~is_lower], y[~is_lower]
    if lower_x.size >= 2 and lower_x[0] < lower_x[-1]:
        lower_x, lower_y = lower_x[::-1], lower_y[::-1]
    if upper_x.size >= 2 and upper_x[0] > upper_x[-1]:
        upper_x, upper_y = upper_x[::-1], upper_y[::-1]
    count = min(lower_x.size, upper_x.size)
    contour_x = np.concatenate((lower_x[:count], upper_x[:count]))
    contour_y = np.concatenate((lower_y[:count], upper_y[:count]))
    return AirfoilPoints(x=contour_x, y=contour_y, n_per_surface=count)


def reload_performance(out_dir: str | Path) -> dict[str, Any]:
    """Read a run's ``performance.json`` for the summary / check commands."""
    from hoverblade_fm.io.readers import read_performance

    return read_performance(Path(out_dir) / "performance.json")


def evaluate_reloaded_design(
    design: Design, cfg: ResolvedConfig
) -> tuple[Design, Performance, ConstraintReport]:
    """Re-run the hover solution for a design rebuilt from disk."""
    return evaluate_design(design, cfg)


__all__ = [
    "NullLog",
    "RunOutcome",
    "design_from_run_dir",
    "evaluate_reloaded_design",
    "generate",
    "reload_performance",
]
