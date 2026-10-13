"""DXF master-section templates (T077).

One polyline per radial station laid out along X in millimetres, on named layers, with a
trailing-edge detail block per station because the blunt edge is the part a shop must check.
``ezdxf`` is used when present; otherwise a minimal R12 ASCII writer produces the same
geometry so the manufacturing package never depends on an optional package.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from hoverblade_fm.cad import backend
from hoverblade_fm.cad.loft import resample_contour
from hoverblade_fm.config.schema import ResolvedConfig
from hoverblade_fm.models import Design

LAYER_SECTIONS = "SECTIONS"
LAYER_TE = "TE_MARKERS"
LAYER_DATUM = "DATUM_25C"
LAYER_TITLE = "TITLE"
DETAIL_X_OVER_C = 0.90  # the trailing-edge detail window starts here

TE_DETAIL_SCALE = 8.0


def _section_contour_mm(section, n_points: int) -> np.ndarray:
    """Un-twisted, chord-aligned closed contour in millimetres (a template a shop can lay out)."""
    pts = section.points.scale(section.chord_m)
    x = np.asarray(pts.x, dtype=float) * 1000.0
    y = np.asarray(pts.y, dtype=float) * 1000.0
    contour = np.column_stack((x, y))
    if contour.shape[0] != n_points:
        idx = np.linspace(0, contour.shape[0] - 1, n_points).astype(int)
        contour = contour[idx]
    return contour


def _te_detail_mm(section, n_points: int) -> np.ndarray:
    """Zoomed trailing-edge block: the last ``1 - DETAIL_X_OVER_C`` of chord, magnified."""
    x_lower, y_lower, x_upper, y_upper = section.points.surface_pairs()
    keep = x_upper >= DETAIL_X_OVER_C
    keep_l = x_lower >= DETAIL_X_OVER_C
    span = 1.0 - DETAIL_X_OVER_C
    xs = np.concatenate((x_lower[keep_l][::-1], x_upper[keep])) - DETAIL_X_OVER_C
    ys = np.concatenate((y_lower[keep_l][::-1], y_upper[keep]))
    out = np.column_stack((xs, ys)) / span * section.chord_m * 1000.0
    if out.shape[0] != n_points:
        idx = np.linspace(0, out.shape[0] - 1, n_points).astype(int)
        out = out[idx]
    return out


def station_layout(
    design: Design, *, spacing_mm: float | None = None, n_points: int = 161
) -> list[dict[str, object]]:
    """Position each station's template and its TE detail block on the drawing."""
    contours = [resample_contour(section, n_points) for section in design.sections]
    widths = [float(np.max(st[:, 0]) - np.min(st[:, 0])) * 1000.0 for st in contours]
    gaps = [float(np.max(st[:, 2]) - np.min(st[:, 2])) * 1000.0 for st in contours]
    if spacing_mm is None:
        spacing_mm = max(2.0 * max(widths), 40.0)
    blocks: list[dict[str, object]] = []
    for i, section in enumerate(design.sections):
        offset_x = i * spacing_mm
        offset_y = (i % 2) * (2.0 * max(gaps) + 20.0)
        blocks.append(
            {
                "station_id": section.station_id,
                "r_R": section.r_R,
                "label": f"SECTION {section.station_id:02d}  r/R={section.r_R:.3f}  c={section.chord_m * 1000:.2f}mm"
                f"  theta={section.twist_deg:.2f}deg  t_TE={section.te_thickness_m * 1000:.3f}mm",
                "contour": _section_contour_mm(section, n_points) + np.array([offset_x, offset_y]),
                "detail": _te_detail_mm(section, n_points)
                + np.array([offset_x, offset_y + max(gaps) + 25.0]),
                "te_thickness_mm": section.te_thickness_m * 1000.0,
            }
        )
    return blocks


def write_master_sections(
    design: Design,
    path: str | Path,
    *,
    cfg: ResolvedConfig | None = None,
    units: str = "MM",
    spacing_mm: float | None = None,
    n_points: int = 161,
) -> Path:
    """Write ``cad/master_sections.dxf`` with one template polyline per station (T077)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    blocks = station_layout(design, spacing_mm=spacing_mm, n_points=n_points)
    if backend.uses_ezdxf():
        return _write_with_ezdxf(blocks, design, cfg, out, units)
    return _write_r12(blocks, design, cfg, out, units)


def _write_with_ezdxf(blocks: list[dict[str, object]], design: Design, cfg, out: Path, units: str) -> Path:
    import ezdxf

    doc = ezdxf.new("R2000", setup=True)
    doc.header["$INSUNITS"] = 4  # 4 = millimetres, declared in the file itself
    for layer in (LAYER_SECTIONS, LAYER_TE, LAYER_DATUM, LAYER_TITLE):
        doc.layers.add(layer)
    msp = doc.modelspace()
    for block in blocks:
        contour = np.asarray(block["contour"], dtype=float)
        detail = np.asarray(block["detail"], dtype=float)
        msp.add_lwpolyline(contour, close=True, dxfattribs={"layer": LAYER_SECTIONS})
        msp.add_lwpolyline(detail, close=True, dxfattribs={"layer": LAYER_TE})
        centre_x = float(np.mean(contour[:, 0]))
        base_y = float(np.min(contour[:, 1]))
        msp.add_line(
            (centre_x, base_y - 6.0),
            (centre_x, float(np.max(contour[:, 1])) + 6.0),
            dxfattribs={"layer": LAYER_DATUM},
        )
        msp.add_text(
            str(block["label"]),
            dxfattribs={
                "layer": LAYER_TITLE,
                "height": 4.0,
                "insert": (float(np.min(contour[:, 0])), base_y - 14.0),
            },
        )
    msp.add_text(
        f"HoverBlade-FM master sections  units={units}  R={design.radius_m * 1000:.1f} mm  blades={design.blade_count}"
        f"  min t_TE={(cfg.te_min_m if cfg is not None else design.te_min_m) * 1000:.3f} mm",
        dxfattribs={"layer": LAYER_TITLE, "height": 6.0, "insert": (0.0, -32.0)},
    )
    doc.saveas(str(out))
    return out


def _write_r12(blocks: list[dict[str, object]], design: Design, cfg, out: Path, units: str) -> Path:
    """Minimal AutoCAD R12 ASCII DXF: POLYLINE / VERTEX / TEXT only, no external writer needed."""
    lines: list[str] = []

    def emit(code: str, value: object = "") -> None:
        lines.append(code)
        lines.append(str(value))

    def layer_entry(name: str) -> None:
        emit("0", "LAYER")
        emit("8", name)
        emit("62", 7 if name != LAYER_TITLE else 1)
        emit("6", "CONTINUOUS")

    emit("0", "SECTION")
    emit("2", "HEADER")
    emit("9", "$INSUNITS")
    emit("40", 4)  # 4 = millimetres
    emit("0", "ENDSEC")
    emit("0", "SECTION")
    emit("2", "TABLES")
    emit("0", "TABLE")
    emit("2", "LAYER")
    emit("70", 4)
    for name in (LAYER_SECTIONS, LAYER_TE, LAYER_DATUM, LAYER_TITLE):
        layer_entry(name)
    emit("0", "ENDTAB")
    emit("0", "ENDSEC")
    emit("0", "SECTION")
    emit("2", "ENTITIES")

    for block in blocks:
        contour = np.asarray(block["contour"], dtype=float)
        detail = np.asarray(block["detail"], dtype=float)
        for points, layer in ((contour, LAYER_SECTIONS), (detail, LAYER_TE)):
            emit("0", "POLYLINE")
            emit("8", layer)
            emit("66", 1)
            emit("70", 1)  # closed
            for x, y in points:
                emit("0", "VERTEX")
                emit("8", layer)
                emit("10", f"{x:.4f}")
                emit("20", f"{y:.4f}")
                emit("30", 0.0)
            emit("0", "SEQEND")
        emit("0", "TEXT")
        emit("8", LAYER_TITLE)
        emit("10", f"{float(np.min(contour[:, 0])):.4f}")
        emit("20", f"{float(np.min(contour[:, 1])) - 14.0:.4f}")
        emit("40", 4.0)
        emit("1", str(block["label"]))

    emit("0", "TEXT")
    emit("8", LAYER_TITLE)
    emit("10", 0.0)
    emit("20", -32.0)
    emit("40", 6.0)
    emit(
        "1",
        f"HoverBlade-FM master sections units={units} R={design.radius_m * 1000:.1f}mm"
        f" blades={design.blade_count} min_t_TE={(cfg.te_min_m if cfg is not None else design.te_min_m) * 1000:.3f}mm",
    )
    emit("0", "ENDSEC")
    emit("0", "EOF")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def read_dxf_summary(path: str | Path) -> dict[str, object]:
    """Count entities per layer in a written DXF without needing ezdxf."""
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    pairs = [(a.strip(), b.strip()) for a, b in zip(lines[::2], lines[1::2], strict=False)]
    counts = {"POLYLINE": 0, "LWPOLYLINE": 0, "VERTEX": 0, "TEXT": 0}
    section_polylines = 0
    te_polylines = 0
    layers: set[str] = set()
    entity_type: str | None = None
    entity_layer: str | None = None

    def flush() -> None:
        nonlocal section_polylines, te_polylines
        if entity_type in ("POLYLINE", "LWPOLYLINE"):
            if entity_layer == LAYER_SECTIONS:
                section_polylines += 1
            elif entity_layer == LAYER_TE:
                te_polylines += 1

    for code, value in pairs:
        if code == "0":
            flush()
            entity_type, entity_layer = value, None
            if value in counts:
                counts[value] += 1
        elif code == "8":
            entity_layer = value
            layers.add(value)
    flush()
    return {
        "polylines": counts["POLYLINE"] + counts["LWPOLYLINE"],
        "section_polylines": section_polylines,
        "te_detail_polylines": te_polylines,
        "texts": counts["TEXT"],
        "layers": sorted(layer for layer in layers if layer),
    }


__all__ = [
    "LAYER_DATUM",
    "LAYER_SECTIONS",
    "LAYER_TE",
    "LAYER_TITLE",
    "read_dxf_summary",
    "station_layout",
    "write_master_sections",
]
