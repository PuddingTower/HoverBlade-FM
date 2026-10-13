"""STEP AP214 writer with no external kernel (plan T075).

The file is generated entity by entity in a deterministic order.  The B-rep topology is a
closed, orientable, edge-to-edge manifold shell built from the loft stations:

* one ``B_SPLINE_CURVE_WITH_KNOTS`` (degree 1, piecewise bezier) per station ring, running
  from the lower trailing-edge corner to the upper one,
* one ``LINE`` per ring closing segment (the blunt trailing-edge facet),
* two ``LINE`` radial seam edges between each pair of stations,
* ``ADVANCED_FACE``s: one spline band per station pair, one planar panel per trailing-edge
  facet and one ``PLANE`` cap at the hub and at the tip.

With ``S`` stations this gives ``V = 2S``, ``E = 4S - 2`` and ``F = 2S`` faces, so the Euler
characteristic is exactly 2 (a topological sphere) and every edge is shared by two faces in
opposite senses: a valid ``MANIFOLD_SOLID_BREP``.  Surface parameters (u = circumferential,
v = radial for the bands) are chosen so the natural surface normal points outward and every
``ADVANCED_FACE`` can carry ``.T.`` for ``same_sense``.

Units are declared explicitly: ``SI_UNIT(.MILLI.,.METRE.)`` for length (millimetres) plus a
radian plane-angle unit, matching the coordinate values written into the file.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from hoverblade_fm.cad import backend
from hoverblade_fm.cad.loft import LoftedBlade
from hoverblade_fm.cad.mesh import TriMesh

SCHEMA = "AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 }"
LINE_WIDTH = 108
_POINT_KEY_DECIMALS = 9

_UNIT_MM = "SI_UNIT(.MILLI.,.METRE.)"
_UNIT_RAD = "SI_UNIT(.NULL.,.RADIAN.)"


@dataclass
class _Builder:
    """Deterministic STEP entity numbering and rendering."""

    lines: list[str] = field(default_factory=list)
    types: dict[int, str] = field(default_factory=dict)
    counter: int = 0

    def add(self, entity_type: str, arguments: str) -> int:
        self.counter += 1
        number = self.counter
        self.types[number] = entity_type
        self.lines.append(_wrap(f"#{number} = {entity_type}({arguments});"))
        return number

    def add_anonymous(self, entity_types: tuple[str, ...], body: str) -> int:
        """Emit a complex (interlocked) instance, e.g. ``(#5 = (A(..)B(..)))``."""
        self.counter += 1
        number = self.counter
        self.types[number] = "&".join(entity_types)
        self.lines.append(_wrap(f"#{number} = {body};"))
        return number

    def render_data(self) -> str:
        return "\n".join(self.lines) + "\n"


def _fmt(value: float) -> str:
    """Six significant digits, STEP-friendly (no trailing garbage, no leading plus)."""
    if not math.isfinite(value):
        raise ValueError("STEP coordinates must be finite")
    text = f"{float(value):.6G}"
    if "E" in text:
        text = text.replace("E+", "E")
        mantissa, _, exponent = text.partition("E")
        if "." not in mantissa:
            mantissa += "."
        return mantissa + "E" + exponent
    if "." not in text:
        text += "."
    elif text.endswith(".0"):
        text = text[:-2] + "."
    return text


def _point_tuple(values: np.ndarray | list[float]) -> str:
    return "(" + ",".join(_fmt(float(v)) for v in values) + ")"


def _wrap(statement: str, width: int = LINE_WIDTH) -> str:
    """Wrap long entity lines at comma boundaries (keeps the file readable for humans)."""
    if len(statement) <= width:
        return statement
    chunks: list[str] = []
    current = ""
    for part in re.split(r"(?<=,)", statement):
        if len(current) + len(part) > width and current:
            chunks.append(current)
            current = ""
        current += part
    chunks.append(current)
    return ("\n").join(chunks)


class _PointRegistry:
    """Share one ``CARTESIAN_POINT`` per distinct coordinate so no duplicate is emitted."""

    def __init__(self, builder: _Builder, scale: float) -> None:
        self._builder = builder
        self._scale = scale
        self._ids: dict[tuple[int, int, int], int] = {}
        self.origin: int | None = None

    def get(self, xyz: np.ndarray | list[float]) -> int:
        scaled = np.asarray(xyz, dtype=float) * self._scale
        key = (
            int(round(scaled[0], _POINT_KEY_DECIMALS) * 10**_POINT_KEY_DECIMALS),
            int(round(scaled[1], _POINT_KEY_DECIMALS) * 10**_POINT_KEY_DECIMALS),
            int(round(scaled[2], _POINT_KEY_DECIMALS) * 10**_POINT_KEY_DECIMALS),
        )
        found = self._ids.get(key)
        if found is not None:
            return found
        number = self._builder.add("CARTESIAN_POINT", f"'',{_point_tuple(scaled)}")
        self._ids[key] = number
        return number

    @property
    def count(self) -> int:
        return len(self._ids)

    def world_origin(self) -> int:
        if self.origin is None:
            self.origin = self.get([0.0, 0.0, 0.0])
        return self.origin


def _direction(builder: _Builder, xyz: list[float]) -> int:
    norm = float(np.linalg.norm(xyz))
    unit = [c / norm for c in xyz] if norm > 0 else [0.0, 0.0, 1.0]
    return builder.add("DIRECTION", f"'',{_point_tuple(unit)}")


def _placement(
    builder: _Builder, points: _PointRegistry, location: list[float], axis: list[float]
) -> tuple[int, int]:
    ref = _direction(builder, [1.0, 0.0, 0.0] if abs(axis[2]) < 0.9 else [0.0, 1.0, 0.0])
    place = points.get(location)
    dir_axis = _direction(builder, axis)
    return builder.add("AXIS2_PLACEMENT_3D", f"'',#{place},#{dir_axis},#{ref}"), place


def _knot_row(count: int) -> tuple[str, str]:
    """Clamped degree-1 knot data for ``count`` poles: multiplicities and knot values.

    Multiplicity ``2`` at both ends, ``1`` for every interior knot, ``count`` strictly
    increasing parameters.  OCCT's reader silently drops the piecewise-Bezier form (every knot
    repeated ``degree + 1`` times) for degree-1 splines, so the clamped convention is always
    written here.
    """
    if count < 2:
        raise ValueError("a degree-1 spline needs at least two control points")
    inner = "" if count <= 2 else "," + ",".join("1" for _ in range(count - 2))
    return f"(2{inner},2)", _point_tuple(np.linspace(0.0, 1.0, count))


def _bspline_curve(builder: _Builder, point_ids: list[int]) -> int:
    """Degree-1 B-spline through the ring points: the exact contour, in STEP-spline form."""
    mults, knots = _knot_row(len(point_ids))
    args = (
        "'',1,("
        + ",".join(f"#{p}" for p in point_ids)
        + f"),.UNSPECIFIED.,.F.,.F.,{mults},{knots},.UNSPECIFIED."
    )
    return builder.add("B_SPLINE_CURVE_WITH_KNOTS", args)


def _vector(builder: _Builder, target: np.ndarray) -> int:
    magnitude = float(np.linalg.norm(target))
    direction = _direction(
        builder, [float(c) for c in (target / magnitude if magnitude > 0 else [1.0, 0.0, 0.0])]
    )
    return builder.add("VECTOR", f"'',#{direction},{_fmt(magnitude)}")


def _bilinear_surface(builder: _Builder, rows: Sequence[Sequence[int]]) -> int:
    """Degree (1, 1) B-spline surface over a control net given as a list of u-rows."""
    net = "(" + ",".join("(" + ",".join(f"#{p}" for p in row) + ")" for row in rows) + ")"
    u_mult, u_knots = _knot_row(len(rows))
    v_mult, v_knots = _knot_row(len(rows[0]))
    return builder.add(
        "B_SPLINE_SURFACE_WITH_KNOTS",
        f"'',1,1,{net},.UNSPECIFIED.,.F.,.F.,.F.,{u_mult},{v_mult},{u_knots},{v_knots},.UNSPECIFIED.",
    )


def _plane(builder: _Builder, points: _PointRegistry, centre: np.ndarray, axis: list[float]) -> int:
    placement, _ = _placement(builder, points, [float(c) for c in centre], axis)
    return builder.add("PLANE", f"'',#{placement}")


def _edge_loop(builder: _Builder, oriented_ids: list[int]) -> int:
    return builder.add("EDGE_LOOP", "'',(" + ",".join(f"#{o}" for o in oriented_ids) + ")")


def _outer_bound(builder: _Builder, loop_id: int) -> int:
    return builder.add("FACE_OUTER_BOUND", f"'',#{loop_id},.T.")


def _oriented_edge(builder: _Builder, edge_id: int, forward: bool) -> int:
    return builder.add("ORIENTED_EDGE", f"'',*,*,#{edge_id},.{'T' if forward else 'F'}.")


def _rings_from_mesh(mesh: TriMesh, *, tolerance: float = 1e-9) -> list[np.ndarray]:
    """Recover ordered station rings from a structured blade mesh (vertices in metres)."""
    vertices = np.asarray(mesh.vertices, dtype=float)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    rings: list[np.ndarray] = []
    for radius in np.unique(np.round(vertices[:, 1], decimals=9)):
        group = np.flatnonzero(np.abs(vertices[:, 1] - radius) <= tolerance)
        local = {int(g): k for k, g in enumerate(group)}
        sub = faces[np.all(np.isin(faces, group), axis=1)]
        pairs = np.concatenate([sub[:, [0, 1]], sub[:, [1, 2]], sub[:, [2, 0]]], axis=0)
        adjacency: dict[int, set[int]] = {}
        for a, b in pairs:
            la, lb = local[int(a)], local[int(b)]
            if la != lb:
                adjacency.setdefault(la, set()).add(lb)
                adjacency.setdefault(lb, set()).add(la)
        # the planar cap faces add a centre vertex of high degree; dropping it leaves the ring
        keep = {k for k, neighbours in adjacency.items() if len(neighbours) == 2}
        for k in list(keep):
            adjacency[k] = {n for n in adjacency[k] if n in keep}
        if len(keep) < 3 or any(len(adjacency[k]) != 2 for k in keep):
            raise ValueError("mesh rings are not simple cycles; pass the LoftedBlade instead of a mesh")
        start = _pick_te_start(keep, vertices, group)
        rings.append(vertices[group[_walk_cycle(start, adjacency, keep)]])
    return rings


def _pick_te_start(keep: set[int], vertices: np.ndarray, group: np.ndarray) -> int:
    """Index of the lower trailing-edge corner: aft-most in x, lowest in z of the two corners."""
    coords = np.array([vertices[group[int(k)]] for k in keep])
    span = float(coords[:, 0].max() - coords[:, 0].min())
    corners = [k for k in keep if vertices[group[int(k)]][0] >= coords[:, 0].max() - 0.005 * span]
    return int(min(corners, key=lambda k: vertices[group[int(k)]][2]))


def _walk_cycle(start: int, adjacency: dict[int, set[int]], keep: set[int]) -> list[int]:
    ordered = [start]
    previous, current = None, start
    while True:
        candidates = [n for n in adjacency[current] if n != previous]
        if not candidates:
            break
        nxt = candidates[0]
        if nxt == start:
            break
        ordered.append(nxt)
        previous, current = current, nxt
    if len(ordered) != len(keep):
        raise ValueError("ring walk did not cover the whole contour")
    return ordered


def _loft_stations(
    source: LoftedBlade | TriMesh | tuple[np.ndarray, np.ndarray],
) -> tuple[list[np.ndarray], np.ndarray]:
    if isinstance(source, LoftedBlade):
        return [np.asarray(st, dtype=float) for st in source.stations], np.asarray(
            source.station_radii, dtype=float
        )
    mesh = source if isinstance(source, TriMesh) else TriMesh(*source)
    stations = _rings_from_mesh(mesh)
    if len(stations) < 2:
        raise ValueError("a STEP solid needs at least two station rings")
    radii = np.array([float(st[0, 1]) for st in stations])
    return stations, radii


def write_step(
    loft_or_mesh: LoftedBlade | TriMesh | tuple[np.ndarray, np.ndarray],
    path: str | Path,
    *,
    units: str = "MM",
    title: str = "HoverBlade-FM blunt-trailing-edge blade",
    product_name: str = "blade",
    colour: tuple[float, float, float] = (0.72, 0.74, 0.78),
) -> Path:
    """Write a STEP AP214 ``MANIFOLD_SOLID_BREP`` of the lofted blade (T075).

    Accepts a :class:`~hoverblade_fm.cad.loft.LoftedBlade` (preferred, exact rings) or a
    structured :class:`~hoverblade_fm.cad.mesh.TriMesh`, whose station rings are recovered
    from the mesh connectivity.
    """
    if units.upper() != "MM":
        raise ValueError("CAD exchange units are millimetres (units='MM')")
    stations, _radii = _loft_stations(loft_or_mesh)
    scale = 1000.0
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    builder = _Builder()
    points = _PointRegistry(builder, scale)

    # --- kernel-free context ---------------------------------------------------------
    application = builder.add("APPLICATION_CONTEXT", "'core data for automotive mechanical design processes'")
    builder.add(
        "APPLICATION_PROTOCOL_DEFINITION",
        f"'international standard','automotive_design',2003,#{application}",
    )
    product_context = builder.add("PRODUCT_CONTEXT", f"'',#{application},'mechanical'")
    product = builder.add("PRODUCT", f"'{product_name}','{title}','',(#{product_context})")
    formation = builder.add(
        "PRODUCT_DEFINITION_FORMATION_WITH_SPECIFIED_SOURCE",
        f"'','',#{product},.NOT_KNOWN.",
    )
    pd_context = builder.add(
        "PRODUCT_DEFINITION_CONTEXT", f"'part definition',#{application},'manufacturing'"
    )
    definition = builder.add("PRODUCT_DEFINITION", f"'design','',#{formation},#{pd_context}")
    pd_shape = builder.add("PRODUCT_DEFINITION_SHAPE", f"'','',#{definition}")
    builder.add("PRODUCT_RELATED_PRODUCT_CATEGORY", f"'part',$,(#{product})")

    # explicit millimetre / radian / steradian units.  AP214 declares units as complex
    # instances of (LENGTH_UNIT | PLANE_ANGLE_UNIT | SOLID_ANGLE_UNIT) + NAMED_UNIT + SI_UNIT;
    # a bare SI_UNIT or LENGTHMEASUREWITHUNIT is rejected by the AP214 schema.
    unit_length = builder.add_anonymous(
        ("LENGTH_UNIT", "NAMED_UNIT", "SI_UNIT"),
        "(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.))",
    )
    unit_angle = builder.add_anonymous(
        ("NAMED_UNIT", "PLANE_ANGLE_UNIT", "SI_UNIT"),
        "(NAMED_UNIT(*)PLANE_ANGLE_UNIT()SI_UNIT($,.RADIAN.))",
    )
    unit_solid = builder.add_anonymous(
        ("NAMED_UNIT", "SI_UNIT", "SOLID_ANGLE_UNIT"),
        "(NAMED_UNIT(*)SI_UNIT($,.STERADIAN.)SOLID_ANGLE_UNIT())",
    )
    geo_context = builder.add_anonymous(
        ("GEOMETRIC_REPRESENTATION_CONTEXT", "GLOBAL_UNIT_ASSIGNED_CONTEXT", "REPRESENTATION_CONTEXT"),
        "(GEOMETRIC_REPRESENTATION_CONTEXT(3)"
        f"GLOBAL_UNIT_ASSIGNED_CONTEXT((#{unit_length},#{unit_angle},#{unit_solid}))"
        "REPRESENTATION_CONTEXT('ID1','Three_Dimension_in_millimetres'))",
    )

    # --- geometry --------------------------------------------------------------------
    ring_ids: list[list[int]] = []
    for station in stations:
        # _PointRegistry applies the metre->millimetre scale itself; passing pre-scaled rows
        # would multiply the coordinates by 1e6
        ring_ids.append([points.get(row) for row in np.asarray(station, dtype=float)])

    vertex_ids: dict[tuple[int, int], int] = {}
    for i, ring in enumerate(ring_ids):
        for corner in (0, len(ring) - 1):
            vertex_ids[(i, corner)] = builder.add("VERTEX_POINT", f"'',#{ring[corner]}")

    edge_curves: dict[str, dict[int, int]] = {"ring": {}, "te": {}, "lower": {}, "upper": {}}
    for i, ring in enumerate(ring_ids):
        curve = _bspline_curve(builder, ring)
        edge_curves["ring"][i] = builder.add(
            "EDGE_CURVE", f"'ring_{i}',#{vertex_ids[(i, 0)]},#{vertex_ids[(i, len(ring) - 1)]},#{curve},.T."
        )
        closing = np.asarray(stations[i][-1], dtype=float) - np.asarray(stations[i][0], dtype=float)
        te_curve = builder.add("LINE", f"'',#{ring[0]},#{_vector(builder, closing * scale)}")
        edge_curves["te"][i] = builder.add(
            "EDGE_CURVE",
            f"'te_{i}',#{vertex_ids[(i, 0)]},#{vertex_ids[(i, len(ring) - 1)]},#{te_curve},.T.",
        )

    for i in range(len(ring_ids) - 1):
        for kind, corner in (("lower", 0), ("upper", -1)):
            index_here = 0 if corner == 0 else len(ring_ids[i]) - 1
            index_next = 0 if corner == 0 else len(ring_ids[i + 1]) - 1
            vector = np.asarray(stations[i + 1][corner], dtype=float) - np.asarray(
                stations[i][corner], dtype=float
            )
            curve = builder.add("LINE", f"'',#{ring_ids[i][index_here]},#{_vector(builder, vector * scale)}")
            edge_curves[kind][i] = builder.add(
                "EDGE_CURVE",
                f"'{kind}_{i}',#{vertex_ids[(i, index_here)]},#{vertex_ids[(i + 1, index_next)]},#{curve},.T.",
            )

    faces: list[int] = []

    def emit_face(name: str, bound_id: int, surface: int) -> None:
        faces.append(builder.add("ADVANCED_FACE", f"'{name}',(#{bound_id}),#{surface},.T."))

    def oe(edge_id: int, forward: bool) -> int:
        return _oriented_edge(builder, edge_id, forward)

    def face_from_loop(name: str, loop: list[int], surface: int) -> None:
        emit_face(name, _outer_bound(builder, _edge_loop(builder, loop)), surface)

    # spline bands between consecutive stations (u = circumferential, v = radial)
    for i in range(len(ring_ids) - 1):
        rows = [(ring_ids[i][j], ring_ids[i + 1][j]) for j in range(len(ring_ids[i]))]
        surface = _bilinear_surface(builder, rows)
        face_from_loop(
            f"band_{i}",
            [
                oe(edge_curves["ring"][i], True),
                oe(edge_curves["upper"][i], True),
                oe(edge_curves["ring"][i + 1], False),
                oe(edge_curves["lower"][i], False),
            ],
            surface,
        )

    # blunt trailing-edge panels (one per station pair; u = radial, v = across the gap)
    for i in range(len(ring_ids) - 1):
        last_here = len(ring_ids[i]) - 1
        last_next = len(ring_ids[i + 1]) - 1
        surface = _bilinear_surface(
            builder,
            [(ring_ids[i][0], ring_ids[i][last_here]), (ring_ids[i + 1][0], ring_ids[i + 1][last_next])],
        )
        face_from_loop(
            f"trailing_edge_{i}",
            [
                oe(edge_curves["lower"][i], True),
                oe(edge_curves["te"][i + 1], True),
                oe(edge_curves["upper"][i], False),
                oe(edge_curves["te"][i], False),
            ],
            surface,
        )

    # planar hub and tip caps (face normal = the plane axis, pointing out of the solid)
    for index, (label, axis) in enumerate((("hub", [0.0, -1.0, 0.0]), ("tip", [0.0, 1.0, 0.0]))):
        i = 0 if index == 0 else len(ring_ids) - 1
        centre = np.mean(np.asarray(stations[i], dtype=float), axis=0)
        surface = _plane(builder, points, centre, axis)
        loop = (
            [oe(edge_curves["te"][i], True), oe(edge_curves["ring"][i], False)]
            if index == 0
            else [oe(edge_curves["ring"][i], True), oe(edge_curves["te"][i], False)]
        )
        face_from_loop(f"{label}_cap", loop, surface)

    shell = builder.add("CLOSED_SHELL", f"'blade_shell',({','.join(f'#{f}' for f in faces)})")
    solid = builder.add("MANIFOLD_SOLID_BREP", f"'',#{shell}")
    root_placement, _ = _placement(builder, points, [0.0, 0.0, 0.0], [0.0, 1.0, 0.0])
    representation = builder.add(
        "ADVANCED_BREP_SHAPE_REPRESENTATION",
        f"'{product_name}',(#{root_placement},#{solid}),#{geo_context}",
    )
    builder.add("SHAPE_DEFINITION_REPRESENTATION", f"#{pd_shape},#{representation}")

    # colour presentation
    colour_rgb = builder.add("COLOUR_RGB", f"'',{_fmt(colour[0])},{_fmt(colour[1])},{_fmt(colour[2])}")
    fill_colour = builder.add("FILL_AREA_STYLE_COLOUR", f"'',#{colour_rgb}")
    fill_area = builder.add("FILL_AREA_STYLE", f"'',(#{fill_colour})")
    style_fill = builder.add("SURFACE_STYLE_FILL_AREA", f"#{fill_area}")
    side_style = builder.add("SURFACE_SIDE_STYLE", f"'',(#{style_fill})")
    usage = builder.add("SURFACE_STYLE_USAGE", f".BOTH.,#{side_style}")
    assignment = builder.add("PRESENTATION_STYLE_ASSIGNMENT", f"(#{usage})")
    for face in faces:
        builder.add("STYLED_ITEM", f"'colour',(#{assignment}),#{face}")

    text = _assemble(out.name, title, builder.render_data())
    out.write_text(text, encoding="utf-8", newline="\n")
    return out


def _assemble(filename: str, description: str, data: str) -> str:
    stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    header = [
        "ISO-10303-21;",
        "HEADER;",
        f"FILE_DESCRIPTION(('{description}'),'2;1');",
        (
            f"FILE_NAME('{filename}','{stamp}',('HoverBlade-FM'),"
            "('HoverBlade-FM'),'STEP AP214 manufacturing solid','HoverBlade-FM','');"
        ),
        f"FILE_SCHEMA(('{SCHEMA}'));",
        "ENDSEC;",
        "DATA;",
    ]
    return "\n".join(header) + "\n" + data + "ENDSEC;\nEND-ISO-10303-21;\n"


_REFERENCE = re.compile(r"#(\d+)")
_ENTITY_START = re.compile(r"^#(\d+) = \(?([A-Z][A-Z0-9_]+)\(")
_TYPE_TOKENS = re.compile(r"([A-Z][A-Z0-9_]{3,})\(")


def step_readback(path: str | Path) -> dict[str, Any]:
    """Lightweight parser: verify the file's own structure and count its entities.

    This is deliberately *not* an OCCT reader; it checks the invariants our writer must keep
    (balanced parentheses, resolvable references, a single ``END-ISO-10303-21;`` terminator,
    no orphan entities) and reports what the file contains.
    """
    text = Path(path).read_text(encoding="utf-8")
    statements = _split_statements(text)
    types = {number: entity for number, entity, _ in statements}
    args = {number: body for number, _, body in statements}
    references: dict[int, set[int]] = {
        number: {int(m) for m in _REFERENCE.findall(body)} for number, body in args.items()
    }
    defined = set(types)
    dangling = sorted({target for deps in references.values() for target in deps if target not in defined})
    referenced_by_others: set[int] = set()
    for deps in references.values():
        referenced_by_others |= deps
    # entry-point entities that a conforming reader finds by scanning, never by reference
    roots = {
        "SHAPE_DEFINITION_REPRESENTATION",
        "STYLED_ITEM",
        "PRODUCT_RELATED_PRODUCT_CATEGORY",
        "APPLICATION_PROTOCOL_DEFINITION",
        "UNIT_ASSIGNMENT",
    }
    orphans = sorted(
        number for number in defined if number not in referenced_by_others and types[number] not in roots
    )
    paren_balance = all(body.count("(") == body.count(")") for body in args.values())
    counts: dict[str, int] = {}
    for entity in types.values():
        for part in entity.split("+"):
            counts[part] = counts.get(part, 0) + 1
    ends_ok = text.rstrip().endswith("END-ISO-10303-21;")
    return {
        "ok": bool(
            paren_balance and not dangling and ends_ok and not orphans and text.startswith("ISO-10303-21;")
        ),
        "parenthesis_balanced": bool(paren_balance),
        "references_resolve": not dangling,
        "dangling_references": dangling,
        "orphan_entities": orphans,
        "ends_with_terminator": ends_ok,
        "schema": SCHEMA in text,
        "entity_count": len(defined),
        "counts": counts,
        "faces": counts.get("ADVANCED_FACE", 0),
        "shells": counts.get("CLOSED_SHELL", 0),
        "solids": counts.get("MANIFOLD_SOLID_BREP", 0),
        "vertices": counts.get("VERTEX_POINT", 0),
        "edges": counts.get("EDGE_CURVE", 0),
        "oriented_edges": counts.get("ORIENTED_EDGE", 0),
        "cartesian_points": counts.get("CARTESIAN_POINT", 0),
        "bspline_curves": counts.get("B_SPLINE_CURVE_WITH_KNOTS", 0),
        "bspline_surfaces": counts.get("B_SPLINE_SURFACE_WITH_KNOTS", 0),
        "planes": counts.get("PLANE", 0),
        "declares_millimetres": _UNIT_MM in text,
        "units": "MM" if _UNIT_MM in text else "UNKNOWN",
    }


def _split_statements(text: str) -> list[tuple[int, str, str]]:
    """Split the DATA section into ``(number, entity_type, arguments)`` triples."""
    data = text.split("DATA;", 1)[1] if "DATA;" in text else text
    out: list[tuple[int, str, str]] = []
    buffer = ""
    for line in data.splitlines():
        stripped = line.strip()
        if not stripped or stripped in {"ENDSEC;", "END-ISO-10303-21;"}:
            continue
        buffer += stripped
        if _balanced(buffer) and buffer.endswith(";"):
            body = buffer[:-1]
            match = _ENTITY_START.match(body)
            if match:
                found = list(dict.fromkeys(_TYPE_TOKENS.findall(body))) or [match.group(2)]
                out.append((int(match.group(1)), "+".join(found), body[body.index("= ") + 2 :]))
            buffer = ""
    return out


def _balanced(text: str) -> bool:
    depth = 0
    in_string = False
    index = 0
    while index < len(text):
        char = text[index]
        if char == "'":
            in_string = not in_string
        elif not in_string:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth < 0:
                    return False
        index += 1
    return depth == 0 and not in_string


def write_step_with_kernel(loft: LoftedBlade, path: str | Path, *, tol: float = 1e-7) -> Path | None:
    """Loft the station rings with the OpenCascade kernel and export STEP, when available.

    Each design ring lies exactly in the plane ``y = r``, so a ruled ``ThruSections`` through
    the polygonal wires yields a capped solid directly.  Returns ``None`` when no kernel is
    installed or the kernel refuses the geometry, so the caller can fall back to
    :func:`write_step`.
    """
    cadquery = backend.module()
    if cadquery is None:
        return None
    try:
        from OCP.BRepBuilderAPI import (
            BRepBuilderAPI_MakeEdge,
            BRepBuilderAPI_MakeSolid,
            BRepBuilderAPI_MakeWire,
        )
        from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
        from OCP.gp import gp_Pnt
        from OCP.TopoDS import TopoDS_Shell
    except Exception:  # pragma: no cover - kernel build without these modules
        return None

    wires: list[object] = []
    for station in loft.stations:
        ring = np.asarray(station, dtype=float) * 1000.0
        maker = BRepBuilderAPI_MakeWire()
        count = ring.shape[0]
        for i in range(count):
            a = gp_Pnt(*(float(v) for v in ring[i]))
            b = gp_Pnt(*(float(v) for v in ring[(i + 1) % count]))
            if a.Distance(b) < tol:
                continue
            maker.Add(BRepBuilderAPI_MakeEdge(a, b).Edge())
        if not maker.IsDone():
            return None
        wires.append(maker.Wire())
    if len(wires) < 2:
        return None

    thru = BRepOffsetAPI_ThruSections(True, False, 1e-6)
    thru.AddWire(wires[0])
    for wire in wires[1:]:
        thru.AddWire(wire)
    thru.Build()
    shape = thru.Shape()
    if shape.IsNull():
        return None
    if str(shape.ShapeType()).endswith("TopAbs_SOLID") is False:
        shell = TopoDS_Shell.DownCast(shape)
        if shell is None or shell.IsNull():
            return None
        builder = BRepBuilderAPI_MakeSolid(shell)
        if not builder.IsDone():
            return None
        shape = builder.Shape()

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cadquery.Shape(shape).exportStep(str(out))
    return out if out.is_file() and out.stat().st_size > 0 else None
