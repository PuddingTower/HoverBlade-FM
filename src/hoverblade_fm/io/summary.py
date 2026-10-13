"""Flat, printable summary of a run directory (plan section 3.3, CLI ``summary`` support).

Never raises for missing artefacts: required files (plan 12.3 data outputs) go to
``missing_required``, while plots, interactive HTML, CAD and other optional products go
to ``missing_optional``.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from hoverblade_fm.models import SECTION_COUNT

REQUIRED_FILES: tuple[str, ...] = (
    "design.json",
    "performance.json",
    "provenance.json",
    "sections.csv",
    "optimization_history.csv",
)

OPTIONAL_FILES: tuple[str, ...] = (
    "optimization_summary.json",
    "constraints.json",
    "report.html",
    "interactive/blade.html",
    "plots/chord_twist.png",
    "plots/airfoil_stack.png",
    "plots/trailing_edge_zoom.png",
    "plots/fm_convergence.png",
    "plots/radial_performance.png",
    "cad/blade.step",
    "cad/blade.stl",
    "cad/master_sections.dxf",
    "cad/manufacturing_report.md",
)


def _load_json(path: Path) -> dict[str, Any] | None:
    """Best-effort JSON-object load returning ``None`` when absent or unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _dig(data: dict[str, Any] | None, *keys: str) -> Any:
    """Follow nested dict *keys*, returning ``None`` on any miss or non-dict step."""
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _finite(value: Any) -> Any:
    """Keep finite numbers (rounded to 6 significant digits), map anything else to ``None``."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return float(f"{number:.6g}")


def summarise_run(out_dir: Path) -> dict[str, Any]:
    """Summarise a run directory as one flat dict: inputs, hover results, TE margin, gaps."""
    out_dir = Path(out_dir)
    design = _load_json(out_dir / "design.json")
    performance = _load_json(out_dir / "performance.json")
    provenance = _load_json(out_dir / "provenance.json")

    missing_required = [name for name in REQUIRED_FILES if not (out_dir / name).is_file()]
    sections_dir = out_dir / "sections"
    coord_files = sorted(sections_dir.glob("section_*.csv")) if sections_dir.is_dir() else []
    if len(coord_files) < SECTION_COUNT:
        missing_required.append(f"sections/section_*.csv (found {len(coord_files)}/{SECTION_COUNT})")
    missing_optional = [name for name in OPTIONAL_FILES if not (out_dir / name).is_file()]
    if not (out_dir / "polars").is_dir():
        missing_optional.append("polars/")

    required_te = _dig(design, "config", "trailing_edge_thickness_m")
    if required_te is None:
        required_te = _dig(design, "te_min_m")
    min_te = _dig(design, "min_te_thickness_m")
    te_margin = _dig(design, "te_margin_m")
    if min_te is not None and te_margin is None and required_te is not None:
        te_margin = float(min_te) - float(required_te)

    summary: dict[str, Any] = {
        "out_dir": str(out_dir),
        "thrust_n": _finite(_dig(design, "config", "thrust_n")),
        "radius_m": _finite(_dig(design, "config", "radius_m")),
        "trailing_edge_thickness_m": _finite(required_te),
        "blade_count": _dig(design, "blade_count"),
        "rpm": _finite(_dig(design, "rpm")),
        "tip_mach": _finite(_dig(design, "config", "tip_mach")),
        "figure_of_merit": _finite(_dig(performance, "figure_of_merit")),
        "power_actual_w": _finite(_dig(performance, "power_actual_w")),
        "power_ideal_w": _finite(_dig(performance, "power_ideal_w")),
        "thrust_calc_n": _finite(_dig(performance, "thrust_calc_n")),
        "thrust_error": _finite(_dig(performance, "thrust_error")),
        "figure_power_w_per_n": _finite(_dig(performance, "figure_power_w_per_n")),
        "min_te_thickness_m": _finite(min_te),
        "te_margin_m": _finite(te_margin),
        "blunt_te_ok": _dig(design, "blunt_te_ok"),
        "program_version": _dig(provenance, "program_version"),
        "git_commit": _dig(provenance, "git_commit"),
        "input_hash": _dig(provenance, "input_hash"),
        "random_seed": _dig(provenance, "random_seed"),
        "timestamp": _dig(provenance, "timestamp"),
        "section_coordinate_files": len(coord_files),
        "missing_required": missing_required,
        "missing_optional": missing_optional,
    }
    return summary
