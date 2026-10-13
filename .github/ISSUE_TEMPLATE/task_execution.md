---
name: Task execution
description: Claim and report an atomic task from docs/agent/tasks.yaml (human or agent)
labels: ["task", "agent"]
body:
  - type: markdown
    attributes:
      value: |
        One issue per atomic task from 计划.md 第 16 节 / `docs/agent/tasks.yaml`.
        The `dod:` list in that file **is** the acceptance test — copy it verbatim below and
        check each item with evidence, not intent.  Follow `docs/agent/execution_plan.md` for
        branch naming (`task/T###-slug`), commit style (计划.md 第 15.4 节) and the DoD gates
        (计划.md 第 23 节).
  - type: input
    id: task-id
    attributes:
      label: Task id
      description: Exactly as in tasks.yaml, e.g. T023.
      placeholder: "T023"
    validations:
      required: true
  - type: input
    id: task-title
    attributes:
      label: Title
      description: Copy the `title:` field verbatim (Chinese is fine).
      placeholder: "实现钝尾缘硬约束"
    validations:
      required: true
  - type: dropdown
    id: priority
    attributes:
      label: Priority
      options: ["P0", "P1", "P2"]
    validations:
      required: true
  - type: input
    id: depends
    attributes:
      label: depends_on
      description: Comma-separated task ids from tasks.yaml, or `无` / `none`.
      placeholder: "T022"
    validations:
      required: false
  - type: input
    id: branch
    attributes:
      label: Branch
      description: task/T###-slug (计划.md 第 15.3 节)
      placeholder: "task/T023-blunt-te-hard-constraint"
    validations:
      required: true
  - type: textarea
    id: dod
    attributes:
      label: Definition of done — evidence per item
      description: |
        Copy each `dod:` line and append the proof next to it: test name, CI run URL, file
        produced, or command output.  Blank evidence means the item is not done.
      value: |
        - [ ] dod item 1 — evidence:
        - [ ] dod item 2 — evidence:
        - [ ] dod item 3 — evidence:
    validations:
      required: true
  - type: textarea
    id: commands
    attributes:
      label: Commands run
      render: shell
      placeholder: |
        uv run ruff check .
        uv run ruff format --check .
        uv run mypy src
        uv run pytest -q
        uv run hoverblade-fm generate examples/input_5000N.json --out output/T023 --preset fast
    validations:
      required: true
  - type: checkboxes
    id: gates
    attributes:
      label: Quality gates (计划.md 第 23 节)
      options:
        - label: ruff check + ruff format --check pass
          required: true
        - label: mypy src passes
          required: true
        - label: pytest passes, with tests added for this task
          required: true
        - label: example run passes (exit 0)
          required: true
        - label: trailing-edge constraint test passes — no sharp edge in any exported section
          required: true
        - label: output files complete for the touched stage
          required: true
        - label: docs updated (user guide / outputs / API / CHANGELOG as applicable)
          required: true
        - label: no closed-source core dependency added
          required: true
        - label: no secrets or absolute local paths committed
          required: true
  - type: textarea
    id: files
    attributes:
      label: Files added or changed
      description: Paths only, one per line, with a one-word reason each.
      placeholder: |
        src/hoverblade_fm/geometry/te_constraint.py — clamp implementation
        tests/unit/test_te_constraint.py — coverage for dod items 1-3
    validations:
      required: true
  - type: textarea
    id: deviations
    attributes:
      label: Deviations from the plan
      description: >
        Anything where the implementation differs from 计划.md, or a task that could not be
        completed as written (missing dependency, blocked by another task, plan contradicts
        reality).  Write `none` if there is nothing to reconcile.
    validations:
      required: true
  - type: textarea
    id: followups
    attributes:
      label: Follow-up tasks created
      description: New task ids added to tasks.yaml because of this work, or `none`.
    validations:
      required: false
  - type: dropdown
    id: executor
    attributes:
      label: Executed by
      options: ["human", "agent"]
    validations:
      required: true
