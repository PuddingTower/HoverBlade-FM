"""Tests for the dependency-free interactive 3D viewer (task T068)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from viz_fixtures import make_design, make_performance

from hoverblade_fm.viz.interactive import POINTS_PER_SURFACE, blade_payload, build_interactive_html

SCRIPT_RE = re.compile(r"<script[^>]*>(.*?)</script>", flags=re.S)


@pytest.fixture
def page(tmp_path: Path) -> tuple[Path, str]:
    design, performance = make_design(), make_performance()
    out = build_interactive_html(design, performance, tmp_path / "interactive")
    assert out == tmp_path / "interactive" / "blade.html"
    assert out.is_file() and out.stat().st_size > 10_000
    return out, out.read_text(encoding="utf-8")


def test_viewer_is_written_inside_an_interactive_directory(page: tuple[Path, str]) -> None:
    out, text = page
    assert out.parent.name == "interactive"
    assert text.startswith("<!DOCTYPE html>")


def test_viewer_never_reaches_the_network(page: tuple[Path, str]) -> None:
    _, text = page
    assert "http://" not in text and "https://" not in text
    assert "cdn" not in text.lower() and "unpkg" not in text.lower() and "jsdelivr" not in text.lower()
    assert not re.search(r"<script[^>]+src=", text, flags=re.I)
    assert "<link" not in text.lower()
    assert "@import" not in text


def test_geometry_json_is_embedded_and_parseable(page: tuple[Path, str]) -> None:
    _, text = page
    blocks = [body for body in SCRIPT_RE.findall(text) if body.strip().startswith("{")]
    assert len(blocks) == 1
    payload = json.loads(blocks[0])
    assert len(payload["sec"]) == 10
    assert payload["stations"] == 10
    assert payload["blades"] == 2
    assert payload["fm"] == pytest.approx(make_performance().figure_of_merit, abs=1e-4)
    for section in payload["sec"]:
        assert len(section["up"]) == len(section["lo"]) == POINTS_PER_SURFACE
        for point in section["up"] + section["lo"]:
            assert len(point) == 3
            assert all(isinstance(v, (int, float)) for v in point)


def test_geometry_payload_is_compactly_rounded(page: tuple[Path, str]) -> None:
    _, text = page
    payload = json.loads([b for b in SCRIPT_RE.findall(text) if b.strip().startswith("{")][0])
    for section in payload["sec"]:
        for point in section["up"] + section["lo"]:
            for value in point:
                assert round(float(value), 4) == float(value)
    assert len(json.dumps(payload)) < 200_000


def test_blade_payload_matches_the_contract(page: tuple[Path, str]) -> None:
    design = make_design()
    payload = blade_payload(design, make_performance(design))
    first, last = payload["sec"][0], payload["sec"][-1]
    assert first["rR"] == pytest.approx(design.sections[0].r_R)
    assert first["c"] == pytest.approx(design.sections[0].chord_m, abs=1e-4)
    assert first["te"] >= design.te_min_m
    assert last["rR"] > first["rR"]
    assert payload["teMin"] == pytest.approx(design.te_min_m, abs=1e-6)
    assert payload["teMinRamp"] <= payload["teMaxRamp"]
    assert all(section["col"].startswith("#") for section in payload["sec"])


def test_viewer_carries_the_summary_header_and_controls(page: tuple[Path, str]) -> None:
    _, text = page
    performance = make_performance()
    assert f"{performance.figure_of_merit:.4f}" in text
    assert f"{performance.thrust_calc_n:.2f} N" in text
    assert "1150.0" in text and "Blades" in text
    for control in (
        'id="mode"',
        'id="proj"',
        'id="reset"',
        'id="station"',
        'id="readout"',
        'id="ramp"',
        'id="view"',
    ):
        assert control in text
    for interaction in ("pointerdown", "pointermove", "wheel", "setPointerCapture"):
        assert interaction in text


def test_explicit_html_target_is_honoured(tmp_path: Path) -> None:
    out = build_interactive_html(make_design(), make_performance(), tmp_path / "deep" / "viewer.html")
    assert out == tmp_path / "deep" / "viewer.html"
    assert out.is_file()


def test_viewer_survives_missing_performance(tmp_path: Path) -> None:
    out = build_interactive_html(make_design(), None, tmp_path / "interactive")
    text = out.read_text(encoding="utf-8")
    assert "n/a" in text
    assert json.loads([b for b in SCRIPT_RE.findall(text) if b.strip().startswith("{")][0])["fm"] is None


def test_plotly_output_is_optional(tmp_path: Path) -> None:
    out = build_interactive_html(make_design(), make_performance(), tmp_path / "interactive")
    bonus = out.parent / "blade_plotly.html"
    try:
        import plotly  # noqa: F401
    except ImportError:
        assert not bonus.exists()
    assert out.is_file()
