"""Differentiable hover BEMT prototype (T087).

One solver written against an array namespace so it runs on NumPy and can be traced by JAX:
:func:`solve_core` takes ``A`` (``numpy`` or ``jax.numpy``) and the fixed-point loop is unrolled
in plain Python, which JAX differentiates directly.  With JAX installed :func:`fm_gradient`
returns exact autodiff gradients; without it the same function falls back to central differences
on the NumPy path, so gradient-driven code stays testable everywhere.

Two properties are enforced because a differentiable model is easy to fool:

* thrust is matched inside the iteration - a collective offset converges toward the target - so
  power is always compared at the required thrust.  An untrimmed surrogate lets gradient ascent
  shed thrust and report a figure of merit above one, which is physically impossible; and
* the Prandtl tip/root loss factor is recomputed from the current inflow angle every iteration,
  exactly as the production solver does.

The reduced model still omits the compressibility and post-stall drag refinements and integrates
on a coarser grid than production: it is a gradient surrogate, not a replacement for it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType
from typing import Any

import numpy as np

try:  # pragma: no cover - optional dependency
    import jax
    import jax.numpy as jnp

    _HAS_JAX = True
except Exception:  # pragma: no cover
    jax = None  # type: ignore[assignment]
    jnp = None  # type: ignore[assignment]
    _HAS_JAX = False

CL_SLOPE = 2.0 * np.pi * 0.93
CAMBER_ALPHA0_DEG = 22.0
FRICTION_REFERENCE_RE = 1.5e6
TE_DRAG_SLOPE = 1.0
TE_THICKNESS_INTERACTION = 0.4


def available() -> bool:
    """True when JAX is importable, i.e. when exact autodiff gradients are available."""
    return _HAS_JAX


def backend_name() -> str:
    return "jax-autodiff" if _HAS_JAX else "numpy-central-difference"


@dataclass(frozen=True)
class ReducedBlade:
    """Spanwise arrays the reduced solver needs, sampled onto its own grid."""

    r_over_R: np.ndarray
    chord_over_R: np.ndarray
    twist_rad: np.ndarray
    t_over_c: np.ndarray
    te_over_c: np.ndarray
    camber: np.ndarray
    radius_m: float
    omega: float
    blade_count: int
    rho: float
    mu: float
    thrust_n: float

    @property
    def area(self) -> float:
        return float(np.pi * self.radius_m**2)

    @property
    def power_ideal(self) -> float:
        return float(self.thrust_n**1.5 / np.sqrt(2.0 * self.rho * self.area))


def reduced_blade_from_design(design, cfg, *, grid: int = 61) -> ReducedBlade:
    """Sample a ten-station design onto the reduced solver's grid."""
    from scipy.interpolate import PchipInterpolator

    x = np.linspace(max(cfg.solver.root_extension_r, 1e-3), 1.0, grid)
    stations = np.asarray([s.r_R for s in design.sections], dtype=float)
    raw = {
        "chord_over_R": np.asarray([s.chord_m for s in design.sections], dtype=float) / design.radius_m,
        "twist_rad": np.deg2rad(np.asarray([s.twist_deg for s in design.sections], dtype=float)),
        "t_over_c": np.asarray([s.thickness_to_chord for s in design.sections], dtype=float),
        "te_over_c": np.asarray([s.te_thickness_m / s.chord_m for s in design.sections], dtype=float),
        "camber": np.asarray([s.camber_m / s.chord_m for s in design.sections], dtype=float),
    }
    clipped = np.clip(x, stations[0], stations[-1])
    arrays = {
        key: np.asarray(PchipInterpolator(stations, value, extrapolate=True)(clipped), dtype=float)
        for key, value in raw.items()
    }
    return ReducedBlade(
        r_over_R=x,
        chord_over_R=arrays["chord_over_R"],
        twist_rad=arrays["twist_rad"],
        t_over_c=arrays["t_over_c"],
        te_over_c=arrays["te_over_c"],
        camber=arrays["camber"],
        radius_m=design.radius_m,
        omega=design.omega,
        blade_count=design.blade_count,
        rho=cfg.rho,
        mu=cfg.mu,
        thrust_n=cfg.thrust_n,
    )


def _as(A: ModuleType, values: np.ndarray) -> Any:
    return np.asarray(values, dtype=float) if A is np else A.asarray(values, dtype=A.float64)


def _prandtl(A: ModuleType, x: Any, sin_phi: Any, blades: int) -> Any:
    """Double (tip and root) Prandtl loss factor in the caller's array namespace."""
    s = A.maximum(A.abs(sin_phi), 1e-6)
    tip = (2.0 / A.pi) * A.arccos(A.clip(A.exp(-0.5 * blades * (1.0 - x) / A.maximum(x * s, 1e-6)), 0.0, 1.0))
    root = (2.0 / A.pi) * A.arccos(
        A.clip(A.exp(-0.5 * blades * x / A.maximum((1.0 - x) * s, 1e-6)), 0.0, 1.0)
    )
    return A.clip(tip * A.where(x < 0.35, root, 1.0), 1e-6, 1.0)


def _polar(A: ModuleType, alpha: Any, alpha0: Any, re: Any, tc: Any, tec: Any) -> tuple[Any, Any]:
    """Reduced analytic polar: tanh-saturated lift, skin friction plus blunt-edge base drag."""
    cl_peak = 1.45 * A.clip((A.maximum(re, 2.0e4) / FRICTION_REFERENCE_RE) ** 0.15, 0.68, 1.03)
    cl = cl_peak * A.tanh(CL_SLOPE * (alpha - alpha0) / cl_peak)
    friction = 2.0 * 0.075 / A.maximum(A.log10(A.maximum(re, 2.0e4)) - 2.0, 0.6) ** 2 * (1.0 + 2.0 * tc)
    return cl, friction + TE_DRAG_SLOPE * tec * (1.0 + TE_THICKNESS_INTERACTION * tc)


def solve_core(
    blade: ReducedBlade,
    *,
    A: ModuleType = np,
    iterations: int = 40,
    trim_steps: int = 12,
    relaxation: float = 0.35,
    trim_gain: float = 0.08,
) -> dict[str, Any]:
    """Nested hover fixed point: momentum converges at fixed collective, then collective is trimmed.

    ``iterations`` is the inner momentum sweep count and ``trim_steps`` the outer thrust-trim
    count.  Both loops are plain Python so JAX traces and differentiates through them.  Doing
    the trim *inside* the momentum loop was tried first and diverges: the induced velocity and
    the collective reinforce each other, the thrust runs away and the surrogate reports a figure
    of merit above one.
    """
    x = _as(A, blade.r_over_R)
    chord_m = _as(A, blade.chord_over_R) * float(blade.radius_m)
    twist = _as(A, blade.twist_rad)
    tc = _as(A, blade.t_over_c)
    tec = _as(A, blade.te_over_c)
    camber = _as(A, blade.camber)
    radius, omega = float(blade.radius_m), float(blade.omega)
    rho, mu = float(blade.rho), float(blade.mu)
    blades, thrust_target = int(blade.blade_count), float(blade.thrust_n)

    r = x * radius
    dr = A.diff(r)
    alpha0 = -A.deg2rad(CAMBER_ALPHA0_DEG) * camber
    v_i = A.sqrt(thrust_target / (2.0 * rho * A.pi * radius**2))
    v = A.full(r.shape, v_i * 1.05) if A is not np else np.full(r.shape, float(v_i) * 1.05)
    collective = A.zeros(()) if A is not np else 0.0
    dT = dQ = A.zeros(r.shape) if A is not np else np.zeros(r.shape)
    thrust = A.zeros(()) if A is not np else 0.0

    def loads(state_v: Any, pitch: Any) -> tuple[Any, Any, Any]:
        phi = A.arctan2(state_v, omega * r)
        v_rel = A.hypot(omega * r, state_v)
        re = rho * v_rel * chord_m / mu
        # effective angle of attack: geometric twist plus collective, minus the local inflow angle
        cl, cd = _polar(A, twist + pitch - phi, alpha0, re, tc, tec)
        loss = _prandtl(A, x, A.sin(phi), blades)
        d_thrust = loss * 0.5 * rho * blades * chord_m * v_rel**2 * (cl * A.cos(phi) - cd * A.sin(phi))
        d_torque = loss * 0.5 * rho * blades * chord_m * v_rel**2 * (cl * A.sin(phi) + cd * A.cos(phi)) * r
        new_v = (1.0 - relaxation) * state_v + relaxation * A.sqrt(
            A.maximum(d_thrust, 0.0) / (4.0 * A.pi * rho * A.maximum(r, 1e-6))
        )
        return A.clip(new_v, 0.0, 0.9 * omega * r), d_thrust, d_torque

    for _ in range(trim_steps):
        for _ in range(iterations):
            v, dT, dQ = loads(v, collective)
        thrust = A.sum(0.5 * (dT[1:] + dT[:-1]) * dr)
        # more collective raises thrust, so the correction moves in the opposite direction
        collective = A.clip(
            collective - trim_gain * A.log(A.maximum(thrust / thrust_target, 1e-3)), -0.35, 0.5
        )

    for _ in range(iterations):
        v, dT, dQ = loads(v, collective)
    thrust = A.sum(0.5 * (dT[1:] + dT[:-1]) * dr)
    power = A.sum(0.5 * (dQ[1:] + dQ[:-1]) * dr) * omega
    # FM is referenced to the *achieved* thrust, exactly like the production solver, so the
    # surrogate cannot buy a better score by producing less thrust than requested.
    power_ideal = A.sqrt(A.maximum(thrust, 0.0) ** 3 / (2.0 * rho * A.pi * radius**2))
    return {
        "figure_of_merit": power_ideal / A.maximum(power, 1e-6),
        "thrust_n": thrust,
        "power_w": power,
        "collective_rad": collective,
        "thrust_error": (thrust - thrust_target) / thrust_target,
    }


def solve_finite_difference(blade: ReducedBlade, **kwargs: Any) -> dict[str, float]:
    """NumPy evaluation of the reduced solver with plain floats in the result."""
    return {key: float(value) for key, value in solve_core(blade, A=np, **kwargs).items()}


def solve_differentiable(blade: ReducedBlade, **kwargs: Any) -> dict[str, float]:
    """Best available evaluation: JAX-traced when installed, NumPy otherwise."""
    if _HAS_JAX:
        return {key: float(value) for key, value in solve_core(blade, A=jnp, **kwargs).items()}
    return solve_finite_difference(blade, **kwargs)


def fm_of_vector(build: Callable[[np.ndarray], ReducedBlade], x: np.ndarray, **kwargs: Any) -> Any:
    """FM as a scalar in the active backend (differentiable under JAX)."""
    A = jnp if _HAS_JAX else np
    return solve_core(build(np.asarray(x, dtype=float)), A=A, **kwargs)["figure_of_merit"]


def fm_gradient(
    build: Callable[[np.ndarray], ReducedBlade],
    x0: np.ndarray,
    *,
    step: float = 1e-3,
    iterations: int = 80,
) -> tuple[float, np.ndarray]:
    """``dFM/dx``: autodiff with JAX, central differences when it is absent."""
    x0 = np.asarray(x0, dtype=float)
    if not _HAS_JAX:
        base = solve_finite_difference(build(x0), iterations=iterations)["figure_of_merit"]
        gradient = np.zeros_like(x0)
        for index in range(x0.size):
            forward, backward = x0.copy(), x0.copy()
            forward[index] += step
            backward[index] -= step
            gradient[index] = (
                solve_finite_difference(build(forward), iterations=iterations)["figure_of_merit"]
                - solve_finite_difference(build(backward), iterations=iterations)["figure_of_merit"]
            ) / (2.0 * step)
        return float(base), gradient
    value, g = jax.value_and_grad(
        lambda z: solve_core(build(np.asarray(z, dtype=float)), A=jnp, iterations=iterations)[
            "figure_of_merit"
        ]
    )(x0)
    return float(value), np.asarray(g, dtype=float)


def solve_for_fm(build: Callable[[np.ndarray], ReducedBlade], x: np.ndarray, **kwargs: Any) -> float:
    return float(solve_differentiable(build(np.asarray(x, dtype=float)), **kwargs)["figure_of_merit"])


def adam_maximise_fm(
    build: Callable[[np.ndarray], ReducedBlade],
    x0: np.ndarray,
    *,
    steps: int = 40,
    learning_rate: float = 5e-3,
    bounds: tuple[float, float] = (-1.0, 1.0),
    iterations: int = 60,
) -> tuple[np.ndarray, float]:
    """Gradient ascent on FM with Adam, clipped to the normalised box (plan 11.4)."""
    x = np.asarray(x0, dtype=float).copy()
    m = np.zeros_like(x)
    v = np.zeros_like(x)
    beta1, beta2, eps = 0.9, 0.999, 1e-8
    best_x, best_fm = x.copy(), -np.inf
    for step in range(1, steps + 1):
        _fm, gradient = fm_gradient(build, x, iterations=iterations)
        g = -gradient
        m = beta1 * m + (1.0 - beta1) * g
        v = beta2 * v + (1.0 - beta2) * g * g
        x = np.clip(
            x - learning_rate * (m / (1.0 - beta1**step)) / (np.sqrt(v / (1.0 - beta2**step)) + eps), *bounds
        )
        fm_now = solve_for_fm(build, x, iterations=iterations)
        if fm_now > best_fm:
            best_fm, best_x = fm_now, x.copy()
    return best_x, float(best_fm)


__all__ = [
    "ReducedBlade",
    "adam_maximise_fm",
    "available",
    "backend_name",
    "fm_gradient",
    "fm_of_vector",
    "reduced_blade_from_design",
    "solve_core",
    "solve_differentiable",
    "solve_finite_difference",
    "solve_for_fm",
]
