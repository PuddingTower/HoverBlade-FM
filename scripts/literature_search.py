#!/usr/bin/env python
"""Literature retrieval for HoverBlade-FM (plan T008, 计划.md section 7).

Standard library only — no third-party imports — so the script runs on any Python >= 3.11
checkout, including a bare CI container where the package itself is not installed.

What it does
------------
1. Runs the English query list of 计划.md section 7.2 (plus a few extra queries that cover
   hover-BEMT robustness, blunt-trailing-edge drag and geometric validity) against
   * the arXiv Atom API  (``https://export.arxiv.org/api/query``), and
   * the Crossref REST API (``https://api.crossref.org/works``).
2. Restricts hits to a publication-year window (default 2022-2025).
3. Deduplicates by DOI first, then by normalised title.
4. Classifies every record with keyword heuristics into the screening columns of 计划.md
   section 7.4 (方法 / 是否悬停 / 是否支持几何约束 / 是否开源 / 关联度 / 可借鉴点).
5. Writes ``raw.jsonl`` (one JSON object per paper) and ``summary.csv``.

Network failure is never fatal: every query is wrapped, and after a few consecutive
transport failures the session goes "degraded" and stops hammering the APIs.  If the live
search yields nothing (``--offline``, no DNS, timeout, HTTP error, TLS problems) the script
falls back to the curated ``SEED_DATA`` below, so CI without network still writes non-empty
outputs.

TLS: requests are made with the default certificate store.  Some sandbox or CI images have
no usable trust store, in which case the metadata APIs fail with a certificate-verification
error.  Set ``HOVERBLADE_FM_INSECURE_TLS=1`` to allow an *unverified* retry, and only for
the two hosts in ``TLS_FALLBACK_HOSTS`` (public bibliographic metadata, never credentials).
The script logs loudly whenever it takes that path.

Usage
-----
    python scripts/literature_search.py --out docs/literature_search
    python scripts/literature_search.py --offline --limit 8 --years 2022-2025

Exit codes: 0 success, 2 invalid arguments, 4 unexpected internal error.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ----------------------------------------------------------------------------- endpoints
ARXIV_ENDPOINT = "https://export.arxiv.org/api/query"
CROSSREF_ENDPOINT = "https://api.crossref.org/works"
TLS_FALLBACK_HOSTS: frozenset[str] = frozenset({"export.arxiv.org", "api.crossref.org"})
INSECURE_TLS_ENV = "HOVERBLADE_FM_INSECURE_TLS"
OFFLINE_ENV = "HOVERBLADE_FM_OFFLINE"
HTTP_TIMEOUT_S = 25.0
POLITE_DELAY_S = 3.0  # arXiv asks clients to wait ~3 s between requests
CROSSREF_DELAY_S = 0.5
BACKOFF_S = 1.5
MAX_RETRIES = 2
RETRY_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})
USER_AGENT = "HoverBlade-FM-literature-search/0.1 (research tool; +https://github.com/hoverblade-fm)"
DEFAULT_YEARS = (2022, 2025)
DEFAULT_LIMIT = 10
DEFAULT_OUT = Path("docs/literature_search")
RAW_NAME = "raw.jsonl"
SUMMARY_NAME = "summary.csv"
LOG_PREFIX = "[literature-search]"
CIRCUIT_BREAKER_AFTER = 3

#: Screening columns, 计划.md section 7.4, in English-key form.  The Chinese headers map 1:1:
#: 标题 / 年份 / 方法 / 是否悬停 / 是否支持几何约束 / 是否开源 / 关联度 / 可借鉴点 (documented in
#: docs/literature_search/queries.md).
SUMMARY_COLUMNS: tuple[str, ...] = (
    "title",
    "year",
    "method",
    "hover",
    "geometry_constraint",
    "open_source",
    "relevance",
    "borrow",
)
#: Provenance columns appended after the screening columns (not part of 计划.md 7.4).
EXTRA_COLUMNS: tuple[str, ...] = ("source", "doi", "arxiv", "url", "query")

#: English query list of 计划.md section 7.2, plus the extras marked below.
QUERIES: tuple[str, ...] = (
    "helicopter rotor hover figure of merit optimization",
    "hovering rotor blade shape optimization",
    "blade element momentum theory hover rotor optimization",
    "airfoil inverse design machine learning",
    "airfoil generation diffusion model",
    "conditional diffusion model airfoil design",
    "differentiable CFD aerodynamic shape optimization",
    "multi-fidelity Bayesian optimization rotor blade",
    "CST airfoil parameterization trailing edge thickness",
    "blunt trailing edge airfoil manufacturing constraint",
    "open source propeller rotor design optimization",
    "hover rotor surrogate model figure of merit",
    # extras, chosen to cover the modules this project actually needs
    "rotor blade design optimization constraints CMA-ES",  # + derivative-free optimiser
    "low Reynolds number airfoil design optimization",  # + small hover rotors live at low Re
    "trailing edge thickness profile drag airfoil",  # + blunt-TE drag penalty model
    "airfoil shape parameterization geometric validity",  # + no-self-intersection constraints
)

#: 计划.md 7.2 Chinese query list.  Documentation only: neither API indexes CNKI/万方, so these
#: are emitted into queries.md for manual follow-up rather than queried.
CHINESE_QUERIES: tuple[str, ...] = (
    "悬停旋翼 品质因数 优化",
    "直升机旋翼 悬停 气动优化",
    "叶片 翼型 逆向设计",
    "翼型生成模型 扩散模型",
    "可微分 气动外形优化",
    "多保真 旋翼优化",
    "尾缘厚度 制造约束",
    "钝尾缘 翼型设计",
    "开源 旋翼设计",
)

YES = "yes"
NO = "no"
PARTIAL = "partial"
UNKNOWN = "unknown"

# ----------------------------------------------------------------------------- record type


@dataclass(slots=True)
class Record:
    """One literature hit, in the shape written to ``raw.jsonl``."""

    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str = ""
    doi: str = ""
    arxiv: str = ""
    url: str = ""
    abstract: str = ""
    query: str = ""
    source: str = ""
    # screening columns (计划.md 7.4)
    method: str = ""
    hover: str = ""
    geometry_constraint: str = ""
    open_source: str = ""
    relevance: str = ""
    borrow: str = ""
    annotated: bool = False  # True for curated seed rows: heuristics are not applied

    def dedup_key(self) -> str:
        if self.doi:
            return f"doi:{self.doi.strip().lower()}"
        return f"title:{normalize_title(self.title)}"

    def title_key(self) -> str:
        return f"title:{normalize_title(self.title)}"

    def text(self) -> str:
        return " ".join((self.title, self.abstract, self.venue)).lower()

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    def summary_row(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "year": self.year if self.year is not None else "",
            "method": self.method,
            "hover": self.hover,
            "geometry_constraint": self.geometry_constraint,
            "open_source": self.open_source,
            "relevance": self.relevance,
            "borrow": self.borrow,
            "source": self.source,
            "doi": self.doi,
            "arxiv": self.arxiv,
            "url": self.url,
            "query": self.query,
        }


def normalize_title(title: str) -> str:
    """Lower-case, drop punctuation, collapse whitespace (used for title-level dedup)."""
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", (title or "").lower())
    return " ".join(cleaned.split())


def clean_whitespace(text: str) -> str:
    return " ".join((text or "").split())


def strip_jats(text: str) -> str:
    """Crossref ships abstracts as JATS/XML; drop tags and common entity references."""
    without_tags = re.sub(r"<[^>]+>", " ", text or "")
    for src, dst in (
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&quot;", '"'),
        ("&#x27;", "'"),
        ("&nbsp;", " "),
        ("&amp;", "&"),
    ):
        without_tags = without_tags.replace(src, dst)
    return clean_whitespace(without_tags)


# ----------------------------------------------------------------------------- classifiers
_METHOD_PATTERNS: tuple[tuple[str, str], ...] = (
    ("diffusion", "generative diffusion model"),
    ("ddpm", "generative diffusion model"),
    ("variational autoencoder", "generative latent model (VAE/CVAE)"),
    ("vae", "generative latent model (VAE/CVAE)"),
    ("generative adversarial", "generative adversarial model"),
    ("gan", "generative adversarial model"),
    ("deep learning", "neural surrogate / deep learning"),
    ("machine learning", "neural surrogate / deep learning"),
    ("neural network", "neural surrogate / deep learning"),
    ("surrogate", "surrogate-assisted optimization"),
    ("kriging", "surrogate-assisted optimization"),
    ("gaussian process", "Bayesian / surrogate optimization"),
    ("bayesian optimization", "Bayesian / surrogate optimization"),
    ("multi-fidelity", "multi-fidelity optimization"),
    ("multifidelity", "multi-fidelity optimization"),
    ("adjoint", "gradient / adjoint optimization"),
    ("differentiable", "differentiable programming"),
    ("topology optimization", "topology optimization"),
    ("shape optimization", "gradient-free / evolutionary shape optimization"),
    ("genetic algorithm", "gradient-free / evolutionary shape optimization"),
    ("evolution strategy", "gradient-free / evolutionary shape optimization"),
    ("cma-es", "gradient-free / evolutionary shape optimization"),
    ("nsga", "gradient-free / evolutionary shape optimization"),
    ("blade element momentum", "BEMT / low-order aerodynamic model"),
    ("blade element", "BEMT / low-order aerodynamic model"),
    ("momentum theory", "BEMT / low-order aerodynamic model"),
    ("vortex lattice", "vortex-lattice / panel method"),
    ("panel method", "vortex-lattice / panel method"),
    ("panel/boundary-layer", "panel/boundary-layer method (XFOIL-class)"),
    ("free vortex", "vortex-wake method"),
    ("large eddy", "CFD (LES/DNS)"),
    ("dns", "CFD (LES/DNS)"),
    ("rans", "CFD (RANS)"),
    ("cfd", "CFD (RANS)"),
    ("xfoil", "panel/boundary-layer method (XFOIL-class)"),
    ("cst", "CST geometry parameterization"),
    ("class shape", "CST geometry parameterization"),
    ("bezier", "geometry parameterization"),
    ("b-spline", "geometry parameterization"),
    ("parameterization", "geometry parameterization"),
)

_HOVER_STRONG: tuple[str, ...] = (
    "hover",
    "figure of merit",
    "coaxial rotor",
    "rotorcraft",
    "helicopter",
    "vtol",
    "multirotor",
)
_HOVER_WEAK: tuple[str, ...] = ("rotor", "propeller", "ducted fan", "uam", "aam", "vertical take")
_GEOMETRY_HARD: tuple[str, ...] = (
    "trailing edge",
    "trailing-edge",
    "manufactur",
    "geometric constraint",
    "thickness constraint",
    "feasible region",
)
_GEOMETRY_SOFT: tuple[str, ...] = (
    "geometry",
    "parameterization",
    "shape function",
    "camber",
    "thickness",
    "constraint",
    "tolerance",
)
_OPENSOURCE_PATTERNS: tuple[str, ...] = (
    "open source",
    "open-source",
    "github.com",
    "gitlab",
    "zenodo",
    "software is available",
    "code is available",
    "publicly available",
)
_CORE_KEYWORDS: tuple[str, ...] = (
    "figure of merit",
    "hover",
    "blade element",
    "rotor",
    "trailing edge",
    "airfoil optimization",
    "airfoil design",
    "cst",
    "manufacturing constraint",
    "surrogate",
)


def _contains_any(text: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        if pattern in text:
            return pattern
    return None


def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        if value not in out:
            out.append(value)
    return out


def classify_method(record: Record) -> str:
    text = record.text()
    hits = [label for needle, label in _METHOD_PATTERNS if needle in text]
    return "; ".join(_unique(hits)[:3]) if hits else "review / other"


def classify_hover(record: Record) -> str:
    text = record.text()
    if _contains_any(text, _HOVER_STRONG):
        return YES
    if _contains_any(text, _HOVER_WEAK):
        return PARTIAL
    return NO


def classify_geometry(record: Record) -> str:
    text = record.text()
    if _contains_any(text, _GEOMETRY_HARD):
        return YES
    if _contains_any(text, _GEOMETRY_SOFT):
        return YES
    if "airfoil" in text or "blade" in text:
        return PARTIAL
    return NO


def classify_open_source(record: Record) -> str:
    blob = f"{record.text()} {record.url}".lower()
    if _contains_any(blob, _OPENSOURCE_PATTERNS):
        return YES
    if record.arxiv:
        return PARTIAL  # open text, code unverified
    return UNKNOWN


def classify_relevance(record: Record) -> str:
    """Score against what HoverBlade-FM is about; high >= 6, medium >= 3."""
    text = record.text()
    score = sum(1 for keyword in _CORE_KEYWORDS if keyword in text)
    if "trailing edge" in text or "trailing-edge" in text:
        score += 2
    if "hover" in text and "figure of merit" in text:
        score += 2
    if record.year is not None and record.year >= 2022:
        score += 1
    if score >= 6:
        return "high"
    if score >= 3:
        return "medium"
    return "low"


def classify_borrow(record: Record) -> str:
    """Concrete module of HoverBlade-FM this record could feed (可借鉴点)."""
    text = record.text()
    notes: list[str] = []
    if "trailing edge" in text or "trailing-edge" in text:
        notes.append("blunt-TE drag penalty and TE-thickness geometry (geometry/, aero/polar)")
    if "figure of merit" in text or "hover" in text:
        notes.append("hover FM objective and thrust-matching tolerance (optimize/objective)")
    if _contains_any(text, ("blade element", "momentum theory")):
        notes.append("BEMT solver structure and loss handling (aero/bemt)")
    if _contains_any(text, ("multi-fidelity", "surrogate", "kriging", "gaussian process")):
        notes.append("fidelity ladder / surrogate caching (aero/polar, optimize/surrogate)")
    if _contains_any(
        text,
        (
            "diffusion",
            "variational autoencoder",
            "generative adversarial",
            "deep learning",
            "neural",
            "latent",
        ),
    ):
        notes.append("generative warm start or learned design space (optimize/init)")
    if _contains_any(text, ("cst", "class shape", "bezier", "b-spline", "parameterization")):
        notes.append("airfoil parameterization with a controllable TE gap (geometry/cst)")
    if _contains_any(text, ("adjoint", "differentiable", "gradient")):
        notes.append("gradient route for local refinement (optimize/local, JAX track)")
    if _contains_any(text, ("manufactur", "tolerance", "cnc")):
        notes.append("manufacturing checks and report items (cad/report)")
    if _contains_any(text, _OPENSOURCE_PATTERNS):
        notes.append("reference implementation to compare against (tests/golden)")
    return "; ".join(notes[:3]) if notes else "background context; no direct module"


def auto_annotate(record: Record) -> Record:
    """Fill the 计划.md 7.4 screening columns from keyword heuristics (live records)."""
    if record.annotated:
        return record
    record.method = record.method or classify_method(record)
    record.hover = record.hover or classify_hover(record)
    record.geometry_constraint = record.geometry_constraint or classify_geometry(record)
    record.open_source = record.open_source or classify_open_source(record)
    record.relevance = record.relevance or classify_relevance(record)
    record.borrow = record.borrow or classify_borrow(record)
    return record


def _richness(record: Record) -> int:
    score = 0
    if record.annotated:
        score += 8
    if record.abstract:
        score += 2
    if record.venue:
        score += 1
    if record.doi:
        score += 1
    if record.year is not None:
        score += 1
    return score


def dedupe(records: list[Record]) -> list[Record]:
    """Drop repeats: DOI match first, then normalised-title match.

    When two rows collide the richer one wins (curated annotation beats heuristics).
    """
    seen: dict[str, int] = {}
    out: list[Record] = []
    for record in records:
        hit = seen.get(record.dedup_key())
        if hit is None:
            hit = seen.get(record.title_key())
        if hit is not None:
            previous = out[hit]
            if _richness(record) > _richness(previous):
                out[hit] = record
            continue
        seen[record.dedup_key()] = len(out)
        seen[record.title_key()] = len(out)
        out.append(record)
    return out


def in_year_window(record: Record, years: tuple[int, int], *, keep_curated: bool = True) -> bool:
    """Curated seed rows always survive: they include deliberately foundational classics."""
    if keep_curated and record.source.startswith("seed"):
        return True
    if record.year is None:
        return True  # unknown year: keep, the CSV shows it blank
    return years[0] <= record.year <= years[1]


# ----------------------------------------------------------------------------- logging


def warn(message: str) -> None:
    print(f"{LOG_PREFIX} warn: {message}", file=sys.stderr)


def info(message: str) -> None:
    print(f"{LOG_PREFIX} {message}", file=sys.stderr)


class HttpError(RuntimeError):
    """Transport-level failure; callers log and continue."""


# ----------------------------------------------------------------------------- HTTP layer


def insecure_tls_allowed(url: str) -> bool:
    """True only when the operator opted in *and* the host is a public metadata endpoint."""
    if os.environ.get(INSECURE_TLS_ENV, "") not in {"1", "true", "yes", "on"}:
        return False
    host = urllib.parse.urlparse(url).netloc.lower()
    return host in TLS_FALLBACK_HOSTS


def _ssl_contexts(url: str) -> list[ssl.SSLContext | None]:
    contexts: list[ssl.SSLContext | None] = [None]
    if insecure_tls_allowed(url):
        with contextlib.suppress(Exception):  # pragma: no cover - exotic platforms without ssl
            contexts.append(ssl._create_unverified_context())
    return contexts


def _is_cert_failure(exc: BaseException) -> bool:
    if isinstance(exc, ssl.SSLCertVerificationError):
        return True
    return "CERTIFICATE_VERIFY_FAILED" in str(exc)


def _open_text(url: str, timeout: float, context: ssl.SSLContext | None) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        payload = response.read()
    return payload.decode("utf-8", errors="replace")


def http_get(url: str, *, timeout: float = HTTP_TIMEOUT_S) -> str:
    """GET a text body with retries and backoff.  Raises HttpError on hard failure."""
    last: BaseException | None = None
    contexts = _ssl_contexts(url)
    for attempt in range(MAX_RETRIES + 1):
        retry_later = False
        for ctx in contexts:
            try:
                body = _open_text(url, timeout, ctx)
                if ctx is not None:
                    warn(
                        f"certificate verification was skipped for {urllib.parse.urlparse(url).netloc} "
                        f"because {INSECURE_TLS_ENV} is set"
                    )
                return body
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code in RETRY_STATUS and attempt < MAX_RETRIES:
                    retry_later = True
                    break
                raise HttpError(f"HTTP {exc.code} for {url}") from exc
            except (ssl.SSLCertVerificationError, urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
                if _is_cert_failure(exc):
                    continue  # try the next context in the list, then give up on this attempt
                retry_later = True
                break
        if retry_later and attempt < MAX_RETRIES:
            time.sleep(BACKOFF_S * (attempt + 1))
    raise HttpError(f"unreachable: {url} ({type(last).__name__}: {last})")


@dataclass(slots=True)
class SearchSession:
    """Holds rate-limit state and a circuit breaker so one dead network is cheap to detect."""

    limit: int = DEFAULT_LIMIT
    years: tuple[int, int] = DEFAULT_YEARS
    failures: int = 0
    calls: int = 0
    polite_delay: float = POLITE_DELAY_S
    circuit_breaker_after: int = CIRCUIT_BREAKER_AFTER

    @property
    def degraded(self) -> bool:
        return self.failures >= self.circuit_breaker_after

    def fetch(self, url: str, *, label: str, sleep: float) -> str:
        if self.degraded:
            return ""
        self.calls += 1
        try:
            body = http_get(url)
        except HttpError as exc:
            self.failures += 1
            warn(f"{label}: {exc}")
            if self.degraded:
                warn("circuit breaker open: too many transport failures; switching to the seed set")
            return ""
        if sleep:
            time.sleep(sleep)
        return body

    def arxiv(self, query: str) -> list[Record]:
        url = arxiv_url(query, limit=self.limit)
        body = self.fetch(url, label=f"arXiv '{query}'", sleep=self.polite_delay)
        return parse_arxiv_atom(body, query=query, source="arxiv")[: self.limit]

    def crossref(self, query: str) -> list[Record]:
        url = crossref_url(query, rows=self.limit, years=self.years)
        body = self.fetch(url, label=f"Crossref '{query}'", sleep=CROSSREF_DELAY_S)
        if not body:
            return []
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            warn(f"Crossref '{query}': non-JSON response")
            return []
        return parse_crossref_json(payload, query=query)[: self.limit]


# ----------------------------------------------------------------------------- arXiv
ATOM_NS = "http://www.w3.org/2005/Atom"
ARXIV_NS = "http://arxiv.org/schemas/atom"


def arxiv_url(query: str, *, limit: int = DEFAULT_LIMIT) -> str:
    params = {
        "search_query": f"all:{query}",
        "start": "0",
        "max_results": str(max(limit, 1)),
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    return f"{ARXIV_ENDPOINT}?{urllib.parse.urlencode(params)}"


def _looks_unsafe(xml_text: str) -> bool:
    """Reject feeds carrying a DTD / entity declarations (billion-laughs, XXE)."""
    head = xml_text[:4000].lower()
    return "<!doctype" in head or "<!entity" in head


def parse_arxiv_atom(xml_text: str, *, query: str = "", source: str = "arxiv") -> list[Record]:
    """Parse an arXiv Atom feed into records; empty/malformed/unsafe input yields []."""
    if not xml_text or not xml_text.strip():
        return []
    if _looks_unsafe(xml_text):
        warn("arXiv feed declares a DTD or entities; refusing to parse")
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        warn("arXiv returned unparseable XML")
        return []
    records: list[Record] = []
    for entry in root.findall(f"{{{ATOM_NS}}}entry"):
        title = clean_whitespace(_atom_text(entry, "title"))
        if not title:
            continue
        raw_id = _atom_text(entry, "id")
        arxiv_id = _arxiv_id(raw_id)
        published = _atom_text(entry, "published") or _atom_text(entry, "updated")
        match = re.match(r"(\d{4})", published)
        year = int(match.group(1)) if match else None
        authors = [
            clean_whitespace(el.findtext(f"{{{ATOM_NS}}}name") or "")
            for el in entry.findall(f"{{{ATOM_NS}}}author")
        ]
        authors = [a for a in authors if a]
        journal_ref = _atom_ns_text(entry, "journal_ref", ARXIV_NS)
        comment = _atom_ns_text(entry, "comment", ARXIV_NS)
        doi = _atom_ns_text(entry, "doi", ARXIV_NS)
        abstract = clean_whitespace(_atom_text(entry, "summary"))
        if comment:
            abstract = clean_whitespace(f"{abstract} [arXiv comment: {comment}]")
        link = raw_id.replace("http://", "https://") if raw_id else ""
        records.append(
            Record(
                title=title,
                authors=authors,
                year=year,
                venue=journal_ref or "arXiv preprint",
                doi=doi,
                arxiv=arxiv_id,
                url=link or (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""),
                abstract=abstract,
                query=query,
                source=source,
            )
        )
    return records


def _atom_text(element: ET.Element, tag: str) -> str:
    child = element.find(f"{{{ATOM_NS}}}{tag}")
    return (child.text or "") if child is not None and child.text else ""


def _atom_ns_text(element: ET.Element, tag: str, ns: str) -> str:
    child = element.find(f"{{{ns}}}{tag}")
    return clean_whitespace(child.text or "") if child is not None and child.text else ""


def _arxiv_id(raw_id: str) -> str:
    """``https://arxiv.org/abs/2408.15898v3`` -> ``2408.15898``."""
    match = re.search(r"(\d{4}\.\d{4,5})(v\d+)?$", raw_id or "")
    if match:
        return match.group(1)
    trimmed = (raw_id or "").rsplit("/", 1)[-1]
    return re.sub(r"v\d+$", "", trimmed)


# ----------------------------------------------------------------------------- Crossref


def crossref_url(
    query: str, *, rows: int = DEFAULT_LIMIT, years: tuple[int, int] | None = DEFAULT_YEARS
) -> str:
    params: dict[str, str] = {
        "query.bibliographic": query,
        "rows": str(max(rows, 1)),
        "select": "DOI,title,author,issued,container-title,abstract,URL,resource,type",
    }
    if years is not None:
        params["filter"] = f"from-created-date:{years[0]}-01-01,until-created-date:{years[1]}-12-31"
    return f"{CROSSREF_ENDPOINT}?{urllib.parse.urlencode(params)}"


def parse_crossref_json(payload: Any, *, query: str = "", source: str = "crossref") -> list[Record]:
    """Parse a Crossref /works response (dict or JSON text); tolerate partial items."""
    if isinstance(payload, str):
        if not payload.strip():
            return []
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            warn("Crossref returned non-JSON body")
            return []
    items = (payload or {}).get("message", {}).get("items", [])
    if not isinstance(items, list):
        return []
    records: list[Record] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        titles = item.get("title") or []
        title = clean_whitespace(titles[0]) if titles else ""
        if not title:
            continue
        authors: list[str] = []
        for author in item.get("author") or []:
            family = clean_whitespace(author.get("family") or "")
            given = clean_whitespace(author.get("given") or "")
            name = f"{family} {given}".strip()
            if name:
                authors.append(name)
        year: int | None = None
        date_parts = (item.get("issued") or {}).get("date-parts") or []
        if date_parts and date_parts[0] and date_parts[0][0]:
            year = int(date_parts[0][0])
        containers = item.get("container-title") or []
        venue = clean_whitespace(containers[0]) if containers else clean_whitespace(item.get("type") or "")
        doi = clean_whitespace(item.get("DOI") or "")
        url = (
            f"https://doi.org/{doi}"
            if doi
            else clean_whitespace((item.get("resource") or {}).get("primary", {}).get("URI"))
        )
        records.append(
            Record(
                title=title,
                authors=authors,
                year=year,
                venue=venue,
                doi=doi,
                url=url,
                abstract=strip_jats(item.get("abstract") or ""),
                query=query,
                source=source,
            )
        )
    return records


# ----------------------------------------------------------------------------- seed data
# Curated fallback set (plan T008: `--offline` must still produce content).  Every entry is a
# real paper whose DOI / arXiv identifier was checked against the Crossref or arXiv API during
# the writing of this repository; the screening columns are hand annotated, not heuristic.
SEED_DATA: tuple[dict[str, Any], ...] = (
    {
        "title": "Recent extensions and applications of the 'CST' universal parametric geometry representation method",
        "authors": ["B. M. Kulfan"],
        "year": 2010,
        "venue": "The Aeronautical Journal 114(1153): 157-176",
        "doi": "10.1017/S0001924000003614",
        "url": "https://doi.org/10.1017/S0001924000003614",
        "abstract": "Defines the class function / shape function transformation (CST) for airfoil geometry: the class-"
        "function exponents set leading-edge roundness and trailing-edge closure, and the paper covers extensions to "
        "finite trailing-edge thickness. Foundation of this project's parameterization.",
        "query": "CST airfoil parameterization trailing edge thickness",
        "method": "CST geometry parameterization",
        "hover": NO,
        "geometry_constraint": YES,
        "open_source": NO,
        "relevance": "high",
        "borrow": "class/shape split and Bernstein shape functions; explicit TE-closure terms (geometry/cst)",
    },
    {
        "title": "Aerodynamic Analysis of Blunt Trailing Edge Airfoils",
        "authors": ["Kevin J. Standish", "Cornelius P. van Dam"],
        "year": 2003,
        "venue": "Journal of Solar Energy Engineering 125(4): 479-487",
        "doi": "10.1115/1.1629103",
        "url": "https://doi.org/10.1115/1.1629103",
        "abstract": "RANS analysis of wind-turbine airfoils cut off at a finite-thickness trailing edge: blunt edges "
        "raise maximum lift and lift-curve slope through a milder pressure recovery at the cost of a substantial base "
        "(profile) drag penalty, while easing structural and manufacturing demands.",
        "query": "blunt trailing edge airfoil manufacturing constraint",
        "method": "CFD (RANS) plus low-order analysis",
        "hover": PARTIAL,
        "geometry_constraint": YES,
        "open_source": NO,
        "relevance": "high",
        "borrow": "sign of the blunt-TE drag penalty versus lift gain; treat t_TE as an explicit design variable "
        "(aero/polar, geometry/cst)",
    },
    {
        "title": "Effect of rounded trailing edges on unsteady airfoil loading at low reynolds numbers",
        "authors": ["Yi Tsung Lee", "Kiran Ramesh", "Ashok Gopalarathnam"],
        "year": 2025,
        "venue": "Theoretical and Computational Fluid Dynamics 39",
        "doi": "10.1007/s00162-025-00747-z",
        "url": "https://doi.org/10.1007/s00162-025-00747-z",
        "abstract": "Unsteady low-Reynolds-number study of airfoils with a rounded rather than sharp trailing edge: at "
        "high reduced frequency the flow curves around the edge, the stagnation point leaves the Kutta point and "
        "loading departs from the sharp-edge case. Finite edges have to be modelled, not patched.",
        "query": "blunt trailing edge airfoil manufacturing constraint",
        "method": "unsteady low-order / vortex methods",
        "hover": PARTIAL,
        "geometry_constraint": YES,
        "open_source": UNKNOWN,
        "relevance": "high",
        "borrow": "low-Re finite/rounded TE behaviour near a rotor blade; supports the included-angle and t/c bounds "
        "(geometry/te_constraint)",
    },
    {
        "title": "Fast and Robust Computation of Optimal Rotor Designs Using Blade Element Momentum Theory",
        "authors": ["Marius L. Ruh", "John T. Hwang"],
        "year": 2023,
        "venue": "AIAA Journal 61(9): 4096-4111",
        "doi": "10.2514/1.J062611",
        "url": "https://doi.org/10.2514/1.J062611",
        "abstract": "Derives a robust BEM procedure that computes optimal rotor designs quickly even when detailed blade "
        "geometry is unavailable, addressing the convergence failures that plague classical BEM iteration loops during "
        "conceptual design of urban-air-mobility aircraft.",
        "query": "blade element momentum theory hover rotor optimization",
        "method": "BEMT / low-order aerodynamic model plus optimization",
        "hover": YES,
        "geometry_constraint": PARTIAL,
        "open_source": UNKNOWN,
        "relevance": "high",
        "borrow": "robustness of the hover BEMT fixed point (relaxation, convergence guarantees) (aero/bemt)",
    },
    {
        "title": "Modeling of Hovering Intermeshing Rotor Using Blade Element Momentum Theory",
        "authors": ["Guanbin Sheen", "Hexi Baoyin", "Xianyu Wang", "Jingyang Li"],
        "year": 2023,
        "venue": "Journal of Aircraft 60(2): 560-576",
        "doi": "10.2514/1.C036528",
        "url": "https://doi.org/10.2514/1.C036528",
        "abstract": "A hover BEMT model that uses blade tip/hub loss factors to represent three-dimensional effects and "
        "adds skew-downwash interference terms for intermeshing rotors, validated against higher-fidelity results.",
        "query": "blade element momentum theory hover rotor optimization",
        "method": "BEMT / low-order aerodynamic model",
        "hover": YES,
        "geometry_constraint": PARTIAL,
        "open_source": UNKNOWN,
        "relevance": "high",
        "borrow": "Prandtl tip/root loss formulation and where it strains as r/R approaches 1 (aero/losses)",
    },
    {
        "title": "Improvements in Robustness and Versatility of Blade Element Momentum Theory for UAM/AAM Applications",
        "authors": ["Myungsik Tai", "Wooseung Lee", "Dahye Kim", "Donghun Park"],
        "year": 2025,
        "venue": "Aerospace 12(8): 728",
        "doi": "10.3390/aerospace12080728",
        "url": "https://doi.org/10.3390/aerospace12080728",
        "abstract": "Reformulates BEMT with a velocity factor that removes the numerical singularity at low inflow "
        "velocity and extends the framework to non-axial inflow and descent, validated for an isolated propeller in "
        "hover against wind-tunnel and CFD data.",
        "query": "blade element momentum theory hover rotor optimization",
        "method": "BEMT / low-order aerodynamic model",
        "hover": YES,
        "geometry_constraint": NO,
        "open_source": UNKNOWN,
        "relevance": "high",
        "borrow": "singularity-free induction iteration; a checklist of hover-solver failure modes for tests",
    },
    {
        "title": "Unified momentum model for rotor aerodynamics across operating regimes",
        "authors": ["Jaime Liew", "Kirby S. Heck", "Michael F. Howland"],
        "year": 2024,
        "venue": "Nature Communications 15",
        "doi": "10.1038/s41467-024-50756-5",
        "url": "https://doi.org/10.1038/s41467-024-50756-5",
        "abstract": "Derives one momentum model that stays valid from wind-turbine to high-thrust hover regimes instead "
        "of stitching empirical corrections together, and ships an open-source Python implementation including a "
        "blade-element form.",
        "query": "open source propeller rotor design optimization",
        "method": "momentum theory plus blade element model; open-source code",
        "hover": YES,
        "geometry_constraint": PARTIAL,
        "open_source": YES,
        "relevance": "high",
        "borrow": "open reference rotor model for regression comparison; principled replacement for empirical polar "
        "corrections (roadmap v0.2)",
    },
    {
        "title": "Multi-Fidelity Bayesian Optimization of a Coaxial Rotor for eVTOL Aircraft",
        "authors": ["Racheal M. Erhard", "Juan J. Alonso"],
        "year": 2024,
        "venue": "AIAA SCITECH 2024 Forum",
        "doi": "10.2514/6.2024-2506",
        "url": "https://doi.org/10.2514/6.2024-2506",
        "abstract": "Couples low-, medium- and high-fidelity rotor analyses inside a Bayesian optimization loop for "
        "coaxial eVTOL rotors, reaching comparable optima with far fewer high-fidelity evaluations.",
        "query": "multi-fidelity Bayesian optimization rotor blade",
        "method": "Bayesian / surrogate optimization; multi-fidelity optimization",
        "hover": YES,
        "geometry_constraint": PARTIAL,
        "open_source": UNKNOWN,
        "relevance": "high",
        "borrow": "the fidelity ladder (analytic polar -> BEMT -> CFD) deferred to v0.3, and how to budget expensive "
        "evaluations",
    },
    {
        "title": "Multi-Fidelity Modeling of Isolated Hovering Rotors",
        "authors": ["Jason Cornelius", "Nicholas Peters", "Tove Ågren", "Hugo Hjelm"],
        "year": 2025,
        "venue": "Aerospace 12(8): 650",
        "doi": "10.3390/aerospace12080650",
        "url": "https://doi.org/10.3390/aerospace12080650",
        "abstract": "Assesses multi-fidelity modelling for hovering rotors by perturbing rotor design and operating "
        "variables across a low- and high-fidelity simulation database, quantifying accuracy gained per unit cost.",
        "query": "hover rotor surrogate model figure of merit",
        "method": "multi-fidelity optimization; surrogate-assisted optimization",
        "hover": YES,
        "geometry_constraint": PARTIAL,
        "open_source": UNKNOWN,
        "relevance": "medium",
        "borrow": "which design perturbations actually move hover performance: a prioritisation of our design vector",
    },
    {
        "title": "Aerodynamic optimization of a coaxial rotor system using a deep learning-based multi-fidelity surrogate model",
        "authors": ["Shu-Hui Qin", "Ai-Ming Yang"],
        "year": 2025,
        "venue": "Engineering Applications of Computational Fluid Mechanics 19(1)",
        "doi": "10.1080/19942060.2025.2528120",
        "url": "https://doi.org/10.1080/19942060.2025.2528120",
        "abstract": "Maximises the hover figure of merit of a coaxial rotor at fixed total thrust coefficient using a "
        "deep-learning multi-fidelity surrogate trained on low-, medium- and high-fidelity aerodynamic models.",
        "query": "helicopter rotor hover figure of merit optimization",
        "method": "neural surrogate / deep learning; multi-fidelity optimization",
        "hover": YES,
        "geometry_constraint": PARTIAL,
        "open_source": UNKNOWN,
        "relevance": "high",
        "borrow": "confirms FM-at-fixed-thrust as the standard hover objective; surrogate warm start ahead of CMA-ES",
    },
    {
        "title": "Topologically assisted optimization for rotor design",
        "authors": ["Tianyu Wang", "Yannian Yang", "Xuanwu Chen", "Pengyu Li", "Angelo Iollo"],
        "year": 2023,
        "venue": "arXiv preprint (physics.flu-dyn)",
        "arxiv": "2302.08728",
        "url": "https://arxiv.org/abs/2302.08728",
        "abstract": "Two-stage rotor optimisation with respect to the figure of merit: a global evolutionary search over "
        "shape parameters followed by a topological analysis of the resulting blade, i.e. exactly the "
        "global-then-local pattern this project adopts with CMA-ES followed by SLSQP.",
        "query": "helicopter rotor hover figure of merit optimization",
        "method": "gradient-free / evolutionary shape optimization; topology optimization",
        "hover": YES,
        "geometry_constraint": YES,
        "open_source": PARTIAL,
        "relevance": "high",
        "borrow": "global evolutionary stage plus local refinement, geometric feasibility as a hard filter "
        "(optimize/cmaes, geometry/checks)",
    },
    {
        "title": "Low-Reynolds-number airfoil design optimization using deep-learning-based tailored airfoil modes",
        "authors": [
            "Jichao Li",
            "Mengqi Zhang",
            "Chien Ming Jonathan Tay",
            "Ningyu Liu",
            "Yongdong Cui",
            "Siou Chye Chew",
        ],
        "year": 2022,
        "venue": "Aerospace Science and Technology 121: 107309",
        "doi": "10.1016/j.ast.2021.107309",
        "url": "https://doi.org/10.1016/j.ast.2021.107309",
        "abstract": "Learns a compact, task-specific airfoil mode basis from data and optimises inside that space for "
        "low-Reynolds-number performance, keeping generated shapes physically meaningful instead of off-manifold.",
        "query": "low Reynolds number airfoil design optimization",
        "method": "neural surrogate / deep learning; geometry parameterization",
        "hover": PARTIAL,
        "geometry_constraint": YES,
        "open_source": UNKNOWN,
        "relevance": "high",
        "borrow": "few well-chosen modes beat many free coefficients: rationale for 5+5 CST orders and the spanwise "
        "smoothness penalty",
    },
    {
        "title": "A generative design method of airfoil based on conditional variational autoencoder",
        "authors": ["Xu Wang", "Weiqi Qian", "Tun Zhao", "Hai Chen", "Lei He", "Haisheng Sun"],
        "year": 2025,
        "venue": "Engineering Applications of Artificial Intelligence 139: 109461",
        "doi": "10.1016/j.engappai.2024.109461",
        "url": "https://doi.org/10.1016/j.engappai.2024.109461",
        "abstract": "Conditions a VAE on target aerodynamic performance to generate airfoils; conditional generative "
        "models propose valid starting shapes but still need geometric post-processing to guarantee manufacturable "
        "trailing edges.",
        "query": "conditional diffusion model airfoil design",
        "method": "generative latent model (VAE/CVAE)",
        "hover": NO,
        "geometry_constraint": PARTIAL,
        "open_source": UNKNOWN,
        "relevance": "medium",
        "borrow": "candidate warm-start mechanism for the generative track (T089); evidence that generated shapes still "
        "need a hard constraint layer",
    },
    {
        "title": "Airfoil Diffusion: Denoising Diffusion Model For Conditional Airfoil Generation",
        "authors": ["Reid Graves", "Amir Barati Farimani"],
        "year": 2024,
        "venue": "arXiv preprint (cs.LG)",
        "arxiv": "2408.15898",
        "url": "https://arxiv.org/abs/2408.15898",
        "abstract": "Trains a diffusion model on existing airfoils to sample arbitrary numbers of conditioned shapes, "
        "loosening the dependence on a hand-written parameterization; satisfaction of hard geometric constraints is "
        "statistical rather than guaranteed.",
        "query": "airfoil generation diffusion model",
        "method": "generative diffusion model",
        "hover": NO,
        "geometry_constraint": NO,
        "open_source": PARTIAL,
        "relevance": "medium",
        "borrow": "future conditional sampler for (thrust, radius, t_TE, target FM); also the clearest argument for "
        "keeping generative models out of the MVP mainline",
    },
    {
        "title": "AeroDiT: Diffusion Transformers for Reynolds-Averaged Navier-Stokes Simulations of Airfoil Flows",
        "authors": ["Chunyang Wang", "Biyue Pan", "Zhibo Dai", "Yudi Cai", "Yuhao Ma"],
        "year": 2024,
        "venue": "arXiv preprint (physics.flu-dyn)",
        "arxiv": "2412.17394",
        "url": "https://arxiv.org/abs/2412.17394",
        "abstract": "A latent diffusion transformer predicts RANS flow fields around airfoils in real time, offering a "
        "middle rung between analytic polars and full CFD.",
        "query": "differentiable CFD aerodynamic shape optimization",
        "method": "generative diffusion model; neural surrogate / deep learning",
        "hover": NO,
        "geometry_constraint": NO,
        "open_source": PARTIAL,
        "relevance": "medium",
        "borrow": "candidate medium-fidelity polar evaluator behind the aero/polar interface",
    },
    {
        "title": "Multi-Fidelity Training Data Generation for the Development of DeepAirfoil, an Airfoil Design and Analysis Tool",
        "authors": ["Richard C. Healy", "Nathan Beals", "Phuriwat Anusonti-Inthra"],
        "year": 2024,
        "venue": "AIAA AVIATION Forum and ASCEND 2024",
        "doi": "10.2514/6.2024-4140",
        "url": "https://doi.org/10.2514/6.2024-4140",
        "abstract": "Describes how the open-source DeepAirfoil toolchain builds its multi-fidelity training set for "
        "airfoil analysis and design: an end-to-end example of an open airfoil-design codebase.",
        "query": "open source propeller rotor design optimization",
        "method": "CFD (RANS); surrogate-assisted optimization; open-source tooling",
        "hover": NO,
        "geometry_constraint": PARTIAL,
        "open_source": YES,
        "relevance": "medium",
        "borrow": "benchmark for our own open-source packaging: data layout and fidelity bookkeeping",
    },
    {
        "title": "Inverse Design of Two-Dimensional Airfoils Using Conditional Generative Models and Surrogate Log-Likelihoods",
        "authors": ["Qiuyi Chen", "Jun Wang", "Phillip Pope", "Wei Chen", "Mark Fuge"],
        "year": 2022,
        "venue": "Journal of Mechanical Design 144(2): 021712",
        "doi": "10.1115/1.4052846",
        "url": "https://doi.org/10.1115/1.4052846",
        "abstract": "Uses conditional generative models with surrogate log-likelihoods to propose good initialisation points "
        "near the optimum for a target performance, explicitly handling the one-to-many ambiguity of airfoil inverse "
        "design.",
        "query": "airfoil inverse design machine learning",
        "method": "generative latent model (VAE/CVAE); surrogate-assisted optimization",
        "hover": NO,
        "geometry_constraint": PARTIAL,
        "open_source": UNKNOWN,
        "relevance": "medium",
        "borrow": "warm-start of the initial-design generator (T049) instead of a hand-set analytic starting blade",
    },
    {
        "title": "Airfoil Design Parameterization and Optimization Using B\u00e9zier Generative Adversarial Networks",
        "authors": ["Wei Chen", "Kevin Chiu", "Mark D. Fuge"],
        "year": 2020,
        "venue": "AIAA Journal 58(11): 4723-4735",
        "doi": "10.2514/1.J059317",
        "url": "https://doi.org/10.2514/1.J059317",
        "abstract": "Replaces hand-tuned parameterization with a Bezier-GAN latent representation and optimises in that "
        "latent space, so every decoded shape is a valid closed contour: valid-by-construction geometry, the same "
        "philosophy as our hard trailing-edge clamp.",
        "query": "airfoil shape parameterization geometric validity",
        "method": "generative adversarial model; geometry parameterization",
        "hover": NO,
        "geometry_constraint": YES,
        "open_source": UNKNOWN,
        "relevance": "medium",
        "borrow": "valid-by-construction parameterization; argument for clamping t_TE at decode time rather than "
        "penalising it (ADR-002)",
    },
    {
        "title": "XFOIL: An Analysis and Design System for Low Reynolds Number Airfoils",
        "authors": ["Mark Drela"],
        "year": 1989,
        "venue": "Lecture Notes in Engineering (Low Reynolds Number Airfoil Design)",
        "doi": "10.1007/978-3-642-84010-4_1",
        "url": "https://doi.org/10.1007/978-3-642-84010-4_1",
        "abstract": "The classical coupled inviscid-panel / boundary-layer tool for low-Reynolds-number airfoil polars "
        "and design; the reference medium-fidelity polar source that HoverBlade-FM keeps optional behind its polar "
        "interface.",
        "query": "low Reynolds number airfoil design optimization",
        "method": "panel/boundary-layer method (XFOIL-class)",
        "hover": PARTIAL,
        "geometry_constraint": PARTIAL,
        "open_source": YES,
        "relevance": "medium",
        "borrow": "what a polar interface must expose (Re, Mach, lift, drag, transition); target of the optional XFOIL "
        "backend (plan section 10.3)",
    },
    {
        "title": "SciPy 1.0: fundamental algorithms for scientific computing in Python",
        "authors": [
            "Pauli Virtanen",
            "Ralf Gommers",
            "Travis E. Oliphant",
            "Matt Haberland",
            "Tyler Reddy",
            "David Cournapeau",
        ],
        "year": 2020,
        "venue": "Nature Methods 17: 261-272",
        "doi": "10.1038/s41592-019-0686-2",
        "url": "https://doi.org/10.1038/s41592-019-0686-2",
        "abstract": "Reference publication for the SciPy stack, including scipy.optimize.minimize with SLSQP - the local "
        "constrained optimiser used for the refinement stage after the global CMA-ES search.",
        "query": "open source propeller rotor design optimization",
        "method": "open-source scientific computing stack",
        "hover": NO,
        "geometry_constraint": NO,
        "open_source": YES,
        "relevance": "medium",
        "borrow": "the SLSQP dependency itself; cite rather than reimplementing a constrained local optimiser",
    },
    {
        "title": "The CMA Evolution Strategy: A Tutorial",
        "authors": ["Nikolaus Hansen"],
        "year": 2016,
        "venue": "arXiv preprint (math.OC)",
        "arxiv": "1604.00772",
        "url": "https://arxiv.org/abs/1604.00772",
        "abstract": "The standard reference for CMA-ES: covariance adaptation, step-size control, population sizing and "
        "restart logic. Governs the parameter choices behind the fast / standard / full optimisation presets.",
        "query": "rotor blade design optimization constraints CMA-ES",
        "method": "gradient-free / evolutionary shape optimization",
        "hover": NO,
        "geometry_constraint": NO,
        "open_source": YES,
        "relevance": "high",
        "borrow": "popsize / sigma / restart guidance for the CMA-ES wrapper and the preset table in config/schema",
    },
    {
        "title": "An efficient blade sweep correction model for blade element momentum theory",
        "authors": ["Erik Kaspar Fritz", "Carlos Ferreira", "Koen Boorsma"],
        "year": 2022,
        "venue": "Wind Energy 25(12): 1977-1994",
        "doi": "10.1002/we.2778",
        "url": "https://doi.org/10.1002/we.2778",
        "abstract": "Extends standard BEM, which assumes a straight blade in the rotor plane, with an efficient induction "
        "correction for swept blades: a template for bolting an extra correction onto a BEM loop without rebuilding "
        "the method.",
        "query": "blade element momentum theory hover rotor optimization",
        "method": "BEMT / low-order aerodynamic model",
        "hover": PARTIAL,
        "geometry_constraint": NO,
        "open_source": UNKNOWN,
        "relevance": "medium",
        "borrow": "how to plug an additional correction factor into the induction iteration and keep it unit-testable",
    },
    {
        "title": "ELISA: A Tool for Optimization of Rotor Hover Performance at Low Reynolds Number in the Mars Atmosphere",
        "authors": [
            "Witold J. F. Koning",
            "B. Natalia Perez",
            "Haley V. Cummings",
            "Ethan A. Romander",
            "Wayne Johnson",
        ],
        "year": 2024,
        "venue": "Journal of the American Helicopter Society 69",
        "doi": "10.4050/jahs.69.042005",
        "url": "https://doi.org/10.4050/jahs.69.042005",
        "abstract": "The Evolutionary aLgorithm for Iterative Studies of Aeromechanics was built for aerodynamic rotor "
        "hover optimisation at low Reynolds number; its first module deliberately couples an unconventional airfoil "
        "parameterization with multi-objective search - the closest published analogue to this project's problem shape.",
        "query": "hovering rotor blade shape optimization",
        "method": "gradient-free / evolutionary shape optimization; geometry parameterization; BEMT / low-order aerodynamic model",
        "hover": YES,
        "geometry_constraint": YES,
        "open_source": UNKNOWN,
        "relevance": "high",
        "borrow": "hover + low-Re + parameterized sections as one search problem; validation discipline for a hover optimiser",
    },
    {
        "title": "Helicopter rotor blade planform optimization using parametric design and multi-objective genetic algorithm",
        "authors": ["Yonghu Wenren", "Joon Lim", "Luke Allen", "Robert Haehnel", "Ian Dettwiler"],
        "year": 2022,
        "venue": "NASA / AFRL technical report (Crossref record 10.21079/11681/46261)",
        "doi": "10.21079/11681/46261",
        "url": "https://doi.org/10.21079/11681/46261",
        "abstract": "An automated framework couples a parametric design tool, a multi-objective genetic algorithm and a "
        "comprehensive rotor analysis code to optimise blade planform, showing that chord and twist distributions and "
        "section loading are best treated as one design problem.",
        "query": "hovering rotor blade shape optimization",
        "method": "gradient-free / evolutionary shape optimization; geometry parameterization",
        "hover": YES,
        "geometry_constraint": YES,
        "open_source": NO,
        "relevance": "high",
        "borrow": "planform (chord/twist) and section shape in a single design vector, as in the plan's design-variable list",
    },
    {
        "title": "Geometric Programming for Airfoil Shape Optimization With Geometric Constraints",
        "authors": ["Dajung Kim", "Arjit Seth", "Rhea P. Liem"],
        "year": 2025,
        "venue": "AIAA SCITECH 2025 Forum",
        "doi": "10.2514/6.2025-0653",
        "url": "https://doi.org/10.2514/6.2025-0653",
        "abstract": "Treats airfoil shape optimisation with geometric constraints as tractable optimisation structure "
        "(geometric programming) rather than a penalty-weight tuning exercise - the strongest recent argument for "
        "handling thickness and trailing-edge constraints as hard constraints.",
        "query": "airfoil shape parameterization geometric validity",
        "method": "gradient / adjoint optimization; geometry parameterization",
        "hover": NO,
        "geometry_constraint": YES,
        "open_source": UNKNOWN,
        "relevance": "high",
        "borrow": "constraints as structure, not penalties: the same philosophy as the decode-time trailing-edge clamp (ADR-002)",
    },
    {
        "title": "Adjoint-Based Aerodynamic Shape Optimization with a Manifold Constraint Learned by Diffusion Models",
        "authors": ["Long Chen", "Emre Oezkaya", "Jan Rottmayer", "Nicolas R. Gauger", "Zebang Shen"],
        "year": 2025,
        "venue": "arXiv preprint",
        "arxiv": "2507.23443",
        "url": "https://arxiv.org/abs/2507.23443",
        "abstract": "A diffusion model trained on existing designs learns a smooth manifold of aerodynamically viable "
        "shapes which is enforced as an equality constraint inside adjoint-based shape optimisation: generative prior "
        "plus hard constraint, the exact composition this project wants for its optional generative track.",
        "query": "conditional diffusion model airfoil design",
        "method": "generative diffusion model; gradient / adjoint optimization",
        "hover": NO,
        "geometry_constraint": YES,
        "open_source": PARTIAL,
        "relevance": "high",
        "borrow": "pattern for T089: a generator may propose the initial design, the constraint layer still decides validity",
    },
    {
        "title": "Cooperation of Thin-Airfoil Theory and Deep Learning for a Compact Airfoil Shape Parameterization",
        "authors": ["Jianmiao Yi", "Feng Deng"],
        "year": 2023,
        "venue": "Aerospace 10(7): 650",
        "doi": "10.3390/aerospace10070650",
        "url": "https://doi.org/10.3390/aerospace10070650",
        "abstract": "Builds a compact parameterization by putting deep learning on top of a thin-airfoil-theory based "
        "mapping, so the design space stays small and physically meaningful - the same dimensionality argument that "
        "keeps our CST shape functions at order five per surface.",
        "query": "airfoil shape parameterization geometric validity",
        "method": "neural surrogate / deep learning; geometry parameterization",
        "hover": NO,
        "geometry_constraint": YES,
        "open_source": UNKNOWN,
        "relevance": "medium",
        "borrow": "compact design spaces beat expressive ones for gradient-free search over ten stations",
    },
    {
        "title": "Influence of the Blunt Trailing-Edge Thickness on the Aerodynamic Characteristics of the Very Thick Airfoil",
        "authors": ["Zhen Pei", "He-Yong Xu", "Lei Deng", "Ling-Xiao Li"],
        "year": 2023,
        "venue": "Wind 3(4)",
        "doi": "10.3390/wind3040025",
        "url": "https://doi.org/10.3390/wind3040025",
        "abstract": "Experimental and RANS study of how a blunt trailing-edge thickness changes the characteristics of a "
        "60 %-chord airfoil, comparing turbulence models: recent quantitative support for charging drag to the edge "
        "instead of ignoring it.",
        "query": "trailing edge thickness profile drag airfoil",
        "method": "CFD (RANS) plus experiment",
        "hover": NO,
        "geometry_constraint": YES,
        "open_source": YES,
        "relevance": "high",
        "borrow": "magnitude of the trailing-edge thickness penalty on thick sections (aero/polar blunt-edge term)",
    },
    {
        "title": "Effects of Trailing Edge Thickness on NACA 4412 Airfoil Performance at Low Reynolds Numbers: A CFD Analysis",
        "authors": ["Sayed Tanvir Ahmed", "Mahadi Hasan Shanto"],
        "year": 2024,
        "venue": "arXiv preprint",
        "arxiv": "2409.13922",
        "url": "https://arxiv.org/abs/2409.13922",
        "abstract": "CFD study of a cambered airfoil whose trailing edge is modified by thickness at low Reynolds number: "
        "the small-rotor regime where HoverBlade-FM's inboard sections live.",
        "query": "trailing edge thickness profile drag airfoil",
        "method": "CFD (RANS); geometry parameterization",
        "hover": NO,
        "geometry_constraint": YES,
        "open_source": PARTIAL,
        "relevance": "medium",
        "borrow": "low-Re trailing-edge thickness trends for sanity-checking the analytic drag correction",
    },
    {
        "title": "Investigation of discontinuous Galerkin methods in adjoint gradient-based aerodynamic shape optimization",
        "authors": ["Yiwei Feng", "Lili Lv", "Tiegang Liu", "Kun Wang", "Bangcheng Ai"],
        "year": 2024,
        "venue": "arXiv preprint",
        "arxiv": "2407.18963",
        "url": "https://arxiv.org/abs/2407.18963",
        "abstract": "Derives adjoint gradients for high-order discontinuous-Galerkin CFD and builds a gradient-based "
        "aerodynamic shape optimisation framework around them: representative of the gradient route that the plan "
        "reserves for its differentiable (JAX) upgrade track.",
        "query": "differentiable CFD aerodynamic shape optimization",
        "method": "gradient / adjoint optimization; CFD (RANS)",
        "hover": NO,
        "geometry_constraint": PARTIAL,
        "open_source": PARTIAL,
        "relevance": "medium",
        "borrow": "what a gradient-based refinement stage buys, and what it costs, compared with CMA-ES + SLSQP",
    },
)


def seed_records() -> list[Record]:
    """Return the curated fallback set with its screening columns preserved verbatim."""
    out: list[Record] = []
    for item in SEED_DATA:
        record = Record(**item)  # type: ignore[arg-type]
        record.annotated = True
        record.source = record.source or "seed"
        if not record.url and record.doi:
            record.url = f"https://doi.org/{record.doi}"
        if not record.url and record.arxiv:
            record.url = f"https://arxiv.org/abs/{record.arxiv}"
        out.append(record)
    return out


# ----------------------------------------------------------------------------- output files


def write_raw_jsonl(records: list[Record], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record.to_json(), ensure_ascii=False))
            handle.write("\n")


def write_summary_csv(records: list[Record], path: Path) -> None:
    """UTF-8 with BOM so that Excel renders the 计划.md Chinese headers and titles correctly."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[*SUMMARY_COLUMNS, *EXTRA_COLUMNS])
        writer.writeheader()
        for record in records:
            writer.writerow(record.summary_row())


def read_existing_records(path: Path) -> list[Record]:
    """Reload a previous raw.jsonl so repeated runs accumulate rather than replace."""
    if not path.exists():
        return []
    allowed = {f.name for f in Record.__dataclass_fields__.values()}
    records: list[Record] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        kwargs = {k: v for k, v in payload.items() if k in allowed}
        try:
            records.append(Record(**kwargs))
        except TypeError:  # pragma: no cover - defensive against schema drift
            continue
    return records


# ----------------------------------------------------------------------------- pipeline


def collect_live(session: SearchSession, queries: tuple[str, ...]) -> list[Record]:
    """Query both APIs for every query string, annotate and year-filter the hits."""
    records: list[Record] = []
    for query in queries:
        if session.degraded:
            break
        arxiv_hits = session.arxiv(query)
        crossref_hits = session.crossref(query)
        info(f"'{query}': arxiv={len(arxiv_hits)} crossref={len(crossref_hits)}")
        for record in [*arxiv_hits, *crossref_hits]:
            if in_year_window(record, session.years):
                records.append(auto_annotate(record))
    return records


def summarise(records: list[Record]) -> dict[str, Any]:
    by_source: dict[str, int] = {}
    by_relevance: dict[str, int] = {}
    for record in records:
        key = record.source.split(":")[0] or "unknown"
        by_source[key] = by_source.get(key, 0) + 1
        by_relevance[record.relevance or UNKNOWN] = by_relevance.get(record.relevance or UNKNOWN, 0) + 1
    years = [r.year for r in records if r.year is not None]
    return {
        "records": len(records),
        "by_source": by_source,
        "by_relevance": by_relevance,
        "year_min": min(years) if years else None,
        "year_max": max(years) if years else None,
        "with_doi": sum(1 for r in records if r.doi),
        "with_arxiv": sum(1 for r in records if r.arxiv),
    }


def sort_records(records: list[Record]) -> list[Record]:
    rank = {"high": 0, "medium": 1, "low": 2}
    return sorted(records, key=lambda r: (rank.get(r.relevance, 3), -(r.year or 0), r.title.lower()))


def parse_years(value: str) -> tuple[int, int]:
    """Accept ``2022-2025`` or a single year ``2024``."""
    text = value.replace(" ", "")
    match = re.fullmatch(r"(\d{4})(?:-(\d{4}))?", text)
    if not match:
        raise argparse.ArgumentTypeError(f"cannot parse year window '{value}'; use e.g. 2022-2025")
    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else start
    if start > end:
        raise argparse.ArgumentTypeError(f"year window start {start} is after end {end}")
    return (start, end)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="literature_search",
        description="Query arXiv and Crossref for HoverBlade-FM literature; write raw.jsonl + summary.csv.",
    )
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help="output directory (default: docs/literature_search)"
    )
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT, help="max records kept per query per source"
    )
    parser.add_argument(
        "--offline", action="store_true", help="skip all network calls and use the curated seed set"
    )
    parser.add_argument(
        "--years",
        type=parse_years,
        default=DEFAULT_YEARS,
        metavar="YYYY-YYYY",
        help="publication-year window (default: 2022-2025)",
    )
    parser.add_argument(
        "--include-seed", action="store_true", help="merge the curated seed set into a live run"
    )
    parser.add_argument(
        "--no-merge", action="store_true", help="overwrite raw.jsonl instead of merging an existing file"
    )
    parser.add_argument("--quiet", action="store_true", help="suppress progress logging")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    quiet = bool(args.quiet)
    offline = bool(args.offline) or os.environ.get(OFFLINE_ENV, "") in {"1", "true", "yes", "on"}

    if args.limit <= 0:
        print(f"{LOG_PREFIX} error: --limit must be >= 1", file=sys.stderr)
        return 2

    def log(message: str) -> None:
        if not quiet:
            info(message)

    out_dir: Path = args.out
    raw_path = out_dir / RAW_NAME
    csv_path = out_dir / SUMMARY_NAME

    live: list[Record] = []
    session = SearchSession(limit=args.limit, years=args.years)
    if offline:
        log("offline mode: arXiv / Crossref queries skipped")
    else:
        live = collect_live(session, QUERIES)

    use_seed = offline or args.include_seed or not live
    pool: list[Record] = list(live)
    if use_seed:
        pool.extend(seed_records())
    if not live and not offline:
        warn("live search returned nothing; falling back to the curated seed set")

    if not args.no_merge and raw_path.exists():
        previous = read_existing_records(raw_path)
        if previous:
            pool = [*previous, *pool]
            log(f"merged {len(previous)} records from the existing {RAW_NAME}")

    pool = [auto_annotate(record) for record in pool]
    pool = [record for record in pool if in_year_window(record, args.years)]
    unique = sort_records(dedupe(pool))

    if not unique:
        warn("no records collected; writing the curated seed set so CI output is never empty")
        unique = sort_records(seed_records())

    write_raw_jsonl(unique, raw_path)
    write_summary_csv(unique, csv_path)

    stats = summarise(unique)
    log(
        f"wrote {stats['records']} records -> {raw_path} ({raw_path.stat().st_size} B) and "
        f"{csv_path} ({csv_path.stat().st_size} B)"
    )
    log(
        f"sources={stats['by_source']} relevance={stats['by_relevance']} years={stats['year_min']}-{stats['year_max']}"
    )
    if session.calls:
        log(f"http calls={session.calls} transport failures={session.failures}")
    if use_seed:
        log(f"curated seed set used: {len(SEED_DATA)} entries")
    return 0


def cli() -> None:  # pragma: no cover - console-script shim
    try:
        sys.exit(main())
    except KeyboardInterrupt:  # pragma: no cover
        warn("interrupted")
        sys.exit(4)
    except Exception as exc:  # noqa: BLE001 - a research helper must not dump tracebacks on users
        warn(f"internal error: {type(exc).__name__}: {exc}")
        sys.exit(4)


if __name__ == "__main__":  # pragma: no cover
    cli()
