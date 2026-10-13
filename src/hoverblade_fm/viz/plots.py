"""Static PNG visuals for a hover blade design (plan section 12.1, tasks T062-T066).

Every plotting function is independently importable and takes an output path, so a caller
can render one figure without pulling in the report or the interactive viewer.  Missing
optional input never raises: the figure becomes an explicit placeholder panel instead.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TypeGuard

import numpy as np
from matplotlib.axes import Axes

from hoverblade_fm.models import SECTION_COUNT, Design, Performance
from hoverblade_fm.viz.theme import PALETTE, axes_grid, note_figure, panel_colors, save_figure

#: Window of the trailing-edge zoom, in chord fractions (plan section 12.1.3).
TE_ZOOM_X0 = 0.90
TE_ZOOM_X1 = 1.02
#: Chord fraction where the blunt-gap reference is measured (plan section 5.5).
TE_GAP_PROBE_X = 0.995

_FM_KEYS = ("figure_of_merit", "fm", "best_fm", "fm_best", "fitness_fm")
_OBJ_KEYS = ("objective", "best_objective", "obj", "cost", "loss", "fitness")
_EVAL_KEYS = ("evaluation", "evaluations", "eval", "nev", "n_eval", "nfe", "iteration", "generation")
_FM_INITIAL_KEYS = ("fm_initial", "initial_fm", "fm0", "figure_of_merit_initial", "fm_start")


class MissingVisualData(ValueError):
    """Raised when a required (not optional) design payload is unusable."""


def _require_design(design: Design) -> Design:
    if len(design.sections) != SECTION_COUNT:
        raise MissingVisualData(f"a design must carry exactly {SECTION_COUNT} sections")
    if not np.all(np.isfinite(design.chord_m)) or np.any(design.chord_m <= 0.0):
        raise MissingVisualData("every section needs a positive finite chord length")
    return design


def _is_finite(value: float | None) -> TypeGuard[float]:
    return value is not None and bool(np.isfinite(value))


def _mm(value: float | None) -> str:
    """Millimetre text for annotations; never lets a NaN reach the image."""
    return "n/a" if not _is_finite(value) else f"{1000.0 * value:.2f} mm"


def _num(value: float | None, digits: int = 4) -> str:
    if not _is_finite(value):
        return "n/a"
    return f"{value:.{digits}g}"


def _first_key(record: Mapping[str, Any], names: Sequence[str]) -> str | None:
    for name in names:
        if name in record and record[name] is not None:
            return name
    return None


def plot_chord_twist(design: Design, path: str | Path) -> Path:
    """Chord and geometric twist versus r/R on twin axes (T062)."""
    _require_design(design)
    fig, (ax,) = axes_grid(1, 1)
    r_R, chord, twist = design.r_R, design.chord_m, design.twist_deg

    ax.plot(r_R, 1000.0 * chord, "o-", color=PALETTE["chord"], label="chord c [mm]")
    ax.set_xlabel("r / R")
    ax.set_ylabel("chord [mm]", color=PALETTE["chord"])
    ax.tick_params(axis="y", colors=PALETTE["chord"])
    ax.set_xlim(float(min(r_R)) - 0.03, 1.0)

    ax2 = ax.twinx()
    ax2.plot(r_R, twist, "s--", color=PALETTE["twist"], label="twist [deg]")
    ax2.set_ylabel("twist [deg]", color=PALETTE["twist"])
    ax2.tick_params(axis="y", colors=PALETTE["twist"])
    ax2.grid(False)

    handles, labels = ax.get_legend_handles_labels()
    extra, extra_labels = ax2.get_legend_handles_labels()
    ax.legend(handles + extra, labels + extra_labels, loc="center right")
    ax.set_title(
        f"Chord / twist loading — R={_num(design.radius_m, 3)} m, "
        f"{design.blade_count} blade(s), {_num(design.rpm, 4)} RPM, solidity={_num(design.solidity, 3)}"
    )
    return save_figure(fig, path)


def _station_profile(section: Any) -> tuple[np.ndarray, np.ndarray]:
    """Section outline in metres: chordwise axis, profile deviation drawn on the radial axis."""
    pts = section.points.in_blade_frame(section.chord_m, section.twist_deg, 0.0)
    return pts[:, 0], pts[:, 2]


def plot_airfoil_stack(design: Design, path: str | Path) -> Path:
    """All 10 sections at their true chord scale, placed at their radial station (T063)."""
    _require_design(design)
    fig, panels = axes_grid(1, 1, width=6.6, height=5.4)
    ax = panels[0]
    colors = panel_colors(len(design.sections))

    for section, color in zip(design.sections, colors, strict=True):
        x, z = _station_profile(section)
        ax.plot(x, z + section.r_m, color=color, linewidth=1.1)

    ax.set_xlabel("chordwise x [m] (origin at 25% chord)")
    ax.set_ylabel("radial station r [m]")
    ax.set_title(f"Airfoil stack — {len(design.sections)} stations, blunt TE {_mm(design.te_min_m)} minimum")
    ax.axhline(0.0, color=PALETTE["grid"], linewidth=0.8)
    r_ticks = np.array([s.r_m for s in design.sections])
    ax.set_yticks(np.round(r_ticks, 3))
    ax.set_yticklabels([f"{s.r_R:.3f}" for s in design.sections])
    ax.set_ylim(-0.02 * design.radius_m, design.radius_m * 1.04)
    ax.set_aspect("equal", adjustable="box")
    return save_figure(fig, path)


def plot_trailing_edge_zoom(design: Design, path: str | Path, *, te_min_m: float | None = None) -> Path:
    """Zoom on x/c 0.90-1.02 of every section with the blunt gap and the required minimum (T064)."""
    _require_design(design)
    required = design.te_min_m if te_min_m is None else float(te_min_m)
    fig, panels = axes_grid(SECTION_COUNT, 1, width=6.6, height=1.05)
    fig.subplots_adjust(hspace=0.42)

    for ax, section in zip(panels, design.sections, strict=True):
        _draw_te_zoom_panel(ax, section, required)

    fig.suptitle(
        "Blunt trailing-edge proof — measured gap (red arrow) versus required minimum (dashed lines)"
    )
    return save_figure(fig, path)


def te_gap_check(section: Any, required_m: float) -> tuple[float, float, bool]:
    """Return ``(measured gap [m], required [m], satisfied)`` at the manufacturing probe."""
    measured = section.points.trailing_edge_gap_at(TE_GAP_PROBE_X) * section.chord_m
    return measured, required_m, bool(measured >= required_m - 1e-12)


def _draw_te_zoom_panel(ax: Axes, section: Any, required_m: float) -> None:
    """One station's blunt gap, plotted about the local midline so the gap is the message."""
    x_lower, y_lower, x_upper, y_upper = section.points.surface_pairs()
    keep = x_upper >= TE_ZOOM_X0
    x = x_upper[keep]
    lower = np.interp(x, x_lower, y_lower)
    upper = y_upper[keep]
    mid = 0.5 * (lower + upper)
    lower, upper = lower - mid, upper - mid

    required_ttc = required_m / section.chord_m if section.chord_m > 0 else float("nan")
    _, _, ok = te_gap_check(section, required_m)
    gap = section.points.te_thickness
    half_required = 0.5 * (required_ttc if np.isfinite(required_ttc) else gap)
    span = max(float(np.max(np.abs(upper))), float(np.max(np.abs(lower))), 1.15 * half_required, 1e-9)

    ax.fill_between(
        x, lower, upper, color=PALETTE["pass"] if ok else PALETTE["fail"], alpha=0.35, linewidth=0
    )
    ax.plot(x, lower, color=PALETTE["ink"], linewidth=1.2)
    ax.plot(x, upper, color=PALETTE["ink"], linewidth=1.2)
    ax.axhline(0.0, color=PALETTE["muted"], linewidth=0.6, alpha=0.7)
    if np.isfinite(required_ttc):
        ax.plot(
            [TE_ZOOM_X0, TE_ZOOM_X1], [half_required] * 2, color=PALETTE["te"], linestyle="--", linewidth=1.1
        )
        ax.plot(
            [TE_ZOOM_X0, TE_ZOOM_X1], [-half_required] * 2, color=PALETTE["te"], linestyle="--", linewidth=1.1
        )
    ax.axvline(1.0, color=PALETTE["muted"], linestyle=":", linewidth=0.9)
    ax.annotate(
        "",
        xy=(1.0, 0.5 * gap),
        xytext=(1.0, -0.5 * gap),
        arrowprops=dict(arrowstyle="<->", color=PALETTE["te"], linewidth=1.1),
    )
    ax.text(
        1.004,
        0.0,
        _mm(section.te_thickness_m),
        va="center",
        ha="left",
        fontsize=7.5,
        color=PALETTE["te"],
        bbox=dict(facecolor="white", edgecolor="none", alpha=0.9, pad=1.2),
    )

    ax.set_xlim(TE_ZOOM_X0, TE_ZOOM_X1)
    ax.set_ylim(-1.3 * span, 1.3 * span)
    ax.set_xlabel("x / c", fontsize=7.5)
    ax.tick_params(labelsize=7.0)
    badge = "PASS" if ok else "FAIL"
    ax.set_title(
        f"r/R={section.r_R:.3f}  measured t_TE={_mm(section.te_thickness_m)} ({gap:.4f} c)   "
        f"required {_mm(required_m)} ({required_ttc:.4f} c)   "
        f"angle={_num(section.te_included_angle_deg, 3)} deg  [{badge}]",
        fontsize=7.8,
        color=PALETTE["pass"] if ok else PALETTE["fail"],
    )


def _history_series(
    history: Sequence[Mapping[str, Any]],
) -> tuple[np.ndarray, np.ndarray | None, np.ndarray | None]:
    eval_key = _first_key(history[0], _EVAL_KEYS)
    fm_key = _first_key(history[0], _FM_KEYS)
    obj_key = _first_key(history[0], _OBJ_KEYS)
    count = len(history)
    x = (
        np.array([float(record.get(eval_key, i)) for i, record in enumerate(history)], dtype=float)
        if eval_key
        else np.arange(count, dtype=float)
    )
    fm = (
        np.array(
            [
                float(record[fm_key]) if fm_key and record.get(fm_key) is not None else math.nan
                for record in history
            ]
        )
        if fm_key
        else None
    )
    obj = (
        np.array(
            [
                float(record[obj_key]) if obj_key and record.get(obj_key) is not None else math.nan
                for record in history
            ]
        )
        if obj_key
        else None
    )
    return x, fm, obj


def _initial_fm(history: Sequence[Mapping[str, Any]]) -> tuple[float | None, str]:
    """Recover the initial-design FM from the history, if it is recorded there."""
    for record in history:
        key = _first_key(record, _FM_INITIAL_KEYS)
        if key:
            value = record[key]
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                return float(value), "initial design FM"
    for record in history:
        stage = record.get("stage", record.get("kind", record.get("tag", "")))
        if isinstance(stage, str) and "initial" in stage.lower():
            key = _first_key(record, _FM_KEYS)
            if key and record.get(key) is not None:
                return float(record[key]), "initial design FM"
    _, fm, _ = _history_series(history)
    if fm is not None:
        finite = fm[np.isfinite(fm)]
        if finite.size:
            return float(finite[0]), "FM of first recorded evaluation"
    return None, ""


def plot_fm_convergence(history: Sequence[Mapping[str, Any]] | None, path: str | Path) -> Path:
    """FM and objective versus evaluations plus the initial-design reference (T065)."""
    records = list(history or [])
    if not records:
        return note_figure(
            "FM convergence",
            "No optimisation history was supplied — run the optimiser to populate this plot.",
            path,
        )

    x, fm, obj = _history_series(records)
    has_curve = fm is not None and bool(np.any(np.isfinite(fm)))
    if not has_curve and obj is None:
        return note_figure(
            "FM convergence",
            "The supplied history carries neither an FM nor an objective column.",
            path,
        )

    fig, panels = axes_grid(1, 1)
    ax = panels[0]
    if fm is not None:
        ax.plot(x, fm, "-", color=PALETTE["fm"], alpha=0.45, linewidth=1.1, label="FM per evaluation")
        mask = np.isfinite(fm)
        if np.any(mask):
            best = np.maximum.accumulate(np.where(mask, fm, -np.inf))
            ax.plot(x[mask], best[mask], "-", color=PALETTE["fm"], linewidth=1.8, label="best FM so far")
    ax.set_xlabel("evaluations")
    ax.set_ylabel("Figure of Merit", color=PALETTE["fm"])
    ax.tick_params(axis="y", colors=PALETTE["fm"])

    initial, label = _initial_fm(records)
    if initial is not None:
        ax.axhline(initial, color=PALETTE["objective"], linestyle="--", linewidth=1.2, label=label)

    if obj is not None and np.any(np.isfinite(obj)):
        ax2 = ax.twinx()
        ax2.plot(x, obj, "-", color=PALETTE["objective"], alpha=0.7, linewidth=1.2, label="objective")
        ax2.set_ylabel("objective", color=PALETTE["objective"])
        ax2.tick_params(axis="y", colors=PALETTE["objective"])
        ax2.grid(False)
        handles, labels = ax.get_legend_handles_labels()
        extra, extra_labels = ax2.get_legend_handles_labels()
        ax.legend(handles + extra, labels + extra_labels, loc="lower right")
    elif fm is not None:
        ax.legend(loc="lower right")

    ax.set_title(f"Hover FM convergence — {len(records)} recorded evaluations")
    return save_figure(fig, path)


_PANEL_SPECS: tuple[tuple[str, str, str, str, float], ...] = (
    ("thrust_gradient", "dT/dr [N/m]", "thrust loading", PALETTE["thrust"], 1.0),
    ("power_gradient", "dP/dr [W/m]", "power loading", PALETTE["power"], 1.0),
    ("induced_velocity", "v_i [m/s]", "induced velocity", PALETTE["induced"], 1.0),
)


def _panel_from_array(
    ax: Axes, r_R: np.ndarray, values: np.ndarray | None, color: str, ylabel: str, scale: float
) -> bool:
    if values is None:
        return False
    arr = np.asarray(values, dtype=float)
    if arr.shape != r_R.shape or not np.any(np.isfinite(arr)):
        return False
    ax.plot(r_R, scale * arr, "-", color=color, linewidth=1.6)
    ax.set_xlabel("r / R")
    ax.set_ylabel(ylabel, color=color)
    ax.tick_params(axis="y", colors=color)
    ax.set_xlim(0.0, 1.0)
    return True


def plot_radial_performance(performance: Performance, path: str | Path) -> Path:
    """Spanwise loading panels; absent quantities are left as notes instead of raising (T066)."""
    radial = performance.radial
    if radial is None:
        return note_figure(
            "Radial performance",
            "The BEMT solver returned no radial distribution (performance.radial is None).",
            path,
        )

    r_R = np.asarray(radial.r_R, dtype=float)
    fig, panels = axes_grid(2, 2, width=6.4, height=3.1)
    drawn = 0
    for ax, (attr, ylabel, name, color, scale) in zip(panels[:3], _PANEL_SPECS, strict=True):
        values = getattr(radial, attr, None)
        if _panel_from_array(ax, r_R, values, color, ylabel, scale):
            ax.set_title(name)
            drawn += 1
        else:
            _empty_panel(ax, f"{name}: {attr} unavailable")

    ax = panels[3]
    cl_ok = _panel_from_array(ax, r_R, radial.cl, PALETTE["cl"], "c_l [-]", 1.0)
    if cl_ok:
        ax2 = ax.twinx()
        if _panel_from_array(ax2, r_R, radial.aoa_deg, PALETTE["alpha"], "alpha_eff [deg]", 1.0):
            ax2.grid(False)
            ax.set_title("section lift and angle of attack")
            drawn += 1
        else:
            ax2.remove()
            _empty_panel(ax, "angle of attack unavailable")
    else:
        _empty_panel(ax, "section lift coefficient unavailable")

    if drawn == 0:
        return note_figure(
            "Radial performance",
            "The radial distribution carried no usable arrays.",
            path,
        )
    fig.suptitle(
        f"Spanwise hover loading — FM={_num(performance.figure_of_merit, 4)}, "
        f"T={_num(performance.thrust_calc_n, 5)} N, tip M={_num(performance.tip_mach, 3)}",
        y=1.0,
    )
    return save_figure(fig, path)


def _empty_panel(ax: Axes, message: str) -> None:
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_facecolor(PALETTE["panel"])
    ax.text(0.5, 0.5, message, ha="center", va="center", wrap=True, fontsize=8, color=PALETTE["muted"])


def plot_sections_preview(design: Design, path: str | Path) -> Path:
    """Small-multiples preview of the 10 generated sections (extra visual)."""
    _require_design(design)
    fig, panels = axes_grid(2, 5, width=2.6, height=2.6)
    colors = panel_colors(len(design.sections))
    for ax, section, color in zip(panels, design.sections, colors, strict=True):
        x_lower, y_lower, x_upper, y_upper = section.points.surface_pairs()
        ax.fill_between(x_lower, y_lower, y_upper, color=color, alpha=0.25, linewidth=0)
        ax.plot(x_lower, y_lower, color=color, linewidth=0.9)
        ax.plot(x_upper, y_upper, color=color, linewidth=0.9)
        ax.set_xlim(0.0, 1.0)
        ax.set_ylim(-0.3, 0.3)
        ax.set_aspect("equal")
        ax.grid(False)
        ax.set_title(f"r/R={section.r_R:.2f}", fontsize=8)
        ax.set_xlabel(
            f"c={_num(1000 * section.chord_m, 4)} mm\nt_TE={_num(1000 * section.te_thickness_m, 3)} mm",
            fontsize=7,
        )
    fig.suptitle("Generated airfoil sections", y=1.0)
    return save_figure(fig, path)


PLOT_NAMES: tuple[str, ...] = (
    "chord_twist",
    "airfoil_stack",
    "trailing_edge_zoom",
    "fm_convergence",
    "radial_performance",
    "sections_preview",
)


def generate_all_visuals(
    design: Design,
    performance: Performance,
    constraints: Any | None = None,
    history: Sequence[Mapping[str, Any]] | None = None,
    provenance: Mapping[str, Any] | None = None,
    out_dir: str | Path = "output",
) -> dict[str, Path]:
    """Render every PNG, the single-page report and the interactive viewer into ``out_dir``."""
    root = Path(out_dir)
    plots = root / "plots"
    records = list(history) if history else None

    paths: dict[str, Path] = {
        "chord_twist": plot_chord_twist(design, plots / "chord_twist.png"),
        "airfoil_stack": plot_airfoil_stack(design, plots / "airfoil_stack.png"),
        "trailing_edge_zoom": plot_trailing_edge_zoom(design, plots / "trailing_edge_zoom.png"),
        "fm_convergence": plot_fm_convergence(records, plots / "fm_convergence.png"),
        "radial_performance": plot_radial_performance(performance, plots / "radial_performance.png"),
        "sections_preview": plot_sections_preview(design, plots / "sections_preview.png"),
    }

    from hoverblade_fm.viz.interactive import build_interactive_html
    from hoverblade_fm.viz.report import render_report

    paths["interactive"] = build_interactive_html(design, performance, root / "interactive")
    paths["report"] = render_report(design, performance, constraints, provenance, paths, root / "report.html")
    return paths


__all__ = [
    "PLOT_NAMES",
    "MissingVisualData",
    "generate_all_visuals",
    "plot_airfoil_stack",
    "plot_chord_twist",
    "plot_fm_convergence",
    "plot_radial_performance",
    "plot_sections_preview",
    "plot_trailing_edge_zoom",
    "te_gap_check",
]
