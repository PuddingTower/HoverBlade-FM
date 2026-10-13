"""Readers for the run-output directory (plan sections 3.3-3.5).

Everything a downstream CLI (``summary``, ``check-manufacturing``) needs to reload a
previous run from disk, with validation of the hard blunt-trailing-edge bookkeeping:
ten sections and ``te_thickness_m >= trailing_edge_thickness_m`` from the design echo.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hoverblade_fm.models import SECTION_COUNT, SECTION_CSV_COLUMNS

_INT_FIELDS = frozenset({"station_id"})
_STR_FIELDS = frozenset({"airfoil_file", "side"})


class RunDataError(ValueError):
    """Raised when a run-output file is missing, malformed or violates the blunt-TE contract."""


@dataclass(slots=True)
class RunSummary:
    """A validated run directory reloaded from disk."""

    design: dict[str, Any]
    performance: dict[str, Any]
    provenance: dict[str, Any]
    sections: list[dict[str, Any]]
    paths: dict[str, Any]


def read_json(path: str | Path) -> dict[str, Any]:
    """Load a JSON object from *path*, raising :class:`RunDataError` on any problem."""
    path = Path(path)
    if not path.is_file():
        raise RunDataError(f"missing run output file: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunDataError(f"{path}: unreadable JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise RunDataError(f"{path}: expected a JSON object")
    return data


def read_design(path: str | Path) -> dict[str, Any]:
    """Load ``design.json`` and require the echo keys the readers depend on."""
    data = read_json(path)
    for key in ("sections", "config"):
        if key not in data:
            raise RunDataError(f"{path}: design echo is missing '{key}'")
    return data


def read_performance(path: str | Path) -> dict[str, Any]:
    """Load ``performance.json``."""
    return read_json(path)


def read_provenance(path: str | Path) -> dict[str, Any]:
    """Load ``provenance.json``."""
    return read_json(path)


def _cell(field: str, raw: str) -> Any:
    text = raw.strip()
    if field in _STR_FIELDS:
        return text
    if text == "":
        return None
    if field in _INT_FIELDS:
        return int(text)
    try:
        return float(text)
    except ValueError as exc:
        raise RunDataError(f"section column '{field}': cannot parse {text!r} as a number") from exc


def read_sections_table(path: str | Path) -> list[dict[str, Any]]:
    """Load ``sections.csv`` as a list of typed rows, enforcing the ten-station rule (T059)."""
    path = Path(path)
    if not path.is_file():
        raise RunDataError(f"missing run output file: {path}")
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        raise RunDataError(f"{path}: empty sections table")
    header = [column.strip() for column in lines[0].split(",")]
    if tuple(header) != SECTION_CSV_COLUMNS:
        raise RunDataError(f"{path}: unexpected sections.csv header: {header}")
    rows: list[dict[str, Any]] = []
    for line in lines[1:]:
        cells = line.split(",")
        if len(cells) != len(header):
            raise RunDataError(f"{path}: row has {len(cells)} cells, expected {len(header)}: {line!r}")
        rows.append({field: _cell(field, cell) for field, cell in zip(header, cells, strict=True)})
    if len(rows) != SECTION_COUNT:
        raise RunDataError(f"{path}: expected {SECTION_COUNT} section rows, found {len(rows)}")
    return rows


def read_section_coordinates(path: str | Path) -> dict[str, Any]:
    """Load one ``sections/section_*.csv`` into its comment-header metadata and point columns.

    Returns ``{"path", "meta", "side", "x_over_c", "y_over_c", "x_m", "y_m"}`` with the
    surface blocks in file order (lower LE->TE, then upper LE->TE); see the coordinate
    convention notes in the header itself and in ``sections/index.json``.
    """
    path = Path(path)
    if not path.is_file():
        raise RunDataError(f"missing run output file: {path}")
    meta: dict[str, Any] = {}
    conventions: list[str] = []
    header: list[str] = []
    data_lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            body = stripped.lstrip("#").strip()
            if body.startswith("convention:"):
                conventions.append(body[len("convention:") :].strip())
            elif ":" in body:
                key, _, value = body.partition(":")
                meta[key.strip()] = _meta_value(value.strip())
            continue
        if not header:
            header = [column.strip() for column in stripped.split(",")]
            continue
        data_lines.append(stripped)
    if tuple(header) != ("side", "x_over_c", "y_over_c", "x_m", "y_m"):
        raise RunDataError(f"{path}: unexpected coordinate header: {header}")
    columns: dict[str, list[Any]] = {name: [] for name in header}
    for line in data_lines:
        cells = line.split(",")
        if len(cells) != len(header):
            raise RunDataError(f"{path}: coordinate row has {len(cells)} cells: {line!r}")
        for name, cell in zip(header, cells, strict=True):
            columns[name].append(_cell(name, cell))
    if conventions:
        meta["convention"] = conventions
    return {"path": str(path), "meta": meta, **columns}


def _meta_value(text: str) -> Any:
    try:
        number = float(text)
    except ValueError:
        return text
    if number.is_integer() and abs(number) < 1e15:
        return int(number)
    return number


def read_section_index(sections_dir: str | Path) -> dict[str, Any]:
    """Load ``sections/index.json`` (written by :func:`hoverblade_fm.io.writers.write_section_coordinates`)."""
    sections_dir = Path(sections_dir)
    path = sections_dir / "index.json"
    if not path.is_file():
        raise RunDataError(f"missing run output file: {path}")
    index = read_json(path)
    if not isinstance(index.get("sections"), list):
        raise RunDataError(f"{path}: index has no 'sections' list")
    return index


def required_te_thickness_m(design: dict[str, Any]) -> float | None:
    """Minimum allowed trailing-edge thickness echoed by a design record."""
    config = design.get("config")
    value = None
    if isinstance(config, dict):
        value = config.get("trailing_edge_thickness_m")
    if value is None:
        value = design.get("te_min_m")
    return float(value) if isinstance(value, (int, float)) and math.isfinite(float(value)) else None


def validate_sections(sections: list[dict[str, Any]], required_te_m: float | None) -> None:
    """Enforce the hard blunt-TE bookkeeping on reloaded section rows (plan section 2)."""
    if len(sections) != SECTION_COUNT:
        raise RunDataError(f"expected {SECTION_COUNT} sections, found {len(sections)}")
    if required_te_m is None:
        return
    for row in sections:
        actual = row.get("te_thickness_m")
        if actual is None:
            raise RunDataError(f"station {row.get('station_id')}: te_thickness_m is missing")
        if float(actual) < required_te_m - 1e-12:
            raise RunDataError(
                f"station {row.get('station_id')} (r/R={row.get('r_R')}): te_thickness_m {float(actual):.6f} "
                f"is below the required minimum {required_te_m:.6f}"
            )


def read_run(out_dir: str | Path) -> RunSummary:
    """Reload and validate a complete run directory.

    Requires ``design.json``, ``performance.json``, ``provenance.json`` and ``sections.csv``;
    validates ten sections and the blunt-TE minimum from the design echo, and resolves the
    per-station coordinate files through ``sections/index.json`` (glob fallback).
    """
    out_dir = Path(out_dir)
    design = read_design(out_dir / "design.json")
    performance = read_performance(out_dir / "performance.json")
    provenance = read_provenance(out_dir / "provenance.json")
    sections = read_sections_table(out_dir / "sections.csv")
    validate_sections(sections, required_te_thickness_m(design))
    paths: dict[str, Any] = {
        "design": out_dir / "design.json",
        "performance": out_dir / "performance.json",
        "provenance": out_dir / "provenance.json",
        "sections_csv": out_dir / "sections.csv",
        "history": out_dir / "optimization_history.csv",
    }
    sections_dir = out_dir / "sections"
    coord_paths: list[Path] = []
    if sections_dir.is_dir():
        try:
            index = read_section_index(sections_dir)
        except RunDataError:
            index = {"sections": []}
        for entry in index["sections"]:
            name = entry.get("file") if isinstance(entry, dict) else None
            if name and (sections_dir / str(name)).is_file():
                coord_paths.append(sections_dir / str(name))
        if not coord_paths:
            coord_paths = sorted(sections_dir.glob("section_*.csv"))
    paths["section_coords"] = coord_paths
    return RunSummary(
        design=design,
        performance=performance,
        provenance=provenance,
        sections=sections,
        paths=paths,
    )
