# Architecture decision records (ADR)

Lightweight decision records for HoverBlade-FM, in the style of Michael Nygard's ADRs: one file per
decision that a future contributor would otherwise re-litigate.  Created under 计划.md T010 (建立 ADR
决策记录).

## Index

| ID | Decision | Status | Date |
|---|---|---|---|
| [0001](0001-cst-plus-bemt-plus-cmaes.md) | CST parameterization + hover BEMT + CMA-ES/SLSQP as the mainline | accepted | 2026-10-14 |
| [0002](0002-blunt-te-as-hard-geometric-constraint.md) | the blunt trailing edge is a hard geometric constraint, not a penalty | accepted | 2026-10-14 |
| [0003](0003-cad-kernel-optional-with-pure-python-fallback.md) | the CAD kernel is an optional extra with a pure-Python fallback | accepted | 2026-10-14 |

Planned as soon as the corresponding work lands:

- `0004` hover-only scope: momentum theory as the reference limit and the trim strategy for thrust
  matching (blocks T034-T036, T044).
- `0005` output schema as a compatibility surface: `sections.csv` columns, coordinate file format, and
  golden-file regression policy (第 21.3 节).
- `0006` fidelity ladder: when an XFOIL / CFD / learned-surrogate polar is worth its cost (T084-T085,
  T090).
- `0007` generative warm start vs. generative design: what a diffusion or VAE model may and may not do
  inside a constraint-by-construction pipeline (T088-T089).

## Format

Each ADR uses the same sections, so they can be diffed and cited from code review:

```markdown
# ADR-NNNN: <decision in one sentence>
- Status: proposed | accepted | superseded by ADR-XXXX | deprecated
- Date: YYYY-MM-DD
- Task: 计划.md / tasks.yaml ids
- Relates to: <other ADRs, docs>
## Context        — forces and facts, including the literature that establishes them
## Options considered — each with pros and cons, including the tempting wrong ones
## Decision       — what we chose and the concrete rule that follows
## Consequences   — positive, negative/accepted, and "Reversible?"
```

## Rules

1. **Write one before the code, not after.**  If a change picks between two viable technical approaches
   (parameterization, solver, optimiser, dependency, output schema), it needs an ADR in the same PR.
2. **Cite.**  A claim about what a technique costs or buys cites a source — the reference lists live in
   [../literature_review.md](../literature_review.md).  A claim about what *this* repository achieves
   cites a test name or a CI step.
3. **Never silently amend.**  A new decision that overrides an old one gets a new ADR with
   `supersedes:` on both files.  An ADR is immutable once accepted; corrections go in an `Amendment`
   section with a date.
4. **Non-decisions are not ADRs.**  Style, refactors, dependency bumps and bug fixes belong in the PR
   description and `CHANGELOG.md`.
5. **Reversibility is part of the record.**  State how much it would cost to undo the decision; that is
   what tells a future contributor whether to challenge it.

## What is *not* a decision here

The trailing-edge requirement itself (用户输入的最小尾缘厚度必须满足) comes from 计划.md 第 5 节 and the
product definition; ADR-002 records *how* it is enforced, not whether it exists.  Likewise the FM
definition `FM = P_ideal / P_actual`, `P_ideal = T^1.5 / sqrt(2 rho A)` is fixed by 第 4.1 节, and the
disclaimer (第 28 节) is not negotiable by design decision — see [../disclaimer.md](../disclaimer.md).
