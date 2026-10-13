"""CST-class airfoil parameterisation with an explicit blunt-trailing-edge gap.

The parameterisation is built so that a sharp trailing edge is *geometrically impossible*:
both surfaces are written as a class function that vanishes at ``x/c = 1`` plus a linear
ramp, hence

    y_u(1) - y_l(1) = te_gap          (exact, by construction)

and ``te_gap`` is clamped to ``max(te_min_m / chord, ...)`` at decode time
(:func:`hoverblade_fm.geometry.trailing_edge.clamp_te_gap`).  Optimiser and user can only
move the gap above the required thickness, never below it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from hoverblade_fm.models import AirfoilPoints

LE_EXPONENT = 0.5
N_PER_SURFACE_DEFAULT = 80


@dataclass(slots=True)
class SectionParameters:
    """Non-dimensional shape description of one radial station."""

    t_over_c: float = 0.12
    x_t: float = 0.30
    camber: float = 0.03
    x_camber: float = 0.40
    te_gap: float = 0.004
    te_mid: float = 0.0
    le_factor: float = 1.0
    aft_upper: float = 0.0
    aft_lower: float = 0.0

    def validate(self) -> None:
        if self.t_over_c <= 1e-4:
            raise ValueError("t_over_c must be positive")
        if not 0.15 <= self.x_t <= 0.65:
            raise ValueError(f"x_t must lie in [0.15, 0.65], got {self.x_t}")
        if not 0.15 <= self.x_camber <= 0.85:
            raise ValueError(f"x_camber must lie in [0.15, 0.85], got {self.x_camber}")
        if self.te_gap < 0.0:
            raise ValueError("te_gap must be non-negative")
        if not 0.6 <= self.le_factor <= 1.8:
            raise ValueError(f"le_factor must lie in [0.6, 1.8], got {self.le_factor}")

    def as_vector(self) -> np.ndarray:
        return np.array(
            [
                self.t_over_c,
                self.x_t,
                self.camber,
                self.x_camber,
                self.te_gap,
                self.te_mid,
                self.le_factor,
                self.aft_upper,
                self.aft_lower,
            ]
        )

    @classmethod
    def from_vector(cls, vec: np.ndarray) -> SectionParameters:
        names = (
            "t_over_c",
            "x_t",
            "camber",
            "x_camber",
            "te_gap",
            "te_mid",
            "le_factor",
            "aft_upper",
            "aft_lower",
        )
        if len(vec) != len(names):
            raise ValueError(f"section parameter vector must have {len(names)} entries")
        return cls(**dict(zip(names, (float(v) for v in vec), strict=True)))


def _class_function(x: np.ndarray, n1: float, n2: float) -> np.ndarray:
    """``x**n1 * (1-x)**n2`` evaluated safely at the endpoints."""
    xc = np.clip(x, 0.0, 1.0)
    with np.errstate(invalid="ignore"):
        out = np.power(xc, n1) * np.power(1.0 - xc, n2)
    return np.where((xc <= 0.0) | (xc >= 1.0), 0.0, out)


def _normalised_bump(n1: float, n2: float) -> tuple[float, float]:
    """Peak value and peak location of the class function, used to normalise amplitudes."""
    x_peak = n1 / (n1 + n2)
    xs = np.linspace(0.0, 1.0, 4001)
    curve = _class_function(xs, n1, n2)
    return float(curve.max()), x_peak


def thickness_exponents(x_t: float) -> tuple[float, float]:
    """Return the class-function exponents whose thickness peak sits at ``x_t``."""
    n2 = LE_EXPONENT * (1.0 - x_t) / max(x_t, 1e-6)
    return LE_EXPONENT, float(np.clip(n2, 0.35, 3.0))


def camber_exponents(x_c: float) -> tuple[float, float]:
    n2 = 1.0 * (1.0 - x_c) / max(x_c, 1e-6)
    return 1.0, float(np.clip(n2, 0.3, 4.0))


def _le_bump(x: np.ndarray) -> np.ndarray:
    """Nose-region bump: peaks at ``x ~ 0.077`` and is ~0 past mid-chord."""
    n1, n2 = LE_EXPONENT, 6.0
    raw = _class_function(x, n1, n2)
    peak, _ = _normalised_bump(n1, n2)
    return raw / max(peak, 1e-12)


def thickness_half_distribution(x: np.ndarray, params: SectionParameters) -> np.ndarray:
    """Half-thickness ``h(x)`` with ``2 h(1) = te_gap`` exactly and ``max 2h = t_over_c``.

    The blunt gap is carried by a linear ramp that is never rescaled, so the trailing-edge
    guarantee survives; the class-function part is scaled by a bisection factor so that the
    *realised* maximum thickness equals the requested ``t_over_c`` rather than drifting upward
    whenever a thick trailing edge is added on top of it.
    """
    x = np.clip(np.asarray(x, dtype=float), 0.0, 1.0)
    n1, n2 = thickness_exponents(params.x_t)
    peak, _ = _normalised_bump(n1, n2)
    base = params.t_over_c * (_class_function(x, n1, n2) / max(peak, 1e-12))
    le_extra = (params.le_factor - 1.0) * 0.35 * params.t_over_c * _le_bump(x)
    shape = 0.5 * np.maximum(base + le_extra, 0.0)
    ramp = 0.5 * params.te_gap * x

    def realised(scale: float) -> float:
        return float(np.max(2.0 * (scale * shape + ramp)))

    target = params.t_over_c
    if realised(1.0) <= target:
        return shape + ramp
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if realised(mid) > target:
            hi = mid
        else:
            lo = mid
    return lo * shape + ramp


def camber_line(x: np.ndarray, params: SectionParameters) -> np.ndarray:
    """Camber-line (midline) height; ``= te_mid`` at the trailing edge."""
    x = np.clip(np.asarray(x, dtype=float), 0.0, 1.0)
    n1, n2 = camber_exponents(params.x_camber)
    peak, _ = _normalised_bump(n1, n2)
    base = params.camber * (_class_function(x, n1, n2) / max(peak, 1e-12))
    aft = 0.25 * (params.aft_upper + params.aft_lower) * (x * x * (1.0 - x) ** 2) * 16.0
    return base + aft + params.te_mid * x


def surface_offsets(x: np.ndarray, params: SectionParameters) -> tuple[np.ndarray, np.ndarray]:
    """Upper and lower surface offsets about the midline (adds aft asymmetry)."""
    yc = camber_line(x, params)
    h = thickness_half_distribution(x, params)
    u = 0.25 * (params.aft_upper - params.aft_lower) * 16.0 * x * x * (1.0 - x) ** 2
    return yc + h + u, yc - h + u


def trailing_edge_gap(x: np.ndarray, params: SectionParameters) -> float:
    upper, lower = surface_offsets(x, params)
    return float(upper[-1] - lower[-1])


def build_airfoil(params: SectionParameters, n_per_surface: int = N_PER_SURFACE_DEFAULT) -> AirfoilPoints:
    """Evaluate the closed contour of one section in chord-normalised coordinates.

    The returned contour starts at the lower trailing edge, runs forward along the lower
    surface through the leading edge, then aft along the upper surface and ends at the
    upper trailing edge (see :class:`hoverblade_fm.models.AirfoilPoints`).
    """
    params.validate()
    if n_per_surface < 12:
        raise ValueError("n_per_surface must be >= 12")
    idx = np.arange(n_per_surface, dtype=float)
    x_le_te = 0.5 * (1.0 - np.cos(np.pi * idx / (n_per_surface - 1)))
    x_le_te[0] = 0.0
    x_le_te[-1] = 1.0
    upper, lower = surface_offsets(x_le_te, params)

    # lower surface ordered TE -> LE, upper surface ordered LE -> TE; both include the nose
    x = np.concatenate((x_le_te[::-1], x_le_te))
    y = np.concatenate((lower[::-1], upper))
    return AirfoilPoints(x=x, y=y, n_per_surface=n_per_surface)


def analytic_te_gap(params: SectionParameters) -> float:
    """The trailing-edge gap the evaluation will produce, without building coordinates."""
    return float(params.te_gap)


def le_radius(points: AirfoilPoints, n_fit: int = 14) -> float:
    """Leading-edge radius from the parabolic nose of the section (chord units).

    Near the nose every well-formed section satisfies ``dy^2 = a x`` measured from the camber
    line, and the radius of curvature there is ``a / 2``; for a NACA four-digit section that
    reproduces the textbook ``1.1019 t^2`` exactly.  A circle fit is kept as a fallback for
    degenerate sampling.
    """
    x_lower, y_lower, x_upper, y_upper = points.surface_pairs()
    n = max(4, min(n_fit, points.n_per_surface - 1))
    xs = x_upper[:n]
    ys = y_upper[:n]
    mid = 0.5 * (np.interp(xs, x_lower, y_lower) + ys)
    dy = ys - mid
    mask = xs > 0.0
    x, y2 = xs[mask], dy[mask] ** 2
    if x.size < 3:
        return _circle_le_radius(xs, dy)
    design = np.column_stack((x, x * x))
    try:
        coef, *_ = np.linalg.lstsq(design, y2, rcond=None)
    except np.linalg.LinAlgError:
        return _circle_le_radius(xs, dy)
    radius = 0.5 * float(coef[0])
    if not np.isfinite(radius) or radius <= 0.0:
        return _circle_le_radius(xs, dy)
    return radius


def _circle_le_radius(x: np.ndarray, y: np.ndarray) -> float:
    """Fallback estimator: circle fit through the leading surface points."""
    k = max(3, min(14, x.size))
    return circle_radius(x[:k], y[:k])


def circle_radius(x: np.ndarray, y: np.ndarray) -> float:
    """Algebraic circle fit (Kasa) radius; returns 0.0 when degenerate."""
    if x.size < 3:
        return 0.0
    A = np.column_stack((x, y, np.ones_like(x)))
    b = x**2 + y**2
    try:
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    except np.linalg.LinAlgError:
        return 0.0
    cx, cy, c = sol
    r2 = c + cx * cx / 4.0 + cy * cy / 4.0
    return float(np.sqrt(r2)) if r2 > 0 else 0.0


def sample_x(n_per_surface: int = N_PER_SURFACE_DEFAULT) -> np.ndarray:
    """Cosine-clustered chordwise grid from LE to TE."""
    idx = np.arange(n_per_surface, dtype=float)
    x = 0.5 * (1.0 - np.cos(np.pi * idx / (n_per_surface - 1)))
    x[0] = 0.0
    x[-1] = 1.0
    return x


__all__ = [
    "LE_EXPONENT",
    "N_PER_SURFACE_DEFAULT",
    "SectionParameters",
    "analytic_te_gap",
    "build_airfoil",
    "camber_exponents",
    "circle_radius",
    "le_radius",
    "sample_x",
    "surface_offsets",
    "thickness_exponents",
    "thickness_half_distribution",
    "trailing_edge_gap",
]
