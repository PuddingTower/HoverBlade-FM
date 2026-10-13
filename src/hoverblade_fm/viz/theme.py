"""Headless, deterministic matplotlib theme shared by every HoverBlade-FM visual.

Importing this module guarantees that no GUI backend is opened and that colours come from
the small :data:`PALETTE`, so plots are reproducible across machines and CI runners.
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from typing import Any, cast

import matplotlib
from cycler import cycler
from matplotlib import colormaps
from matplotlib import colors as mplcolors
from matplotlib.figure import Figure

#: Resolution of every raster output (plan section 12.1).
DPI = 150
CMAP_NAME = "hoverblade"

PALETTE: dict[str, str] = {
    "ink": "#1f2430",
    "muted": "#6b7280",
    "grid": "#dfe3ea",
    "panel": "#f7f8fa",
    "chord": "#2b6cb0",
    "twist": "#c05621",
    "te": "#b83232",
    "pass": "#2f855a",
    "fail": "#c53030",
    "fm": "#2f855a",
    "objective": "#805ad5",
    "thrust": "#2b6cb0",
    "power": "#805ad5",
    "induced": "#0987a0",
    "cl": "#dd6b20",
    "alpha": "#4a5568",
    "mach": "#b83232",
    "loading": "#38a169",
    "warn": "#d69e2e",
}

CYCLE: tuple[str, ...] = (
    PALETTE["chord"],
    PALETTE["twist"],
    PALETTE["fm"],
    PALETTE["objective"],
    PALETTE["induced"],
    PALETTE["cl"],
    PALETTE["te"],
    PALETTE["alpha"],
)

_CMAP_STOPS: tuple[str, ...] = ("#16324f", "#1f7a8c", "#4fb286", "#e3a33d", "#d96c4f")

_THEME_PARAMS: dict[str, Any] = {
    "font.family": "DejaVu Sans",
    "font.size": 9.0,
    "axes.labelsize": 9.5,
    "axes.titlesize": 10.5,
    "axes.titleweight": "bold",
    "axes.labelcolor": PALETTE["ink"],
    "axes.edgecolor": PALETTE["muted"],
    "axes.linewidth": 0.8,
    "axes.grid": True,
    "axes.axisbelow": True,
    "grid.color": PALETTE["grid"],
    "grid.linewidth": 0.7,
    "lines.linewidth": 1.5,
    "lines.markersize": 4.5,
    "xtick.color": PALETTE["ink"],
    "ytick.color": PALETTE["ink"],
    "xtick.labelsize": 8.5,
    "ytick.labelsize": 8.5,
    "legend.fontsize": 8.0,
    "legend.framealpha": 0.9,
    "figure.facecolor": "white",
    "savefig.dpi": DPI,
    "savefig.format": "png",
    "savefig.bbox": "tight",
    "axes.prop_cycle": cycler(color=list(CYCLE)),
}


def _backend_explicitly_chosen() -> bool:
    """True when the caller picked a backend through env, matplotlibrc or ``matplotlib.use``."""
    if os.environ.get("MPLBACKEND"):
        return True
    origin = getattr(matplotlib, "get_backend_origin", None)
    if callable(origin):
        with contextlib.suppress(Exception):
            return str(origin()) != "default"
    with contextlib.suppress(Exception):
        return str(matplotlib.rcParams["backend"]) != str(matplotlib.rcParamsDefault["backend"])
    return False


def ensure_headless_backend() -> str:
    """Select ``Agg`` unless the caller already picked a backend on purpose.

    An explicit ``MPLBACKEND``/matplotlibrc/``matplotlib.use`` choice is respected, as is a
    live plotting session that already has open figures; everything else is forced headless
    so the library never tries to open a window.
    """
    if _backend_explicitly_chosen():
        return str(matplotlib.get_backend())
    pyplot = sys.modules.get("matplotlib.pyplot")
    if pyplot is not None:
        with contextlib.suppress(Exception):
            if pyplot.get_fignums():
                return str(matplotlib.get_backend())
    matplotlib.use("Agg", force=True)
    return str(matplotlib.get_backend())


def apply_theme() -> None:
    """Push :data:`_THEME_PARAMS` and register the project colormap (idempotent)."""
    matplotlib.rcParams.update(cast("Any", _THEME_PARAMS))
    if CMAP_NAME not in colormaps:
        colormaps.register(mplcolors.LinearSegmentedColormap.from_list(CMAP_NAME, list(_CMAP_STOPS)))


ensure_headless_backend()
apply_theme()

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.axes import Axes  # noqa: E402


def section_colormap() -> mplcolors.Colormap:
    apply_theme()
    return cast("mplcolors.Colormap", colormaps[CMAP_NAME])


def axes_grid(rows: int, cols: int, *, width: float = 6.6, height: float = 3.6) -> tuple[Figure, list[Axes]]:
    """Return a figure holding ``rows * cols`` panels of roughly ``width`` x ``height`` inches."""
    fig, axes = plt.subplots(rows, cols, figsize=(width * cols, height * rows))
    panels = [axes] if rows == cols == 1 else [cast("Axes", a) for a in _flatten_axes(axes)]
    return cast("Figure", fig), panels


def _flatten_axes(axes: Any) -> list[Any]:
    """Flatten matplotlib's arbitrary axes container into a list."""
    try:
        return list(axes.flat)
    except AttributeError:
        return [axes]


def panel_colors(count: int) -> list[str]:
    """Sample the deterministic station colormap ``count`` times (trailing-edge thickness ramp)."""
    cmap = section_colormap()
    if count <= 1:
        return ["#1f7a8c"]
    return [mplcolors.to_hex(cmap(i / (count - 1))) for i in range(count)]


def ramp_color(value: float, low: float, high: float, *, invert: bool = False) -> str:
    """Map ``value`` inside ``[low, high]`` onto the station colormap."""
    cmap = section_colormap()
    span = high - low
    frac = 0.5 if span <= 0 else min(1.0, max(0.0, (value - low) / span))
    if invert:
        frac = 1.0 - frac
    return str(mplcolors.to_hex(cmap(frac)))


def save_figure(fig: Figure, path: str | Path, *, note: str | None = None) -> Path:
    """Write ``path`` as a 150 dpi PNG, release the figure and return the path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if note is not None:
        fig.text(0.5, 0.015, note, ha="center", va="bottom", color=PALETTE["muted"], fontsize=8)
    with contextlib.suppress(Exception):
        fig.tight_layout()
    fig.savefig(target, dpi=DPI)
    plt.close(fig)
    return target


def note_figure(title: str, message: str, path: str | Path) -> Path:
    """Draw an explicit placeholder panel so a missing input degrades visibly, not loudly."""
    fig, panels = axes_grid(1, 1, width=6.6, height=3.0)
    ax = panels[0]
    ax.set_title(title)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_facecolor(PALETTE["panel"])
    ax.text(0.5, 0.55, message, ha="center", va="center", wrap=True, color=PALETTE["ink"], fontsize=9.5)
    return save_figure(fig, path)


__all__ = [
    "CMAP_NAME",
    "CYCLE",
    "DPI",
    "PALETTE",
    "apply_theme",
    "axes_grid",
    "ensure_headless_backend",
    "note_figure",
    "panel_colors",
    "ramp_color",
    "save_figure",
    "section_colormap",
]
