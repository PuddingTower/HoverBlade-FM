"""Tests for provenance writing (plan T069, section 12.3)."""

from __future__ import annotations

import json
import platform
import re
from pathlib import Path

import numpy as np
import pytest
from io_fixtures import make_config, make_constraints, make_design, make_optimization_result, make_performance

import hoverblade_fm
from hoverblade_fm.io import resolve_git_commit, write_provenance, write_run_outputs
from hoverblade_fm.models import Provenance

TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


@pytest.fixture()
def provenance_payload(tmp_path: Path) -> dict:
    config = make_config()
    provenance = write_provenance(config, tmp_path, seed=13)
    payload = json.loads((tmp_path / "provenance.json").read_text(encoding="utf-8"))
    payload["_returned"] = provenance
    return payload


def test_provenance_fields(provenance_payload: dict) -> None:
    config = make_config()
    returned: Provenance = provenance_payload["_returned"]
    assert returned.program_version == hoverblade_fm.__version__ == "0.1.0"
    assert provenance_payload["program_version"] == "0.1.0"
    assert provenance_payload["input_hash"] == config.input_hash == "0123abcd4567ef89"
    assert provenance_payload["random_seed"] == 13
    assert TIMESTAMP_RE.match(provenance_payload["timestamp"])
    assert isinstance(provenance_payload["git_commit"], str) and provenance_payload["git_commit"]
    assert provenance_payload["python"] == platform.python_version()
    assert provenance_payload["numpy"] == np.__version__
    assert provenance_payload["scipy"] not in ("", None)
    assert returned.to_dict()["random_seed"] == 13


def test_resolved_config_and_settings_echo(provenance_payload: dict) -> None:
    config = make_config()
    assert provenance_payload["resolved_config"] == json.loads(json.dumps(config.to_dict()))
    assert provenance_payload["optimization_settings"] == config.optimization.model_dump()


def test_explicit_settings_and_extra_keys(tmp_path: Path) -> None:
    config = make_config()
    write_provenance(
        config,
        tmp_path,
        seed=1,
        optimization_settings={"preset": "custom", "max_evaluations": 10},
        extra={"note": "manual", "count": 3},
    )
    payload = json.loads((tmp_path / "provenance.json").read_text(encoding="utf-8"))
    assert payload["optimization_settings"] == {"preset": "custom", "max_evaluations": 10}
    assert payload["note"] == "manual"
    assert payload["count"] == 3
    returned = write_provenance(config, tmp_path, seed=1)
    assert isinstance(returned, Provenance)


def test_strict_json_no_nan_tokens(tmp_path: Path) -> None:
    write_provenance(make_config(), tmp_path, seed=0)
    text = (tmp_path / "provenance.json").read_text(encoding="utf-8")
    assert "NaN" not in text and "Infinity" not in text


def test_git_commit_never_raises() -> None:
    assert isinstance(resolve_git_commit(), str)


def test_run_outputs_embed_optimization_summary(tmp_path: Path) -> None:
    opt = make_optimization_result()
    config = make_config()
    paths = write_run_outputs(
        opt.design, opt.performance, opt.constraints, config, tmp_path, seed=7, optimization=opt
    )
    payload = json.loads(paths["provenance"].read_text(encoding="utf-8"))
    assert payload["optimization"] == {"evaluations": 42, "strategy": "cma-es+slsqp", "seed": 7}
    assert payload["random_seed"] == 7
    history_lines = paths["history"].read_text(encoding="utf-8").rstrip("\n").split("\n")
    assert len(history_lines) == 1 + len(opt.history)


def test_run_outputs_keys_and_types(tmp_path: Path) -> None:
    config = make_config()
    paths = write_run_outputs(
        make_design(), make_performance(), make_constraints(), config, tmp_path, history=[], seed=0
    )
    assert set(paths) == {
        "design",
        "performance",
        "provenance",
        "sections_csv",
        "section_coords",
        "history",
        "constraints",
    }
    assert isinstance(paths["section_coords"], list) and len(paths["section_coords"]) == 11
    for key, value in paths.items():
        if key == "section_coords":
            continue
        assert isinstance(value, Path) and value.is_file(), key
