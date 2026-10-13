"""Spanwise lofting of the 10 design sections into a blade surface (plan T071-T074).

The loft is built from *closed* station contours that all share the same point count and
the same parametric ordering, so consecutive stations can be stitched without any risk of
a twisted (self-intersecting) panel:

* contour index ``0`` is the **lower trailing edge** point,
* indices ``1 .. k`` run forward along the lower surface to the leading edge,
* the leading edge is rounded through the middle of the array,
* the remaining indices run back along the upper surface to the **upper trailing edge**
  (the last index),
* the closing segment ``last -> first`` across the trailing-edge gap is the blunt facet.

Two phantom regions are added around the 10 design stations so the surface is closed:

Root blend (T074)
    Between the hub (default ``r/R = 0.04``, matching the solver's innermost integration
    limit) and the innermost station (``r/R = 0.10``) three extra stations are inserted.
    The chord is blended with a C1 smoothstep ``s(u) = 3u^2 - 2u^3`` from
    ``ROOT_CHORD_RATIO * c_inner`` at the hub up to ``c_inner``, while the thickness scale
    runs the other way from ``ROOT_THICKNESS_GAIN`` down to ``1.0``.  The root therefore
    becomes a blunt rounded blank (high ``t/c``, near-rectangular planform) and the
    absolute trailing-edge gap can only *increase*, so the hard blunt-TE constraint holds
    in the structural transition region too.

Tip cap (T073)
    Beyond the outermost station (``r/R = 0.97``) a shrunken/rotated phantom section is
    placed at ``tip_extent_over_r * R`` with two intermediate stations, so the surface
    curves inward and closes like a rounded tip.  The final contour is capped by a planar
    face, which keeps the mesh and the STEP B-rep geometrically identical.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

from hoverblade_fm.models import AirfoilPoints, Design, Section

#: Default points per resampled closed contour (odd so the LE sits at an integer index).
DEFAULT_POINTS_PER_SECTION = 161
#: Hub as a fraction of the rotor radius when the caller does not override it.
DEFAULT_HUB_RADIUS_OVER_R = 0.04
#: Chord of the root blank relative to the innermost design section.
ROOT_CHORD_RATIO = 0.85
#: Thickness scale of the root blank relative to the innermost design section.
ROOT_THICKNESS_GAIN = 1.35
#: Number of blended phantom stations inserted between the hub and the first section.
ROOT_BLEND_STATIONS = 3
#: Number of phantom stations (including the cap section) beyond the outer section.
TIP_BLEND_STATIONS = 3
#: Chord of the tip cap section relative to the outermost design section.
TIP_CHORD_RATIO = 0.30
#: Extra twist applied to the tip cap section [deg], mimicking a rounded/machined tip.
TIP_ROTATION_DEG = -2.0


@dataclass
class LoftedBlade:
    """A lofted blade: closed station contours at increasing radius, in metres."""

    stations: list[np.ndarray]
    station_radii: np.ndarray
    n_per_section: int
    hub_radius_m: float
    tip_radius_m: float
    radius_m: float
    is_design_station: list[bool] = field(default_factory=list)
    label: str = "blade"

    def __post_init__(self) -> None:
        self.station_radii = np.asarray(self.station_radii, dtype=float)
        if len(self.stations) != self.station_radii.size:
            raise ValueError("loft stations and station_radii must have equal length")
        if not self.is_design_station:
            self.is_design_station = [False] * len(self.stations)
        for st in self.stations:
            if st.shape != (self.n_per_section, 3):
                raise ValueError(f"station contour has shape {st.shape}, expected ({self.n_per_section}, 3)")

    @property
    def n_stations(self) -> int:
        return len(self.stations)

    @property
    def contour_array(self) -> np.ndarray:
        """All stations stacked as ``(n_stations, n_per_section, 3)`` in metres."""
        return np.stack(self.stations, axis=0)

    @property
    def sections_mm(self) -> list[np.ndarray]:
        """Every station contour converted to millimetres for CAD output."""
        return [np.asarray(st, dtype=float) * 1000.0 for st in self.stations]

    @property
    def radii_mm(self) -> np.ndarray:
        return self.station_radii * 1000.0

    @property
    def design_indices(self) -> np.ndarray:
        return np.flatnonzero(np.asarray(self.is_design_station, dtype=bool))

    def chord_at(self, station_index: int) -> float:
        st = self.stations[station_index]
        return float(np.max(st[:, 0]) - np.min(st[:, 0]))

    def te_gap_at(self, station_index: int) -> float:
        """Length of the blunt trailing-edge facet of a loft station [m].

        Measured as the distance between the two parametric end points, which is invariant
        to the twist rotation and therefore equals ``t_TE`` of the source section (plan 5.2).
        """
        st = self.stations[station_index]
        return float(np.hypot(st[-1, 0] - st[0, 0], st[-1, 2] - st[0, 2]))


def _smoothstep(u: np.ndarray | float) -> np.ndarray | float:
    return 3.0 * u**2 - 2.0 * u**3


def resample_contour(section: Section, n_points: int) -> np.ndarray:
    """Return the station's closed contour as ``(n_points, 3)`` metres in the blade frame.

    The raw airfoil ordering (lower TE -> lower surface -> LE -> upper surface -> upper TE)
    is preserved and the contour is resampled by arc length, with the two trailing-edge
    corner points kept exactly so the blunt facet carries ``section.te_thickness_m``.
    """
    if n_points < 8:
        raise ValueError("a resampled contour needs at least 8 points")
    raw = section.points.in_blade_frame(section.chord_m, section.twist_deg, section.r_m)
    planar = raw[:, (0, 2)]
    step = np.linalg.norm(np.diff(planar, axis=0), axis=1)
    cum = np.concatenate(([0.0], np.cumsum(step)))
    total = float(cum[-1])
    if total <= 0.0:  # degenerate section: duplicate the single available point
        out = np.tile(raw[0], (n_points, 1))
        out[:, 1] = section.r_m
        return out
    targets = np.linspace(0.0, total, n_points)
    out = np.empty((n_points, 3), dtype=float)
    for col in (0, 2):
        out[:, col] = np.interp(targets, cum, raw[:, col])
    out[:, 1] = section.r_m
    out[0] = raw[0]
    out[-1] = raw[-1]
    out[0, 1] = section.r_m
    out[-1, 1] = section.r_m
    return out


def _phantom_section(
    section: Section, chord_scale: float, thickness_scale: float, extra_twist_deg: float = 0.0
) -> Section:
    """A synthetic section with a scaled chord and a scaled *absolute* thickness.

    Working in the non-dimensional airfoil frame keeps the plan 5.2 definition of
    ``t_TE`` exact: an absolute trailing-edge gap of ``thickness_scale * t_TE_section``,
    independent of the chord scale (a shrunken chord simply raises ``t/c``).
    """
    y_scale = thickness_scale / chord_scale
    points = AirfoilPoints(
        x=section.points.x.copy(),
        y=section.points.y * y_scale,
        n_per_surface=section.points.n_per_surface,
    )
    phantom = replace(
        section,
        points=points,
        chord_m=section.chord_m * chord_scale,
        twist_deg=section.twist_deg + extra_twist_deg,
        te_thickness_m=section.te_thickness_m * thickness_scale,
        max_thickness_m=section.max_thickness_m * thickness_scale,
        camber_m=section.camber_m * thickness_scale,
    )
    return phantom


def root_blend_stations(
    inner: Section,
    r_inner: float,
    r_hub: float,
    count: int = ROOT_BLEND_STATIONS,
    points_per_section: int = DEFAULT_POINTS_PER_SECTION,
) -> list[np.ndarray]:
    """Phantom sections from the hub face up to (but not including) the innermost station."""
    if count <= 0 or r_inner <= r_hub:
        return []
    u = np.linspace(0.0, 1.0, count, endpoint=False)
    radii = r_hub + (r_inner - r_hub) * u
    out: list[np.ndarray] = []
    for ui, r in zip(u, radii, strict=True):
        blend = float(_smoothstep(ui))
        chord_scale = ROOT_CHORD_RATIO + (1.0 - ROOT_CHORD_RATIO) * blend
        thickness_scale = ROOT_THICKNESS_GAIN + (1.0 - ROOT_THICKNESS_GAIN) * blend
        contour = resample_contour(_phantom_section(inner, chord_scale, thickness_scale), points_per_section)
        contour[:, 1] = r
        out.append(contour)
    return out


def tip_cap_stations(
    outer: Section,
    r_outer: float,
    r_tip: float,
    count: int = TIP_BLEND_STATIONS,
    points_per_section: int = DEFAULT_POINTS_PER_SECTION,
) -> list[np.ndarray]:
    """Phantom sections from the outermost station out to the rounded tip cap.

    The chord shrinks toward :data:`TIP_CHORD_RATIO` while the *absolute* thickness is held
    constant, so the tip rounds off and the trailing-edge gap never drops below the design
    value (:meth:`LoftedBlade.te_gap_at` stays >= ``te_min_m``).
    """
    if count <= 0 or r_tip <= r_outer:
        return []
    u = np.linspace(1.0 / count, 1.0, count)
    out: list[np.ndarray] = []
    for ui in u:
        chord_scale = 1.0 + (TIP_CHORD_RATIO - 1.0) * float(_smoothstep(ui))
        extra_twist = TIP_ROTATION_DEG * ui
        contour = resample_contour(_phantom_section(outer, chord_scale, 1.0, extra_twist), points_per_section)
        contour[:, 1] = r_outer + (r_tip - r_outer) * ui
        out.append(contour)
    return out


def _insert_interpolated(
    stations: list[np.ndarray], radii: list[float], flags: list[bool], target: int
) -> None:
    """Densify a loft in place by inserting ruled (linear) stations at the widest gaps."""
    while len(stations) < target:
        widths = [radii[i + 1] - radii[i] for i in range(len(stations) - 1)]
        if not widths or max(widths) <= 0.0:
            return
        i = int(np.argmax(widths))
        mid = 0.5 * (stations[i] + stations[i + 1])
        mid[:, 1] = 0.5 * (radii[i] + radii[i + 1])
        stations.insert(i + 1, mid)
        radii.insert(i + 1, float(mid[0, 1]))
        flags.insert(i + 1, False)


def loft_blade(
    design: Design,
    *,
    n_spanwise: int | None = None,
    hub_radius_over_r: float | None = None,
    tip_extent_over_r: float = 1.02,
    points_per_section: int = DEFAULT_POINTS_PER_SECTION,
) -> LoftedBlade:
    """Loft the 10 design sections into a closed blade surface (T072, T073, T074).

    ``n_spanwise`` is the requested total station count; when larger than the natural
    station list, ruled stations are inserted (the design sections always survive).
    """
    radius = float(design.radius_m)
    r_hub = (DEFAULT_HUB_RADIUS_OVER_R if hub_radius_over_r is None else hub_radius_over_r) * radius
    r_tip = float(tip_extent_over_r) * radius
    r_stations = np.asarray(design.r_R, dtype=float) * radius
    r_inner = float(r_stations[0])
    r_outer = float(r_stations[-1])

    contours = [resample_contour(s, points_per_section) for s in design.sections]
    radii = [float(r) for r in r_stations]
    flags = [True] * len(contours)

    root = root_blend_stations(design.sections[0], r_inner, r_hub, points_per_section=points_per_section)
    tip = tip_cap_stations(design.sections[-1], r_outer, r_tip, points_per_section=points_per_section)
    stations = root + contours + tip
    all_radii = [float(st[0, 1]) for st in root] + radii + [float(st[0, 1]) for st in tip]
    all_flags = [False] * len(root) + flags + [False] * len(tip)

    if n_spanwise is not None:
        _insert_interpolated(stations, all_radii, all_flags, int(n_spanwise))

    order = np.argsort(np.asarray(all_radii, dtype=float))
    stations = [stations[int(i)] for i in order]
    all_radii = [float(all_radii[int(i)]) for i in order]
    all_flags = [bool(all_flags[int(i)]) for i in order]

    return LoftedBlade(
        stations=stations,
        station_radii=np.asarray(all_radii, dtype=float),
        n_per_section=int(points_per_section),
        hub_radius_m=float(r_hub),
        tip_radius_m=float(r_tip),
        radius_m=radius,
        is_design_station=all_flags,
        label=design.label,
    )


def loft_section_properties(loft: LoftedBlade) -> dict[str, np.ndarray]:
    """Chord, thickness and TE gap of every loft station (metres) for reports/checks."""
    chord = np.array([loft.chord_at(i) for i in range(loft.n_stations)])
    thick = np.array([float(np.max(s[:, 2]) - np.min(s[:, 2])) for s in loft.stations])
    gap = np.array([loft.te_gap_at(i) for i in range(loft.n_stations)])
    return {"chord_m": chord, "thickness_m": thick, "te_gap_m": gap}
