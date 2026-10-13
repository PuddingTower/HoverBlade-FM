---
name: Feature request
description: Propose a capability, solver, output format or workflow improvement
labels: ["enhancement"]
body:
  - type: markdown
    attributes:
      value: |
        Tell us the problem you are trying to solve, not only the feature you have in mind.
        HoverBlade-FM keeps a deliberately narrow scope (计划.md 第 2.2 节): hover only, three
        inputs, no structural or airworthiness analysis in the core.  Anything outside the
        core is a plugin or a later release.
  - type: textarea
    id: problem
    attributes:
      label: Problem statement
      description: What are you trying to do today, and where does the tool stop helping?
      placeholder: |
        I design small hover rotors and need the polar source to be replaceable, because ...
    validations:
      required: true
  - type: textarea
    id: proposal
    attributes:
      label: Proposed solution
      description: The CLI surface, output files and modules you imagine touching.
      placeholder: |
        New flag `--polar-source xfoil`; new optional extra `aero-high`;
        keeps the analytic polar as the default fallback.
    validations:
      required: true
  - type: dropdown
    id: area
    attributes:
      label: Area
      options:
        - config / input schema
        - geometry / CST
        - aero / BEMT / polar
        - optimisation
        - outputs / visualisation
        - CAD / manufacturing
        - docs / CI / packaging
        - new top-level capability
    validations:
      required: true
  - type: dropdown
    id: priority
    attributes:
      label: How much does it block you
      options:
        - blocker
        - important
        - nice to have
    validations:
      required: true
  - type: checkboxes
    id: constraints
    attributes:
      label: Constraint check
      description: Confirm the request does not trade away the project's guarantees (计划.md 第 1.3 节).
      options:
        - label: The blunt trailing edge (钝尾缘) hard constraint still holds after this feature
          required: true
        - label: The three-input minimal invocation still works without new required arguments
          required: true
        - label: No closed-source dependency is required in the core path (计划.md 第 23 节 gate 8)
          required: true
  - type: textarea
    id: alternatives
    attributes:
      label: Alternatives considered
      description: Other designs, existing tools, or why a local workaround is not enough.
    validations:
      required: false
  - type: textarea
    id: literature
    attributes:
      label: Related literature or prior art
      description: >
        Papers or repositories that already do this (DOI or arXiv id preferred).  They can be
        added to `docs/literature_review.md` and `scripts/literature_search.py`'s query list.
    validations:
      required: false
  - type: textarea
    id: acceptance
    attributes:
      label: Acceptance criteria
      description: Bullet list we can turn into a `dod:` field in docs/agent/tasks.yaml.
      placeholder: |
        - `hoverblade-fm generate --polar-source xfoil` matches the analytic polar within 5 %
        - unit tests pass with XFOIL absent (graceful fallback)
        - docs/user_guide.md documents the extra and its failure mode
    validations:
      required: true
  - type: checkboxes
    id: help
    attributes:
      label: Will you work on it?
      options:
        - label: I am willing to open a PR (see CONTRIBUTING.md; branch `feat/<slug>` or `task/T###-<slug>`)
          validated: false
