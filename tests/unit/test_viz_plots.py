"""Tests for the static PNG visuals (tasks T062-T066)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import pytest
from viz_fixtures import make_constraints, make_design, make_history, make_performance, make_provenance

from hoverblade_fm.viz.plots import (
    generate_all_visuals,
    plot_airfoil_stack,
    plot_chord_twist,
    plot_fm_convergence,
    plot_radial_performance,
    plot_sections_preview,
    plot_trailing_edge_zoom,
    te_gap_check,
)
from hoverblade_fm.viz.theme import CYCLE, DPI, PALETTE, ramp_color

PNG_KEYS = (
    "chord_twist",
    "airfoil_stack",
    "trailing_edge_zoom",
    "fm_convergence",
    "radial_performance",
    "sections_preview",
)


def test_backend_is_forced_headless() -> None:
    assert matplotlib.get_backend().lower() == "agg"
    assert DPI == 150


def test_theme_palette_is_small_and_deterministic() -> None:
    assert len(PALETTE) <= 24
    assert all(color.startswith("#") for color in PALETTE.values())
    assert ramp_color(0.002, 0.001, 0.003) == ramp_color(0.002, 0.001, 0.003)
    assert ramp_color(0.001, 0.001, 0.003) != ramp_color(0.003, 0.001, 0.003)
    assert PALETTE["chord"] in CYCLE


@pytest.mark.parametrize(
    ("func", "uses_performance"),
    [
        (plot_chord_twist, False),
        (plot_airfoil_stack, False),
        (plot_trailing_edge_zoom, False),
        (plot_sections_preview, False),
        (plot_radial_performance, True),
    ],
)
def test_each_plot_writes_a_real_png(func, uses_performance: bool, tmp_path: Path) -> None:
    design = make_design()
    payload = make_performance(design) if uses_performance else design
    out = func(payload, tmp_path / "nested" / f"{func.__name__}.png")
    assert out == tmp_path / "nested" / f"{func.__name__}.png"
    assert out.is_file()
    assert out.stat().st_size > 5_000


def test_generate_all_visuals_writes_the_planned_layout(tmp_path: Path) -> None:
    design, performance = make_design(), make_performance()
    paths = generate_all_visuals(
        design, performance, make_constraints(design), make_history(), make_provenance(), tmp_path
    )

    assert set(paths) >= {*PNG_KEYS, "report", "interactive"}
    for key in PNG_KEYS:
        target = paths[key]
        assert target.parent == tmp_path / "plots"
        assert target.name == f"{key}.png"
        assert target.is_file() and target.stat().st_size > 5_000
    assert paths["report"] == tmp_path / "report.html"
    assert paths["interactive"] == tmp_path / "interactive" / "blade.html"
    assert paths["interactive"].is_file() and paths["report"].is_file()


def test_generate_all_visuals_tolerates_missing_optional_data(tmp_path: Path) -> None:
    design = make_design()
    performance = make_performance(design, radial=False)
    paths = generate_all_visuals(design, performance, None, None, None, tmp_path)
    assert set(paths) >= {*PNG_KEYS, "report", "interactive"}
    for key in PNG_KEYS:
        assert paths[key].is_file() and paths[key].stat().st_size > 3_000


def test_figures_are_always_closed(tmp_path: Path) -> None:
    assert plt.get_fignums() == []
    generate_all_visuals(make_design(), make_performance(), None, [], None, tmp_path)
    assert plt.get_fignums() == []


def test_fm_convergence_reads_history_columns(tmp_path: Path) -> None:
    out = plot_fm_convergence(make_history(count=12), tmp_path / "fm.png")
    assert out.is_file() and out.stat().st_size > 5_000


def test_fm_convergence_placeholder_for_empty_history(tmp_path: Path) -> None:
    for empty in ([], None, [{"evaluation": 1}]):
        out = plot_fm_convergence(empty, tmp_path / "fm_empty.png")  # type: ignore[arg-type]
        assert out.is_file() and out.stat().st_size > 3_000


def test_radial_performance_skips_missing_arrays(tmp_path: Path) -> None:
    performance = make_performance()
    assert performance.radial is not None
    performance.radial.thrust_gradient = performance.radial.thrust_gradient[:3]
    performance.radial.induced_velocity = None  # type: ignore[assignment]
    out = plot_radial_performance(performance, tmp_path / "radial.png")
    assert out.is_file() and out.stat().st_size > 5_000


def test_design_must_have_ten_sections(tmp_path: Path) -> None:
    design = make_design()
    design.sections.pop()
    with pytest.raises(ValueError, match="10 sections"):
        plot_chord_twist(design, tmp_path / "nope.png")
    with pytest.raises(ValueError, match="10 sections"):
        generate_all_visuals(design, make_performance(), None, None, None, tmp_path)


def test_trailing_edge_check_flags_a_sharp_edge(tmp_path: Path) -> None:
    good = make_design()
    assert all(te_gap_check(section, good.te_min_m)[2] for section in good.sections)
    sharp = make_design(te_scale=0.4)
    assert not any(te_gap_check(section, sharp.te_min_m)[2] for section in sharp.sections)
    measured, required, ok = te_gap_check(sharp.sections[0], sharp.te_min_m)
    assert measured < required and not ok
    out = plot_trailing_edge_zoom(sharp, tmp_path / "te_sharp.png")
    assert out.is_file() and out.stat().st_size > 5_000


def test_zoom_accepts_an_explicit_required_thickness(tmp_path: Path) -> None:
    out = plot_trailing_edge_zoom(make_design(), tmp_path / "te.png", te_min_m=0.0008)
    assert out.is_file()


def test_design_constructor_enforces_ten_sections() -> None:
    with pytest.raises(ValueError, match="10 sections"):
        make_design(sections=4)


def test_rendering_is_deterministic(tmp_path: Path) -> None:
    design = make_design()
    digests = {
        hashlib.sha256(plot_chord_twist(design, tmp_path / f"ct_{run}.png").read_bytes()).hexdigest()
        for run in (0, 1)
    }
    assert len(digests) == 1


def test_history_column_names_are_discovered(tmp_path: Path) -> None:
    aliases = [
        {"nev": 10, "fm": 0.60, "cost": 1.2},
        {"nev": 20, "fm": 0.65, "cost": 1.1},
        {"nev": 30, "fm": 0.70, "cost": 1.0},
    ]
    staged = [
        {"evaluation": 1, "figure_of_merit": 0.55, "stage": "initial"},
        {"evaluation": 2, "figure_of_merit": 0.62},
    ]
    for index, history in enumerate((aliases, staged)):
        out = plot_fm_convergence(history, tmp_path / f"fm_{index}.png")
        assert out.is_file() and out.stat().st_size > 5_000


def test_output_paths_may_be_strings(tmp_path: Path) -> None:
    out = plot_chord_twist(make_design(), str(tmp_path / "as_string.png"))
    assert isinstance(out, Path) and out.is_file()
