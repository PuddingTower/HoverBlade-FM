"""Shared data contracts for HoverBlade-FM.

Every subsystem (geometry, aero, optimisation, CAD, visualisation, IO) speaks these
objects, so they are kept dependency-light and serialisable.  NumPy arrays are used for
point sets; Pydantic only for user-facing validation (:mod:`hoverblade_fm.config`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

SECTION_COUNT = 10

#: Default non-dimensional radial stations (plan section 9.1).  The outermost station is
#: strictly below 1.0 so that a tip cap can be lofted beyond it.
DEFAULT_R_STATIONS: tuple[float, ...] = (
    0.10,
    0.20,
    0.30,
    0.40,
    0.50,
    0.60,
    0.70,
    0.80,
    0.90,
    0.97,
)

#: Column order of ``sections.csv`` (plan section 3.4).
SECTION_CSV_COLUMNS: tuple[str, ...] = (
    "station_id",
    "r_R",
    "r_m",
    "chord_m",
    "twist_deg",
    "te_thickness_m",
    "max_thickness_m",
    "thickness_to_chord",
    "camber_m",
    "le_radius_m",
    "te_included_angle_deg",
    "reynolds",
    "mach",
    "cl_design",
    "cd_design",
    "loading_n_m",
    "alpha_effective_deg",
    "airfoil_file",
)


@dataclass(slots=True)
class AirfoilPoints:
    """Closed airfoil contour in non-dimensional chord coordinates.

    ``x`` runs 0 (leading edge) -> 1 (trailing edge); ``y`` is positive "up".  The contour
    starts at the lower trailing edge, goes forward along the lower surface, rounds the
    leading edge, comes back along the upper surface and ends at the upper trailing edge,
    i.e. ``y[-1] - y[0]`` is the trailing-edge thickness.
    """

    x: np.ndarray
    y: np.ndarray
    n_per_surface: int = 80

    def __post_init__(self) -> None:
        self.x = np.asarray(self.x, dtype=float)
        self.y = np.asarray(self.y, dtype=float)
        if self.x.shape != self.y.shape:
            raise ValueError("airfoil x/y arrays must share a shape")
        if self.x.ndim != 1 or self.x.size < 8:
            raise ValueError("airfoil contour must be a 1-D array of at least 8 points")

    # -- geometry probes ---------------------------------------------------------------
    @property
    def te_thickness(self) -> float:
        """Non-dimensional trailing-edge thickness ``t_TE / c``."""
        return float(self.y[-1] - self.y[0])

    @property
    def te_midline(self) -> float:
        return float(0.5 * (self.y[-1] + self.y[0]))

    @property
    def max_thickness(self) -> float:
        _x_lower, y_lower, _x_upper, y_upper = self.surface_pairs()
        return float(np.max(y_upper - y_lower))

    @property
    def thickness_location(self) -> float:
        _x_lower, y_lower, x_upper, y_upper = self.surface_pairs()
        return float(x_upper[int(np.argmax(y_upper - y_lower))])

    @property
    def max_camber(self) -> float:
        _x_lower, y_lower, _x_upper, y_upper = self.surface_pairs()
        return float(np.max(0.5 * (y_upper + y_lower)))

    @property
    def camber_line(self) -> np.ndarray:
        _x_lower, y_lower, _x_upper, y_upper = self.surface_pairs()
        return 0.5 * (y_upper + y_lower)

    def surface_pairs(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Return ``(x_lower, y_lower, x_upper, y_upper)``, both ordered LE -> TE."""
        x_lower = self.x[: self.n_per_surface][::-1]
        y_lower = self.y[: self.n_per_surface][::-1]
        x_upper = self.x[self.n_per_surface :]
        y_upper = self.y[self.n_per_surface :]
        return x_lower, y_lower, x_upper, y_upper

    def trailing_edge_gap_at(self, x_query: float) -> float:
        """Thickness measured at ``x_query`` (used for the 0.995 / 0.990 manufacturing checks)."""
        x_lower, y_lower, x_upper, y_upper = self.surface_pairs()
        yl = float(np.interp(x_query, x_lower, y_lower))
        yu = float(np.interp(x_query, x_upper, y_upper))
        return yu - yl

    # -- transforms --------------------------------------------------------------------
    def scale(self, chord: float) -> AirfoilPoints:
        return AirfoilPoints(x=self.x * chord, y=self.y * chord, n_per_surface=self.n_per_surface)

    def rotate(self, twist_deg: float) -> AirfoilPoints:
        """Rotate about the 25% chord reference point (plan section 3.5)."""
        ang = np.deg2rad(twist_deg)
        c, s = np.cos(ang), np.sin(ang)
        xr = self.x - 0.25
        yr = self.y
        return AirfoilPoints(
            x=0.25 + c * xr - s * yr,
            y=s * xr + c * yr,
            n_per_surface=self.n_per_surface,
        )

    def in_blade_frame(self, chord: float, twist_deg: float, r: float) -> np.ndarray:
        """Physical section coordinates placed in the blade frame.

        Returned shape is ``(N, 3)`` with ``x`` along the rotation (chordwise) direction,
        ``y`` the radial blade axis (``r``) and ``z`` the blade thickness/collective axis.
        """
        pts = self.scale(chord).rotate(twist_deg)
        out = np.zeros((pts.x.size, 3))
        out[:, 0] = pts.x - 0.25 * chord
        out[:, 1] = r
        out[:, 2] = pts.y
        return out


@dataclass(slots=True)
class Section:
    """One radial blade station: geometry plus its design-point aero values."""

    station_id: int
    r_R: float
    r_m: float
    chord_m: float
    twist_deg: float
    te_thickness_m: float
    max_thickness_m: float
    thickness_to_chord: float
    camber_m: float
    le_radius_m: float
    te_included_angle_deg: float
    reynolds: float
    mach: float
    cl_design: float
    cd_design: float
    loading_n_m: float
    alpha_effective_deg: float
    points: AirfoilPoints
    airfoil_file: str = ""

    @property
    def te_thickness_to_chord(self) -> float:
        return self.te_thickness_m / self.chord_m if self.chord_m else float("nan")

    def to_row(self) -> dict[str, Any]:
        return {
            "station_id": self.station_id,
            "r_R": self.r_R,
            "r_m": self.r_m,
            "chord_m": self.chord_m,
            "twist_deg": self.twist_deg,
            "te_thickness_m": self.te_thickness_m,
            "max_thickness_m": self.max_thickness_m,
            "thickness_to_chord": self.thickness_to_chord,
            "camber_m": self.camber_m,
            "le_radius_m": self.le_radius_m,
            "te_included_angle_deg": self.te_included_angle_deg,
            "reynolds": self.reynolds,
            "mach": self.mach,
            "cl_design": self.cl_design,
            "cd_design": self.cd_design,
            "loading_n_m": self.loading_n_m,
            "alpha_effective_deg": self.alpha_effective_deg,
            "airfoil_file": self.airfoil_file,
        }


@dataclass(slots=True)
class Design:
    """A complete blade design: 10 sections + the operating point that produced them."""

    sections: list[Section]
    radius_m: float
    blade_count: int
    rpm: float
    omega: float
    te_min_m: float
    design_vector: np.ndarray | None = None
    collective_trim_deg: float = 0.0
    label: str = "design"

    def __post_init__(self) -> None:
        if len(self.sections) != SECTION_COUNT:
            raise ValueError(f"a design must carry exactly {SECTION_COUNT} sections")
        self.sections.sort(key=lambda s: s.r_R)

    @property
    def r_R(self) -> np.ndarray:
        return np.array([s.r_R for s in self.sections])

    @property
    def chord_m(self) -> np.ndarray:
        return np.array([s.chord_m for s in self.sections])

    @property
    def twist_deg(self) -> np.ndarray:
        return np.array([s.twist_deg for s in self.sections])

    @property
    def te_thickness_m(self) -> np.ndarray:
        return np.array([s.te_thickness_m for s in self.sections])

    @property
    def solidity(self) -> float:
        c = self.chord_m
        r = self.r_R * self.radius_m
        dr = np.gradient(r)
        return float(self.blade_count * np.sum(c * dr) / (np.pi * self.radius_m**2))

    @property
    def tip_speed(self) -> float:
        return self.omega * self.radius_m

    def min_te_thickness(self) -> float:
        return float(np.min(self.te_thickness_m))

    def te_margin(self) -> float:
        return self.min_te_thickness() - self.te_min_m

    def is_blunt(self, tol: float = 0.0) -> bool:
        """Hard constraint of the whole project: no sharp trailing edge anywhere."""
        return bool(self.min_te_thickness() >= self.te_min_m - tol)

    def contour_arrays(self) -> list[np.ndarray]:
        """Physical ``(N, 2)`` contours per station, ready for plotting/lofting."""
        return [s.points.in_blade_frame(s.chord_m, s.twist_deg, s.r_m)[:, (0, 2)] for s in self.sections]


@dataclass(slots=True)
class RadialLoad:
    """Spanwise loading distributions returned by the BEMT solver."""

    r_R: np.ndarray
    thrust_gradient: np.ndarray  # dT/dr [N/m]
    torque_gradient: np.ndarray  # dQ/dr [N m]
    power_gradient: np.ndarray  # dP/dr [W/m]
    induced_velocity: np.ndarray  # axial induced velocity [m/s]
    inflow_angle_deg: np.ndarray
    aoa_deg: np.ndarray
    cl: np.ndarray
    cd: np.ndarray
    mach: np.ndarray
    reynolds: np.ndarray
    loss_factor: np.ndarray


@dataclass(slots=True)
class Performance:
    """Hover performance record (plan section 20.3)."""

    thrust_target_n: float
    thrust_calc_n: float
    torque_nm: float
    power_ideal_w: float
    power_induced_w: float
    power_profile_w: float
    power_swirl_w: float
    power_misc_w: float
    power_actual_w: float
    figure_of_merit: float
    rpm: float
    tip_mach: float
    solidity: float
    blade_count: int
    tip_loss_factor: float = 1.0
    collective_trim_deg: float = 0.0
    radial: RadialLoad | None = None

    @property
    def thrust_error(self) -> float:
        return (self.thrust_calc_n - self.thrust_target_n) / self.thrust_target_n

    @property
    def figure_power(self) -> float:
        """P/T, the classic hover efficiency number [W/N]."""
        return self.power_actual_w / self.thrust_calc_n if self.thrust_calc_n else float("nan")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("radial", None)
        data["thrust_error"] = self.thrust_error
        data["figure_power_w_per_n"] = self.figure_power
        return data


@dataclass(slots=True)
class ConstraintReport:
    """Feasibility of a design against the plan's hard and soft constraints."""

    te_min_required_m: float
    te_min_actual_m: float
    te_violation_m: float
    te_included_angle_ok: bool
    thrust_error: float
    geometry_valid: bool
    geometry_issues: tuple[str, ...] = ()
    smoothness_penalty: float = 0.0

    @property
    def feasible(self) -> bool:
        return bool(
            self.geometry_valid
            and self.te_violation_m <= 1e-12
            and abs(self.thrust_error) < 0.02
            and self.te_included_angle_ok
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["feasible"] = self.feasible
        data["geometry_issues"] = list(self.geometry_issues)
        return data


@dataclass(slots=True)
class OptimizationResult:
    """Best design plus the search history (plan section 12.3 / T054)."""

    design: Design
    performance: Performance
    constraints: ConstraintReport
    initial_performance: Performance | None
    initial_constraints: ConstraintReport | None
    history: list[dict[str, Any]] = field(default_factory=list)
    evaluations: int = 0
    strategy: str = "cma-es+slsqp"
    seed: int = 0
    report: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class Provenance:
    program_version: str
    git_commit: str
    input_hash: str
    random_seed: int
    timestamp: str
    optimization_settings: dict[str, Any]
    resolved_config: dict[str, Any]
    python: str
    numpy: str
    scipy: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def stations_as_array(r_R: np.ndarray | list[float] | None) -> np.ndarray:
    """Return the radial stations as a float array, defaulting to the 10-station plan."""
    if r_R is None:
        return np.array(DEFAULT_R_STATIONS, dtype=float)
    arr = np.asarray(r_R, dtype=float)
    if arr.size != SECTION_COUNT:
        raise ValueError(f"expected {SECTION_COUNT} radial stations, got {arr.size}")
    return arr
