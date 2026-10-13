"""IO layer: run-output writers, provenance, readers and run summaries (plan T057-T061, T069)."""

from __future__ import annotations

from hoverblade_fm.io.provenance import UNKNOWN_COMMIT, resolve_git_commit, write_provenance
from hoverblade_fm.io.readers import (
    RunDataError,
    RunSummary,
    read_design,
    read_json,
    read_performance,
    read_provenance,
    read_run,
    read_section_coordinates,
    read_section_index,
    read_sections_table,
    required_te_thickness_m,
    validate_sections,
)
from hoverblade_fm.io.summary import summarise_run
from hoverblade_fm.io.writers import (
    COORDINATE_CONVENTION,
    HISTORY_COLUMNS,
    SECTION_COORD_COLUMNS,
    section_coordinate_name,
    write_design_json,
    write_optimization_history,
    write_performance_json,
    write_polars,
    write_run_outputs,
    write_section_coordinates,
    write_sections_csv,
)

__all__ = [
    "UNKNOWN_COMMIT",
    "COORDINATE_CONVENTION",
    "HISTORY_COLUMNS",
    "SECTION_COORD_COLUMNS",
    "RunDataError",
    "RunSummary",
    "read_design",
    "read_json",
    "read_performance",
    "read_provenance",
    "read_run",
    "read_section_coordinates",
    "read_section_index",
    "read_sections_table",
    "required_te_thickness_m",
    "resolve_git_commit",
    "section_coordinate_name",
    "summarise_run",
    "validate_sections",
    "write_design_json",
    "write_optimization_history",
    "write_performance_json",
    "write_polars",
    "write_provenance",
    "write_run_outputs",
    "write_section_coordinates",
    "write_sections_csv",
]
