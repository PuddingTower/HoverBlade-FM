# Execution plan for autonomous agents

How to work this repository's task list.  Written from 计划.md 第 15 节 (Agent 自动执行方案),
第 16 节 (原子任务), 第 23 节 (质量门槛) and 第 29 节 (机读格式).  The task data itself lives in
[`tasks.yaml`](tasks.yaml); this document is the procedure.

---

## 1. Read this first

1. `计划.md` sections you are about to touch (geometry → 第 5/9 节, aero → 第 10 节, optimisation → 第 11 节,
   outputs → 第 3/12 节, CAD → 第 13 节, release → 第 14 节).
2. `docs/adr/` — the mainline is already decided (constrained CST + hover BEMT + CMA-ES/SLSQP + optional
   generative/differentiable upgrades + optional CAD kernel).  Do not re-open a decided question inside a task
   PR; open a new ADR if you must.
3. `docs/agent/tasks.yaml` — pick a task whose `depends_on` are all `done`.
4. `docs/quality_gates.md` — the merge definition of done.

**The invariant that outranks every task**: `te_thickness_m >= trailing_edge_thickness_m` at all ten
stations, no sharp or degenerate edge anywhere in geometry, coordinates, plots, CSV, STEP, STL, DXF.
If your change cannot preserve it, your change is wrong (see ADR-002).

## 2. Task selection

```bash
# tasks that are still open, with dependencies satisfied
uv run python - <<'PY'
import pathlib, sys
try:
    import yaml
except ImportError:
    sys.exit("pyyaml is not a project dependency; read docs/agent/tasks.yaml directly")
data = yaml.safe_load(pathlib.Path("docs/agent/tasks.yaml").read_text(encoding="utf-8"))
done = {t["id"] for t in data["tasks"] if t.get("status") == "done"}
for t in data["tasks"]:
    if t.get("status") == "done":
        continue
    if all(d in done or d in {"CAD", "CAD 或几何"} for d in t.get("depends_on", [])):
        print(t["id"], t["priority"], t["title"], "<- needs", t.get("depends_on"))
PY
```

Ordering rules:

1. `P0` before `P1` before `P2` (第 16 节: P0 blocks the release).
2. Within a priority, lowest task number first — the phases are already dependency-ordered (第 25 节:
   T001-T010 → T011-T018 → T020-T032 → T033-T045 → T046-T056 → T057-T069 → T070-T081 → T082-T090 → T091-T100).
3. Never take a task whose `depends_on` list is not `done` **in the branch you branch from**.
4. One task per branch, per PR, per commit series (第 15.2 节 rule 1).

## 3. Branch naming (计划.md 第 15.3 节)

```text
task/T###-<kebab-slug>            # the normal case, one numbered task
task/T023-blunt-te-hard-constraint
task/T008-literature-search-script
task/T075-step-export
feat/<slug> | fix/<slug> | docs/<slug> | ci/<slug>    # unnumbered maintenance only
```

Rules: the `T###` must exist in `tasks.yaml`; the slug is 2-5 hyphenated words describing the change, not
the task title verbatim; branch from the default branch (`main`), not from another `task/` branch unless the
tasks are chained by `depends_on` (say so in the PR).

## 4. Commit convention (计划.md 第 15.4 节)

Conventional Commits, scope = module:

```text
feat(geometry): add CST airfoil with blunt TE constraint
feat(aero): add blunt trailing edge drag term to analytic polar
feat(optimize): CMA-ES global stage with SLSQP refinement
fix(io): write te_thickness_m with enough precision to avoid false CI violation
test(aero): add hover momentum theory validation
docs(literature): add search script and summary template
docs(user-guide): document --preset budgets and exit codes
ci: add example-run smoke test and blunt-TE gate
chore(packaging): add Dockerfile entrypoint for generate
```

* `feat`/`fix` imply a test change in the same commit; if there is none, the commit is not ready.
* A breaking output-schema change is `feat!:` or `BREAKING CHANGE:` in the body — the schema is a
  compatibility surface (第 21.3 节: "不得擅自改变输出 schema").
* Reference the task and PR: footer `Task: T023`, `Closes: #<n>`.
* No "WIP" merges; push the branch and open a draft PR instead.

## 5. Working loop per task

1. `git switch -c task/T###-<slug>` (after `git fetch && git switch main`).
2. Implement the smallest change that satisfies the `dod` list.
3. Add or update tests **before** declaring the behaviour done.
4. Update docs in the same branch: user guide / outputs / API / ADR / `CHANGELOG.md`, and the CLI help if a
   flag changed.  计划.md 第 15.2 节 rules 3 and 6-7 require docs and provenance per task.
5. Run the local gate (§6).  Then set `status: in_progress` (or leave `todo`) in `tasks.yaml` if the task is
   not finished and stop; do not open a red PR.
6. Commit, push, open the PR from the template, set `tasks.yaml` `status: done` only when every `dod` item has
   evidence.

## 6. DoD gates before requesting review (计划.md 第 23 节)

```bash
uv run ruff check .            # 1
uv run ruff format --check .   # 1
uv run mypy src                # 2
uv run pytest -q               # 3
uv run hoverblade-fm generate examples/input_5000N.json --out output/T### --preset fast   # 4
uv run pytest -q -k "trailing or blunt or te_"      # 5 the constraint
find output/T### -type f | sort                    # 6 outputs complete
uv run --extra docs mkdocs build --strict          # 7 docs build
```

Gate-by-gate checklist (full mapping in [quality_gates.md](../quality_gates.md)):

| # | Gate | Proof required in the PR |
|---|---|---|
| 1 | ruff | `ruff check` and `ruff format --check` clean |
| 2 | mypy | `mypy src` clean |
| 3 | pytest | `pytest -q` green, including the new tests |
| 4 | example run | exit code 0 from the `generate` command above |
| 5 | trailing-edge constraint | the `sections.csv` assertion is green in CI *and* the constraint unit tests pass |
| 6 | outputs complete | `design.json`, `performance.json`, `provenance.json`, `sections.csv`, ten section CSVs, the five PNGs, `report.html` (+ `cad/` when the extra is installed) |
| 7 | docs updated | the pages listed in §5.4 changed in this PR |
| 8 | no closed-source core dependency | dependency diff reviewed; new heavy or GPL/LGPL-ish tools go to an extra, never the core |
| 9 | no secrets | no tokens, no `.env`, no absolute local paths, no large binaries in `output/` |
| 10 | release build | `uv build` produces sdist + wheel locally when the change affects packaging |

Extra, HoverBlade-FM-specific:

* **Reproducibility**: a run reported in a docstring, README or PR body must state seed, preset and
  `program_version`, and be reproducible by copy-pasting the command.
* **Schema stability**: adding a column is `feat`; renaming or removing one is breaking and needs an ADR.
* **No fabricated results**: do not write FM/thrust numbers into docs or tests as "expected" unless a run in
  this branch produced them; golden files are generated, not invented.

## 7. Failure handling

| Failure | Action |
|---|---|
| your test fails | fix the code, not the assertion, unless the assertion contradicts 计划.md |
| CI red on an unrelated file | rebase on `main` once; if still red, report it in the PR and do not merge |
| the trailing-edge assertion fails | stop; treat as a blocking defect; the constraint outranks the feature |
| an infeasible design (exit 3) | check the input band (disk loading, `CT`, `t_TE/c`) and report the warning text; do not loosen the constraint to make it pass |
| no network for `literature-search` | run `--offline`; the curated seed set is a supported path |
| a dependency will not install on 3.13 | it belongs in an extra (see ADR-003); do not pin the whole project down |
| the task's `dod` is unachievable as written | open an issue with the `task_execution` template, set the task `in_progress`, propose the amendment in 计划.md terms |

## 8. Roles (计划.md 第 15.1 节)

| Role | Owns | Typical tasks |
|---|---|---|
| Planner | dependency order, priorities, the task list | keeps `tasks.yaml` statuses truthful |
| LiteratureAgent | retrieval + review | T008, T009, T088 |
| GeometryAgent | stations, CST, trailing-edge clamp, validity | T011-T018, T020-T032 |
| AeroAgent | BEMT, losses, polars, FM | T033-T045, T082-T084 |
| OptimizationAgent | vector, objective, CMA-ES/SLSQP, selection | T046-T056, T085-T087 |
| VizAgent | plots, `report.html`, `interactive/blade.html` | T057-T069 |
| CADAgent | splines, loft, caps, STEP/STL/DXF, checks | T070-T081 |
| QA-Agent | tests, golden files, CI | gates 1-6, 10 on every PR |
| DocsAgent | README, guides, ADRs, changelog | T003, T004, T091-T093, T095, T096 |
| ReleaseAgent | tag, artefacts, Zenodo/GitHub Release | T097-T100 |

Agents may cross roles for review, but a task is `done` only when its owner checks every `dod` item with
evidence attached.

## 9. Status discipline in `tasks.yaml`

* `todo` — not started or not verifiable; the default.
* `in_progress` — part of the `dod` is met; `notes` says what is missing and why.
* `done` — every `dod` item has evidence; `verified_by` names the test, CI step or artefact.
* Never mark `done` because the code exists; `done` means the acceptance criteria are demonstrably met.
* Statuses describe the working tree, not intentions.  When the tree changes (dependency removed, artefact
  renamed), re-audit the affected tasks rather than inheriting the old status.
* Release- and hosting-dependent tasks (T001 push, T007 CI actually running, T097 tag trigger, T099 image
  build, T100 download) stay `in_progress`/`todo` until they have actually run on GitHub.

## 10. Definition of "the project is done"

第 24 节 acceptance, condensed to what an agent can check mechanically:

* three inputs run end-to-end (功能验收 1);
* ten sections, all blunt (2, 3);
* FM, power, thrust, torque exported (4);
* 2D plots, 3D interactive view, HTML report (5);
* STEP / STL / DXF + manufacturing report (6);
* optimised FM >= initial feasible FM (7);
* repo public, README/LICENSE present, CI green, examples reproducible, core coverage >= 80 % (工程验收);
* user docs explain the three inputs, the outputs and manufacturing use; developer docs explain architecture
  and tests; literature review present or regenerable (文档验收);
* an agent can read the task list, order it, branch, test, fix, and release (Agent 验收).
