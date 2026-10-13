"""Conditional design generator prototype (T088, T089).

The plan lists a generative model as a non-blocking enhancement: conditions in (thrust, radius,
trailing-edge thickness, target FM, Reynolds and Mach bands) and ten legal sections out.  This is
a working prototype of that contract without a deep-learning dependency: designs are sampled from
the constrained decoder, scored with the reduced differentiable solver, and a latent basis (PCA
over the per-station shape fields) plus a ridge mapping from conditions to latent codes is fitted
on the high-performing tail of the sample.  Sampling therefore produces designs *conditioned* on
the requested operating point rather than random ones.

Legality is not learned, it is inherited: every generated vector is decoded through
:func:`hoverblade_fm.geometry.sections.plan_from_raw`, so the blunt-trailing-edge projection and
the planform rate limits apply to generated designs exactly as they do to optimiser candidates.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hoverblade_fm.optimize.vector import DesignVectorSpec

import numpy as np

from hoverblade_fm.aero.differentiable import reduced_blade_from_design, solve_finite_difference
from hoverblade_fm.config.schema import ResolvedConfig
from hoverblade_fm.geometry.cst import SectionParameters
from hoverblade_fm.geometry.sections import build_design
from hoverblade_fm.models import Design

#: Shape fields, derived from the section parameter dataclass so geometry never imports optimise.
SHAPE_FIELDS: tuple[str, ...] = tuple(
    name for name in SectionParameters.__dataclass_fields__ if name != "te_mid"
)


@dataclass
class Conditions:
    """What the generator is conditioned on (plan section 6.3, method D)."""

    thrust_n: float
    radius_m: float
    te_min_m: float
    target_fm: float = 0.6
    reynolds_mid: float = 5.0e5
    tip_mach: float = 0.6
    blade_count: int = 4

    def as_vector(self) -> np.ndarray:
        return np.array(
            [
                np.log10(max(self.thrust_n, 1e-3)),
                np.log10(max(self.radius_m, 1e-3)),
                np.log10(max(self.te_min_m, 1e-6)),
                self.target_fm,
                np.log10(max(self.reynolds_mid, 1.0)),
                self.tip_mach,
                float(self.blade_count),
            ],
            dtype=float,
        )


@dataclass
class LatentModel:
    """PCA shape basis plus a ridge map from operating conditions to latent codes."""

    mean: np.ndarray
    components: np.ndarray
    condition_mean: np.ndarray
    condition_scale: np.ndarray
    weights: np.ndarray
    n_components: int
    trained_on: int = 0
    elite_fraction: float = 0.25
    fm_range: tuple[float, float] = (0.0, 1.0)

    def encode(self, matrix: np.ndarray) -> np.ndarray:
        return (np.asarray(matrix, dtype=float) - self.mean) @ self.components

    def decode_codes(self, codes: np.ndarray) -> np.ndarray:
        return np.asarray(codes, dtype=float) @ self.components + self.mean

    def latent_for(self, conditions: Conditions) -> np.ndarray:
        z = (conditions.as_vector() - self.condition_mean) / self.condition_scale
        return self.weights.T @ z

    def to_dict(self) -> dict[str, Any]:
        return {
            "mean": self.mean.tolist(),
            "components": self.components.tolist(),
            "condition_mean": self.condition_mean.tolist(),
            "condition_scale": self.condition_scale.tolist(),
            "weights": self.weights.tolist(),
            "n_components": self.n_components,
            "trained_on": self.trained_on,
            "elite_fraction": self.elite_fraction,
            "fm_range": list(self.fm_range),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> LatentModel:
        return cls(
            mean=np.array(payload["mean"], dtype=float),
            components=np.array(payload["components"], dtype=float),
            condition_mean=np.array(payload["condition_mean"], dtype=float),
            condition_scale=np.array(payload["condition_scale"], dtype=float),
            weights=np.array(payload["weights"], dtype=float),
            n_components=int(payload["n_components"]),
            trained_on=int(payload.get("trained_on", 0)),
            elite_fraction=float(payload.get("elite_fraction", 0.25)),
            fm_range=tuple(payload.get("fm_range", (0.0, 1.0))),
        )

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return target


def shape_matrix(plan) -> np.ndarray:
    """Per-station shape parameters as a flat ``(stations * fields)`` vector."""
    return np.array([[getattr(p, name) for name in SHAPE_FIELDS] for p in plan.params], dtype=float).ravel()


def plan_from_matrix(
    spec: DesignVectorSpec, cfg: ResolvedConfig, matrix: np.ndarray, planform: np.ndarray
) -> Any:
    """Rebuild a plan from a shape matrix and a planform vector, through the constrained decoder."""
    from hoverblade_fm.geometry.cst import SectionParameters
    from hoverblade_fm.geometry.sections import plan_from_raw

    rows = matrix.reshape(len(cfg.r_stations), len(SHAPE_FIELDS))
    lo, hi = spec.bounds()
    params: list[SectionParameters] = []
    for row in rows:
        base = SectionParameters()
        values = {name: float(row[index]) for index, name in enumerate(SHAPE_FIELDS)}
        params.append(
            SectionParameters(
                t_over_c=float(
                    np.clip(
                        values["t_over_c"],
                        cfg.airfoil.thickness_to_chord_min,
                        cfg.airfoil.thickness_to_chord_max,
                    )
                ),
                x_t=float(np.clip(values["x_t"], 0.20, 0.45)),
                camber=float(np.clip(values["camber"], 0.0, cfg.airfoil.camber_max_over_chord)),
                x_camber=float(np.clip(values["x_camber"], 0.28, 0.60)),
                te_gap=float(max(values["te_gap"], cfg.te_min_m / max(cfg.chord_max_m, 1e-9))),
                te_mid=base.te_mid,
                le_factor=float(np.clip(values["le_factor"], 0.85, 1.45)),
                aft_upper=float(np.clip(values["aft_upper"], -0.02, 0.02)),
                aft_lower=float(np.clip(values["aft_lower"], -0.02, 0.02)),
            )
        )
    n_chord, n_twist = spec.n_chord_cp, spec.n_twist_cp
    return plan_from_raw(
        cfg,
        chord_cp=np.asarray(planform[:n_chord], dtype=float),
        twist_cp=np.asarray(planform[n_chord : n_chord + n_twist], dtype=float),
        section_params=params,
        chord_cp_r=spec.chord_cp_r,
        twist_cp_r=spec.twist_cp_r,
    )


def dataset(
    cfg: ResolvedConfig,
    spec: DesignVectorSpec,
    *,
    samples: int = 160,
    seed: int = 0,
    jitter: float = 0.45,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Sample the constrained decoder and score every design with the reduced solver.

    Returns ``(shape_matrices, planforms, figures_of_merit)``.  Every row is legal by
    construction, which is what makes the latent basis usable for generation.
    """
    from hoverblade_fm.optimize.vector import decode, from_normalised, initial_vector

    rng = np.random.default_rng(seed)
    matrices: list[np.ndarray] = []
    planforms: list[np.ndarray] = []
    scores: list[float] = []

    z0 = initial_vector(spec, cfg)
    for _ in range(samples):
        z = np.clip(z0 + rng.normal(0.0, jitter, size=spec.dim), -1.0, 1.0)
        plan = decode(spec, z, cfg)
        design = build_design(plan, cfg)
        try:
            fm = float(
                solve_finite_difference(reduced_blade_from_design(design, cfg), iterations=25, trim_steps=10)[
                    "figure_of_merit"
                ]
            )
        except Exception:
            continue
        if not np.isfinite(fm) or fm <= 0.0:
            continue
        matrices.append(shape_matrix(plan))
        n_chord, n_twist = spec.n_chord_cp, spec.n_twist_cp
        physical = from_normalised(spec, z)
        planforms.append(physical[: n_chord + n_twist])
        scores.append(fm)
    if not matrices:
        raise RuntimeError("no legal designs were sampled; widen the jitter or check the configuration")
    return np.array(matrices, dtype=float), np.array(planforms, dtype=float), np.array(scores, dtype=float)


def train(
    cfg: ResolvedConfig,
    spec: DesignVectorSpec,
    *,
    samples: int = 160,
    n_components: int = 6,
    seed: int = 0,
    elite_fraction: float = 0.25,
    ridge: float = 1.0,
) -> tuple[LatentModel, dict[str, Any]]:
    """Fit the latent basis on the best-scoring designs and map conditions onto latent codes."""
    matrices, planforms, scores = dataset(cfg, spec, samples=samples, seed=seed)
    n_components = int(min(n_components, matrices.shape[1], matrices.shape[0] - 1))
    keep = max(4, int(round(elite_fraction * matrices.shape[0])))
    order = np.argsort(-scores)[:keep]
    elite = matrices[order]
    centered = elite - elite.mean(axis=0)
    _, singular, rotation = np.linalg.svd(centered, full_matrices=False)
    components = rotation[:n_components]
    mean = matrices.mean(axis=0)
    reynolds_mid = float(cfg.rho * cfg.tip_speed * float(np.mean(planforms.mean(axis=0)[:1])) / cfg.mu)
    conditions = np.array(
        [
            Conditions(
                thrust_n=cfg.thrust_n,
                radius_m=cfg.radius_m,
                te_min_m=cfg.te_min_m,
                target_fm=float(score),
                reynolds_mid=reynolds_mid,
                tip_mach=cfg.tip_mach,
                blade_count=cfg.blade_count,
            ).as_vector()
            for score in scores[order]
        ],
        dtype=float,
    )
    latent = (elite - mean) @ components.T
    c_mean = conditions.mean(axis=0)
    c_scale = np.maximum(conditions.std(axis=0), 1e-6)
    zc = (conditions - c_mean) / c_scale
    augmented = np.column_stack((zc, np.ones(zc.shape[0])))
    solved = np.linalg.solve(
        augmented.T @ augmented + ridge * np.eye(augmented.shape[1]), augmented.T @ latent
    )
    model = LatentModel(
        mean=mean,
        components=components,
        condition_mean=c_mean,
        condition_scale=c_scale,
        weights=solved[:-1],
        n_components=n_components,
        trained_on=int(matrices.shape[0]),
        elite_fraction=elite_fraction,
        fm_range=(float(scores.min()), float(scores.max())),
    )
    info = {
        "samples": int(matrices.shape[0]),
        "elite": keep,
        "explained_variance": (singular[:n_components] ** 2 / np.sum(singular**2)).tolist(),
        "fm_min": float(scores.min()),
        "fm_max": float(scores.max()),
        "fm_median": float(np.median(scores)),
        "seed": seed,
    }
    return model, info


def sample(
    model: LatentModel,
    spec: DesignVectorSpec,
    cfg: ResolvedConfig,
    conditions: Conditions,
    *,
    count: int = 4,
    spread: float = 0.35,
    seed: int = 0,
) -> list[Design]:
    """Generate legal, conditioned candidate blades; each is scored and sorted by FM."""
    from hoverblade_fm.optimize.vector import initial_vector

    rng = np.random.default_rng(seed)
    centre_code = model.latent_for(conditions)
    scored: list[tuple[float, Design]] = []
    for _ in range(count):
        codes = centre_code + rng.normal(0.0, spread, size=model.n_components)
        matrix = model.decode_codes(codes)
        # the planform is inherited from the reference design: the generator's job is the section family
        from hoverblade_fm.optimize.vector import from_normalised

        physical = from_normalised(spec, initial_vector(spec, cfg))
        planform = physical[: spec.n_chord_cp + spec.n_twist_cp]
        plan = plan_from_matrix(spec, cfg, matrix, planform)
        design = build_design(plan, cfg, label="generated")
        result = solve_finite_difference(reduced_blade_from_design(design, cfg), iterations=25, trim_steps=10)
        scored.append((float(result["figure_of_merit"]), design))
    scored.sort(key=lambda item: -item[0])
    return [design for _fm, design in scored]


def legality_report(designs: list[Design], cfg: ResolvedConfig) -> dict[str, Any]:
    """Every generated design must satisfy the hard constraint - this proves it does."""
    return {
        "count": len(designs),
        "all_blunt": all(design.is_blunt() for design in designs),
        "all_ten_sections": all(len(design.sections) == 10 for design in designs),
        "min_te_mm": round(1000.0 * min(design.min_te_thickness() for design in designs), 4)
        if designs
        else None,
        "required_te_mm": round(1000.0 * cfg.te_min_m, 4),
        "worst_margin_mm": round(1000.0 * min(design.te_margin() for design in designs), 6)
        if designs
        else None,
    }


__all__ = [
    "Conditions",
    "LatentModel",
    "dataset",
    "latency_note",
    "legality_report",
    "plan_from_matrix",
    "sample",
    "shape_matrix",
    "train",
]

latency_note = (
    "Prototype status: a linear latent basis conditioned on the operating point, deliberately not a "
    "VAE or diffusion model. See docs/generative_airfoil_feasibility.md (T088) for what a full "
    "generative model would need."
)
