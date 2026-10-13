---
name: Bug report
description: A run, output or constraint does not behave as documented
labels: ["bug", "triage"]
body:
  - type: markdown
    attributes:
      value: |
        Thanks for the report.  Fill in the exact command line — HoverBlade-FM is
        deterministic once the seed and the resolved configuration are known, so a complete
        repro is usually enough to fix the bug without a back-and-forth.

        If your bug is **a section with a trailing edge thinner than requested**
        (钝尾缘硬约束违反, 计划.md 第 5 节), tick the security-ish box below: that constraint
        is the project's hard gate and blocks merges.
  - type: checkboxes
    id: blunt-te
    attributes:
      label: Is this a blunt trailing edge (钝尾缘) violation?
      description: >
        Any section whose `te_thickness_m` is below the requested `trailing_edge_thickness_m`,
        a sharp/closed trailing edge in the exported coordinates, or a zero-thickness edge in
        STEP / STL / DXF output.
      options:
        - label: Yes, a trailing edge is sharp or thinner than requested
          validated: false
  - type: textarea
    id: what-happened
    attributes:
      label: What happened
      description: Observed behaviour, including the full error text and exit code (0/2/3/4).
      placeholder: |
        Exit code 3 with: infeasible design ...
        Expected exit code 0 or a warning plus a feasible design.
    validations:
      required: true
  - type: textarea
    id: command
    attributes:
      label: Command line
      description: The exact command, verbatim, including `--preset`, `--seed` and every override.
      render: shell
      placeholder: |
        uv run hoverblade-fm generate examples/input_5000N.json \
          --out output/bug_001 --preset standard --seed 0
    validations:
      required: true
  - type: textarea
    id: input
    attributes:
      label: Input file
      description: The JSON passed as INPUT_JSON (or the three flags if you used them).
      render: json
      placeholder: |
        {
          "thrust_n": 5000.0,
          "radius_m": 2.5,
          "trailing_edge_thickness_m": 0.0025
        }
    validations:
      required: true
  - type: textarea
    id: provenance
    attributes:
      label: provenance.json
      description: Paste `provenance.json` from the run directory (version, input hash, seed, resolved config).
      render: json
    validations:
      required: false
  - type: textarea
    id: sections
    attributes:
      label: sections.csv
      description: >
        For geometry, aero or manufacturing bugs, paste the ten-row `sections.csv`.  It is the
        table the CI assertion step checks (`te_thickness_m`, `chord_m`, `twist_deg`, ...).
      render: csv
    validations:
      required: false
  - type: dropdown
    id: stage
    attributes:
      label: Which stage broke
      options:
        - input / config resolution
        - geometry (CST, trailing edge)
        - aero (BEMT, polar, FM)
        - optimisation (CMA-ES / SLSQP)
        - outputs (CSV / JSON / plots / HTML)
        - CAD / manufacturing package
        - CLI / packaging / install
    validations:
      required: true
  - type: textarea
    id: environment
    attributes:
      label: Environment
      description: OS, Python version, `uv --version`, whether the `cad` extra is installed.
      placeholder: |
        Ubuntu 24.04, Python 3.12.7, uv 0.5.x, cad extra: no
    validations:
      required: true
  - type: textarea
    id: expected
    attributes:
      label: Expected behaviour
      description: What the docs or 计划.md promise instead, with a section number if you have one.
    validations:
      required: true
  - type: textarea
    id: workaround
    attributes:
      label: Workaround / regression scope
      description: Does another preset, seed or `--no-cad` / `--no-viz` avoid it?  Which version last worked?
    validations:
      required: false
