"""Tests for sections.csv writing and table reading (plan T059, section 3.4)."""

from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path

import pytest
from io_fixtures import make_design

from hoverblade_fm.io import RunDataError, read_sections_table, write_sections_csv
from hoverblade_fm.models import SECTION_CSV_COLUMNS


@pytest.fixture()
def sections_path(tmp_path: Path) -> Path:
    design = make_design()
    return write_sections_csv(design, tmp_path / "sections.csv")


def test_header_is_exact_column_order(sections_path: Path) -> None:
    first_line = sections_path.read_text(encoding="utf-8").splitlines()[0]
    assert first_line == ",".join(SECTION_CSV_COLUMNS)


def test_exactly_ten_data_rows(sections_path: Path) -> None:
    rows = list(csv.reader(StringIO(sections_path.read_text(encoding="utf-8"))))
    assert len(rows) == 1 + 10
    assert [int(r[0]) for r in rows[1:]] == [s.station_id for s in make_design().sections]


def test_file_uses_lf_endings_and_utf8(sections_path: Path) -> None:
    raw = sections_path.read_bytes()
    assert b"\r" not in raw
    assert raw.endswith(b"\n")


def test_round_trip_values_match_rows(sections_path: Path) -> None:
    design = make_design()
    rows = read_sections_table(sections_path)
    assert len(rows) == 10
    for row, section in zip(rows, design.sections, strict=True):
        assert row["station_id"] == section.station_id
        assert tuple(row) == SECTION_CSV_COLUMNS
        for key, value in section.to_row().items():
            if isinstance(value, (str, int)):
                assert row[key] == value
            else:
                assert row[key] == pytest.approx(float(value), rel=1e-8, abs=1e-12)


def test_reader_rejects_short_table(tmp_path: Path) -> None:
    design = make_design()
    path = write_sections_csv(design, tmp_path / "short.csv")
    text = path.read_text(encoding="utf-8").rstrip("\n").split("\n")
    path.write_text("\n".join(text[:6]) + "\n", encoding="utf-8", newline="\n")
    with pytest.raises(RunDataError, match="expected 10 section rows, found 5"):
        read_sections_table(path)


def test_reader_rejects_bad_header(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("station_id,r_R\n0,0.1\n", encoding="utf-8", newline="\n")
    with pytest.raises(RunDataError, match="unexpected sections.csv header"):
        read_sections_table(path)


def test_reader_missing_file(tmp_path: Path) -> None:
    with pytest.raises(RunDataError, match="missing run output file"):
        read_sections_table(tmp_path / "nope.csv")
