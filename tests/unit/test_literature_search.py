"""Tests for the literature retrieval helper (plan T008).

No test here touches the network.  The live arXiv / Crossref round trip is covered by
``test_live_search_smoke``, which stays skipped unless ``HOVERBLADE_FM_LIVE_LITERATURE=1``
is set in the environment.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "literature_search.py"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("hoverblade_literature_search", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


lit = _load_module()

ARXIV_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <title>Query Interface</title>
  <entry>
    <id>http://arxiv.org/abs/2408.15898v3</id>
    <updated>2024-09-01T00:00:00Z</updated>
    <published>2024-08-28T17:41:00Z</published>
    <title>Airfoil   Diffusion: Denoising
      Diffusion Model For Conditional Airfoil Generation</title>
    <summary>  A diffusion model for conditional airfoil generation.  </summary>
    <author><name>Reid Graves</name></author>
    <author><name>Amir Barati Farimani</name></author>
    <arxiv:comment>22 pages, 12 figures</arxiv:comment>
    <link href="http://arxiv.org/abs/2408.15898v3" rel="alternate"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2302.08728v2</id>
    <published>2023-02-17T09:00:00Z</published>
    <title>Topologically assisted optimization for rotor design</title>
    <summary>Rotor shape optimisation with respect to figure of merit.</summary>
    <author><name>Tianyu Wang</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/9999.00001v1</id>
    <published>2021-01-01T00:00:00Z</published>
    <summary>an entry without a title must be dropped</summary>
  </entry>
</feed>
"""

CROSSREF_FIXTURE: dict[str, Any] = {
    "message": {
        "items": [
            {
                "DOI": "10.2514/1.J062611",
                "title": [
                    "Fast and Robust Computation of Optimal Rotor Designs Using Blade Element Momentum Theory"
                ],
                "author": [
                    {"family": "Ruh", "given": "Marius L."},
                    {"family": "Hwang", "given": "John T."},
                ],
                "issued": {"date-parts": [[2023, 9]]},
                "container-title": ["AIAA Journal"],
                "abstract": "<jats:p>A <b>robust</b> BEM method for optimal rotor designs in hover.</jats:p>",
                "URL": "https://doi.org/10.2514/1.j062611",
            },
            {
                "DOI": "10.1016/j.ast.2024.999999",
                "title": ["Aerodynamic design and optimization of ducted multirotor UAVs"],
                "issued": {"date-parts": [[2024]]},
                "container-title": [],
                "type": "journal-article",
            },
            {"title": [], "DOI": "10.0000/no-title"},
            "not-a-dict",
        ]
    }
}


# --------------------------------------------------------------------------- arXiv parsing
def test_parse_arxiv_atom_extracts_fields() -> None:
    records = lit.parse_arxiv_atom(ARXIV_FIXTURE, query="airfoil generation diffusion model")
    assert len(records) == 2  # the title-less entry is dropped
    first = records[0]
    assert first.title == "Airfoil Diffusion: Denoising Diffusion Model For Conditional Airfoil Generation"
    assert first.arxiv == "2408.15898"
    assert first.year == 2024
    assert first.authors == ["Reid Graves", "Amir Barati Farimani"]
    assert first.venue == "arXiv preprint"
    assert first.url == "https://arxiv.org/abs/2408.15898v3"
    assert "12 figures" in first.abstract
    assert first.query == "airfoil generation diffusion model"
    assert first.source == "arxiv"


def test_parse_arxiv_atom_handles_journal_ref_and_doi() -> None:
    xml = (
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">'
        "<entry><id>http://arxiv.org/abs/2201.00001v1</id><published>2022-01-01T00:00:00Z</published>"
        "<title>Hover rotor study</title><summary>abstract</summary>"
        "<arxiv:doi>10.1000/xyz</arxiv:doi>"
        "<arxiv:journal_ref>J. Rotorcraft 12 (2022)</arxiv:journal_ref></entry></feed>"
    )
    (record,) = lit.parse_arxiv_atom(xml)
    assert record.doi == "10.1000/xyz"
    assert record.venue == "J. Rotorcraft 12 (2022)"
    assert record.year == 2022


@pytest.mark.parametrize("bad_input", ["", "   ", "<not xml at all", "<a><b></a>"])
def test_parse_arxiv_atom_never_crashes(bad_input: str) -> None:
    assert lit.parse_arxiv_atom(bad_input) == []


def test_parse_arxiv_atom_rejects_dtd_and_entities() -> None:
    evil = (
        '<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>&x;</title></entry></feed>'
    )
    assert lit.parse_arxiv_atom(evil) == []


# ------------------------------------------------------------------------ Crossref parsing
def test_parse_crossref_json_from_dict() -> None:
    records = lit.parse_crossref_json(CROSSREF_FIXTURE, query="hover rotor")
    assert len(records) == 2
    first = records[0]
    assert first.doi == "10.2514/1.J062611"
    assert first.year == 2023
    assert first.authors == ["Ruh Marius L.", "Hwang John T."]
    assert first.venue == "AIAA Journal"
    assert first.url == "https://doi.org/10.2514/1.J062611"
    # JATS markup stripped, text kept
    assert first.abstract == "A robust BEM method for optimal rotor designs in hover."
    second = records[1]
    assert second.authors == []
    assert second.venue == "journal-article"  # falls back to the Crossref type


def test_parse_crossref_json_from_text_and_garbage() -> None:
    text = json.dumps(CROSSREF_FIXTURE)
    assert len(lit.parse_crossref_json(text)) == 2
    assert lit.parse_crossref_json("") == []
    assert lit.parse_crossref_json("nonsense{") == []
    assert lit.parse_crossref_json({"message": {}}) == []
    assert lit.parse_crossref_json({"message": {"items": "not-a-list"}}) == []


def test_strip_jats_decodes_entities() -> None:
    assert lit.strip_jats("<jats:p>a &amp; b &lt; c</jats:p>") == "a & b < c"


# ------------------------------------------------------------------------------------ dedup
def test_normalize_title_ignores_punctuation_case() -> None:
    assert lit.normalize_title("  Hover, Rotor: FM! ") == "hover rotor fm"


def test_dedupe_by_doi_keeps_the_curated_row() -> None:
    live = lit.Record(
        title="Airfoil Diffusion",
        doi="10.48550/arXiv.2408.15898",
        arxiv="2408.15898",
        year=2024,
        source="arxiv",
    )
    curated = lit.Record(
        title="Airfoil Diffusion: Denoising", doi="10.48550/arXiv.2408.15898", year=2024, source="seed"
    )
    curated.annotated = True
    deduped = lit.dedupe([live, curated])
    assert len(deduped) == 1
    assert deduped[0].source == "seed"


def test_dedupe_by_title_when_dois_differ() -> None:
    a = lit.Record(title="Blunt Trailing Edge Airfoils", doi="10.1/one", year=2003)
    b = lit.Record(title="Blunt trailing-edge airfoils!", doi="10.2/two", year=2003)
    deduped = lit.dedupe([a, b])
    assert len(deduped) == 1
    assert deduped[0].doi == "10.1/one"


# --------------------------------------------------------------------------- classification
def test_classify_hover_detects_hover_records() -> None:
    assert lit.classify_hover(lit.Record(title="Hover figure of merit of a coaxial rotor")) == "yes"
    assert lit.classify_hover(lit.Record(title="Wind turbine wake modelling")) == "no"
    assert lit.classify_hover(lit.Record(title="Propeller performance at advance ratio 0.3")) == "partial"


def test_classify_method_labels_from_keywords() -> None:
    method = lit.classify_method(
        lit.Record(title="CST airfoil parameterization", abstract="diffusion model + BEMT")
    )
    assert "generative diffusion model" in method
    assert "CST geometry parameterization" in method


def test_classify_relevance_scores_this_projects_topics() -> None:
    hot = lit.Record(
        title="hover rotor figure of merit with trailing edge thickness constraint",
        abstract="blade element momentum theory, CST parameterization, surrogate model",
        year=2024,
    )
    cold = lit.Record(title="Pipe flow friction reduction", abstract="polymer additives", year=2019)
    assert lit.classify_relevance(hot) == "high"
    assert lit.classify_relevance(cold) == "low"


def test_auto_annotate_fills_every_screening_column() -> None:
    record = lit.auto_annotate(
        lit.Record(
            title="Blunt trailing edge airfoil drag",
            abstract="manufacturing constraint, hover rotor",
            year=2023,
        )
    )
    for column in lit.SUMMARY_COLUMNS:
        if column in ("title", "year"):
            continue
        assert getattr(record, column), f"{column} left blank"


def test_auto_annotate_leaves_curated_rows_untouched() -> None:
    record = lit.Record(title="Whatever", source="seed")
    record.annotated = True
    record.method = "hand annotated"
    assert lit.auto_annotate(record).method == "hand annotated"
    assert lit.classify_method(record) != "hand annotated"


def test_in_year_window_filters_live_but_not_seed_records() -> None:
    window = (2022, 2025)
    assert lit.in_year_window(lit.Record(title="t", year=2019, source="crossref"), window) is False
    assert lit.in_year_window(lit.Record(title="t", year=None, source="crossref"), window) is True
    assert lit.in_year_window(lit.Record(title="t", year=1989, source="seed"), window) is True


# ------------------------------------------------------------------------------ seed set
def test_seed_dataset_is_substantial_and_identifiable() -> None:
    assert len(lit.SEED_DATA) >= 15
    records = lit.seed_records()
    assert len(records) == len(lit.SEED_DATA)
    for record in records:
        assert record.title
        assert record.year is not None and 1980 <= record.year <= 2030
        assert record.doi or record.arxiv, f"no identifier for {record.title}"
        assert record.url.startswith("https://")
        assert record.annotated is True
        for column in lit.SUMMARY_COLUMNS[2:]:
            assert getattr(record, column), f"{column} empty for {record.title}"


def test_seed_records_deduplicate_against_live_hits() -> None:
    live = lit.Record(
        title="Aerodynamic Analysis of Blunt Trailing Edge Airfoils", doi="10.1115/1.1629103", year=2003
    )
    merged = lit.dedupe([live, *lit.seed_records()])
    assert len(merged) == len(lit.SEED_DATA)
    blunt = [r for r in merged if "Blunt Trailing Edge" in r.title]
    assert len(blunt) == 1
    assert blunt[0].source == "seed"


# ---------------------------------------------------------------------------- URL building
def test_arxiv_url_targets_the_atom_api_with_polite_parameters() -> None:
    url = lit.arxiv_url("hover rotor", limit=5)
    assert url.startswith("https://export.arxiv.org/api/query?")
    assert "search_query=all%3Ahover+rotor" in url
    assert "max_results=5" in url
    assert "sortBy=relevance" in url


def test_crossref_url_applies_the_year_filter() -> None:
    url = lit.crossref_url("hover rotor", rows=7, years=(2022, 2025))
    assert url.startswith("https://api.crossref.org/works?")
    assert "rows=7" in url
    assert "from-created-date%3A2022-01-01" in url
    assert "until-created-date%3A2025-12-31" in url


def test_insecure_tls_fallback_is_opt_in_and_host_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(lit.INSECURE_TLS_ENV, raising=False)
    assert lit.insecure_tls_allowed("https://export.arxiv.org/api/query") is False
    monkeypatch.setenv(lit.INSECURE_TLS_ENV, "1")
    assert lit.insecure_tls_allowed("https://export.arxiv.org/api/query") is True
    assert lit.insecure_tls_allowed("https://evil.example.com/token") is False


# ------------------------------------------------------------------------- parse_years
@pytest.mark.parametrize(
    ("value", "expected"),
    [("2022-2025", (2022, 2025)), ("2024", (2024, 2024)), ("2020 - 2023", (2020, 2023))],
)
def test_parse_years_accepts_windows(value: str, expected: tuple[int, int]) -> None:
    assert lit.parse_years(value) == expected


@pytest.mark.parametrize("value", ["", "last year", "2025-2022", "22-25"])
def test_parse_years_rejects_junk(value: str) -> None:
    import argparse

    with pytest.raises(argparse.ArgumentTypeError):
        lit.parse_years(value)


# ---------------------------------------------------------------------- offline end to end
def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def test_offline_run_writes_both_outputs(tmp_path: Path) -> None:
    code = lit.main(["--offline", "--out", str(tmp_path), "--quiet", "--no-merge"])
    assert code == 0
    raw = tmp_path / "raw.jsonl"
    summary = tmp_path / "summary.csv"
    assert raw.exists() and raw.stat().st_size > 0
    assert summary.exists() and summary.stat().st_size > 0

    rows = _read_jsonl(raw)
    assert len(rows) == len(lit.SEED_DATA)
    assert all(row["source"].startswith("seed") for row in rows)
    assert all(row["title"] for row in rows)

    with summary.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == [*lit.SUMMARY_COLUMNS, *lit.EXTRA_COLUMNS]
        table = list(reader)
    assert len(table) == len(lit.SEED_DATA)
    for row in table:
        assert row["title"]
        assert row["year"].isdigit()
        for column in lit.SUMMARY_COLUMNS[2:]:
            assert row[column].strip(), f"empty screening column {column}"


def test_repeat_run_is_idempotent_under_merge(tmp_path: Path) -> None:
    first = lit.main(["--offline", "--out", str(tmp_path), "--quiet"])
    before = _read_jsonl(tmp_path / "raw.jsonl")
    second = lit.main(["--offline", "--out", str(tmp_path), "--quiet"])
    after = _read_jsonl(tmp_path / "raw.jsonl")
    assert (first, second) == (0, 0)
    assert len(before) == len(after)  # merge + dedup keeps the count stable


def test_invalid_limit_exits_with_code_two(tmp_path: Path) -> None:
    assert lit.main(["--offline", "--out", str(tmp_path), "--limit", "0", "--quiet"]) == 2


def test_unknown_preset_year_flag_exits_with_code_two(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        lit.main(["--offline", "--out", str(tmp_path), "--years", "not-a-window"])
    assert excinfo.value.code == 2


# ----------------------------------------------------------------------------------- live
@pytest.mark.skipif(
    os.environ.get("HOVERBLADE_FM_LIVE_LITERATURE") != "1",
    reason="opt-in live network test: set HOVERBLADE_FM_LIVE_LITERATURE=1",
)
def test_live_search_smoke(tmp_path: Path) -> None:
    session = lit.SearchSession(limit=3, years=(2022, 2025), polite_delay=3.0)
    records = lit.collect_live(session, ("hover rotor figure of merit optimization",))
    assert records or session.degraded  # either hits came back or the breaker opened cleanly
    if records:
        assert any(record.doi or record.arxiv for record in records)
