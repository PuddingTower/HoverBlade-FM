"""Visualisation layer: PNG plots, the single-page HTML report and the 3D blade viewer.

Public API (used by :mod:`hoverblade_fm.cli`)::

    generate_all_visuals(design, performance, constraints, history, provenance, out_dir)
    render_report(design, performance, constraints, provenance, plot_paths, out_path)
    build_interactive_html(design, performance, out_path)

All matplotlib work is headless (``Agg``), figures are closed after saving and no visual
raises on missing optional data: it degrades to a labelled placeholder instead.
"""

from __future__ import annotations

from hoverblade_fm.viz.interactive import POINTS_PER_SURFACE, blade_payload, build_interactive_html
from hoverblade_fm.viz.plots import (
    PLOT_NAMES,
    MissingVisualData,
    generate_all_visuals,
    plot_airfoil_stack,
    plot_chord_twist,
    plot_fm_convergence,
    plot_radial_performance,
    plot_sections_preview,
    plot_trailing_edge_zoom,
)
from hoverblade_fm.viz.report import render_report
from hoverblade_fm.viz.theme import DPI, PALETTE, ensure_headless_backend

__all__ = [
    "DPI",
    "PALETTE",
    "PLOT_NAMES",
    "POINTS_PER_SURFACE",
    "MissingVisualData",
    "blade_payload",
    "build_interactive_html",
    "ensure_headless_backend",
    "generate_all_visuals",
    "plot_airfoil_stack",
    "plot_chord_twist",
    "plot_fm_convergence",
    "plot_radial_performance",
    "plot_sections_preview",
    "plot_trailing_edge_zoom",
    "render_report",
]
