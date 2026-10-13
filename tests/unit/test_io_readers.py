"""Tests for readers, run validation, summaries and the optional history/polars writers."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from io_fixtures import make_config, make_constraints, make_design, make_history, make_performance

from hoverblade_fm.io import (
    RunDataError,
    RunSummary,
    read_design,
    read_run,
    read_section_coordinates,
    summarise_run,
    validate_sections,
    write_design_json,
    write_optimization_history,
    write_polars,
    write_run_outputs,
)


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    out_dir = tmp_path / "run"
    write_run_outputs(make_design(), make_performance(), make_constraints(), make_config(), out_dir, seed=3)
    return out_dir


# -- read_run ---------------------------------------------------------------------------------


def test_read_run_round_trip(run_dir: Path) -> None:
    run = read_run(run_dir)
    assert isinstance(run, RunSummary)
    assert len(run.sections) == 10
    assert run.design["blade_count"] == 2
    assert run.design["config"]["trailing_edge_thickness_m"] == pytest.approx(0.002)
    assert run.performance["figure_of_merit"] == pytest.approx(132.4 / 220.0, rel=1e-6)
    assert run.provenance["random_seed"] == 3
    coords = run.paths["section_coords"]
    assert len(coords) == 10
    assert all(p.is_file() for p in coords)
    assert coords[0].name == "section_00_r0.100.csv"


def test_read_run_rejects_missing_files(tmp_path: Path) -> None:
    with pytest.raises(RunDataError, match="missing run output file.*design.json"):
        read_run(tmp_path)


def test_read_run_rejects_sharp_te(run_dir: Path) -> None:
    sections = [dict(row) for row in read_run(run_dir).sections]
    required = read_design(run_dir / "design.json")["config"]["trailing_edge_thickness_m"]
    sections[4]["te_thickness_m"] = 0.0001
    with pytest.raises(RunDataError, match="below the required minimum"):
        validate_sections(sections, required)
    sections[4]["te_thickness_m"] = 0.0036
    assert validate_sections(sections, required) is None


def test_read_run_raises_on_violating_written_run(tmp_path: Path) -> None:
    out_dir = tmp_path / "bad_run"
    design = make_design(te_over_c_scale=0.05)
    assert not design.is_blunt()
    write_run_outputs(design, make_performance(), make_constraints(), make_config(), out_dir)
    with pytest.raises(RunDataError, match="te_thickness_m .* below the required minimum"):
        read_run(out_dir)


def test_read_design_requires_config_echo(tmp_path: Path) -> None:
    path = tmp_path / "design.json"
    path.write_text(json.dumps({"sections": []}), encoding="utf-8")
    with pytest.raises(RunDataError, match="design echo is missing 'config'"):
        read_design(path)


# -- design.json / performance.json content ----------------------------------------------------


def test_design_json_fields(run_dir: Path) -> None:
    design = read_design(run_dir / "design.json")
    for key in (
        "blade_count",
        "rpm",
        "omega",
        "tip_mach",
        "tip_speed",
        "solidity",
        "collective_trim_deg",
        "min_te_thickness_m",
        "te_margin_m",
        "blunt_te_ok",
        "config",
        "sections",
    ):
        assert key in design, key
    assert design["blunt_te_ok"] is True
    assert design["min_te_thickness_m"] >= 0.002
    assert len(design["sections"]) == 10
    section = design["sections"][0]
    for key in ("te_thickness_m", "te_thickness_to_chord", "blunt_te_ok", "shape_parameters", "twist_deg"):
        assert key in section, key
    assert section["blunt_te_ok"] is True
    assert section["shape_parameters"] == {}
    text = (run_dir / "design.json").read_text(encoding="utf-8")
    assert "NaN" not in text


def test_design_json_shape_parameters_of_subclass(tmp_path: Path) -> None:
    from hoverblade_fm.models import Section

    @dataclasses.dataclass(slots=True)
    class CstSection(Section):
        cst_upper: Any = None
        cst_lower: Any = None

    base = make_design()
    design = make_design()
    design.sections = [
        CstSection(
            **{field.name: getattr(section, field.name) for field in dataclasses.fields(Section)},
            cst_upper=[0.5, 0.4],
            cst_lower=np.array([0.2, 0.1]),
        )
        for section in base.sections
    ]
    path = write_design_json(design, make_config(), tmp_path / "design.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    first = payload["sections"][0]["shape_parameters"]
    assert first["cst_upper"] == [0.5, 0.4]
    assert first["cst_lower"] == [0.2, 0.1]


def test_performance_json_radial_and_nan_handling(run_dir: Path) -> None:
    payload = json.loads((run_dir / "performance.json").read_text(encoding="utf-8"))
    assert payload["thrust_error"] == pytest.approx(0.5 / 120.0, rel=1e-6)
    radial = payload["radial"]
    assert radial["r_R"][0] == pytest.approx(0.04)
    assert radial["power_gradient"][5] is None
    assert len(radial["reynolds"]) == 25
    text = (run_dir / "performance.json").read_text(encoding="utf-8")
    assert "NaN" not in text and "Infinity" not in text


def test_performance_json_without_radial(tmp_path: Path) -> None:
    from hoverblade_fm.io import write_performance_json

    path = write_performance_json(make_performance(with_radial=False), tmp_path / "perf.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "radial" not in payload
    assert payload["figure_of_merit"] == pytest.approx(132.4 / 220.0, rel=1e-6)


# -- history / polars ---------------------------------------------------------------------------


def test_history_tolerates_missing_keys(tmp_path: Path) -> None:
    path = write_optimization_history(make_history(), tmp_path, strategy="cma-es", seed=5)
    lines = path.read_text(encoding="utf-8").rstrip("\n").split("\n")
    header = lines[0].split(",")
    assert header == [
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
    ]
    assert len(lines) == 1 + 4
    row2 = dict(zip(header, lines[2].split(","), strict=True))
    assert row2["thrust_n"] == "" and row2["wall_time_s"] == ""
    assert row2["geometry_valid"] == "false"
    row1 = dict(zip(header, lines[1].split(","), strict=True))
    assert row1["feasible"] == "true"
    row3 = dict(zip(header, lines[3].split(","), strict=True))
    assert row3["objective"] == ""
    summary = json.loads((tmp_path / "optimization_summary.json").read_text(encoding="utf-8"))
    assert summary["best_objective"] == 0.58
    assert summary["best_evaluation"] == 2
    assert summary["evaluations"] == 4
    assert summary["strategy"] == "cma-es"
    assert summary["seed"] == 5


def test_history_empty(tmp_path: Path) -> None:
    path = write_optimization_history([], tmp_path)
    assert path.read_text(encoding="utf-8").strip() == ",".join(
        [
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
        ]
    )
    summary = json.loads((tmp_path / "optimization_summary.json").read_text(encoding="utf-8"))
    assert summary == {
        "best_objective": None,
        "best_evaluation": None,
        "evaluations": 0,
        "strategy": None,
        "seed": None,
    }


def test_write_polars_mapping_and_list(tmp_path: Path) -> None:
    curve = np.array([[0.0, 0.1, 0.01, -0.05], [4.0, 0.7, 0.02, -0.02]])
    written = write_polars({1: curve, 0: curve.tolist()}, tmp_path)
    assert [p.name for p in written] == ["section_00_polar.csv", "section_01_polar.csv"]
    lines = written[0].read_text(encoding="utf-8").rstrip("\n").split("\n")
    assert lines[0] == "alpha_deg,cl,cd,cm"
    assert lines[1].startswith("0.000000,0.100000,0.010000,-0.050000")
    written2 = write_polars([curve, None, [[1, 2, 3]], curve], tmp_path / "second")
    assert [p.name for p in written2] == ["section_00_polar.csv", "section_03_polar.csv"]


def test_write_polars_skips_without_data(tmp_path: Path) -> None:
    assert write_polars(None, tmp_path) == []
    assert write_polars({}, tmp_path) == []
    assert write_polars([], tmp_path) == []
    assert write_polars({"not-a-station": [[1.0, 2.0, 3.0, 4.0]]}, tmp_path) == []
    assert not (tmp_path / "polars").exists()


# -- summarise_run -------------------------------------------------------------------------------


def test_summarise_full_run(run_dir: Path) -> None:
    summary = summarise_run(run_dir)
    assert summary["missing_required"] == []
    assert summary["thrust_n"] == 120.0
    assert summary["radius_m"] == 0.5
    assert summary["trailing_edge_thickness_m"] == 0.002
    assert summary["blade_count"] == 2
    assert summary["rpm"] == pytest.approx(1200.0)
    assert summary["figure_of_merit"] == pytest.approx(132.4 / 220.0, rel=1e-6)
    assert summary["power_actual_w"] == pytest.approx(220.0, rel=1e-6)
    assert summary["thrust_error"] == pytest.approx(0.5 / 120.0, rel=1e-5)
    assert summary["min_te_thickness_m"] >= 0.002
    assert summary["te_margin_m"] is not None and summary["te_margin_m"] > 0
    assert summary["blunt_te_ok"] is True
    assert summary["input_hash"] == "0123abcd4567ef89"
    assert summary["random_seed"] == 3
    assert summary["section_coordinate_files"] == 10
    assert "cad/blade.step" in summary["missing_optional"]
    assert "plots/chord_twist.png" in summary["missing_optional"]
    assert "report.html" in summary["missing_optional"]
    assert "polars/" in summary["missing_optional"]
    assert json.dumps(summary)  # flat summary stays printable/serialisable


def test_summarise_partial_run(tmp_path: Path) -> None:
    write_design_json(make_design(), make_config(), tmp_path / "design.json")
    summary = summarise_run(tmp_path)
    assert "performance.json" in summary["missing_required"]
    assert "provenance.json" in summary["missing_required"]
    assert any(entry.startswith("sections/section_*.csv") for entry in summary["missing_required"])
    assert summary["figure_of_merit"] is None
    assert summary["thrust_n"] == 120.0


def test_read_section_coordinates_missing_file(tmp_path: Path) -> None:
    with pytest.raises(RunDataError, match="missing run output file"):
        read_section_coordinates(tmp_path / "sections" / "section_00_r0.100.csv")
