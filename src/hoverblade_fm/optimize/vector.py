"""Design-vector encoding / decoding (T046, T049).

The optimiser works in a normalised box ``z in [-1, 1]**n`` because CMA-ES and SLSQP behave
much better on comparable scales.  Shape parameters are *spanwise fields* with three control
points each rather than ten independent stations, which keeps the dimension at 34, guarantees
smooth spanwise variation and still spans a rich design space.  Decoding always goes through
:func:`hoverblade_fm.geometry.sections.plan_from_raw`, so every decoded candidate carries the
blunt-trailing-edge hard clamp and the planform rate projection.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from hoverblade_fm.config.schema import TWIST_MAX_DEG, TWIST_MIN_DEG, ResolvedConfig
from hoverblade_fm.geometry.cst import SectionParameters
from hoverblade_fm.geometry.sections import BladePlan, plan_from_raw
from hoverblade_fm.geometry.spanwise import DEFAULT_CHORD_CP_R, DEFAULT_TWIST_CP_R, make_spanwise

#: Spanwise knots for each shape field (three control points -> smooth, low dimensional).
SHAPE_CP_R: tuple[float, ...] = (0.15, 0.50, 0.95)
SHAPE_FIELDS: tuple[str, ...] = (
    "t_over_c",
    "x_t",
    "camber",
    "x_camber",
    "te_gap",
    "le_factor",
    "aft_upper",
    "aft_lower",
)

#: Physical bounds of the shape fields, in the order of :data:`SHAPE_FIELDS`.
SHAPE_LO = np.array([0.060, 0.200, 0.000, 0.280, 0.002, 0.850, -0.020, -0.020])
SHAPE_HI = np.array([0.240, 0.450, 0.070, 0.600, 0.060, 1.450, 0.020, 0.020])

#: Centre of the initial shape field, in the same order.
SHAPE_SEED = np.array([0.150, 0.300, 0.038, 0.420, 0.012, 1.050, 0.000, 0.000])
SHAPE_SPAN = np.array([-0.025, 0.040, -0.018, -0.060, -0.003, 0.080, 0.000, 0.000])


@dataclass
class DesignVectorSpec:
    """Bounds and layout of the normalised design vector for one configuration."""

    cfg: ResolvedConfig
    n_chord_cp: int = len(DEFAULT_CHORD_CP_R)
    n_twist_cp: int = len(DEFAULT_TWIST_CP_R)
    n_shape_cp: int = len(SHAPE_CP_R)
    chord_cp_r: np.ndarray = field(default_factory=lambda: np.asarray(DEFAULT_CHORD_CP_R))
    twist_cp_r: np.ndarray = field(default_factory=lambda: np.asarray(DEFAULT_TWIST_CP_R))

    def __post_init__(self) -> None:
        self.n_per_field = self.n_shape_cp
        self.shape_block = self.n_shape_cp * len(SHAPE_FIELDS)
        self.dim = self.n_chord_cp + self.n_twist_cp + self.shape_block

    @property
    def slices(self) -> dict[str, slice]:
        c = slice(0, self.n_chord_cp)
        t = slice(self.n_chord_cp, self.n_chord_cp + self.n_twist_cp)
        s = slice(self.n_chord_cp + self.n_twist_cp, self.dim)
        return {"chord": c, "twist": t, "shape": s}

    def te_gap_bounds(self) -> tuple[np.ndarray, np.ndarray]:
        """TE gap bounds per shape control point: never below what the user demanded."""
        lo = np.full(
            self.n_shape_cp, max(self.cfg.airfoil.te_over_chord_min, self.cfg.te_min_m / self.cfg.chord_max_m)
        )
        hi = np.full(self.n_shape_cp, self.cfg.airfoil.te_over_chord_max)
        return lo, hi

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        lo = np.empty(self.dim)
        hi = np.empty(self.dim)
        s = self.slices
        lo[s["chord"]] = self.cfg.chord_min_m
        hi[s["chord"]] = self.cfg.chord_max_m
        lo[s["twist"]] = TWIST_MIN_DEG
        hi[s["twist"]] = TWIST_MAX_DEG
        te_lo, te_hi = self.te_gap_bounds()
        block = self.n_shape_cp
        for i, name in enumerate(SHAPE_FIELDS):
            sl = slice(s["shape"].start + i * block, s["shape"].start + (i + 1) * block)
            if name == "te_gap":
                lo[sl], hi[sl] = te_lo, te_hi
            else:
                idx = SHAPE_FIELDS.index(name)
                lo[sl] = SHAPE_LO[idx]
                hi[sl] = SHAPE_HI[idx]
        return lo, hi

    def names(self) -> list[str]:
        names = [f"chord_cp_{i}" for i in range(self.n_chord_cp)]
        names += [f"twist_cp_{i}" for i in range(self.n_twist_cp)]
        for name in SHAPE_FIELDS:
            names += [f"{name}_cp_{i}" for i in range(self.n_shape_cp)]
        return names


def to_normalised(spec: DesignVectorSpec, physical: np.ndarray) -> np.ndarray:
    lo, hi = spec.bounds()
    mid = 0.5 * (lo + hi)
    half = np.maximum(0.5 * (hi - lo), 1e-9)
    return np.clip((np.asarray(physical, dtype=float) - mid) / half, -1.0, 1.0)


def from_normalised(spec: DesignVectorSpec, z: np.ndarray) -> np.ndarray:
    lo, hi = spec.bounds()
    mid = 0.5 * (lo + hi)
    half = np.maximum(0.5 * (hi - lo), 1e-9)
    z = np.clip(np.asarray(z, dtype=float), -1.0, 1.0)
    return mid + half * z


def decode(spec: DesignVectorSpec, z: np.ndarray, cfg: ResolvedConfig | None = None) -> BladePlan:
    """Map a normalised vector to a constraint-projected :class:`BladePlan`."""
    cfg = cfg or spec.cfg
    phys = from_normalised(spec, z)
    s = spec.slices
    chord_cp = phys[s["chord"]]
    twist_cp = phys[s["twist"]]
    block = spec.n_shape_cp
    field_values = {
        name: phys[s["shape"].start + i * block : s["shape"].start + (i + 1) * block]
        for i, name in enumerate(SHAPE_FIELDS)
    }
    r_stations = np.asarray(cfg.r_stations, dtype=float)
    per_station = {
        name: make_spanwise(values, SHAPE_CP_R, name).evaluate(r_stations)
        for name, values in field_values.items()
    }
    params = [
        SectionParameters(
            t_over_c=float(per_station["t_over_c"][i]),
            x_t=float(per_station["x_t"][i]),
            camber=float(max(0.0, per_station["camber"][i])),
            x_camber=float(per_station["x_camber"][i]),
            te_gap=float(max(0.001, per_station["te_gap"][i])),
            le_factor=float(per_station["le_factor"][i]),
            aft_upper=float(per_station["aft_upper"][i]),
            aft_lower=float(per_station["aft_lower"][i]),
        )
        for i in range(r_stations.size)
    ]
    return plan_from_raw(
        cfg,
        chord_cp=chord_cp,
        twist_cp=twist_cp,
        section_params=params,
        chord_cp_r=spec.chord_cp_r,
        twist_cp_r=spec.twist_cp_r,
    )


def encode(spec: DesignVectorSpec, plan: BladePlan) -> np.ndarray:
    """Inverse of :func:`decode` for the control-point subset it can represent."""
    if plan.chord_control_points is None or plan.twist_control_points is None:
        raise ValueError("plan has no control points to encode; rebuild it with plan_from_raw")
    chord_cp = np.asarray(plan.chord_control_points, dtype=float)[: spec.n_chord_cp]
    twist_cp = np.asarray(plan.twist_control_points, dtype=float)[: spec.n_twist_cp]
    r = np.asarray(SHAPE_CP_R, dtype=float)
    fields: list[float] = []
    for name in SHAPE_FIELDS:
        values = np.array([getattr(p, name) for p in plan.params], dtype=float)
        span = make_spanwise(values, np.asarray(spec.cfg.r_stations, dtype=float), name)
        fields.extend(span.evaluate(r))
    physical = np.concatenate((chord_cp, twist_cp, np.array(fields, dtype=float)))
    return to_normalised(spec, physical)


def initial_vector(spec: DesignVectorSpec, cfg: ResolvedConfig | None = None) -> np.ndarray:
    """Feasible starting design: physics planform + hover-oriented shape field (T049)."""
    cfg = cfg or spec.cfg
    from hoverblade_fm.geometry.spanwise import reference_planform

    chord_cp, twist_cp = reference_planform(cfg)
    chord_cp = np.asarray(chord_cp, dtype=float)[: spec.n_chord_cp]
    twist_cp = np.asarray(twist_cp, dtype=float)[: spec.n_twist_cp]
    te_required = cfg.te_min_m / max(cfg.radius_m * 0.10, 1e-9)
    values = SHAPE_SEED.copy()
    fields: list[np.ndarray] = []
    for i, _name in enumerate(SHAPE_FIELDS):
        base = values[i]
        taper = SHAPE_SPAN[i]
        cp = np.array([base + 0.6 * taper, base, base - 0.6 * taper], dtype=float)
        if _name == "te_gap":
            cp = np.maximum(cp, max(cfg.airfoil.te_over_chord_min, cfg.te_min_m / cfg.chord_max_m))
            cp = np.maximum(cp, np.minimum(te_required, cfg.airfoil.te_over_chord_max))
        fields.append(cp)
    physical = np.concatenate((chord_cp, twist_cp, np.concatenate(fields)))
    lo, hi = spec.bounds()
    physical = np.clip(physical, lo, hi)
    return to_normalised(spec, physical)


def vector_report(spec: DesignVectorSpec, z: np.ndarray) -> dict[str, float]:
    """Human-readable decoding of a vector, for logs and the run report."""
    phys = from_normalised(spec, z)
    names = spec.names()
    return {name: round(float(value), 6) for name, value in zip(names, phys, strict=True)}


def random_vector(spec: DesignVectorSpec, rng: np.random.Generator, radius: float = 0.25) -> np.ndarray:
    """Perturbed vector for restarts / multi-seed runs (T053)."""
    base = initial_vector(spec)
    return np.clip(base + rng.normal(0.0, radius, size=spec.dim), -1.0, 1.0)


__all__ = [
    "SHAPE_CP_R",
    "SHAPE_FIELDS",
    "SHAPE_HI",
    "SHAPE_LO",
    "DesignVectorSpec",
    "decode",
    "encode",
    "from_normalised",
    "initial_vector",
    "random_vector",
    "to_normalised",
    "vector_report",
]
