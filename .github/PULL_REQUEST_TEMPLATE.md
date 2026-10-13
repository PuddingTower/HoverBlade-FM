# Pull Request template

<!--
One atomic task per PR (计划.md 第 15.2 节).  Branch name: task/T###-slug.
Fill every box; delete nothing.  Drafts are welcome while CI is red — say what is missing.
-->

**Task / issue**: `T###` (see `docs/agent/tasks.yaml`) · Closes #
**Type**: feat / fix / docs / test / ci / refactor / perf
**Risk to the blunt trailing edge (钝尾缘) constraint**: none / possible — explain below

## What changes

One paragraph: the behaviour a reviewer should expect, not the file list.

## Why

- Requirement or defect being addressed (cite 计划.md section, ADR, or issue).
- Alternative approaches considered and why this one was picked (link the ADR if the
  decision is architectural).

## How it was verified

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -q
uv run hoverblade-fm generate examples/input_5000N.json --out output/pr_demo --preset fast
```

- [ ] `ruff check` clean
- [ ] `ruff format --check` clean
- [ ] `mypy src` clean
- [ ] `pytest -q` passes (with the new/changed tests included)
- [ ] example run exits 0
- [ ] **no sharp trailing edge**: `min(te_thickness_m) >= trailing_edge_thickness_m` in the
      produced `sections.csv`, and the CI assertion step is green
- [ ] outputs complete for the touched stage (see 计划.md 第 3.3 节 / `docs/outputs.md`)
- [ ] `provenance.json` still records version, input hash, seed
- [ ] no closed-source core dependency added (计划.md 第 23 节 gate 8)
- [ ] no secrets, tokens or absolute local paths added
- [ ] docs updated (`docs/user_guide.md`, `docs/outputs.md`, CLI help, `CHANGELOG.md`)
- [ ] `tasks.yaml` status for this task updated, with `dod` items checked

## Definition of done mapping

| `dod` item from tasks.yaml | Evidence (test name / CI step / file) |
| --- | --- |
|  |  |

## Reproducibility

Seed, preset and machine notes for any reported number.  Do not quote performance figures
that are not reproducible from an attached run.

## Screen-shots / artefacts

Plot or `report.html` excerpts when the change affects visualisation or manufacturing output.
