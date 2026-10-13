"""External solver adapters: XFOIL polars (T084) and CFD verification (T090).

Both are strictly optional.  When the external tool is missing the adapter reports why and
the caller keeps the analytic polar / BEMT result, so the core program never depends on a
closed-source or heavy external package.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from hoverblade_fm.aero.polar import AnalyticPolar, PolarPoint, PolarSource
from hoverblade_fm.models import Design


def xfoil_executable() -> Path | None:
    """Location of an ``xfoil`` binary, if one is on ``PATH`` or given explicitly."""
    import os

    explicit = os.environ.get("HOVERBLADE_FM_XFOIL")
    if explicit:
        path = Path(explicit)
        return path if path.is_file() else None
    found = shutil.which("xfoil") or shutil.which("xfoil.exe")
    return Path(found) if found else None


@dataclass
class XfoilPolar(PolarSource):
    """Level-3 polar source: ask XFOIL for the polars and interpolate them.

    The section geometry is written as an XFoil-format coordinate file, a script drives
    ``ITER``/``PACC``, and the resulting polar table is cached on disk.  Any failure - missing
    binary, non-zero exit, unparsable output - falls back to the analytic polar so the caller
    always receives numbers, and records what happened in :attr:`status`.
    """

    inner: PolarSource = field(default_factory=AnalyticPolar)
    reynolds: float = 5.0e5
    mach: float = 0.0
    alpha_range: tuple[float, float] = (-12.0, 18.0)
    cache_dir: Path | None = None
    status: dict[str, Any] = field(default_factory=dict)
    _table: dict[float, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = field(default_factory=dict)

    def available(self) -> bool:
        return xfoil_executable() is not None

    def run(self, points, *, reynolds: float | None = None, mach: float | None = None) -> dict[str, Any]:
        binary = xfoil_executable()
        if binary is None:
            self.status = {"ok": False, "reason": "xfoil executable not found (set HOVERBLADE_FM_XFOIL)"}
            return self.status
        re_value = float(reynolds if reynolds is not None else self.reynolds)
        m_value = float(mach if mach is not None else self.mach)
        workdir = (
            Path(self.cache_dir) if self.cache_dir else Path(tempfile.mkdtemp(prefix="hoverblade_xfoil_"))
        )
        workdir.mkdir(parents=True, exist_ok=True)
        geo = workdir / "section.dat"
        geo.write_text(_xfoil_geometry_file(points), encoding="utf-8")
        script = workdir / "run.xfo"
        script.write_text(
            "\n".join(
                [
                    f"FILE {geo.name}",
                    "GDEF",
                    "PANE",
                    f"VISC {re_value:.1f}",
                    f"MACH {m_value:.4f}",
                    "ITER 150",
                    f"ALP {self.alpha_range[0]:.1f} {self.alpha_range[1]:.1f} 0.5",
                    "PACC 0.5",
                    "QUIT",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        try:
            completed = subprocess.run(
                [str(binary)],
                input=script.read_text(encoding="utf-8"),
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=120,
                shell=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            self.status = {"ok": False, "reason": f"xfoil invocation failed: {exc}"}
            return self.status
        polar_file = next((p for p in workdir.glob("*.pol")), None)
        if polar_file is None:
            self.status = {
                "ok": False,
                "reason": "xfoil produced no .pol file",
                "returncode": completed.returncode,
                "stderr": completed.stderr[-400:],
            }
            return self.status
        alpha, cl, cd, cm = _parse_xfoil_polar(polar_file)
        self._table[re_value] = (alpha, cl, cd, cm)
        self.status = {
            "ok": True,
            "binary": str(binary),
            "reynolds": re_value,
            "samples": int(alpha.size),
            "polar_file": str(polar_file),
        }
        return self.status

    def __call__(
        self,
        alpha_deg: np.ndarray | float,
        reynolds: np.ndarray | float,
        mach: np.ndarray | float,
        t_over_c: np.ndarray | float,
        te_over_c: np.ndarray | float,
        camber: np.ndarray | float,
    ) -> PolarPoint:
        base = self.inner(alpha_deg, reynolds, mach, t_over_c, te_over_c, camber)
        if not self._table:
            return base
        a = np.atleast_1d(np.asarray(alpha_deg, dtype=float))
        re_array = np.broadcast_to(np.atleast_1d(np.asarray(reynolds, dtype=float)), a.shape)
        cl = np.asarray(base.cl, dtype=float).copy()
        cd = np.asarray(base.cd, dtype=float).copy()
        cm = np.asarray(base.cm, dtype=float).copy()
        for index, re_value in enumerate(re_array):
            nearest = min(self._table, key=lambda cached: abs(cached - float(re_value)))
            if abs(nearest - float(re_value)) / max(float(re_value), 1.0) > 0.25:
                continue
            alpha, table_cl, table_cd, table_cm = self._table[nearest]
            if alpha.size == 0:
                continue
            cl[index] = float(np.interp(a[index], alpha, table_cl))
            cd[index] = max(float(np.interp(a[index], alpha, table_cd)), 1e-4)
            cm[index] = float(np.interp(a[index], alpha, table_cm))
        return PolarPoint(alpha_deg=a, cl=cl, cd=cd, cm=cm)


def _xfoil_geometry_file(points) -> str:
    """XFoil coordinate file: upper surface TE->LE, lower surface LE->TE, no duplicate nose."""
    x_lower, y_lower, x_upper, y_upper = points.surface_pairs()
    lines = ["hoverblade section", "  1.0  1.0"]
    for x, y in zip(x_upper[::-1], y_upper[::-1], strict=True):
        lines.append(f"{float(x):9.6f} {float(y):10.7f}")
    for x, y in zip(x_lower[1:], y_lower[1:], strict=True):
        lines.append(f"{float(x):9.6f} {float(y):10.7f}")
    return "\n".join(lines) + "END\n"


def _parse_xfoil_polar(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Read an XFOIL ``PACC`` polar table, skipping its two header lines."""
    rows: list[list[float]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            values = [float(part) for part in parts[:4]]
        except ValueError:
            continue
        rows.append(values)
    if not rows:
        return np.array([]), np.array([]), np.array([]), np.array([])
    data = np.array(rows, dtype=float)
    return data[:, 0], data[:, 1], data[:, 2], data[:, 3]


@dataclass
class CfdCase:
    """A prepared external CFD verification case."""

    directory: Path
    geometry_files: list[Path]
    command: list[str]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "directory": str(self.directory),
            "geometry_files": [str(p) for p in self.geometry_files],
            "command": " ".join(self.command),
            "notes": self.notes,
        }


def prepare_cfd_case(
    design: Design,
    out_dir: str | Path,
    *,
    mesher: str = "su2",
    rpm: float | None = None,
    thrust_n: float | None = None,
) -> CfdCase:
    """Write Level-4 verification inputs: one surface per station plus a case template.

    The adapter deliberately stops at *preparation*: running OpenFOAM or SU2 is the user's
    cluster's job.  It reports which executable it would have used and, when the solver is
    absent, the case is still produced so it can be carried to a machine that has one.
    """
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    geometry: list[Path] = []
    for section in design.sections:
        path = directory / f"section_{section.station_id:02d}_r{section.r_R:.3f}.sur"
        path.write_text(_xfoil_geometry_file(section.points), encoding="utf-8")
        geometry.append(path)
    known = shutil.which("su2_CFD") or shutil.which("pimpleFoam")
    if mesher == "su2":
        case_file = directory / "rotor.cfg"
        case_file.write_text(
            "\n".join(
                [
                    "% Hover verification case generated by HoverBlade-FM (T090)",
                    "SOLVER= RANS",
                    "MODEL= NAVIER_STOKES",
                    "KIND_SOLVER= GO_DIRECT",
                    "RESTART_solution= NO",
                    f"MACH_NUMBER= {design.sections[5].mach:.4f}",
                    f"REYNOLDS_NUMBER= {design.sections[5].reynolds:.1f}",
                    f"ROTATING_FRAME= YES   % Omega = {design.omega:.4f} rad/s",
                    "SURFACE_FILE= rotor_surfaces.dat",
                    "CONV_CRITERIA= 1E-6",
                    "TIME_ITERATIONS= 5000",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        geometry.append(case_file)
        command = ["su2_CFD", "rotor.cfg"]
    else:
        block = directory / "rotor.blockedMesh"
        block.write_text(
            json.dumps(
                {
                    "generateMesh": True,
                    "scaleUnits": 1.0,
                    "rotor": {
                        "omega_rad_s": design.omega,
                        "blade_count": design.blade_count,
                        "radius_m": design.radius_m,
                    },
                    "sections": [str(p.name) for p in geometry],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        geometry.append(block)
        command = ["blockMesh", "-case", str(directory)]
    notes: list[str] = []
    if known is None:
        notes.append("no CFD executable found on PATH: the case is prepared but not run")
    else:
        notes.append(f"would use {known}; run it on your solver host, this adapter never launches a CFD job")
    if thrust_n is not None:
        notes.append(
            f"target thrust {thrust_n:.1f} N; compare the integrated moment against {design.rpm:.1f} RPM"
        )
    elif rpm is not None:
        notes.append(f"verification rotation rate overridden to {rpm:.1f} RPM")
    return CfdCase(directory=directory, geometry_files=geometry, command=command, notes=notes)


def summarize_cfd_case(case: CfdCase) -> dict[str, Any]:
    return case.to_dict()


__all__ = ["CfdCase", "XfoilPolar", "prepare_cfd_case", "summarize_cfd_case", "xfoil_executable"]
