"""Surrogate-assisted and Bayesian optimisation (T085, T086).

A Gaussian-process surrogate over the normalised design vector, with expected-improvement
acquisition, is implemented directly on NumPy/SciPy so the optional path has no heavy
dependency (boTorch is not required).  It is offered as an alternative to CMA-ES for expensive
polar sources (XFOIL or CFD-backed evaluations) and as a pre-screen for the CMA-ES result.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.optimize import minimize
from scipy.stats import norm

from hoverblade_fm.config.schema import ResolvedConfig
from hoverblade_fm.optimize.objective import Objective
from hoverblade_fm.optimize.vector import DesignVectorSpec, initial_vector


def _kernel_matrix(a: np.ndarray, b: np.ndarray, lengthscales: np.ndarray, variance: float) -> np.ndarray:
    scaled_a = a / lengthscales
    scaled_b = b / lengthscales
    sq = (
        np.sum(scaled_a**2, axis=1)[:, None]
        + np.sum(scaled_b**2, axis=1)[None, :]
        - 2.0 * scaled_a @ scaled_b.T
    )
    return variance * np.exp(-0.5 * np.maximum(sq, 0.0))


@dataclass
class GaussianProcess:
    """Squared-exponential GP with a small ridge, fitted by moment matching on the lengthscales."""

    lengthscales: np.ndarray
    variance: float = 1.0
    noise: float = 1e-6
    mean: np.ndarray = field(default_factory=lambda: np.array([0.0]))
    X: np.ndarray | None = None
    y: np.ndarray | None = None
    _alpha: np.ndarray | None = None
    _L: np.ndarray | None = None

    @classmethod
    def fit(
        cls, X: np.ndarray, y: np.ndarray, *, noise: float = 1e-6, lengthscale: float | None = None
    ) -> GaussianProcess:
        X = np.atleast_2d(np.asarray(X, dtype=float))
        y = np.asarray(y, dtype=float).ravel()
        span = np.maximum(X.max(axis=0) - X.min(axis=0), 1e-6)
        ls = span / math.sqrt(X.shape[1]) if lengthscale is None else np.full(X.shape[1], float(lengthscale))
        variance = float(np.var(y)) or 1.0
        model = cls(lengthscales=ls, variance=variance, noise=noise, mean=np.array([float(np.mean(y))]))
        model.X, model.y = X, y
        K = _kernel_matrix(X, X, ls, variance) + np.eye(X.shape[0]) * noise
        model._L = np.linalg.cholesky(K + np.eye(X.shape[0]) * 1e-10)
        model._alpha = np.linalg.solve(model._L.T, np.linalg.solve(model._L, y - model.mean[0]))
        return model

    def mean_at(self, Xs: np.ndarray) -> np.ndarray:
        """Posterior mean only, with a shape mypy can track."""
        value = self.predict(Xs, return_std=False)
        assert isinstance(value, np.ndarray)
        return value

    def predict(self, Xs: np.ndarray, return_std: bool = False) -> Any:
        assert self.X is not None and self._L is not None and self._alpha is not None
        Xs = np.atleast_2d(np.asarray(Xs, dtype=float))
        Ks = _kernel_matrix(self.X, Xs, self.lengthscales, self.variance)
        mean = self.mean[0] + Ks.T @ self._alpha
        if not return_std:
            return mean
        v = np.linalg.solve(self._L, Ks)
        var = np.maximum(self.variance - np.sum(v**2, axis=0), 1e-12)
        return mean, np.sqrt(var)


def _acquisition_objective(model: GaussianProcess, best: float, xi: float) -> Callable[[np.ndarray], float]:
    """Bind the current model and incumbent explicitly (avoids late-binding on loop variables)."""

    def objective(z: np.ndarray) -> float:
        return -float(expected_improvement(np.atleast_2d(z), model, best, xi=xi)[0])

    return objective


def expected_improvement(Xs: np.ndarray, model: GaussianProcess, best: float, xi: float = 0.01) -> np.ndarray:
    """Acquisition for a *minimised* objective (we model the negated FM-plus-penalty)."""
    mu, sigma = model.predict(Xs, return_std=True)
    improvement = -(mu - best) - xi
    z = improvement / sigma
    return improvement * norm.cdf(z) + sigma * norm.pdf(z)


@dataclass
class BayesianResult:
    """Outcome of a Bayesian campaign."""

    best_z: np.ndarray
    best_value: float
    evaluations: int
    surrogate_samples: int
    history: list[dict[str, float]] = field(default_factory=list)
    note: str = ""


def bayesian_optimise(
    cost: Callable[[np.ndarray], float],
    bounds: tuple[np.ndarray, np.ndarray],
    x0: np.ndarray,
    *,
    max_evaluations: int = 120,
    initial_samples: int = 12,
    seed: int = 0,
    xi: float = 0.01,
) -> BayesianResult:
    """GP + expected improvement over the normalised box, seeded with the reference design."""
    lo, hi = np.asarray(bounds[0], dtype=float), np.asarray(bounds[1], dtype=float)
    dim = lo.size
    rng = np.random.default_rng(seed)
    X = [np.asarray(x0, dtype=float)]
    for _ in range(max(0, initial_samples - 1)):
        X.append(rng.uniform(lo, hi))
    X_arr = np.clip(np.array(X, dtype=float), lo, hi)
    y = np.array([float(cost(x)) for x in X_arr], dtype=float)
    history = [{"evaluation": i + 1, "objective": float(v)} for i, v in enumerate(y)]
    evaluations = int(y.size)

    while evaluations < max_evaluations:
        model = GaussianProcess.fit(X_arr, -y)
        best = float(np.max(-y))
        candidates = rng.uniform(lo, hi, size=(max(64, 8 * dim), dim))
        scores = expected_improvement(candidates, model, best, xi=xi)
        pick = candidates[int(np.argmax(scores))]
        refined = minimize(
            _acquisition_objective(model, best, xi),
            pick,
            bounds=list(zip(lo, hi, strict=True)),
            method="L-BFGS-B",
        )
        candidate = np.clip(np.asarray(refined.x if refined.success else pick, dtype=float), lo, hi)
        if np.any(np.all(np.isclose(X_arr, candidate, atol=1e-6), axis=1)):
            candidate = np.clip(rng.uniform(lo, hi), lo, hi)
        value = float(cost(candidate))
        evaluations += 1
        history.append({"evaluation": evaluations, "objective": value})
        X_arr = np.vstack([X_arr, candidate])
        y = np.append(y, value)

    index = int(np.argmin(y))
    return BayesianResult(
        best_z=X_arr[index],
        best_value=float(y[index]),
        evaluations=evaluations,
        surrogate_samples=int(X_arr.shape[0]),
        history=history,
        note="GP squared-exponential, expected improvement acquisition",
    )


def surrogate_prescreen(
    evaluated_z: list[np.ndarray] | np.ndarray,
    evaluated_values: np.ndarray | list[float],
    candidates: list[np.ndarray] | np.ndarray,
    *,
    keep: int = 5,
) -> list[np.ndarray]:
    """Rank unaudited candidates with a GP fitted on already-evaluated designs.

    Only the ``keep`` best predicted vectors are worth an expensive evaluation, which is the
    point of the prescreen: an XFOIL- or CFD-backed objective is asked about a handful of
    designs instead of hundreds.
    """
    X = np.atleast_2d(np.asarray(evaluated_z, dtype=float))
    y = np.asarray(evaluated_values, dtype=float).ravel()
    C = np.atleast_2d(np.asarray(candidates, dtype=float))
    if X.shape[0] < 4 or C.shape[0] == 0:
        return (
            [np.asarray(c, dtype=float) for c in C[:keep]]
            if keep
            else [np.asarray(c, dtype=float) for c in C]
        )
    model = GaussianProcess.fit(X, -y)
    predicted = model.mean_at(C)
    order = np.argsort(-predicted)[: max(1, int(keep))]
    return [C[int(i)] for i in order]


def campaign(
    cfg: ResolvedConfig, spec: DesignVectorSpec, *, max_evaluations: int = 120, seed: int = 0
) -> tuple[BayesianResult, Objective]:
    """Run a Bayesian campaign against the standard FM objective and return the best vector."""
    objective = Objective(cfg=cfg, spec=spec)
    result = bayesian_optimise(
        objective.value,
        (np.full(spec.dim, -1.0), np.full(spec.dim, 1.0)),
        initial_vector(spec, cfg),
        max_evaluations=max_evaluations,
        seed=seed,
    )
    return result, objective


__all__ = [
    "BayesianResult",
    "GaussianProcess",
    "bayesian_optimise",
    "campaign",
    "expected_improvement",
    "surrogate_prescreen",
]
