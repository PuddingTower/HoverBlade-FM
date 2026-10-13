# ADR-003: the CAD kernel is an optional extra with a pure-Python fallback

- **Status**: accepted
- **Date**: 2026-10-14
- **Task**: 计划.md T010, 第 13 节 (生产制造输出设计), 第 6.2 节 (CAD: CadQuery 或 build123d, STEP: OpenCascade 后端)
- **Relates to**: [ADR-001](0001-cst-plus-bemt-plus-cmaes.md), [ADR-002](0002-blunt-te-as-hard-geometric-constraint.md),
  [outputs reference](../outputs.md)

## Context

The deliverable includes manufacturing geometry: `cad/blade.step`, `cad/blade.stl`,
`cad/master_sections.dxf` and `manufacturing_report.md` (第 13.2–13.3 节), which requires lofting the ten
sections into a watertight solid with a tip cap and a root interface, then checking trailing-edge
thickness, edge degeneracy and surface quality (T070-T081).

Hard project constraints pull in different directions:

* 计划.md 第 23 节 gate 8: **no closed-source core dependency**, and 第 22.1 节 lists "外部求解器许可问题 →
  开源受阻" as a risk.
* The pipeline must run in CI in minutes, on Python 3.11 and 3.12, and inside a slim Docker image.
* The wheel must stay installable on systems that will never produce a STEP file (a researcher who only
  wants FM curves).
* `cadquery` is pinned to `python_version < '3.13'` in `pyproject.toml` because its OpenCascade wheels do
  not publish for newer interpreters — a hard packaging fact, not a preference.

## Options considered

### Option A — a B-rep kernel as a required core dependency (CadQuery/OCP, or build123d/OCP)

- *Pros*: one code path; real STEP geometry; NURBS lofting, boolean tip caps, `CheckGeometry` diagnostics
  for free; STEP/IGES readability is the industry expectation for a manufacturing package.
- *Cons*: LGPL-2.1 transitively through OpenCascade and ~200 MB of wheels; no wheel availability for
  Python ≥ 3.13, which would make the *whole package* uninstallable there; a build-time failure mode
  (missing `libocct*`) that breaks `uv sync` for users who never touch CAD; CI cost on two interpreters.

### Option B — no CAD at all in 0.1.0 (CSV/DXF only)

- *Pros*: smallest dependency surface.
- *Cons*: fails the plan's own deliverable list (第 30 节: "STEP / STL / DXF 输出", "制造检查报告"), and
  a manufacturing package without a solid model is not usable for mould or CNC work.

### Option C — pure-Python mesh/B-rep-ish writer in the core (hand-rolled STEP)

- *Pros*: no dependency at all; STEP is nominally text.
- *Cons*: a hand-written STEP subset is not a trusted solid model (no topology validation, readers choke
  on corner cases); we would be maintaining a CAD kernel, which is out of scope for an aerodynamic tool.

### Option D — optional extra with a capability-detecting backend (chosen)

- *Pros*: `uv pip install hoverblade-fm` gives the complete aero/geometry/optimisation/visualisation
  product; `--extra cad` adds real kernel-backed STEP/STL/DXF where available; the pipeline degrades
  explicitly (`--no-cad`, or automatic fallback) instead of crashing; CI stays fast and the Python-3.13
  story stays clean; the license note stays honest in `NOTICE`.
- *Cons*: two code paths in `cad/`; the fallback export is lower fidelity (mesh/tessellation-based STL
  and 2D DXF masters rather than an exact B-rep), so the report must state which backend produced the
  files; contributors must not let the fallback silently become the default expectation.

## Decision

**Option D.**  CadQuery (+ OpenCascade) and `ezdxf` live only in the `cad` optional extra, pinned to
Python < 3.13 as `pyproject.toml` already does:

```toml
[project.optional-dependencies]
cad = ["cadquery>=2.5 ; python_version < '3.13'", "ezdxf>=1.3"]
```

The `cad` package is written against a backend interface (`cad/backend.py`):

1. **preferred backend** — kernel available: spline-interpolated sections → ruled/lofted surface → tip
   cap and root interface → solid → STEP, tessellated STL, DXF master sections, geometric checks
   (degenerate edges, self-intersection, watertightness).
2. **fallback** — no kernel: STL from the tessellated loft (pure Python), DXF masters from the section
   polylines, and a `manufacturing_report.md` that explicitly records *"no B-rep kernel available: STEP
   not produced"*; the trailing-edge checks are computed from the exported section coordinates, which do
   not need a kernel at all.
3. `--no-cad` skips the stage deliberately, and `--mesh-resolution coarse|standard|fine` controls
   tessellation.

CI mirrors the split: the blocking jobs sync `--extra dev` only and assert the core artefacts, while a
dedicated **non-blocking** job (`continue-on-error: true`) syncs `--extra cad` on Python 3.12, runs a
real generate, and checks `blade.step` / `blade.stl` / `master_sections.dxf` /
`manufacturing_report.md`.  The CAD tests are marked `cad` (`pytest -m cad`) and are skipped when the
kernel is absent.

Whatever produced the files is recorded: the manufacturing report names the backend, and
`provenance.json` records the resolved settings, so a fallback STEP-less run can never be mistaken for a
full CAD package.

## Consequences

**Positive**

- Core install stays light, permissively licensed and available on every supported interpreter.
- The STEP path still exists for users who need it, and the packaging fact (no cadquery on 3.13+) is
  surfaced in the docs rather than discovered as a broken install.
- Trailing-edge and smoothness checks — the project's hard gate — are computed without a kernel, so the
  gate cannot be disabled by a missing CAD dependency.
- CI runtime stays inside the smoke budget on two Python versions.

**Negative / accepted costs**

- Two backends to maintain and two behaviours to document; the fallback cannot claim STEP readability.
- STL watertightness and STEP "opens in FreeCAD" (第 13.3 节) can only be asserted in the optional job, so
  a kernel regression can be merged without CI going red.  Mitigation: the job is visible in the PR and
  the release workflow runs the CAD-enabled path before publishing the manufacturing package.
- Users must know to install `--extra cad`; `--no-cad` and the report line make the absence loud rather
  than mysterious.

**Reversible?** Yes, cheaply in either direction: promoting the extra to a core requirement is a
`pyproject.toml` edit (and an interpreter-support decision), and dropping the kernel path entirely would
only cost STEP fidelity, since every check and every coordinate file is produced without it.
