# HoverBlade-FM developer entry points (计划.md 第 14.3 节).
# Every target goes through `uv run`, so it works on a fresh clone without activating a
# virtualenv by hand.  Requires `uv` on PATH: https://docs.astral.sh/uv/getting-started/installation/
#
#   make install     -> sync the dev environment
#   make lint        -> ruff check
#   make typecheck   -> mypy src
#   make test        -> pytest
#   make smoke       -> one fast end-to-end CLI run + blunt trailing-edge assertion
#   make example     -> the README quick-start run
#   make docs        -> mkdocs build (docs extra)
#   make dist        -> uv build (sdist + wheel)
#   make clean       -> remove generated artefacts

UV      ?= uv
EXAMPLE := examples/input_5000N.json
OUT     := output/example
SMOKE   := output/smoke
TE_MIN  := 0.0025
SEED    ?= 0

.PHONY: help install format lint typecheck test cov smoke example literature docs serve clean dist check-te

help:
	@printf "%s\n" \
	  "install    uv sync --extra dev (+ optional pre-commit hooks)" \
	  "lint       ruff check ." \
	  "format     ruff format . (--check to only verify)" \
	  "typecheck  mypy src" \
	  "test       pytest -q (full suite)" \
	  "smoke      fast generate + 10-section + blunt-TE gate on $(SMOKE)" \
	  "example    the README quick-start run into $(OUT)" \
	  "literature scripts/literature_search.py (offline by default)" \
	  "docs       mkdocs build --strict" \
	  "serve      mkdocs serve" \
	  "dist       uv build (sdist + wheel into dist/)" \
	  "clean      remove output/, dist/, caches"

install:
	$(UV) sync --extra dev
	@printf '%s\n' "Optional: run 'uv run pre-commit install' to enable the git hooks."

format:
	$(UV) run ruff format .

lint:
	$(UV) run ruff check .

typecheck:
	$(UV) run mypy src

test:
	$(UV) run pytest -q

cov:
	$(UV) run pytest -q --cov --cov-report=term-missing

# 计划.md 第 21.2 节: exit code 0, ten sections, blunt trailing edge, report present.
# NOTE: this exercises the CAD path as well.  While the DXF writer in the tree fails
# (ezdxf R12 vs LWPOLYLINE, task T077) this target exits 4; add --no-cad to isolate the
# rest of the pipeline.
smoke:
	$(UV) run hoverblade-fm generate $(EXAMPLE) --out $(SMOKE) --preset fast --seed $(SEED)
	test -s $(SMOKE)/sections.csv
	test -s $(SMOKE)/report.html
	$(MAKE) check-te OUT_DIR=$(SMOKE)

example:
	$(UV) run hoverblade-fm generate --thrust-n 5000 --radius-m 2.5 \
	  --trailing-edge-thickness-m $(TE_MIN) --out $(OUT) --preset fast --seed $(SEED)
	$(UV) run hoverblade-fm summary $(OUT)
	$(MAKE) check-te OUT_DIR=$(OUT)

# The project's hard gate (计划.md 第 5.1 / 23 节): no sharp trailing edge, exactly ten rows.
check-te:
	$(UV) run python -c "import csv,sys; rows=list(csv.DictReader(open(sys.argv[1],encoding='utf-8-sig'))); te=[float(r['te_thickness_m']) for r in rows]; req=float(sys.argv[2]); print('sections=%d min_te_m=%.6f required=%.6f' % (len(rows), min(te), req)); sys.exit(0 if len(rows)==10 and min(te)>=req-1e-12 else 1)" $(OUT_DIR)/sections.csv $(TE_MIN)

# Standard library only; HOVERBLADE_FM_OFFLINE=1 keeps it hermetic for CI.
literature:
	$(UV) run python scripts/literature_search.py --out docs/literature_search --offline

docs:
	$(UV) run --extra docs mkdocs build --strict

serve:
	$(UV) run --extra docs mkdocs serve

dist:
	$(UV) build

clean:
	rm -rf output site dist build .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
