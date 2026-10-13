"""Triangulated blade mesh, watertightness and binary STL output (plan T076).

The mesh is the same geometry as the STEP B-rep: a ruled/bilinear surface through the
resampled station contours, blunt trailing-edge panels closing the contour ring, and planar
root/tip cap faces.  Winding is consistently outward (right-hand rule around the outward
normal) so slicers and OCCT agree on inside/outside.

Resolution is controlled by :data:`MESH_RESOLUTIONS` (points per contour, spanwise
densification, midpoint subdivision level).  Decimation is done by choosing a coarser
loft grid instead of removing triangles after the fact, which would open the mesh.
"""

from __future__ import annotations

import struct
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from hoverblade_fm.cad.loft import LoftedBlade, loft_blade
from hoverblade_fm.models import Design

#: Mesh density presets used by the CLI and by CI (``coarse`` is the fast path).
MESH_RESOLUTIONS: dict[str, dict[str, int]] = {
    "coarse": {"points_per_section": 65, "spanwise_factor": 1, "subdivision": 0},
    "standard": {"points_per_section": 161, "spanwise_factor": 1, "subdivision": 0},
    "fine": {"points_per_section": 241, "spanwise_factor": 2, "subdivision": 1},
    "ultra": {"points_per_section": 361, "spanwise_factor": 3, "subdivision": 1},
}

STL_HEADER = b"HoverBlade-FM blade solid (units: mm)"


@dataclass
class TriMesh:
    """Triangle soup: ``vertices`` in metres, ``faces`` as ``(F, 3)`` int indices."""

    vertices: np.ndarray
    faces: np.ndarray

    def __post_init__(self) -> None:
        self.vertices = np.asarray(self.vertices, dtype=float)
        self.faces = np.asarray(self.faces, dtype=np.int64)
        if self.vertices.ndim != 2 or self.vertices.shape[1] != 3:
            raise ValueError("mesh vertices must be an (N, 3) array")
        if self.faces.ndim != 2 or self.faces.shape[1] != 3:
            raise ValueError("mesh faces must be an (F, 3) index array")

    def __iter__(self) -> Iterable[np.ndarray]:
        yield self.vertices
        yield self.faces

    def as_tuple(self) -> tuple[np.ndarray, np.ndarray]:
        return self.vertices, self.faces


def resolution_profile(name: str) -> dict[str, int]:
    if name not in MESH_RESOLUTIONS:
        raise ValueError(f"unknown mesh resolution '{name}', expected {sorted(MESH_RESOLUTIONS)}")
    return MESH_RESOLUTIONS[name]


def _ring_index(station: int, point: int, n: int) -> int:
    return station * n + point % n


def loft_to_triangles(stations: list[np.ndarray], n_per_section: int) -> tuple[np.ndarray, np.ndarray]:
    """Build outward-wound triangles for a stack of same-ordered closed contours."""
    count = len(stations)
    if count < 2:
        raise ValueError("a loft needs at least two stations")
    vertices = np.concatenate([np.asarray(st, dtype=float) for st in stations], axis=0)
    faces: list[tuple[int, int, int]] = []
    for i in range(count - 1):
        for j in range(n_per_section):
            a = _ring_index(i, j, n_per_section)
            b = _ring_index(i, j + 1, n_per_section)
            c = _ring_index(i + 1, j + 1, n_per_section)
            d = _ring_index(i + 1, j, n_per_section)
            # contours run clockwise in the (chord, thickness) plane, so tangent x radial is
            # the outward normal: (a, b, c) and (a, c, d) are outward-wound
            faces.append((a, b, c))
            faces.append((a, c, d))
    # planar cap faces, fanned from the contour centroid (the centre vertex is shared)
    hub_centre = len(vertices)
    tip_centre = len(vertices) + 1
    first = stations[0].mean(axis=0)
    last = stations[-1].mean(axis=0)
    vertices = np.vstack([vertices, first, last])
    for j in range(n_per_section):
        a = _ring_index(0, j, n_per_section)
        b = _ring_index(0, j + 1, n_per_section)
        faces.append((hub_centre, b, a))
    for j in range(n_per_section):
        a = _ring_index(count - 1, j, n_per_section)
        b = _ring_index(count - 1, j + 1, n_per_section)
        faces.append((tip_centre, a, b))
    return vertices, np.asarray(faces, dtype=np.int64)


def loft_to_mesh(
    loft: LoftedBlade,
    *,
    resolution: str = "standard",
    subdivision: int | None = None,
) -> TriMesh:
    """Triangulate a loft into an outward-wound, watertight blade mesh (metres)."""
    profile = resolution_profile(resolution)
    mesh = TriMesh(*loft_to_triangles(loft.stations, loft.n_per_section))
    level = int(subdivision if subdivision is not None else profile["subdivision"])
    if level > 0:
        mesh = subdivide_mesh(mesh, level)
    return mesh


def design_to_mesh(
    design: Design,
    *,
    resolution: str = "standard",
    points_per_section: int | None = None,
    hub_radius_over_r: float | None = None,
    tip_extent_over_r: float = 1.02,
) -> tuple[LoftedBlade, TriMesh]:
    """Loft and triangulate a design in one call (used by the package export)."""
    profile = resolution_profile(resolution)
    n_pts = int(points_per_section or profile["points_per_section"])
    factor = int(profile["spanwise_factor"])
    n_spanwise = factor * len(design.sections) if factor > 1 else None
    loft = loft_blade(
        design,
        n_spanwise=n_spanwise,
        hub_radius_over_r=hub_radius_over_r,
        tip_extent_over_r=tip_extent_over_r,
        points_per_section=n_pts,
    )
    return loft, loft_to_mesh(loft, resolution=resolution)


def subdivide_mesh(mesh: TriMesh, level: int = 1) -> TriMesh:
    """Midpoint (1 -> 4) refinement; watertightness and winding are preserved."""
    vertices = np.array(mesh.vertices, dtype=float, copy=True)
    faces = np.array(mesh.faces, dtype=np.int64, copy=True)
    for _ in range(max(0, int(level))):
        edges = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]], axis=0)
        key = np.sort(edges, axis=1)
        unique, inverse = np.unique(key, axis=0, return_inverse=True)
        mid_points = 0.5 * (vertices[unique[:, 0]] + vertices[unique[:, 1]])
        mid_index = (inverse.reshape(3, -1).T + vertices.shape[0]).astype(np.int64)
        ab, bc, ca = mid_index[:, 0], mid_index[:, 1], mid_index[:, 2]
        new_faces = np.empty((faces.shape[0] * 4, 3), dtype=np.int64)
        new_faces[0::4] = np.column_stack([faces[:, 0], ab, ca])
        new_faces[1::4] = np.column_stack([faces[:, 1], bc, ab])
        new_faces[2::4] = np.column_stack([faces[:, 2], ca, bc])
        new_faces[3::4] = np.column_stack([ab, bc, ca])
        vertices = np.vstack([vertices, mid_points])
        faces = new_faces
    return TriMesh(vertices, faces)


def mesh_bounds(vertices: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(minimum, maximum)`` corner of the mesh, in its own units."""
    v = np.asarray(vertices, dtype=float)
    if v.size == 0:
        empty = np.zeros(3)
        return empty, empty
    return v.min(axis=0), v.max(axis=0)


def is_watertight(faces: np.ndarray, vertices: np.ndarray | None = None) -> bool:
    """True when every undirected edge is used exactly twice with opposite orientation."""
    f = np.asarray(faces, dtype=np.int64)
    if f.ndim != 2 or f.shape[1] != 3 or f.shape[0] < 4:
        return False
    directed: Counter[tuple[int, int]] = Counter()
    for a, b, c in f:
        for p, q in ((int(a), int(b)), (int(b), int(c)), (int(c), int(a))):
            if p == q:
                return False
            directed[(p, q)] += 1
    for (u, w), n in directed.items():
        if n != 1:
            return False
        if directed.get((w, u), 0) != 1:
            return False
    if vertices is not None:
        v = np.asarray(vertices, dtype=float)
        used = np.unique(f)
        if used.max(initial=-1) >= v.shape[0] or used.size != v.shape[0]:
            return False  # unreferenced vertices would break the B-rep / slicer
    return True


def mesh_volume(vertices: np.ndarray, faces: np.ndarray) -> float:
    """Signed volume via the divergence theorem; positive for outward-wound meshes [m^3]."""
    v = np.asarray(vertices, dtype=float)
    f = np.asarray(faces, dtype=np.int64)
    if f.size == 0:
        return 0.0
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    return float(np.sum(np.einsum("ij,ij->i", a, np.cross(b, c))) / 6.0)


def mesh_surface_area(vertices: np.ndarray, faces: np.ndarray) -> float:
    v = np.asarray(vertices, dtype=float)
    f = np.asarray(faces, dtype=np.int64)
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    return float(0.5 * np.sum(np.linalg.norm(np.cross(b - a, c - a), axis=1)))


def mesh_normals(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    f = np.asarray(faces, dtype=np.int64)
    v = np.asarray(vertices, dtype=float)
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    n = np.cross(b - a, c - a)
    length = np.linalg.norm(n, axis=1, keepdims=True)
    return np.divide(n, length, out=np.zeros_like(n), where=length > 0)


def mesh_to_binary_stl(
    vertices: np.ndarray,
    faces: np.ndarray,
    path: str | Path,
    *,
    source_units: str = "M",
    target_units: str = "MM",
) -> Path:
    """Write an outward-wound binary STL (plan T076).  Metres are converted to millimetres."""
    scale = _unit_scale(source_units, target_units)
    v = np.asarray(vertices, dtype=float) * scale
    f = np.asarray(faces, dtype=np.int64)
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    normals = mesh_normals(v, f)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    count = int(f.shape[0])
    records = np.zeros(count, dtype=_stl_record_dtype())
    records["normal"] = normals
    records["vertices"] = np.concatenate([a, b, c], axis=1)
    with out.open("wb") as fh:
        fh.write(STL_HEADER.ljust(80, b" ")[:80])
        fh.write(struct.pack("<I", count))
        fh.write(records.tobytes())
    return out


def write_stl(loft: LoftedBlade, path: str | Path, *, resolution: str = "standard") -> tuple[Path, TriMesh]:
    """Triangulate a loft and write it as binary STL, returning the mesh too."""
    mesh = loft_to_mesh(loft, resolution=resolution)
    stl_path = mesh_to_binary_stl(mesh.vertices, mesh.faces, path)
    return stl_path, mesh


def _stl_record_dtype() -> np.dtype:
    """The 50-byte binary STL record: normal (3), vertices (9), attribute size (1)."""
    return np.dtype([("normal", "<f4", (3,)), ("vertices", "<f4", (9,)), ("attribute", "<u2")], align=False)


def read_binary_stl_records(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    """Return the raw ``(normals, triangles)`` of a binary STL, exactly as stored."""
    raw = Path(path).read_bytes()
    if len(raw) < 84:
        raise ValueError(f"{path} is not a binary STL (too short)")
    (count,) = struct.unpack_from("<I", raw, 80)
    expected = 84 + count * 50
    if len(raw) < expected:
        raise ValueError(f"{path} truncated: expected {expected} bytes, found {len(raw)}")
    data = np.frombuffer(raw, dtype=_stl_record_dtype(), count=count, offset=84)
    normals = data["normal"].astype(float).reshape(count, 3)
    triangles = data["vertices"].astype(float).reshape(count, 3, 3)
    return normals, triangles


def stl_to_mesh(path: str | Path, *, decimals: int = 4) -> TriMesh:
    """Read a binary STL back into a deduplicated mesh (coordinates kept in file units)."""
    return TriMesh(*_stl_rebuild(read_binary_stl_records(path)[1], decimals))


def _stl_rebuild(triangles: np.ndarray, decimals: int) -> tuple[np.ndarray, np.ndarray]:
    """Deduplicate a ``(count, 3, 3)`` array of STL triangles into a shared vertex list."""
    count = triangles.shape[0]
    flat = triangles.reshape(count * 3, 3).astype(float)
    uniq, inverse = np.unique(np.round(flat, decimals=decimals), axis=0, return_inverse=True)
    faces = inverse.reshape(count, 3).astype(np.int64)
    return uniq, faces


def te_gap_from_mesh(
    vertices: np.ndarray,
    radii: np.ndarray,
    *,
    radius_tol: float = 0.0,
    band_over_chord: float = 0.005,
) -> np.ndarray:
    """Measure the blunt trailing-edge thickness of each requested station from a vertex cloud.

    Works on loft vertices, a mesh, or an STL read back from disk (any consistent unit).
    For every station ring the trailing-edge band ``x >= x_max - band_over_chord * chord`` is
    extracted and the widest distance inside it is reported: that is the blunt facet, i.e.
    ``t_TE`` of plan 5.2, to within the discretisation of the contour.
    """
    v = np.asarray(vertices, dtype=float)
    out: list[float] = []
    for r in np.atleast_1d(np.asarray(radii, dtype=float)):
        ring = v[np.abs(v[:, 1] - r) <= radius_tol]
        if ring.shape[0] < 3:
            raise ValueError(f"no mesh ring found at radius {r} (tolerance {radius_tol})")
        span = float(ring[:, 0].max() - ring[:, 0].min())
        band = ring[ring[:, 0] >= ring[:, 0].max() - band_over_chord * span]
        planar = band[:, (0, 2)]
        delta = planar[:, None, :] - planar[None, :, :]
        out.append(float(np.sqrt((delta**2).sum(axis=-1)).max()))
    return np.asarray(out, dtype=float)


def _unit_scale(source: str, target: str) -> float:
    """Multiplicative factor converting a length expressed in ``source`` into ``target``."""
    table = {"M": 1.0, "MM": 0.001, "CM": 0.01, "IN": 0.0254}
    if source not in table or target not in table:
        raise ValueError(f"unsupported unit conversion {source} -> {target}")
    return table[source] / table[target]
