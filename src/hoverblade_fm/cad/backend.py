"""CAD backend detection (plan T070).

The builtin backend (pure NumPy writers) is always available and is the default.  If a
real kernel (``cadquery`` or ``build123d``) imports successfully it is used only for the
higher-fidelity B-spline loft and its native STEP export; nothing in the package may
depend on it being present.  Set ``HOVERBLADE_FM_CAD=builtin`` to force the fallback.
"""

from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from types import ModuleType

ENV_VAR = "HOVERBLADE_FM_CAD"


@dataclass(frozen=True)
class BackendInfo:
    """Availability snapshot of the optional CAD dependencies."""

    name: str
    cadquery: bool
    build123d: bool
    ezdxf: bool
    forced: bool = False

    @property
    def has_kernel(self) -> bool:
        return self.cadquery or self.build123d

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "cadquery": self.cadquery,
            "build123d": self.build123d,
            "ezdxf": self.ezdxf,
            "kernel_available": self.has_kernel,
            "forced_by_environment": self.forced,
        }


def _try_import(module_name: str) -> ModuleType | None:
    try:
        return importlib.import_module(module_name)
    except Exception:  # pragma: no cover - any import failure means "not available"
        return None


@lru_cache(maxsize=1)
def _detect() -> BackendInfo:
    forced = os.environ.get(ENV_VAR, "").strip().lower()
    cadquery = None
    build123d = None
    if forced not in ("builtin", "none", "off"):
        cadquery = _try_import("cadquery")
        build123d = _try_import("build123d") if cadquery is None else None
    ezdxf = None if forced == "no-dxf" else _try_import("ezdxf")
    if cadquery is not None:
        chosen = "cadquery"
    elif build123d is not None:
        chosen = "build123d"
    else:
        chosen = "builtin"
    return BackendInfo(
        name=chosen,
        cadquery=cadquery is not None,
        build123d=build123d is not None,
        ezdxf=ezdxf is not None,
        forced=bool(forced),
    )


def info() -> BackendInfo:
    """Return the cached availability snapshot (never raises)."""
    return _detect()


def name() -> str:
    """Return ``"cadquery" | "build123d" | "builtin"``."""
    return _detect().name


def has_kernel() -> bool:
    """True when a B-rep kernel can be used for STEP/loft refinement."""
    return _detect().has_kernel


def module() -> ModuleType | None:
    """Return the imported kernel module, or ``None`` for the builtin backend."""
    active = _detect()
    if active.cadquery:
        return _try_import("cadquery")
    if active.build123d:
        return _try_import("build123d")
    return None


def uses_ezdxf() -> bool:
    """True when ``ezdxf`` is importable and may be used for DXF output."""
    return _detect().ezdxf


def occt_available() -> bool:
    """True when the OpenCASCADE bindings behind ``cadquery`` import."""
    if not _detect().cadquery:
        return False
    return _try_import("OCP.OCP.BRepCheck") is not None


def occt_validate(step_path: str, *, expected_units_mm: bool = True) -> dict[str, object]:
    """Load a STEP file with OCCT and report validity/volume; ``{"error": ...}`` on failure."""
    cadquery = _try_import("cadquery")
    if cadquery is None:
        return {"available": False, "error": "cadquery is not installed"}
    try:
        from OCP.BRepCheck import BRepCheck_Analyzer
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps

        workplane = cadquery.importers.importStep(str(step_path))
        shape = workplane.val() if hasattr(workplane, "val") else workplane
        try:
            solid_count = len(list(shape.solids()))
        except TypeError:  # pragma: no cover - kernel version differences
            solid_count = int(shape.solids().Size()) if hasattr(shape.solids(), "Size") else 0
        prop = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape.wrapped, prop)
        volume_mm3 = float(prop.Mass())
        return {
            "available": True,
            "valid": bool(BRepCheck_Analyzer(shape.wrapped).IsValid()),
            "solid_count": solid_count,
            "volume_mm3": volume_mm3,
            "volume_m3": volume_mm3 / 1e9,
            "occt_version": getattr(cadquery, "__version__", ""),
        }
    except Exception as exc:  # pragma: no cover - depends on kernel build
        return {
            "available": True,
            "valid": False,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": _tb(),
        }


def _tb() -> str:
    import traceback

    return traceback.format_exc(limit=3)


def describe() -> str:
    """One-line human readable backend summary for reports."""
    active = _detect()
    return (
        f"backend={active.name} (python {sys.version_info.major}.{sys.version_info.minor}, "
        f"cadquery={active.cadquery}, build123d={active.build123d}, ezdxf={active.ezdxf})"
    )
