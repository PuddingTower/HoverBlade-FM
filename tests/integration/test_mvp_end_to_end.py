"""End-to-end CLI / pipeline tests: the MVP definition of plan section 26 and the smoke test of 21.2."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

from hoverblade_fm.cli import EXIT_INFEASIBLE, EXIT_INVALID_INPUT, EXIT_OK, main
from hoverblade_fm.config.schema import OptimizationSettings, SolverSettings, UserInput, resolve
from hoverblade_fm.models import SECTION_COUNT
from hoverblade_fm.pipeline import design_from_run_dir, generate

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
FAST = OptimizationSettings(preset="custom", max_evaluations=80, popsize=8, local_refine=False, restarts=1)
COARSE = SolverSettings(n_radial=61, n_itmax=120)


def _fast_cfg(thrust: float = 5000.0, radius: float = 2.5, te_min: float = 0.0025, **extra):
    """A cheap, deterministic configuration for tests (few evaluations, coarse solver)."""
    payload = {"thrust_n": thrust, "radius_m": radius, "trailing_edge_thickness_m": te_min, **extra}
    cfg = resolve(UserInput.model_validate(payload))
    cfg.optimization = FAST
    return cfg


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("mvp_run")
    cfg = _fast_cfg()
    outcome = generate(cfg, out, search_solver=COARSE, with_cad=False, with_viz=True, seed=5)
    assert outcome.exit_code == EXIT_OK, outcome.notes
    return out


def test_sections_csv_has_ten_rows(run_dir: Path):
    rows = list(csv.reader((run_dir / "sections.csv").read_text(encoding="utf-8").splitlines()))
    assert len(rows) == SECTION_COUNT + 1
    header = rows[0]
    assert header[:6] == ["station_id", "r_R", "r_m", "chord_m", "twist_deg", "te_thickness_m"]
    assert "reynolds" in header and "mach" in header and "cl_design" in header


def test_ten_coordinate_files_exist_and_close_on_a_blunt_edge(run_dir: Path):
    files = sorted((run_dir / "sections").glob("section_*_r*.csv"))
    assert len(files) == SECTION_COUNT
    for path in files:
        text = path.read_text(encoding="utf-8").splitlines()
        body = [line for line in text if line and not line.startswith("#") and not line.startswith("side,")]
        lower = [line for line in body if line.startswith("lower,")]
        upper = [line for line in body if line.startswith("upper,")]
        assert len(lower) == len(upper) and len(lower) >= 24
        # the contour must not close to a point at the trailing edge
        last_upper = float(upper[-1].split(",")[2])
        first_lower = float(lower[-1].split(",")[2])
        assert abs(last_upper - first_lower) > 1e-6  # y_gap at x/c = 1 in physical units below


def test_reloaded_design_is_blunt(run_dir: Path):
    cfg = _fast_cfg()
    design = design_from_run_dir(run_dir, cfg)
    assert len(design.sections) == SECTION_COUNT
    assert design.is_blunt()
    assert design.min_te_thickness() >= cfg.te_min_m - 1e-12


def test_performance_json_reports_fm(run_dir: Path):
    data = json.loads((run_dir / "performance.json").read_text(encoding="utf-8"))
    assert data["figure_of_merit"] > 0.0
    assert data["power_actual_w"] > data["power_ideal_w"] * 0.999
    assert abs(data["thrust_calc_n"] - data["thrust_target_n"]) / data["thrust_target_n"] < 0.02
    assert data["blade_count"] >= 2


def test_provenance_is_complete(run_dir: Path):
    data = json.loads((run_dir / "provenance.json").read_text(encoding="utf-8"))
    for key in (
        "program_version",
        "git_commit",
        "input_hash",
        "random_seed",
        "timestamp",
        "optimization_settings",
    ):
        assert key in data, key
    assert data["input_hash"]


def test_report_and_plots_are_written(run_dir: Path):
    assert (run_dir / "report.html").is_file()
    plots = list((run_dir / "plots").glob("*.png"))
    names = {p.name for p in plots}
    assert {"chord_twist.png", "airfoil_stack.png", "trailing_edge_zoom.png", "fm_convergence.png"} <= names
    html = (run_dir / "report.html").read_text(encoding="utf-8")
    assert "Figure of Merit" in html or "figure of merit" in html.lower()
    assert "PASS" in html  # blunt trailing-edge proof block


def test_optimization_history_is_written(run_dir: Path):
    text = (run_dir / "optimization_history.csv").read_text(encoding="utf-8").splitlines()
    assert len(text) > 10
    assert "figure_of_merit" in text[0]


@pytest.mark.parametrize(
    "example",
    ["input_120N.json", "input_5000N.json", "input_20000N.json"],
)
def test_all_three_examples_generate_blunt_ten_section_blades(example, tmp_path):
    cfg = resolve(UserInput.from_json(EXAMPLES / example))
    cfg.optimization = OptimizationSettings(
        preset="custom",
        max_evaluations=40,
        popsize=8,
        local_refine=False,
        restarts=1,
        seed=cfg.optimization.seed,
    )
    outcome = generate(
        cfg, tmp_path / example, optimize=True, with_cad=False, with_viz=False, search_solver=COARSE
    )
    assert outcome.exit_code == EXIT_OK, outcome.notes
    assert len(outcome.design.sections) == SECTION_COUNT
    assert outcome.design.is_blunt()
    assert outcome.performance.figure_of_merit > 0.0


def test_cli_entry_point_runs(tmp_path, capsys):
    out = tmp_path / "cli_run"
    code = main(
        [
            "generate",
            "--thrust-n",
            "5000",
            "--radius-m",
            "2.5",
            "--trailing-edge-thickness-m",
            "0.0025",
            "--out",
            str(out),
            "--preset",
            "fast",
            "--no-cad",
            "--no-viz",
            "--seed",
            "1",
        ]
    )
    assert code == EXIT_OK
    assert (out / "sections.csv").is_file()
    assert (out / "design.json").is_file()


def test_cli_json_output_is_machine_readable(tmp_path):
    out = tmp_path / "json_run"
    code = main(
        [
            "generate",
            str(EXAMPLES / "input_120N.json"),
            "--out",
            str(out),
            "--preset",
            "fast",
            "--no-cad",
            "--no-viz",
            "--json",
        ]
    )
    assert code == EXIT_OK
    payload = json.loads(out.joinpath("design.json").read_text(encoding="utf-8"))
    assert payload["blunt_te_ok"] is True


def test_cli_rejects_bad_input(tmp_path, capsys):
    code = main(
        [
            "generate",
            "--thrust-n",
            "-5",
            "--radius-m",
            "2.5",
            "--trailing-edge-thickness-m",
            "0.002",
            "--out",
            str(tmp_path),
        ]
    )
    assert code == EXIT_INVALID_INPUT
    assert "error" in capsys.readouterr().err.lower()


def test_cli_version_exits_cleanly(capsys):
    assert main(["version"]) == EXIT_OK
    assert "HoverBlade-FM" in capsys.readouterr().out


def test_console_script_is_installed():
    result = subprocess.run(
        [sys.executable, "-m", "hoverblade_fm.cli", "version"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0
    assert "HoverBlade-FM" in result.stdout


def test_summary_command_reports_the_run(run_dir: Path, capsys):
    assert main(["summary", str(run_dir), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["blunt_te_ok"] is True
    assert payload["figure_of_merit"] > 0.0
    assert payload["section_coordinate_files"] == SECTION_COUNT


def test_exit_code_is_infeasible_when_constraints_are_missed(tmp_path, monkeypatch):
    """Force a design the trim cannot reach and require a non-zero exit code."""
    cfg = _fast_cfg()
    cfg.optimization = FAST.model_copy(update={"max_evaluations": 0, "local_refine": False})
    monkeypatch.setattr(cfg, "thrust_n", 5.0e6)  # absurd thrust for this radius
    outcome = generate(
        cfg, tmp_path / "bad", optimize=False, with_cad=False, with_viz=False, search_solver=COARSE
    )
    assert outcome.exit_code == EXIT_INFEASIBLE
