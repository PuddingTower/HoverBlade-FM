# Literature search: queries, sources and column mapping

Companion to `scripts/literature_search.py` (任务 T008) and to
[literature_review.md](../literature_review.md).  This page records *what was asked* and *how the answers
were classified*, so the retrieval can be re-run and audited.

## 1. Retrieval targets (计划.md 第 7.1 节)

Ten directions, 2022-2025 emphasis: hover rotor Figure of Merit (悬停品质因数) optimisation; helicopter
rotor blade aerodynamic shape optimisation; airfoil inverse and generative design; differentiable
aerodynamic shape optimisation; machine-learning airfoil generation; diffusion models for airfoils and
blades; multi-fidelity aerodynamic optimisation; Bayesian optimisation and surrogates; blunt trailing edge
(钝尾缘) / manufacturing-constrained airfoil design; open-source rotor and propeller design tools.

## 2. English query list (计划.md 第 7.2 节 英文检索词, verbatim + 4 extras)

| # | Query | Origin |
|---|---|---|
| 1 | `helicopter rotor hover figure of merit optimization` | plan |
| 2 | `hovering rotor blade shape optimization` | plan |
| 3 | `blade element momentum theory hover rotor optimization` | plan |
| 4 | `airfoil inverse design machine learning` | plan |
| 5 | `airfoil generation diffusion model` | plan |
| 6 | `conditional diffusion model airfoil design` | plan |
| 7 | `differentiable CFD aerodynamic shape optimization` | plan |
| 8 | `multi-fidelity Bayesian optimization rotor blade` | plan |
| 9 | `CST airfoil parameterization trailing edge thickness` | plan |
| 10 | `blunt trailing edge airfoil manufacturing constraint` | plan |
| 11 | `open source propeller rotor design optimization` | plan |
| 12 | `hover rotor surrogate model figure of merit` | plan |
| 13 | `rotor blade design optimization constraints CMA-ES` | **added** — the mainline optimiser is CMA-ES + SLSQP and the plan's list never names it |
| 14 | `low Reynolds number airfoil design optimization` | **added** — small hover rotors work at Re ≈ 10⁵-10⁶, where the polar model is weakest |
| 15 | `trailing edge thickness profile drag airfoil` | **added** — targets the blunt-edge drag penalty term (T043) specifically |
| 16 | `airfoil shape parameterization geometric validity` | **added** — targets valid-by-construction parameterization (no self-intersection, no closed edge) |

The list lives in `QUERIES` in `scripts/literature_search.py`; adding a row there is the intended way to
extend the search (it needs no other code change).

## 3. Chinese query list (计划.md 第 7.2 节 中文检索词)

```text
悬停旋翼 品质因数 优化            可微分 气动外形优化
直升机旋翼 悬停 气动优化          多保真 旋翼优化
叶片 翼型 逆向设计                尾缘厚度 制造约束
翼型生成模型 扩散模型             钝尾缘 翼型设计
                                 开源 旋翼设计
```

These are stored in `CHINESE_QUERIES` for documentation.  **They are not queried programmatically**: neither
the arXiv Atom API nor the Crossref REST API indexes the Chinese-language literature (CNKI / 万方 / 中文学报),
so sending those strings would return English-language noise.  The manual follow-up found, in 太阳能学报:

* 基于多岛遗传算法的钝尾缘翼型多目标优化设计 (2022), DOI `10.19912/j.0254-0096.tynxb.2021-0253` — combines a
  Hicks-Henne type function with a blunt-edge shaping function.
* 考虑结构特性的钝尾缘翼型气动优化设计 (2024), DOI `10.19912/j.0254-0096.tynxb.2024-0150` — CST parameterization
  combined with blunt-trailing-edge modification, i.e. the same geometry layer this project builds.

Both are cited in `docs/literature_review.md` under "Chinese-language work" with the journal resolver URL.
They are *not* in `raw.jsonl`, because the file is the machine-generated log of what the two APIs returned.

## 4. Sources (计划.md 第 7.3 节) and how each is reached

| Planned source | Programmatic? | Route |
|---|---|---|
| arXiv | yes | Atom API `https://export.arxiv.org/api/query`, `sortBy=relevance` |
| AIAA SciTech / Aviation, AIAA Journal, JAHS, Aerospace Science and Technology, J. Fluid Mechanics, NASA NTRS, Zenodo | yes, indirectly | Crossref REST API `https://api.crossref.org/works` returns those publishers' records with DOIs |
| Semantic Scholar | no | not queried: the script is standard-library-only and keeps to two stable endpoints |
| GitHub | no (manual) | open-source tools are recorded through their publications (XFOIL, DeepAirfoil, the unified momentum model's open implementation, QBlade) and by hand in the review |

## 5. Screening columns: Chinese header → English key

`summary.csv` uses English keys so the file can be diffed, grepped and parsed; the mapping to 计划.md 第 7.4 节
is one-to-one and ordered exactly as in the plan.  The file is written as UTF-8 **with BOM** so spreadsheet
applications display the Chinese headers and accented author names correctly.

| 计划.md 7.4 header | CSV key | Vocabulary | How it is decided |
|---|---|---|---|
| 标题 | `title` | free text | API value |
| 年份 | `year` | integer, blank if unknown | API `issued` / Atom `published` |
| 方法 | `method` | `CST geometry parameterization`, `geometry parameterization`, `BEMT / low-order aerodynamic model`, `panel/boundary-layer method (XFOIL-class)`, `vortex-lattice / panel method`, `vortex-wake method`, `CFD (RANS)`, `CFD (LES/DNS)`, `surrogate-assisted optimization`, `Bayesian / surrogate optimization`, `multi-fidelity optimization`, `gradient / adjoint optimization`, `differentiable programming`, `gradient-free / evolutionary shape optimization`, `topology optimization`, `generative latent model (VAE/CVAE)`, `generative adversarial model`, `generative diffusion model`, `neural surrogate / deep learning`, `review / other` | keyword match on title + abstract + venue (`_METHOD_PATTERNS`), up to three labels |
| 是否悬停 | `hover` | `yes` / `partial` / `no` | `yes` for hover / figure of merit / coaxial rotor / rotorcraft / helicopter / VTOL / multirotor; `partial` for rotor / propeller / ducted fan / UAM / AAM; `no` otherwise (wind-turbine-only work) |
| 是否支持几何约束 | `geometry_constraint` | `yes` / `partial` / `no` | `yes` if trailing edge / manufactur / thickness or geometric constraint / parameterization / camber / tolerance appears; `partial` if the object is an airfoil or blade with no stated constraint handling |
| 是否开源 | `open_source` | `yes` / `partial` / `unknown` / `no` | `yes` on open-source / GitHub / Zenodo / "software is available" wording; `partial` for an arXiv preprint (open text, code unverified); `unknown` otherwise; `no` only where the curated annotation says so |
| 关联度 | `relevance` | `high` / `medium` / `low` | score over the project's own vocabulary (figure of merit, hover, blade element, rotor, trailing edge, airfoil design/optimisation, CST, manufacturing constraint, surrogate), +2 for trailing-edge content, +2 for hover **and** FM, +1 for 2022 or later: `high >= 6`, `medium >= 3`, else `low` |
| 可借鉴点 | `borrow` | free text, up to three notes | maps matched themes to concrete modules of this repository (`geometry/cst`, `aero/bemt`, `aero/polar`, `optimize/cmaes`, `optimize/init`, `cad/report`, `tests/golden`) |

Appended provenance columns (not part of 第 7.4 节): `source` (`seed` / `arxiv` / `crossref`), `doi`, `arxiv`,
`url`, `query`.

**Curated versus heuristic annotation.**  Records whose `source` is `seed` carry hand-annotated screening
columns written by the person who read the abstract — the heuristics are skipped for them
(`Record.annotated = True`).  Everything from the APIs is keyword-classified, so treat `method` / `relevance`
on live rows as a first-pass triage, not a judgement.

## 6. Filtering rules

1. **Year window** — default `2022-2025` (`--years YYYY-YYYY`), applied twice: server-side for Crossref
   (`filter=from-created-date:…,until-created-date:…`) and client-side on the resolved `year`.  arXiv has no
   server-side year filter, so it is client-side only.
2. **Curated rows are exempt from the year filter.**  Foundational sources (CST 2010, blunt-edge airfoils
   2003, XFOIL 1989, CMA-ES 2016, SciPy 2020) predate the window on purpose; the plan prioritises 2022-2025
   but does not forbid earlier work.
3. **Deduplication** — by lower-cased DOI first, then by normalised title (case- and punctuation-insensitive).
   On collision the *richer* row wins, and a curated row always beats a live row for the same work.
4. **Ordering** — `high` before `medium` before `low`, then newest year first, then title.

## 7. Reproducing these files

```bash
# hermetic (what CI runs): curated set only
uv run python scripts/literature_search.py --offline --out docs/literature_search --no-merge

# live, curated set merged in (how the committed files were produced)
uv run python scripts/literature_search.py --out docs/literature_search --limit 5 --include-seed

# through the CLI
uv run hoverblade-fm literature-search --offline
uv run hoverblade-fm literature-search --limit 8 --years 2022-2025
```

Behaviour worth knowing when re-running:

* `--limit N` caps records **per query per source** (16 queries × 2 sources ⇒ up to 32 N before dedup).
* Requests are rate-limited: 3 s after each arXiv call (its API terms of use ask for it) and 0.5 s for
  Crossref, with retries and linear backoff on HTTP 429/5xx.
* After three consecutive transport failures the session opens a circuit breaker, stops querying and falls
  back to the curated set, so a dead network costs seconds rather than minutes.
* Nothing ever raises to the user for a network problem: each failure is logged to stderr and the run
  continues.  Exit codes are `0` on success, `2` for bad arguments (`--limit 0`, an unparsable `--years`),
  `4` for an unexpected internal error.
* `HOVERBLADE_FM_OFFLINE=1` forces offline behaviour, matching the package's environment contract.
* `HOVERBLADE_FM_INSECURE_TLS=1` is the only way to get an unverified TLS retry, it applies **only** to
  `export.arxiv.org` and `api.crossref.org`, and it logs a warning each time.  On machines without a usable
  certificate store (some sandboxed CI images) arXiv otherwise fails with `CERTIFICATE_VERIFY_FAILED`.
  Set it back to unset for normal use.
* Merging is on by default: an existing `raw.jsonl` is re-read and unioned, which is how a curated baseline
  and a live pass combine.  `--no-merge` overwrites instead.

## 8. Files written

| File | Content |
|---|---|
| `raw.jsonl` | one JSON object per paper: `title`, `authors[]`, `year`, `venue`, `doi`, `arxiv`, `url`, `abstract`, `query`, `source`, plus the eight screening fields and `annotated` |
| `summary.csv` | the same records as a spreadsheet-compatible table: the 第 7.4 节 columns in plan order, then the provenance columns |

## 9. Known limitations

1. **Keyword search is noisy.**  `all:` arXiv queries and Crossref `query.bibliographic` return off-topic
   computer-vision, wind-energy and unrelated aerospace records; roughly half of the live hits in the current
   `raw.jsonl` are classified `low` relevance.  That is the intended shape of a *raw* log — read the top of
   `summary.csv`, not the bottom.
2. **Abstracts are not always available.**  Crossref returns abstracts for some publishers only; a live record
   may therefore classify as `low` because there was no text to score, not because the paper is irrelevant.
3. **`open_source` is a text signal, not a repository check.**  `yes` means the retrieved metadata says so.
   Verifying a licence and a buildable code base is a manual step before citing a tool as reusable.
4. **Two endpoints only.**  NASA NTRS, Semantic Scholar and GitHub are not queried programmatically; material
   from them enters the review by hand.
5. **Year drift.**  The default window is 2022-2025 because the plan asked for it; work published in 2026 is
   excluded from the default run unless `--years` is widened.
