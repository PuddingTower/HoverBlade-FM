"""Tests for the single-page HTML report (task T067)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from viz_fixtures import make_constraints, make_design, make_history, make_performance, make_provenance

from hoverblade_fm.viz.plots import generate_all_visuals
from hoverblade_fm.viz.report import format_number, render_report


@pytest.fixture
def visuals(tmp_path: Path) -> dict[str, Path]:
    design, performance = make_design(), make_performance()
    paths = generate_all_visuals(
        design, performance, make_constraints(design), make_history(), make_provenance(), tmp_path
    )
    assert paths["report"].is_file()
    return paths


def test_report_is_one_self_contained_file(visuals: dict[str, Path]) -> None:
    report = visuals["report"]
    text = report.read_text(encoding="utf-8")
    assert report.stat().st_size > 50_000
    assert text.startswith("<!DOCTYPE html>")
    assert "data:image/png;base64," in text
    assert text.count("<img") >= 5
    assert "http://" not in text and "https://" not in text
    assert "<script" not in text.lower()
    assert "<link" not in text.lower()


def test_report_shows_the_headline_performance_numbers(visuals: dict[str, Path]) -> None:
    text = visuals["report"].read_text(encoding="utf-8")
    performance = make_performance()
    fm = format_number(performance.figure_of_merit, 4)
    assert f"Figure of Merit</span><b>{fm}</b>" in text
    assert f"{fm}" in text
    for label in (
        "P_ideal",
        "P_induced",
        "P_profile",
        "P_swirl",
        "P_total",
        "Shaft torque",
        "Tip Mach",
        "Solidity",
        "Thrust error",
    ):
        assert label in text


def test_blunt_trailing_edge_proof_is_prominent_and_passes(visuals: dict[str, Path]) -> None:
    text = visuals["report"].read_text(encoding="utf-8")
    assert "Blunt trailing-edge proof" in text
    assert "CONSTRAINT SATISFIED" in text
    assert text.count('badge ok">PASS') >= 10
    assert 'badge bad">FAIL' not in text
    assert "required t_TE [m]" in text and "margin [m]" in text


def test_sharp_edge_design_fails_the_proof(tmp_path: Path) -> None:
    design = make_design(te_scale=0.4)
    out = render_report(
        design,
        make_performance(design),
        make_constraints(design, violated=True),
        make_provenance(),
        {},
        tmp_path / "r.html",
    )
    text = out.read_text(encoding="utf-8")
    assert "CONSTRAINT VIOLATED" in text
    assert text.count('badge bad">FAIL') >= 10
    assert "class='proof fail'" in text or 'class="proof fail"' in text


def test_station_table_lists_all_ten_sections(visuals: dict[str, Path]) -> None:
    text = visuals["report"].read_text(encoding="utf-8")
    body = text.split("Radial station data", 1)[1]
    rows = re.findall(r"<tr><td>\d</td>", body)
    assert len(rows) == 10
    for header in ("chord [m]", "twist [deg]", "t/c", "Re", "c_l", "c_d"):
        assert header in body


def test_inputs_and_auto_resolved_reasons_are_reported(visuals: dict[str, Path]) -> None:
    text = visuals["report"].read_text(encoding="utf-8")
    assert "220.000 N" in text and "0.8500 m" in text and "0.00150 m" in text
    assert "disk loading chose 2 blades" in text
    assert "tip Mach limited to 0.55" in text
    assert "transonic cushion" in text


def test_provenance_footer_is_present(visuals: dict[str, Path]) -> None:
    text = visuals["report"].read_text(encoding="utf-8")
    for token in ("0.1.0", "8f3c1d2", "a1b2c3d4e5f60718", "1234", "2026-02-11T09:41:03Z"):
        assert token in text


def test_nan_and_none_never_reach_the_html(tmp_path: Path) -> None:
    design = make_design()
    performance = make_performance(
        design, radial=False, nan_fields=("tip_mach", "figure_of_merit", "torque_nm")
    )
    out = render_report(design, performance, None, None, {}, tmp_path / "nan.html")
    text = out.read_text(encoding="utf-8")
    assert re.search(r"\bnan\b", text, flags=re.I) is None
    assert "n/a" in text
    assert "No ConstraintReport was supplied" in text
    assert "no radial distribution" in text


def test_user_supplied_strings_are_escaped(tmp_path: Path) -> None:
    design = make_design()
    design.label = "<script>alert('x')</script>"
    provenance = make_provenance(
        resolved_config={"warnings": ["</body></html> injected"], "auto_reasons": ["<b>bold</b>"]}
    )
    out = render_report(design, make_performance(design), None, provenance, {}, tmp_path / "esc.html")
    text = out.read_text(encoding="utf-8")
    assert "<script>alert" not in text
    assert "&lt;script&gt;alert" in text
    assert "&lt;/body&gt;&lt;/html&gt; injected" in text
    assert "<b>bold</b>" not in text


def test_missing_plot_files_degrade_to_notes(tmp_path: Path) -> None:
    design = make_design()
    out = render_report(
        design,
        make_performance(design),
        None,
        make_provenance(),
        {"chord_twist": tmp_path / "does_not_exist.png"},
        tmp_path / "sub" / "report.html",
    )
    text = out.read_text(encoding="utf-8")
    assert out.parent == tmp_path / "sub"
    assert "was not generated" in text


def test_provenance_object_is_accepted(tmp_path: Path) -> None:
    from viz_fixtures import make_provenance_object

    design = make_design()
    out = render_report(
        design, make_performance(design), None, make_provenance_object(), {}, tmp_path / "obj.html"
    )
    text = out.read_text(encoding="utf-8")
    assert "8f3c1d2" in text
