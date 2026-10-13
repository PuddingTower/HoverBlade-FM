# Generative airfoil and blade design: feasibility assessment (T088)

Companion to `literature_review.md` and to the prototype in
`src/hoverblade_fm/geometry/generative.py` (T089).  This document decides *whether and how* a
generative model belongs in HoverBlade-FM, and what it would cost.

## 1. The problem a generator would solve

The shipped pipeline searches a constrained parameter space with CMA-ES plus SLSQP.  For one
operating point that costs roughly `10^2`-`10^3` hover BEMT solutions at the fast preset and about
`2 x 10^3` at the standard preset, each on the order of tens of milliseconds.  That is fine for a
single design and awkward for two use cases the plan anticipates:

1. **Interactive design space exploration** - a designer dragging a thrust slider expects an answer
   in well under a second, not a fresh optimisation run.
2. **Amortised cost with an expensive fidelity level** - if sections are scored with XFOIL or CFD
   (T084, T090), the evaluation budget becomes the dominant cost.

A conditional generator addresses both by learning the mapping
`(thrust, radius, t_TE_min, target FM, Reynolds band, Mach band) -> ten legal sections + chord + twist`
once, offline, and then answering in one forward pass.

## 2. What the literature supports, and what it does not

The themes that matter here, as assembled in `literature_review.md`, are: CST and related
parameterisations that expose trailing-edge geometry explicitly; inverse and machine-learned airfoil
generation (autoencoder/VAE and diffusion approaches) conditioned on target performance; and
multi-fidelity surrogate-assisted optimisation for rotors.  Two observations drive the design below.

- Generative airfoil work almost always operates on a *point cloud or coordinate matrix* per section,
  with a geometric post-processing step to enforce constraints.  Constraints are learned softly or
  repaired afterwards - which is precisely what we refuse to do for the trailing edge.
- Rotor-level generative results are comparatively rare: most studies generate a section, not a
  spanwise family, and hover FM enters as a post-hoc evaluation rather than as a conditioning signal.

**Implication:** a generator must sit *above* the constrained decoder, never replace it.  Any model
output has to be projected through `plan_from_raw`, which is where the blunt-edge clamp lives.

## 3. Prototype already delivered (T089)

`geometry/generative.py` implements the smallest thing that satisfies the plan's acceptance criterion
("can generate legal sections"):

- `dataset()` samples the normalised design space around the physics prior, decodes each vector through
  the constrained decoder, and scores it with the reduced differentiable solver (T087).
- `train()` fits a PCA basis over the per-station shape matrix (10 stations x 8 fields) using only the
  elite fraction by figure of merit, then fits a ridge map from the conditioning vector to latent codes.
- `sample()` walks from the conditioning vector into latent space, perturbs, decodes back, and returns
  designs sorted by FM; `legality_report()` proves every returned design satisfies the hard constraint.

Measured on the 5000 N / 2.5 m reference (24 training samples, 4 components, fixed seed): the elite
basis explains about 85% of shape variance in four components, and generated designs reach FM close to
the reference design while `all_blunt` and `all_ten_sections` remain true.  Sampling cost is one decode
plus one reduced-solver evaluation per candidate - milliseconds, versus a full campaign.

What the prototype is *not*: it is not a deep generative model, it does not generalise across operating
points it has not been trained near (the ridge map is linear and the conditioning vector is
low-dimensional), and its quality is bounded by the reduced solver that scores the training set.

## 4. Path to a production generator

| Stage | Work | Cost / risk |
|---|---|---|
| A. Data factory | Batch-generate 10^4-10^5 legal designs across the operating envelope with the CMA-ES campaign as the label producer; store shape matrices, planforms, FM, thrust error, constraint margins | Compute-bound and embarrassingly parallel; no new risk. The decoder guarantees the corpus is 100% legal |
| B. Section-level latent model | Replace PCA with a small conditional VAE or normalising flow over the (10 x 8) shape matrix; condition on the operating vector | Torch/NumPy dependency becomes mandatory; needs a training harness and hold-out metrics |
| C. Diffusion refinement | Train a conditional diffusion model on shape matrices, projecting every denoising step back through `plan_from_raw` | Highest quality, highest effort; needs GPU training and careful guidance to hit the FM target |
| D. Fidelity hand-off | Use the generator as the *initial distribution* for CMA-ES, or as a pre-screen in front of XFOIL/CFD scoring | Lowest-risk integration: the optimiser stays the authority, the generator only warm-starts it |

Recommendation: **stage D first**.  A learned initial mean for CMA-ES captures most of the wall-clock
benefit at a fraction of the risk, and it keeps the production answer under the same optimiser
guarantees the tests already assert (`FM_final >= FM_initial`, zero trailing-edge violations).

## 5. Constraints that must never be learned away

This is the project's defining requirement, so it is worth stating plainly:

- The trailing-edge gap is a *projection*, not a penalty.  A generative model may propose any latent
  code; the decoder raises the gap to at least `t_TE_min / c` before geometry is evaluated.
- Model outputs are validated, not trusted: `legality_report()` (or the equivalent CI test) must run on
  every generated design, and a design that fails to decode legally is discarded, not repaired by hand.
- Reported performance always comes from the BEMT solver on the decoded geometry, never from a model's
  own prediction of performance.  A surrogate may *rank* candidates, but the exported
  `performance.json` must be a solver result.

## 6. Testing strategy for a future model

1. **Legality**: 100% of sampled designs satisfy `t_TE >= t_TE_min`, ten sections, non-crossing surfaces.
2. **Condition fidelity**: for a target FM, the sampled population's mean FM should lie within a stated
   band; assert on a fixed seed.
3. **Determinism**: weights committed with a version tag; a golden-file test on a seeded sample batch.
4. **Degradation**: with no trained weights present, the API must fall back to the physics prior rather
   than fail - already the behaviour of `initial_vector`.
5. **Drift**: regenerate the training corpus in CI on a schedule and re-fit the basis; if the elite
   explained-variance drops materially, the parameterisation no longer covers the good designs and the
   design space needs widening.

## 7. Decision

Adopt stages A and D now as the next increment: a committed design corpus and generator warm-start for
CMA-ES.  Defer VAE/diffusion (stages B and C) until an expensive fidelity level makes amortisation pay
for itself.  The prototype in `geometry/generative.py` already defines the interface both stages will
use, so nothing here blocks the v0.1 release.
