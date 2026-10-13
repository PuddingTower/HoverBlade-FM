# HoverBlade-FM container image (计划.md T099).
#
# Default command is the generate entry point, so a run is one line:
#
#   docker build -t hoverblade-fm .
#   docker run --rm -v "$PWD/output:/app/output" hoverblade-fm \
#       --thrust-n 5000 --radius-m 2.5 --trailing-edge-thickness-m 0.0025 --out output/demo
#
# Other subcommands (viz, summary, check-manufacturing, version) need the entrypoint
# overridden, because the default entrypoint already pins `generate`:
#
#   docker run --rm -v "$PWD/output:/app/output" --entrypoint hoverblade-fm hoverblade-fm \
#       summary output/demo
#   docker run --rm --entrypoint hoverblade-fm hoverblade-fm version
#
# The CAD extra (cadquery -> OpenCascade) is large and pinned to Python < 3.13; the core
# pipeline falls back to pure-Python exports without it (docs/adr/0003).  Build with
# `--build-arg WITH_CAD=1` to include STEP/STL/DXF export in the image.

FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Lockfile-driven dependency layer first, so source edits do not re-resolve dependencies.
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY src ./src
COPY scripts ./scripts
COPY examples ./examples

ARG WITH_CAD=0
ARG EXTRAS=""
RUN if [ "$WITH_CAD" = "1" ]; then EXTRAS="--extra cad"; fi \
 && uv sync --frozen --no-dev ${EXTRAS}

FROM python:3.12-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="HoverBlade-FM" \
      org.opencontainers.image.description="Hover-only rotor generator: three inputs in, ten blunt-trailing-edge sections, hover FM and a manufacturing package out." \
      org.opencontainers.image.source="https://github.com/hoverblade-fm/HoverBlade-FM" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    HOVERBLADE_FM_OFFLINE=1 \
    MPLBACKEND=Agg

# Matplotlib needs nothing else for PNG output; add libgl1 only if a CAD stack is baked in.
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app/pyproject.toml /app/uv.lock /app/
COPY --from=builder /app/LICENSE /app/NOTICE /app/README.md /app/

RUN useradd --create-home --uid 10001 hoverblade
USER hoverblade
WORKDIR /app

# Reproducible default output location; mount a volume to keep artefacts.
RUN mkdir -p /app/output
VOLUME ["/app/output"]

ENTRYPOINT ["hoverblade-fm", "generate"]
CMD ["--help"]
