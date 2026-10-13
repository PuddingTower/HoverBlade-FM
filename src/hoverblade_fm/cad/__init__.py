"""CAD layer: loft the ten stations into a blade solid and export STEP / STL / DXF with checks.

The builtin (pure NumPy) writers are the default path and always work; if a CAD kernel
(cadquery / build123d) is installed it is used for the STEP export as well, and
:func:`backend.name` reports which one produced the file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hoverblade_fm.cad import backend
from hoverblade_fm.cad.checks import ManufacturingChecks, run_manufacturing_checks
from hoverblade_fm.cad.dxf import write_master_sections
from hoverblade_fm.cad.loft import LoftedBlade, loft_blade
from hoverblade_fm.cad.mesh import TriMesh, write_stl
from hoverblade_fm.cad.report import write_manifest, write_manufacturing_report
from hoverblade_fm.cad.step import write_step
from hoverblade_fm.config.schema import ResolvedConfig
from hoverblade_fm.models import Design


@dataclass
class CadPackage:
    """Everything the manufacturing export produced."""

    step_path: Path
    stl_path: Path
    dxf_path: Path
    report_path: Path
    manifest_path: Path
    checks: ManufacturingChecks
    backend: str = field(default_factory=backend.name)
    loft: LoftedBlade | None = None
    mesh: TriMesh | None = None

    def paths(self) -> dict[str, Path]:
        return {
            "step": self.step_path,
            "stl": self.stl_path,
            "dxf": self.dxf_path,
            "report": self.report_path,
            "manifest": self.manifest_path,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "paths": {key: str(value) for key, value in self.paths().items()},
            "checks": self.checks.to_dict(),
        }


def export_manufacturing_package(
    design: Design,
    cfg: ResolvedConfig,
    out_dir: Path,
    *,
    mesh_resolution: str = "standard",
    points_per_section: int | None = None,
    performance: dict[str, Any] | None = None,
) -> CadPackage:
    """Loft, export and check the full manufacturing package into ``out_dir/cad``."""
    out_dir = Path(out_dir)
    cad_dir = out_dir / "cad" if out_dir.name != "cad" else out_dir
    cad_dir.mkdir(parents=True, exist_ok=True)

    loft = (
        loft_blade(design, points_per_section=points_per_section)
        if points_per_section
        else loft_blade(design)
    )
    step_path = cad_dir / "blade.step"
    stl_path, mesh = write_stl(loft, cad_dir / "blade.stl", resolution=mesh_resolution)
    _write_step_best_effort(loft, step_path)
    dxf_path = write_master_sections(design, cad_dir / "master_sections.dxf", cfg=cfg)

    checks = run_manufacturing_checks(
        design, cfg, loft=loft, mesh=mesh, step_path=step_path, dxf_path=dxf_path
    )
    report_path = write_manufacturing_report(design, cfg, checks, cad_dir, performance=performance)
    manifest_path = write_manifest(cad_dir, checks=checks)

    return CadPackage(
        step_path=step_path,
        stl_path=stl_path,
        dxf_path=dxf_path,
        report_path=report_path,
        manifest_path=manifest_path,
        checks=checks,
        backend=backend.name(),
        loft=loft,
        mesh=mesh,
    )


def _write_step_best_effort(loft: LoftedBlade, path: Path) -> Path:
    """Prefer the kernel's native B-rep export when a kernel is present, else the builtin writer."""
    if backend.has_kernel():
        try:
            from hoverblade_fm.cad.step import write_step_with_kernel

            written = write_step_with_kernel(loft, path)
            if written is not None:
                return written
        except Exception:  # pragma: no cover - kernel failure must not lose the package
            pass
    return write_step(loft, path)


__all__ = [
    "CadPackage",
    "ManufacturingChecks",
    "backend",
    "export_manufacturing_package",
    "run_manufacturing_checks",
]
