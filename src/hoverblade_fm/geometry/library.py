"""Built-in airfoil library (T082).

Sections are generated analytically from the public-domain NACA thickness/camber formulae
implemented here, never loaded from a third-party coordinate dump, and then converted to the
project's hard constraint with :func:`blunt_trailing_edge`: the sharp NACA trailing edge is
closed with an explicit flat gap so the library can never hand back an unmanufacturable edge.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from hoverblade_fm.geometry.cst import SectionParameters, build_airfoil, sample_x
from hoverblade_fm.models import AirfoilPoints

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "airfoils.json"

#: Fraction of the aft chord over which the blunt closure is blended in.
BLUNT_BLEND = 0.05


@dataclass(frozen=True)
class LibraryEntry:
    """One selectable section in the built-in library."""

    name: str
    family: str
    code: str
    t_over_c: float
    camber: float
    camber_position: float
    design_note: str

    @property
    def label(self) -> str:
        return str(self.name)


def naca_thickness(x: np.ndarray, t_over_c: float) -> np.ndarray:
    """NACA four-digit half-thickness distribution (sharp trailing edge by definition).

    ``y_t = 5 t (0.2969 sqrt(x) - 0.1260 x - 0.3516 x^2 + 0.2843 x^3 - 0.1015 x^4)``; the
    leading factor of five is what makes the maximum half-thickness equal ``t``.
    """
    xv = np.clip(np.asarray(x, dtype=float), 0.0, 1.0)
    terms = ((0.2969, 0.5), (-0.1260, 1.0), (-0.3516, 2.0), (0.2843, 3.0), (-0.1015, 4.0))
    series = sum(c * xv**e for c, e in terms)
    return 5.0 * t_over_c * series


def naca_camber_line(x: np.ndarray, m: float, p: float) -> np.ndarray:
    """NACA four-digit mean camber line for maximum camber ``m`` at ``p`` chord."""
    xv = np.clip(np.asarray(x, dtype=float), 0.0, 1.0)
    if m <= 0.0 or not 0.0 < p < 1.0:
        return np.zeros_like(xv)
    front = m / p**2 * (2.0 * p * xv - xv**2)
    aft = m / (1.0 - p) ** 2 * ((1.0 - 2.0 * p) + 2.0 * p * xv - xv**2)
    return np.where(xv < p, front, aft)


def naca_camber_slope(x: np.ndarray, m: float, p: float) -> np.ndarray:
    xv = np.clip(np.asarray(x, dtype=float), 0.0, 1.0)
    if m <= 0.0 or not 0.0 < p < 1.0:
        return np.zeros_like(xv)
    front = 2.0 * m / p**2 * (p - xv)
    aft = 2.0 * m / (1.0 - p) ** 2 * (p - xv)
    return np.where(xv < p, front, aft)


def naca_four_digit(code: str, n_per_surface: int = 80) -> AirfoilPoints:
    """Generate a NACA four-digit section (``2412``, ``0015``, ...) in chord units."""
    if not (len(code) == 4 and code.isdigit()):
        raise ValueError(f"NACA four-digit code must be four digits, got {code!r}")
    m, p, t = int(code[0]) / 100.0, int(code[1]) / 100.0, int(code[2:]) / 100.0
    x = sample_x(n_per_surface)
    yc = naca_camber_line(x, m, p)
    dyc = naca_camber_slope(x, m, p)
    yt = naca_thickness(x, t)
    theta = np.arctan(dyc)
    upper_x = x - yt * np.sin(theta)
    upper_y = yc + yt * np.cos(theta)
    lower_x = x - yt * np.sin(theta)
    lower_y = yc - yt * np.cos(theta)
    return contour_from_surfaces(lower_x, lower_y, upper_x, upper_y, n_per_surface)


def contour_from_surfaces(
    x_lower: np.ndarray,
    y_lower: np.ndarray,
    x_upper: np.ndarray,
    y_upper: np.ndarray,
    n_per_surface: int,
) -> AirfoilPoints:
    """Assemble the closed contour convention used across the project."""
    x = np.concatenate((x_lower[::-1], x_upper))
    y = np.concatenate((y_lower[::-1], y_upper))
    return AirfoilPoints(x=x, y=y, n_per_surface=n_per_surface)


def blunt_trailing_edge(points: AirfoilPoints, te_over_c: float) -> AirfoilPoints:
    """Convert any section (including a sharp-edged NACA) into a blunt one.

    A linear closure is blended into the aft ``BLUNT_BLEND`` of the chord and continued to the
    edge, so ``y_upper(1) - y_lower(1) == max(existing gap, te_over_c)`` while the forward
    shape is untouched.
    """
    if te_over_c <= 0.0:
        raise ValueError("te_over_c must be positive - a sharp trailing edge is not representable")
    x_lower, y_lower, x_upper, y_upper = points.surface_pairs()
    existing = float(y_upper[-1] - y_lower[-1])
    gap = max(existing, te_over_c)
    additional = gap - existing
    if additional <= 0.0:
        return points
    blend = np.clip((x_upper - (1.0 - BLUNT_BLEND)) / BLUNT_BLEND, 0.0, 1.0)
    ramp = 0.5 * additional * np.maximum(blend, x_upper)
    new_upper = y_upper + ramp
    new_lower = y_lower - 0.5 * additional * np.maximum(blend, x_lower)
    return contour_from_surfaces(x_lower, new_lower, x_upper, new_upper, points.n_per_surface)


@lru_cache(maxsize=1)
def _registry_payload() -> dict[str, list[dict[str, Any]]]:
    if not DATA_FILE.is_file():
        raise FileNotFoundError(f"airfoil library metadata missing at {DATA_FILE}")
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def available() -> list[LibraryEntry]:
    """Every library entry, with its design note."""
    payload = _registry_payload()
    entries: list[LibraryEntry] = []
    for item in payload.get("naca_four_digit", []):
        entries.append(
            LibraryEntry(
                name=item["name"],
                family="NACA four-digit",
                code=item["code"],
                t_over_c=float(item["t_over_c"]),
                camber=float(item["camber"]),
                camber_position=float(item.get("camber_position", 0.0)),
                design_note=str(item.get("note", "")),
            )
        )
    for item in payload.get("cst_hover", []):
        entries.append(
            LibraryEntry(
                name=item["name"],
                family="CST hover section",
                code=item["name"],
                t_over_c=float(item["t_over_c"]),
                camber=float(item["camber"]),
                camber_position=float(item.get("camber_position", 0.4)),
                design_note=str(item.get("note", "")),
            )
        )
    return entries


def names() -> list[str]:
    return [entry.label for entry in available()]


def section_parameters_for(name: str) -> SectionParameters:
    """Library entry expressed as this project's own shape parameters."""
    entry = _entry(name)
    payload = _registry_payload()
    for item in payload.get("cst_hover", []):
        if item["name"] == name:
            return SectionParameters(
                t_over_c=float(item["t_over_c"]),
                x_t=float(item.get("x_t", 0.30)),
                camber=float(item["camber"]),
                x_camber=float(item.get("camber_position", 0.4)),
                te_gap=float(item.get("te_gap", 0.008)),
                le_factor=float(item.get("le_factor", 1.0)),
            )
    return SectionParameters(
        t_over_c=entry.t_over_c,
        camber=entry.camber,
        x_camber=max(entry.camber_position, 0.28),
        te_gap=0.008,
    )


def _entry(name: str) -> LibraryEntry:
    for entry in available():
        if entry.label == name:
            return entry
    raise KeyError(f"unknown airfoil '{name}', available: {', '.join(names())}")


def load(name: str, te_over_c: float | None = None, n_per_surface: int = 80) -> AirfoilPoints:
    """Load a library section as a closed contour, always with a blunt trailing edge.

    ``te_over_c`` is the required non-dimensional edge thickness; when omitted the entry's own
    design value is used.  A request below the library value keeps the larger one, so loading
    can never *sharpen* an edge.
    """
    entry = _entry(name)
    required = float(te_over_c if te_over_c is not None else entry.t_over_c * 0.06)
    payload = _registry_payload()
    for item in payload.get("cst_hover", []):
        if item["name"] == name:
            points = build_airfoil(section_parameters_for(name), n_per_surface)
            return blunt_trailing_edge(points, max(required, float(item.get("te_gap", required))))
    points = naca_four_digit(entry.code, n_per_surface)
    return blunt_trailing_edge(points, required)


def library_summary() -> dict[str, object]:
    """Human-readable inventory for the run report and the docs."""
    payload = _registry_payload()
    return {
        "naca_four_digit": len(payload.get("naca_four_digit", [])),
        "cst_hover": len(payload.get("cst_hover", [])),
        "total": len(names()),
        "provenance": str(payload.get("provenance", "")),
        "blunt_enforcement": f"every loaded section passes through blunt_trailing_edge (>= {BLUNT_BLEND:g} aft blend)",
    }


__all__ = [
    "BLUNT_BLEND",
    "LibraryEntry",
    "available",
    "blunt_trailing_edge",
    "contour_from_surfaces",
    "library_summary",
    "load",
    "naca_camber_line",
    "naca_camber_slope",
    "naca_four_digit",
    "naca_thickness",
    "names",
    "section_parameters_for",
]
