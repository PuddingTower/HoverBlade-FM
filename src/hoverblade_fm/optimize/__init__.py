"""Optimisation layer: design vector, FM objective with penalties, CMA-ES + SLSQP campaign."""

from hoverblade_fm.optimize.objective import Evaluation, Objective, momentum_feasibility_guard
from hoverblade_fm.optimize.pipeline import evaluate_initial, run_optimization
from hoverblade_fm.optimize.search import (
    SearchReport,
    cma_search,
    de_search,
    refine_local,
    refinement_indices,
)
from hoverblade_fm.optimize.selection import rank_key, repair_design, select_best, select_from_objective
from hoverblade_fm.optimize.surrogate import (
    BayesianResult,
    GaussianProcess,
    bayesian_optimise,
    expected_improvement,
    surrogate_prescreen,
)
from hoverblade_fm.optimize.surrogate import (
    campaign as bayesian_campaign,
)
from hoverblade_fm.optimize.vector import (
    SHAPE_CP_R,
    SHAPE_FIELDS,
    DesignVectorSpec,
    decode,
    encode,
    from_normalised,
    initial_vector,
    random_vector,
    to_normalised,
    vector_report,
)

__all__ = [
    "BayesianResult",
    "GaussianProcess",
    "SHAPE_CP_R",
    "SHAPE_FIELDS",
    "DesignVectorSpec",
    "Evaluation",
    "Objective",
    "SearchReport",
    "cma_search",
    "bayesian_campaign",
    "bayesian_optimise",
    "decode",
    "de_search",
    "encode",
    "evaluate_initial",
    "from_normalised",
    "initial_vector",
    "momentum_feasibility_guard",
    "rank_key",
    "random_vector",
    "expected_improvement",
    "refine_local",
    "refinement_indices",
    "repair_design",
    "run_optimization",
    "select_best",
    "select_from_objective",
    "to_normalised",
    "surrogate_prescreen",
    "vector_report",
]
