"""Global / local optimiser wrappers (T050, T051, T052).

CMA-ES explores the normalised design box; when the ``cma`` distribution is unavailable the
search falls back to scipy's differential evolution so the core never hard-depends on one
optimizer.  SLSQP then refines a physically meaningful subset of the coordinates (planform
control points and the thickness / camber / trailing-edge fields).  Evaluation logging lives
in :class:`hoverblade_fm.optimize.objective.Objective`, so the reports here stay thin.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
from scipy.optimize import differential_evolution, minimize

try:  # pragma: no cover - optional dependency guard
    import cma as _cma
except ImportError:  # pragma: no cover
    _cma = None


@dataclass
class SearchReport:
    """Outcome of one search phase."""

    best_z: np.ndarray
    best_value: float
    evaluations: int
    algorithm: str
    converged: bool
    note: str = ""


def cma_search(
    cost: Callable[[np.ndarray], float],
    x0: np.ndarray,
    *,
    sigma0: float = 0.35,
    popsize: int = 48,
    max_evaluations: int = 1500,
    seed: int = 0,
    tol_fun: float = 1e-7,
    verbose: bool = False,
) -> SearchReport:
    """CMA-ES global search on the normalised box (the cost function clips outside values)."""
    if _cma is None:
        return de_search(cost, x0, popsize=popsize, max_evaluations=max_evaluations, seed=seed)
    opts = {
        "popsize": int(popsize),
        "seed": int(seed),
        "maxfevals": int(max_evaluations),
        "tolfun": tol_fun,
        "verbose": 1 if verbose else -9,
    }
    es = _cma.CMAEvolutionStrategy(np.asarray(x0, dtype=float), sigma0, opts)
    evaluations = 0
    iterations = 0
    best_value = math.inf
    best_z = np.asarray(x0, dtype=float).copy()
    while not es.stop():
        solutions = es.ask()
        values = []
        for candidate in solutions:
            value = float(cost(candidate))
            evaluations += 1
            values.append(value)
            if value < best_value:
                best_value = value
                best_z = np.asarray(candidate, dtype=float).copy()
        es.tell(solutions, values)
        iterations += 1
        if evaluations >= max_evaluations:
            break
    return SearchReport(
        best_z=best_z,
        best_value=best_value,
        evaluations=evaluations,
        algorithm="CMA-ES",
        converged=bool(es.stop()) or evaluations >= max_evaluations,
        note=f"dim={x0.size}, popsize={popsize}, generations={iterations}",
    )


def de_search(
    cost: Callable[[np.ndarray], float],
    x0: np.ndarray,
    *,
    popsize: int = 32,
    max_evaluations: int = 1500,
    seed: int = 0,
) -> SearchReport:
    """Differential-evolution fallback used only when CMA-ES is not installed."""
    dim = int(np.size(x0))
    iterations = max(1, int(max_evaluations) // (popsize * (dim + 1)))
    result = differential_evolution(
        cost,
        bounds=[(-1.0, 1.0)] * dim,
        maxiter=iterations,
        popsize=popsize,
        seed=seed,
        polish=False,
    )
    return SearchReport(
        best_z=np.asarray(result.x, dtype=float),
        best_value=float(result.fun),
        evaluations=int(result.nfev),
        algorithm="differential-evolution",
        converged=bool(result.success),
        note="cma package unavailable, used scipy differential evolution",
    )


def refinement_indices(
    n_chord: int, n_twist: int, shape_start: int, n_shape_cp: int, fields: Sequence[int]
) -> list[int]:
    """Coordinates used by the local phase: the planform plus selected shape fields."""
    idx = list(range(n_chord + n_twist))
    for field_index in fields:
        idx.extend(
            range(shape_start + field_index * n_shape_cp, shape_start + (field_index + 1) * n_shape_cp)
        )
    return idx


def refine_local(
    cost: Callable[[np.ndarray], float],
    z0: np.ndarray,
    free_indices: Sequence[int] | None = None,
    *,
    max_iterations: int = 40,
    step: float = 5e-3,
) -> SearchReport:
    """SLSQP refinement of the selected coordinates inside the normalised box."""
    z0 = np.asarray(z0, dtype=float)
    dim = z0.size
    idx = np.arange(dim, dtype=int) if free_indices is None else np.asarray(list(free_indices), dtype=int)
    full_best_z = z0.copy()
    full_best_value = float(cost(z0))
    calls = 0

    def sub_cost(sub: np.ndarray) -> float:
        nonlocal full_best_z, full_best_value, calls
        calls += 1
        candidate = z0.copy()
        candidate[idx] = np.clip(sub, -1.0, 1.0)
        value = float(cost(candidate))
        if value < full_best_value:
            full_best_value = value
            full_best_z = candidate.copy()
        return value

    try:
        result = minimize(
            sub_cost,
            z0[idx],
            method="SLSQP",
            bounds=[(-1.0, 1.0)] * idx.size,
            options={"maxiter": int(max_iterations), "eps": float(step), "ftol": 1e-9, "disp": False},
        )
        refined = np.asarray(result.x, dtype=float)
        note = f"iterations={getattr(result, 'nit', 0)}, success={result.success}"
        converged = bool(result.success)
    except Exception as exc:  # pragma: no cover - numerical safety net
        refined = z0[idx]
        note = f"SLSQP aborted ({type(exc).__name__}: {exc}); kept global best"
        converged = False

    candidate = z0.copy()
    candidate[idx] = np.clip(refined, -1.0, 1.0)
    final_value = float(cost(candidate))
    if final_value <= full_best_value:
        full_best_z, full_best_value = candidate, final_value
    return SearchReport(
        best_z=full_best_z,
        best_value=full_best_value,
        evaluations=idx.size * max_iterations,
        algorithm="SLSQP",
        converged=converged,
        note=note,
    )


def __getattr__(name: str) -> object:  # pragma: no cover - clarity for callers
    raise AttributeError(name)


__all__ = [
    "SearchReport",
    "cma_search",
    "de_search",
    "refinement_indices",
    "refine_local",
]
