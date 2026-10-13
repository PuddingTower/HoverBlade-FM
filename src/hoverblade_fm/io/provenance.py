"""Provenance record for a run (plan T069 / section 12.3).

Writes ``provenance.json`` capturing program version, git commit, input hash, random seed,
UTC timestamp, the resolved configuration echo and the runtime library versions.  Nothing
in this module may fail when the checkout is not a git repository.
"""

from __future__ import annotations

import json
import platform
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import scipy

from hoverblade_fm import __version__
from hoverblade_fm.config.schema import ResolvedConfig
from hoverblade_fm.models import Provenance

#: Literal written as ``git_commit`` when the commit cannot be determined.
UNKNOWN_COMMIT = "unknown"

_PROVENANCE_NAME = "provenance.json"


def write_provenance(
    config: ResolvedConfig,
    out_dir: Path,
    seed: int,
    *,
    optimization_settings: Mapping[str, Any] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> Provenance:
    """Build the :class:`Provenance` for a run and write ``provenance.json`` into *out_dir*.

    ``optimization_settings`` defaults to the resolved configuration's optimisation block;
    ``extra`` adds free-form keys to the JSON file only (not to the returned dataclass).
    """
    provenance = Provenance(
        program_version=__version__,
        git_commit=resolve_git_commit(),
        input_hash=config.input_hash,
        random_seed=int(seed),
        timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        optimization_settings=(
            dict(optimization_settings)
            if optimization_settings is not None
            else config.optimization.model_dump()
        ),
        resolved_config=config.to_dict(),
        python=platform.python_version(),
        numpy=np.__version__,
        scipy=scipy.__version__,
    )
    payload = provenance.to_dict()
    if extra:
        payload.update(dict(extra))
    out_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False, sort_keys=False) + "\n"
    out_dir.joinpath(_PROVENANCE_NAME).write_bytes(text.encode("utf-8"))
    return provenance


def resolve_git_commit() -> str:
    """Short HEAD commit of the project checkout, or ``"unknown"`` if it cannot be resolved.

    Tries the project root inferred from the package location, then the current working
    directory; a missing git binary or a non-repository tree never raises.
    """
    candidates = [Path(__file__).resolve().parents[3], Path.cwd()]
    for root in candidates:
        try:
            result = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return UNKNOWN_COMMIT
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return UNKNOWN_COMMIT
