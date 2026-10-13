"""Writers for every run-output artefact (plan T057-T061, sections 3.3-3.5, 12.3).

All files are UTF-8 with LF endings and deterministic ordering; floats are rounded to
significant digits so golden-file comparisons stay stable, and JSON payloads are coerced
to strict JSON (NaN/Inf become ``null``, never bare ``NaN`` tokens).
"""

from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from hoverblade_fm.config.schema import ResolvedConfig
from hoverblade_fm.io.provenance import write_provenance
from hoverblade_fm.models import (
    SECTION_CSV_COLUMNS,
    ConstraintReport,
    Design,
    OptimizationResult,
    Performance,
    Section,
)

#: Column order of the per-section coordinate CSVs (plan section 3.5).
SECTION_COORD_COLUMNS: tuple[str, ...] = ("side", "x_over_c", "y_over_c", "x_m", "y_m")

#: Column order of ``optimization_history.csv`` (plan T061).
HISTORY_COLUMNS: tuple[str, ...] = (
    "evaluation",
    "objective",
    "figure_of_merit",
    "thrust_n",
    "thrust_error",
    "te_min_m",
    "te_margin_m",
    "geometry_valid",
    "smoothness_penalty",
    "feasible",
    "wall_time_s",
)

#: Coordinate-convention notes repeated in every section CSV header and in ``index.json``.
COORDINATE_CONVENTION: tuple[str, ...] = (
    "x_over_c/y_over_c are non-dimensional chord coordinates: x=0 at the leading edge, x=1 at the "
    "trailing edge, y positive 'up' (plan 3.5).",
    "x_m is the chordwise physical coordinate and y_m the thickness/lift-direction coordinate of the "
    "twist-rotated section; both come from AirfoilPoints.in_blade_frame with the origin at 25% chord.",
    "Rows list the lower surface LE->TE followed by the upper surface LE->TE.",
    "The internal closed contour runs lower-surface TE->LE then upper-surface LE->TE, so the closed "
    "contour is recovered by reversing the lower-surface block and appending the upper-surface block.",
    "The leading edge point appears once in each surface block; the trailing edge stays blunt "
    "(upper and lower TE points never coincide).",
)

_TE_TOL_M = 1e-12


# -- low-level formatting helpers ---------------------------------------------------------


def _round_sig(value: float, digits: int) -> float:
    """Round *value* to *digits* significant decimal digits (0 maps to 0.0)."""
    if value == 0.0 or not math.isfinite(value):
        return float(value)
    return float(f"{value:.{digits}g}")


def _to_jsonable(obj: Any, *, digits: int | None = None) -> Any:
    """Recursively coerce *obj* into strictly JSON-serialisable Python types.

    NumPy scalars/arrays become ``int``/``float``/``list``, non-finite floats become ``None``,
    and, when *digits* is given, finite floats are rounded to that many significant digits.
    """
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, np.ndarray):
        return [_to_jsonable(v, digits=digits) for v in obj.tolist()]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        obj = float(obj)
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return _round_sig(obj, digits) if digits is not None else obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, Mapping):
        return {str(k): _to_jsonable(v, digits=digits) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v, digits=digits) for v in obj]
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return _to_jsonable(dataclasses.asdict(obj), digits=digits)
    return str(obj)


def _num(value: Any) -> str:
    """Format one scalar for a CSV cell: 9-significant-digit floats, ``true``/``false`` bools."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, int):
        return str(value)
    v = _round_sig(float(value), 9)
    if not math.isfinite(v):
        return ""
    if v == 0.0:
        v = 0.0
    return repr(v)


def _write_json(path: Path, payload: Any, *, digits: int | None = None) -> Path:
    """Write *payload* (coerced via :func:`_to_jsonable`) as UTF-8/LF JSON with LF terminator."""
    text = (
        json.dumps(_to_jsonable(payload, digits=digits), indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def _write_text(path: Path, text: str) -> Path:
    """Write *text* with LF line endings in UTF-8."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\r\n", "\n").encode("utf-8"))
    return path


# -- design / performance ----------------------------------------------------------------


def write_design_json(design: Design, config: ResolvedConfig, path: Path) -> Path:
    """Write ``design.json``: full design parameters plus the resolved-config echo (T057)."""
    sections: list[dict[str, Any]] = []
    for section in design.sections:
        row = dict(section.to_row())
        row["te_thickness_to_chord"] = section.te_thickness_to_chord
        row["blunt_te_ok"] = bool(section.te_thickness_m >= design.te_min_m - _TE_TOL_M)
        row["shape_parameters"] = _shape_parameters(section)
        sections.append(row)
    payload: dict[str, Any] = {
        "label": design.label,
        "radius_m": design.radius_m,
        "blade_count": design.blade_count,
        "rpm": design.rpm,
        "omega": design.omega,
        "tip_mach": config.tip_mach,
        "tip_speed": design.tip_speed,
        "solidity": design.solidity,
        "collective_trim_deg": design.collective_trim_deg,
        "te_min_m": design.te_min_m,
        "min_te_thickness_m": design.min_te_thickness(),
        "te_margin_m": design.te_margin(),
        "blunt_te_ok": design.is_blunt(tol=_TE_TOL_M),
        "config": config.to_dict(),
        "stations": {
            "r_R": design.r_R.tolist(),
            "r_m": [s.r_m for s in design.sections],
            "chord_m": design.chord_m.tolist(),
            "twist_deg": design.twist_deg.tolist(),
            "te_thickness_m": design.te_thickness_m.tolist(),
        },
        "sections": sections,
    }
    if design.design_vector is not None:
        payload["design_vector"] = np.asarray(design.design_vector).tolist()
    return _write_json(path, payload, digits=9)


def _shape_parameters(section: Section) -> dict[str, Any]:
    """CST/shape parameters carried by a (possibly subclassed) section, minus the core fields."""
    core = set(section.to_row()) | {"points"}
    params: dict[str, Any] = {}
    for field in dataclasses.fields(section):
        if field.name in core:
            continue
        value = getattr(section, field.name, None)
        if value is not None:
            params[field.name] = _to_jsonable(value, digits=9)
    return params


def write_performance_json(performance: Performance, path: Path) -> Path:
    """Write ``performance.json``: hover totals plus radial arrays, NaN/Inf as null (T058)."""
    payload = performance.to_dict()
    if performance.radial is not None:
        payload["radial"] = dataclasses.asdict(performance.radial)
    return _write_json(path, payload, digits=6)


def write_sections_csv(design: Design, path: Path) -> Path:
    """Write ``sections.csv``: header plus exactly the section rows in radial order (T059)."""
    lines = [",".join(SECTION_CSV_COLUMNS)]
    for section in design.sections:
        row = section.to_row()
        lines.append(",".join(_num(row[column]) for column in SECTION_CSV_COLUMNS))
    return _write_text(path, "\n".join(lines) + "\n")


# -- section coordinates -------------------------------------------------------------------


def section_coordinate_name(index: int, r_R: float) -> str:
    """File name of station *index* at *r_R*: ``section_{i:02d}_r{r_R:.3f}.csv`` (plan 3.3)."""
    return f"section_{index:02d}_r{r_R:.3f}.csv"


def write_section_coordinates(design: Design, out_dir: Path) -> list[Path]:
    """Write the ten ``sections/section_XX_rYYY.csv`` files and ``sections/index.json`` (T060).

    Each file carries a ``#`` comment header (station metadata plus the coordinate
    convention) followed by the ``side,x_over_c,y_over_c,x_m,y_m`` table, lower surface
    LE->TE first, then upper surface LE->TE.
    """
    sections_dir = out_dir / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    entries: list[dict[str, Any]] = []
    for index, section in enumerate(design.sections):
        name = section_coordinate_name(index, section.r_R)
        lines = [f"# HoverBlade-FM section coordinates: {name}"]
        meta = {
            "station_id": section.station_id,
            "r_R": section.r_R,
            "r_m": section.r_m,
            "chord_m": section.chord_m,
            "twist_deg": section.twist_deg,
            "te_thickness_m": section.te_thickness_m,
            "max_thickness_m": section.max_thickness_m,
            "thickness_to_chord": section.thickness_to_chord,
            "camber_m": section.camber_m,
            "le_radius_m": section.le_radius_m,
            "te_included_angle_deg": section.te_included_angle_deg,
            "n_per_surface": section.points.n_per_surface,
        }
        for key, value in meta.items():
            lines.append(f"# {key}: {_num(value)}")
        for note in COORDINATE_CONVENTION:
            lines.append(f"# convention: {note}")
        lines.append(",".join(SECTION_COORD_COLUMNS))
        for side, xn, yn, xm, ym in _surface_rows(section):
            for i in range(len(xn)):
                lines.append(f"{side},{xn[i]:.6f},{yn[i]:.6f},{xm[i]:.7f},{ym[i]:.7f}")
        paths.append(_write_text(sections_dir / name, "\n".join(lines) + "\n"))
        entries.append({**{k: _to_jsonable(v, digits=9) for k, v in meta.items()}, "file": name})
    index_payload = {
        "columns": list(SECTION_COORD_COLUMNS),
        "convention": list(COORDINATE_CONVENTION),
        "sections": entries,
    }
    paths.append(_write_json(sections_dir / "index.json", index_payload, digits=9))
    return paths


def _surface_rows(
    section: Section,
) -> list[tuple[str, list[float], list[float], list[float], list[float]]]:
    """Per-surface (normalised, physical) coordinate columns, each ordered LE -> TE."""
    points = section.points
    x_lower, y_lower, x_upper, y_upper = points.surface_pairs()
    frame = points.in_blade_frame(section.chord_m, section.twist_deg, section.r_m)
    n = points.n_per_surface
    lower_m = frame[:n][::-1]
    upper_m = frame[n:, :]
    return [
        ("lower", x_lower.tolist(), y_lower.tolist(), lower_m[:, 0].tolist(), lower_m[:, 2].tolist()),
        ("upper", x_upper.tolist(), y_upper.tolist(), upper_m[:, 0].tolist(), upper_m[:, 2].tolist()),
    ]


# -- optimisation history / polars -----------------------------------------------------------


def write_optimization_history(
    history: Sequence[Mapping[str, Any]],
    out_dir: Path,
    *,
    strategy: str | None = None,
    seed: int | None = None,
) -> Path:
    """Write ``optimization_history.csv`` (missing keys produce empty cells) + summary JSON (T061)."""
    rows = list(history or ())
    lines = [",".join(HISTORY_COLUMNS)]
    for row in rows:
        lines.append(",".join(_num(row.get(column)) for column in HISTORY_COLUMNS))
    path = _write_text(out_dir / "optimization_history.csv", "\n".join(lines) + "\n")
    best_row: Mapping[str, Any] | None = None
    best_value = math.inf
    for row in rows:
        objective = _as_float(row.get("objective"))
        if objective is not None and objective < best_value:
            best_value, best_row = objective, row
    summary = {
        "best_objective": _as_float(best_row.get("objective")) if best_row is not None else None,
        "best_evaluation": best_row.get("evaluation") if best_row is not None else None,
        "evaluations": len(rows),
        "strategy": strategy,
        "seed": seed,
    }
    _write_json(out_dir / "optimization_summary.json", summary, digits=6)
    return path


def _as_float(value: Any) -> float | None:
    """Finite float of *value*, or ``None`` for non-numeric / non-finite / bool input."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def write_polars(polar_curves: Mapping[Any, Any] | Sequence[Any] | None, out_dir: Path) -> list[Path]:
    """Write ``polars/section_XX_polar.csv`` files with columns ``alpha_deg,cl,cd,cm``.

    Accepts a mapping keyed by station index or a sequence indexed by station; entries that
    are not ``(N, 4)``-shaped data are skipped and, with no usable data at all, an empty list
    is returned without creating the directory (polar data is optional).
    """
    if not polar_curves:
        return []
    if isinstance(polar_curves, Mapping):
        items: list[tuple[int, Any]] = []
        for key, value in polar_curves.items():
            station = int(key) if isinstance(key, (int, np.integer)) else _as_float(key)
            if station is None:
                continue
            items.append((int(station), value))
        items.sort()
    else:
        items = list(enumerate(polar_curves))
    paths: list[Path] = []
    polars_dir = out_dir / "polars"
    for station, curve in items:
        rows = _as_matrix(curve)
        if rows is None:
            continue
        lines = ["alpha_deg,cl,cd,cm"]
        for row in rows:
            lines.append(",".join(f"{float(v):.6f}" for v in row))
        polars_dir.mkdir(parents=True, exist_ok=True)
        path = polars_dir / f"section_{station:02d}_polar.csv"
        _write_text(path, "\n".join(lines) + "\n")
        paths.append(path)
    return paths


def _as_matrix(curve: Any) -> np.ndarray | None:
    """Return *curve* as an ``(N >= 1, 4)`` float array, or ``None`` when it cannot be one."""
    try:
        array = np.asarray(curve, dtype=float)
    except (TypeError, ValueError):
        return None
    if array.ndim != 2 or array.shape[1] != 4 or array.shape[0] == 0 or not np.all(np.isfinite(array)):
        return None
    return array


# -- top-level orchestration --------------------------------------------------------------------


def write_run_outputs(
    design: Design,
    performance: Performance,
    constraints: ConstraintReport,
    config: ResolvedConfig,
    out_dir: Path,
    *,
    history: list[dict[str, Any]] | None = None,
    optimization: OptimizationResult | None = None,
    seed: int = 0,
) -> dict[str, Path | list[Path]]:
    """Write the data outputs of plan section 3.3 into *out_dir* and return key -> path.

    Keys: ``design``, ``performance``, ``provenance``, ``sections_csv``, ``section_coords``
    (list), ``history``, ``constraints``.  When *history* is omitted it is taken from
    *optimization*; provenance records the optimiser settings plus a summary of the search.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    resolved: dict[str, Path | list[Path]] = {}
    resolved["design"] = write_design_json(design, config, out_dir / "design.json")
    resolved["performance"] = write_performance_json(performance, out_dir / "performance.json")
    resolved["constraints"] = _write_json(out_dir / "constraints.json", constraints.to_dict(), digits=9)
    if history is not None:
        run_history: Sequence[Mapping[str, Any]] = history
    elif optimization is not None:
        run_history = optimization.history
    else:
        run_history = []
    strategy = optimization.strategy if optimization is not None else None
    history_seed = optimization.seed if optimization is not None else seed
    resolved["history"] = write_optimization_history(
        run_history, out_dir, strategy=strategy, seed=history_seed
    )
    resolved["sections_csv"] = write_sections_csv(design, out_dir / "sections.csv")
    resolved["section_coords"] = write_section_coordinates(design, out_dir)
    extra: dict[str, Any] | None = None
    if optimization is not None:
        extra = {
            "optimization": {
                "evaluations": int(optimization.evaluations),
                "strategy": str(optimization.strategy),
                "seed": int(optimization.seed),
            }
        }
    write_provenance(config, out_dir, seed, extra=extra)
    resolved["provenance"] = out_dir / "provenance.json"
    return resolved
