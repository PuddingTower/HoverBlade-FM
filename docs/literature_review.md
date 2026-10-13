# Literature review (文献综述)

**How this document was produced.**  Automated retrieval (`scripts/literature_search.py`, task T008)
against the arXiv Atom API and the Crossref REST API, using the English query list of 计划.md 第 7.2 节
plus four additional queries covering CMA-ES rotor design, low-Reynolds airfoil optimisation,
trailing-edge thickness drag and geometric validity.  The retrieval window is 2022-2025 (the plan's
priority band); foundational pre-2022 work is cited where it defines a technique.  The run reported
here collected **108 unique records** (30 curated seed entries, 60 Crossref, 18 arXiv — 90 with a DOI, 25
with an arXiv identifier, 96 inside the 2022-2025 window), stored in
[`literature_search/raw.jsonl`](literature_search/raw.jsonl) and scored into the screening columns of
计划.md 第 7.4 节 in [`literature_search/summary.csv`](literature_search/summary.csv).  Every reference
cited below appears in those files with a resolvable DOI or arXiv identifier, or was verified against
the publisher record; nothing here is quoted from memory.

---

## 中文摘要 (Chinese summary)

本项目要在"只有三个输入(拉力、半径、最小尾缘厚度)"的条件下，端到端生成仅用于**悬停**的旋翼叶片，
输出 10 个剖面并最大化**悬停品质因数 FM(品质因数)**，同时把**钝尾缘(钝尾缘)**作为不可违反的硬约束。
围绕这个目标检索 2022—2025 年文献，可归纳为五条主线：

1. **悬停气动建模与 FM 优化**：低阶动量/叶素动量(BEM/BEMT)方法仍在快速演进。Ruh & Hwang(2023)给出
   在概念设计阶段快速、稳健求最优旋翼的方法；Sheen 等(2023)用叶尖/根部损失因子表达三维效应；Tai 等(2025)
   消除了低来流速度下的数值奇异性；Liew 等(2024)给出跨工况的统一动量模型并提供开源实现；
   Koning 等(2024, ELISA)与 Qin & Yang(2025)分别在火星低雷诺数与同轴旋翼场景以 FM 为目标做优化。
   **结论**：BEMT + 解析极曲线作为主评估模型是被社区反复验证的选择，但必须处理收敛性与阻力惩罚。
2. **几何参数化与尾缘**：CST(型函数/类别函数)仍是优化首选参数化(Kulfan 2010)，但其类函数默认使尾缘
   闭合，需要显式引入尾缘间隙；近期工作把"几何合法性"当作一等公民——Chen 等(2020)用 Bézier-GAN 保证
   解码即合法，Yi & Deng(2023)用薄翼理论+深度学习压缩参数维度，Kim/Seth/Liem(2025)用几何规划显式处理
   几何约束，Chen 等(2025)用扩散模型学出的流形作为等式约束。**结论**：硬约束应实现在解码阶段而非仅作惩罚。
3. **钝尾缘气动代价**：Standish & van Dam(2003)确立钝尾缘"升力改善+压差阻力显著上升+结构更好"的折中；
   Lee 等(2025)在低雷诺数非定常条件下证明圆/钝尾缘会改变附着点与载荷；Pei 等(2023)、Ahmed & Shanto(2024)
   进一步量化尾缘厚度对厚剖面的影响。**结论**：FM 目标里必须计入尾缘厚度阻力修正，否则优化器会滥用钝尾缘。
4. **生成式翼型设计**：CVAE/WGAN/扩散模型可给出"条件生成"的起始外形(Wang 2025；Graves & Farimani 2024；
   AeroDiT 2024；Healy 2024 DeepAirfoil)，但约束满足是统计意义上的。**结论**：生成模型作为初始化/加速手段
   (T088-T089)合理，作为主链路风险高。
5. **多保真、代理与可微分**：Erhard & Alonso(2024)证明多保真贝叶斯可在少量高保真样本内收敛；
   Cornelius 等(2025)系统评估了悬停旋翼多保真建模收益；Ho & Farhat(2023)、Feng 等(2024)、Chen 等(2025)
   给出伴随/可微分路径。**结论**：本项目 MVP 用 CMA-ES + SLSQP，可微分与多保真放到 v0.2/v0.3。

**最终技术路线判断**：约束化 CST(显式尾缘间隙+硬钳制) → 悬停 BEMT(Prandtl 叶尖/根部损失) + 解析极曲线
(雷诺数/马赫数/钝尾缘阻力修正) → 罚函数目标 `-FM + penalties` → CMA-ES 全局 + SLSQP 局部细化 →
CAD 放样与制造导出；生成式模型与可微分求解器作为可选升级。该路线与计划文档第 7.5 节结论一致，
并在上表每一类文献中都能找到直接支撑。

---

## 1. Hover performance modelling and its failure modes

The rotor-side literature splits into *model fidelity* work and *optimisation* work, and the model work
is where the useful engineering detail is.

Ruh and Hwang (2023, [10.2514/1.J062611](https://doi.org/10.2514/1.J062611)) address precisely the
conceptual-design situation HoverBlade-FM is in: the blade geometry is not yet known, and classical BEM
iteration loops fail to converge or converge slowly when the actuation approaches the momentum-theory
limits.  Their "fast and robust" formulation is evidence that the hover solver needs an explicit
convergence strategy, not just a loop — which is why this project exposes relaxation, iteration caps and
an inner thrust-matching tolerance in `SolverSettings`.

Sheen et al. (2023, [10.2514/1.C036528](https://doi.org/10.2514/1.C036528)) build a hover BEMT model whose
three-dimensionality is carried entirely by tip/hub loss factors plus a skew-downwash correction, and
validate it against higher-fidelity results.  This is the same architecture HoverBlade-FM uses (Prandtl
tip loss, a root correction, and the analytic polar), and it is the literature basis for treating loss
factors as adequate at the hover design stage.

Tai et al. (2025, [10.3390/aerospace12080728](https://doi.org/10.3390/aerospace12080728)) identify the specific
numerical pathology that a hover-only generator must survive: the standard BEMT induction equation is
singular as inflow velocity goes to zero — which is the *normal* operating point of a hovering rotor, not
an edge case.  Their velocity-factor reformulation removes it and extends the method to non-axial inflow
and descent.  The same failure mode shows up in the plan's own smoke criteria (thrust matching must be
computable, 第 21.5 节), so the lesson is adopted as a solver requirement rather than a nice-to-have.

Liew, Heck and Howland (2024, [10.1038/s41467-024-50756-5](https://doi.org/10.1038/s41467-024-50756-5)) go
further: they derive a momentum model valid across operating regimes rather than patching classical
momentum theory with fitted corrections, and publish an open-source Python implementation with a
blade-element version.  Two things are taken from this.  First, a *reference implementation to compare
against* — useful for golden tests.  Second, an honest statement of the limitation of our own approach:
empirical corrections are a stopgap, and a v0.2 upgrade could adopt a principled momentum model instead
of adding more fudge factors.

Fritz, Ferreira and Boorsma (2022, [10.1002/we.2778](https://doi.org/10.1002/we.2778)) extend BEM to swept
blades by correcting the axial induction for the trailed-vortex displacement and the curved bound vortex.
HoverBlade-FM has no sweep as a design variable in 0.1.0, so the paper is not adopted — but it is the
template for *how* to bolt a correction onto an existing BEM loop while keeping it unit-testable, and it
becomes directly relevant if tip sweep enters a later version.

On the optimisation side, Koning et al. (2024, [10.4050/jahs.69.042005](https://doi.org/10.4050/jahs.69.042005))
describe ELISA, built for rotor *hover* optimisation at very low Reynolds number, whose first module is
deliberately an "unconventional airfoil parameterization" plus multi-objective search.  It is the closest
published analogue of this project's shape of problem: hover, low Re, geometry constrained, few
evaluations affordable.  Wenren et al. (2022, [10.21079/11681/46261](https://doi.org/10.21079/11681/46261))
couple a parametric design tool and a multi-objective genetic algorithm to rotor planform optimisation with
a comprehensive analysis backend — the same "parametric planform + evolutionary search + moderate-fidelity
analysis" triad, with the reminder that planform (chord/twist distribution) and section shape are best
optimised together, as they are here.  Wang et al. (2023, [arXiv:2302.08728](https://arxiv.org/abs/2302.08728))
optimise a two-blade rotor **with respect to the figure of merit** in two stages — global evolutionary
search over shape parameters, then a topological analysis — which is the published precedent for our
CMA-ES-then-SLSQP sequence (第 11.3 节).  Qin and Yang (2025, [10.1080/19942060.2025.2528120](https://doi.org/10.1080/19942060.2025.2528120))
maximise hover FM of a coaxial system at fixed thrust coefficient through a deep-learning multi-fidelity
surrogate: fixed-thrust FM maximisation is exactly our objective shape, and their surrogate route is the
one we defer to the "optional surrogate" task T085.  A 2025 BEMT-based hover study of coaxial rotors
([10.2139/ssrn.5135051](https://doi.org/10.2139/ssrn.5135051), preprint) and a "high-fidelity rotor design
based on blade element momentum theory" study (2025, [10.2514/6.2025-1635](https://doi.org/10.2514/6.2025-1635))
confirm that BEM-centred rotor design remains an active, accepted engineering path rather than a legacy
shortcut.

## 2. Airfoil parameterization and hard geometric constraints

CST remains the reference parameterization for shape optimisation: Kulfan (2010,
[10.1017/S0001924000003614](https://doi.org/10.1017/S0001924000003614)) defines the class function
`C(x) = x^N1 (1-x)^N2` multiplying a Bernstein form function, and shows how the class exponents encode the
leading-edge and trailing-edge behaviour of the family.  The direct consequence for this project is the
one in the plan's §9.2: a plain CST class function makes `y_u(1) = y_l(1) = 0`, so a blunt edge must be
added as an explicit term (`+ x·y_te_upper / + x·y_te_lower`) rather than hoped out of the shape
coefficients.  The order of the shape function is a real design decision: Kulfan's motivation is limiting
the *number* of geometric variables, and our defaults (order 5 per surface, bounded `t/c`, bounded camber,
bounded `t_TE/c`) follow that logic.

The "valid by construction" idea is well established.  Chen, Chiu and Fuge (2020,
[10.2514/1.J059317](https://doi.org/10.2514/1.J059317)) optimise in a Bézier-GAN latent space in which every
decoded shape is a valid airfoil — the same philosophy we apply at a much simpler level: decode, then
project onto the feasible set, so the search cannot emit an illegal geometry.  Yi and Deng (2023,
[10.3390/aerospace10070650](https://doi.org/10.3390/aerospace10070650)) build a compact parameterization on
top of thin-airfoil theory plus deep learning to shrink the design space, reinforcing that *dimensionality
control* matters more than expressive power for a ten-station hover blade.  Kim, Seth and Liem (2025,
[10.2514/6.2025-0653](https://doi.org/10.2514/6.2025-0653)) handle airfoil shape optimisation with geometric
constraints through geometric programming, i.e. constraints treated as tractable optimisation structure
rather than penalties — the strongest published argument in this cluster for our choice of ADR-002, even
though we implement the constraint by construction instead of by conic reformulation.  And the CST
parameterization is still being extended in 2025 — a CST-plus-LLM variant appears as
[10.2139/ssrn.5125589](https://doi.org/10.2139/ssrn.5125589) (preprint) — which is a useful signal that CST
is not a dead end for a 2026 tool.

## 3. Blunt trailing edges: real aerodynamics, real cost

Standish and van Dam (2003, [10.1115/1.1629103](https://doi.org/10.1115/1.1629103)) is the canonical study
of cut-off (blunt) trailing edges for thick blade sections: blunt edges raise maximum lift and
lift-curve slope by easing the aft pressure recovery, and they improve structural and fabrication
behaviour, at the price of a clear profile-drag increase from the finite base.  This is precisely the
trade-off HoverBlade-FM must not hide: if the polar charged nothing for `t_TE`, the optimiser would happily
sit at the clamp at every station and report an inflated FM (计划.md 第 22.1 节 lists "钝尾缘导致阻力过高 /
FM 下降" as a risk and prescribes a trailing-edge drag correction).  T043 therefore implements a blunt-edge
drag term in the analytic polar.

Newer work confirms that a finite edge has to be modelled rather than approximated.  Lee, Ramesh and
Gopalarathnam (2025, [10.1007/s00162-025-00747-z](https://doi.org/10.1007/s00162-025-00747-z)) show that at
low Reynolds number and high reduced frequency the flow curves around a rounded trailing edge and the
stagnation point leaves the classical Kutta point, so unsteady loading departs from sharp-edge theory —
directly relevant to a rotor section, which sees unsteady loading in reality even in "steady" hover.  Pei
et al. (2023, [10.3390/wind3040025](https://doi.org/10.3390/wind3040025)) study the influence of blunt-edge
thickness on a very thick (60 %-chord) section with RANS against experiment, and Ahmed and Shanto (2024,
[arXiv:2409.13922](https://arxiv.org/abs/2409.13922)) quantify trailing-edge thickness effects on a NACA 4412
at low Reynolds number.  Blunt-edge sections also remain an active engineering topic in turbine and
vertical-axis machinery (2025, [10.47176/jafm.18.4.3019](https://doi.org/10.47176/jafm.18.4.3019);
[10.3901/jme.2023.06.123](https://doi.org/10.3901/jme.2023.06.123)), and the low-Re rounded-edge work has a
direct bearing on the included-angle bounds we impose (`te_included_angle_min_deg = 6°`, 第 5.4 节).

**What this means for the design rule.**  A blunt edge is *not* a small penalty you can tune away: it
changes the lift curve, the drag, and the validity of the Kutta assumption itself.  That is the literature
basis for treating `trailing_edge_thickness_m` as an input to the physics (the polar correction) *and* as a
hard geometric floor — not merely as a drawing tolerance.

## 4. Generative and machine-learning airfoil design

Conditional generative models are now the standard "warm-start" story for inverse design.  Chen et al.
(2022, [10.1115/1.4052846](https://doi.org/10.1115/1.4052846)) use conditional generative models with
surrogate log-likelihoods to place an optimiser near the optimum while embracing the one-to-many ambiguity
of inverse design.  Wang et al. (2025, [10.1016/j.engappai.2024.109461](https://doi.org/10.1016/j.engappai.2024.109461))
condition a VAE on target aerodynamic performance to generate airfoils.  Graves and Farimani (2024,
[arXiv:2408.15898](https://arxiv.org/abs/2408.15898)) replace the parameterization entirely with a denoising
diffusion model that samples conditioned airfoils, and Wang et al. (2024,
[arXiv:2412.17394](https://arxiv.org/abs/2412.17394), AeroDiT) apply a latent diffusion transformer to
predict RANS flow fields around airfoils in real time — a candidate medium-fidelity evaluator.  DeepAirfoil
(Healy et al. 2024, [10.2514/6.2024-4140](https://doi.org/10.2514/6.2024-4140)) is the closest thing to an
open, multi-fidelity airfoil design-and-analysis toolchain in current literature, and Li et al. (2022,
[10.1016/j.ast.2021.107309](https://doi.org/10.1016/j.ast.2021.107309)) show the low-Reynolds,
data-driven-mode approach that best matches small hover rotors.

The shared limitation, stated openly in this literature, is that generative output is *statistically*
feasible rather than guaranteed feasible: sampling a manifold does not enforce `t_TE >= t_min` or
"no self-intersection".  Chen et al. (2025, [arXiv:2507.23443](https://arxiv.org/abs/2507.23443)) is the
most instructive recent attempt to close that gap: a diffusion model learns a manifold of aerodynamically
viable shapes which is then enforced as an **equality constraint** inside adjoint-based shape optimisation.
That is exactly the composition this project wants — generative model as prior, hard constraint as the
projection step — and it is why the generative track (T088-T089) is planned as an *initialiser* feeding the
same clamped decoder, never as a replacement for it.

## 5. Multi-fidelity, Bayesian and differentiable optimisation

Erhard and Alonso (2024, [10.2514/6.2024-2506](https://doi.org/10.2514/6.2024-2506)) show multi-fidelity
Bayesian optimisation of a coaxial rotor in hover and edgewise flight, using BEM-level and higher-fidelity
analyses inside one acquisition loop; Cornelius et al. (2025, [10.3390/aerospace12080650](https://doi.org/10.3390/aerospace12080650))
quantify what multi-fidelity modelling buys for isolated hovering rotors when design and operating
variables are perturbed.  Both support the plan's three-rung ladder (第 6.3 节 C) — and both also explain
why it is out of scope for 0.1.0: the benefit accrues when the expensive rung is genuinely expensive,
whereas HoverBlade-FM's main evaluation is sub-millisecond and can simply be run thousands of times.

On gradients, Ho and Farhat (2023, [10.1016/j.jcp.2023.112191](https://doi.org/10.1016/j.jcp.2023.112191))
perform aerodynamic optimisation with large shape and topology changes through a *differentiable* embedded
boundary method, Feng et al. (2024, [arXiv:2407.18963](https://arxiv.org/abs/2407.18963)) derive adjoint
gradients for high-order discontinuous-Galerkin solvers, and the diffusion-manifold work above computes
adjoint gradients of drag/lift objectives.  These establish that differentiable aerodynamics is practical,
but at CFD-level cost and complexity; the plan's JAX-differentiable BEMT prototype (T087, 第 11.4 节) is the
cheap analogue — same idea, low-order model.  For 0.1.0 the derivative-free path stays, with SciPy's SLSQP
(Virtanen et al. 2020, [10.1038/s41592-019-0686-2](https://doi.org/10.1038/s41592-019-0686-2)) for the local
constrained refinement and the CMA-ES literature (Hansen 2016, [arXiv:1604.00772](https://arxiv.org/abs/1604.00772))
for population sizing, step-size and restart choices that our presets encode.

## 6. Open-source tooling

Three open tools shape expectations about packaging.  XFOIL (Drela 1989,
[10.1007/978-3-642-84010-4_1](https://doi.org/10.1007/978-3-642-84010-4_1)) defines what a low-Re polar
interface must expose and why a hover tool at 10⁵–10⁶ Reynolds needs more than thin-airfoil theory; the plan
keeps it as an optional Level-3 backend (T084).  QBlade, whose hydrodynamic extension was verified in 2023
([10.5194/wes-2023-117](https://doi.org/10.5194/wes-2023-117)), is the closest existing open tool to an
end-to-end blade design workflow (airfoil design → polars → BEM rotor → structure), and its existence is
an argument for HoverBlade-FM's specific gap: hover-only, three-input, FM-maximising, constraint-first.
Liew et al. (2024) and the open-source multi-rotor reference-rotor work (2025,
[10.5194/wes-2025-274](https://doi.org/10.5194/wes-2025-274)) show that publishing the code alongside the
model is now the norm in rotor aerodynamics — which this repository matches by shipping examples, outputs
and CI rather than a paper-only description.

## 7. Decision table

| Line of work | Representative source | What HoverBlade-FM **borrows** | What it **rejects**, and why |
|---|---|---|---|
| Hover BEMT with loss factors | Ruh & Hwang 2023; Sheen et al. 2023 | BEM + Prandtl tip/root losses as the main evaluator; explicit convergence strategy | free-vortex / CFD as the *loop* evaluator: cost per evaluation would end the smoke test and the agent loop |
| BEM numerical robustness | Tai et al. 2025 | treat zero/low inflow as the normal case, guard the induction iteration | their non-axial / descent extensions: out of scope for hover-only (第 2.2 节) |
| Principled momentum modelling | Liew et al. 2024 | open reference implementation for validation; the argument that empirical corrections are provisional | adopting a new momentum closure in 0.1.0: unnecessary churn, deferred to v0.2 |
| FM as the objective, global-then-local search | Wang et al. 2023; Qin & Yang 2025; Koning et al. 2024 | `max FM` at fixed thrust via a thrust-matching penalty; CMA-ES → local refinement ordering | deep surrogate of the flow field as the main objective function: unverifiable at 0.1.0 fidelity |
| Planform + section simultaneous optimisation | Wenren et al. 2022 | chord, twist and section shape in one design vector (第 4.2 节) | aeroelastic/RCAS coupling: no structural model in scope |
| CST parameterization | Kulfan 2010 | class/shape split, low coefficient count, bounded `t/c`, camber, TE ratio | textbook `N2 = 1` closure as the final word on the edge (it would forbid a blunt TE) |
| Valid-by-construction geometry | Chen, Chiu & Fuge 2020; Kim, Seth & Liem 2025 | constraints enforced in the decode/projection step, not only as penalties | latent-space and geometric-programming machinery: heavier than a ten-station hover blade needs |
| Blunt-edge aerodynamics | Standish & van Dam 2003; Lee et al. 2025; Pei et al. 2023 | a real drag term charged for `t_TE`; per-station `t_TE/c` and included-angle bounds; explicit Kutta-condition caveat | assuming sharp-edge theory holds at a blunt edge (would inflate FM) |
| Generative design | Chen et al. 2022; Wang et al. 2025; Graves & Farimani 2024; Chen et al. 2025 | a future *initialiser* that still passes through the clamped decoder (T089) | generative sampling as the primary geometry source: constraint satisfaction would become statistical |
| Multi-fidelity Bayesian optimisation | Erhard & Alonso 2024; Cornelius et al. 2025 | the fidelity *interface* (analytic polar → BEMT → CFD) and the evaluation-budget discipline | a GP/BoTorch dependency in the core: the cheap model allows brute sampling now (T085/T086 stay optional) |
| Differentiable / adjoint ASO | Ho & Farhat 2023; Feng et al. 2024; ADR-001 | the v0.2 gradient path (`geometry → aero → FM` in JAX, T087) | a JAX requirement in 0.1.0: it would break the "install with uv, run anywhere" guarantee |
| Open tooling precedent | XFOIL (Drela 1989); QBlade; DeepAirfoil (Healy et al. 2024) | optional backends behind stable interfaces; committed examples and reproducible outputs | a GUI, a closed solver dependency, or a service-oriented architecture |

**Bottom line.**  The retrieved literature supports the plan's chosen mainline (第 7.5 节) rather than
contradicting it: constrained CST for geometry, hover BEMT with losses and a corrected analytic polar for
evaluation, derivative-free global search plus a local constrained polish for optimisation, with
generative and differentiable methods as documented upgrades and multi-fidelity Bayesian optimisation as a
deferred track.  The two places where the literature argues *against* a naive implementation are the BEM
low-inflow singularity (Tai et al. 2025) and the blunt-edge drag and Kutta-condition consequences
(Standish & van Dam 2003; Lee et al. 2025) — both are addressed in the aero module requirements.

## 8. Gaps this review found in the plan

1. **Hover FM validation data.**  The retrieved work validates against CFD or experiment, but the plan
   (第 10.3 节) has no anchor case for the analytic polar.  Recommendation: a golden-test case (T045/T056
   scope) plus one published hover dataset before a *performance* claim appears in the README.
2. **Low-Reynolds stall and transition.**  Small rotors run at Re ≈ 10⁵–10⁶ where laminar separation
   bubbles dominate; an analytic polar with a stall penalty is a coarse surrogate.  The plan's Level-2/3
   polar backends (T082-T084) should be sequenced before any FM figure is presented as engineering-grade.
3. **Trailing-edge included angle is not well characterised for rotating sections.**  The bounds in
   第 5.4 节 are pragmatic; the unsteady rounded-edge behaviour reported by Lee et al. (2025) suggests the
   angle check is a proxy, not a physical criterion — documented as such rather than oversold.
4. **Tip Mach limit vs. noise.**  The default `M_tip <= 0.65` is inherited from acoustic practice; no
   aeroacoustic model exists here (第 2.2 节 excludes noise), so the limit is presented as a design
   convention, not a noise guarantee.

---

## References (BibTeX-style, with URLs)

Retrieved 2026-10-14 through `scripts/literature_search.py`; DOI/arXiv identifiers verified against the
Crossref or arXiv API.  Titles and venues are reproduced as the APIs report them.

```bibtex
@article{Kulfan2010cst,
  title   = {Recent extensions and applications of the {CST} universal parametric geometry representation method},
  author  = {Kulfan, B. M.},
  journal = {The Aeronautical Journal},
  volume  = {114}, number = {1153}, pages = {157--176}, year = {2010},
  doi     = {10.1017/S0001924000003614},
  url     = {https://doi.org/10.1017/S0001924000003614}
}

@article{StandishVanDam2003blunt,
  title   = {Aerodynamic Analysis of Blunt Trailing Edge Airfoils},
  author  = {Standish, Kevin J. and van Dam, Cornelius P.},
  journal = {Journal of Solar Energy Engineering},
  volume  = {125}, number = {4}, pages = {479--487}, year = {2003},
  doi     = {10.1115/1.1629103},
  url     = {https://doi.org/10.1115/1.1629103}
}

@article{LeeRameshGopalarathnam2025rounded,
  title   = {Effect of rounded trailing edges on unsteady airfoil loading at low reynolds numbers},
  author  = {Lee, Yi Tsung and Ramesh, Kiran and Gopalarathnam, Ashok},
  journal = {Theoretical and Computational Fluid Dynamics},
  volume  = {39}, year = {2025},
  doi     = {10.1007/s00162-025-00747-z},
  url     = {https://doi.org/10.1007/s00162-025-00747-z}
}

@article{PeiEtAl2023bluntthick,
  title   = {Influence of the Blunt Trailing-Edge Thickness on the Aerodynamic Characteristics of the Very Thick Airfoil},
  author  = {Pei, Zhen and Xu, He-Yong and Deng, Lei and Li, Ling-Xiao},
  journal = {Wind},
  volume  = {3}, number = {4}, year = {2023},
  doi     = {10.3390/wind3040025},
  url     = {https://doi.org/10.3390/wind3040025}
}

@misc{AhmedShanto2024teCFD,
  title         = {Effects of Trailing Edge Thickness on NACA 4412 Airfoil Performance at Low Reynolds Numbers: A CFD Analysis},
  author        = {Ahmed, Sayed Tanvir and Shanto, Mahadi Hasan},
  year          = {2024},
  eprint        = {2409.13922}, archivePrefix = {arXiv}, primaryClass = {physics.flu-dyn},
  url           = {https://arxiv.org/abs/2409.13922}
}

@article{RuhHwang2023bemrotor,
  title   = {Fast and Robust Computation of Optimal Rotor Designs Using Blade Element Momentum Theory},
  author  = {Ruh, Marius L. and Hwang, John T.},
  journal = {AIAA Journal},
  volume  = {61}, number = {9}, pages = {4096--4111}, year = {2023},
  doi     = {10.2514/1.J062611},
  url     = {https://doi.org/10.2514/1.J062611}
}

@article{SheenEtAl2023intermeshing,
  title   = {Modeling of Hovering Intermeshing Rotor Using Blade Element Momentum Theory},
  author  = {Sheen, Guanbin and Baoyin, Hexi and Wang, Xianyu and Li, Jingyang},
  journal = {Journal of Aircraft},
  volume  = {60}, number = {2}, pages = {560--576}, year = {2023},
  doi     = {10.2514/1.C036528},
  url     = {https://doi.org/10.2514/1.C036528}
}

@article{TaiEtAl2025bemtuam,
  title   = {Improvements in Robustness and Versatility of Blade Element Momentum Theory for UAM/AAM Applications},
  author  = {Tai, Myungsik and Lee, Wooseung and Kim, Dahye and Park, Donghun},
  journal = {Aerospace},
  volume  = {12}, number = {8}, pages = {728}, year = {2025},
  doi     = {10.3390/aerospace12080728},
  url     = {https://doi.org/10.3390/aerospace12080728}
}

@article{LiewHeckHowland2024unified,
  title   = {Unified momentum model for rotor aerodynamics across operating regimes},
  author  = {Liew, Jaime and Heck, Kirby S. and Howland, Michael F.},
  journal = {Nature Communications},
  volume  = {15}, year = {2024},
  doi     = {10.1038/s41467-024-50756-5},
  url     = {https://doi.org/10.1038/s41467-024-50756-5}
}

@article{FritzEtAl2022sweepbem,
  title   = {An efficient blade sweep correction model for blade element momentum theory},
  author  = {Fritz, Erik Kaspar and Ferreira, Carlos and Boorsma, Koen},
  journal = {Wind Energy},
  volume  = {25}, number = {12}, pages = {1977--1994}, year = {2022},
  doi     = {10.1002/we.2778},
  url     = {https://doi.org/10.1002/we.2778}
}

@article{KoningEtAl2024elisa,
  title   = {{ELISA}: A Tool for Optimization of Rotor Hover Performance at Low Reynolds Number in the Mars Atmosphere},
  author  = {Koning, Witold J. F. and Perez, B. Natalia and Cummings, Haley V. and Romander, Ethan A. and Johnson, Wayne},
  journal = {Journal of the American Helicopter Society},
  volume  = {69}, year = {2024},
  doi     = {10.4050/jahs.69.042005},
  url     = {https://doi.org/10.4050/jahs.69.042005}
}

@techreport{WenrenEtAl2022planform,
  title       = {Helicopter rotor blade planform optimization using parametric design and multi-objective genetic algorithm},
  author      = {Wenren, Yonghu and Lim, Joon and Allen, Luke and Haehnel, Robert and Dettwiler, Ian},
  year        = {2022},
  doi         = {10.21079/11681/46261},
  url         = {https://doi.org/10.21079/11681/46261}
}

@misc{WangEtAl2023taorotor,
  title         = {Topologically assisted optimization for rotor design},
  author        = {Wang, Tianyu and Yang, Yannian and Chen, Xuanwu and Li, Pengyu and Iollo, Angelo},
  year          = {2023},
  eprint        = {2302.08728}, archivePrefix = {arXiv}, primaryClass = {physics.flu-dyn},
  url           = {https://arxiv.org/abs/2302.08728}
}

@article{QinYang2025coaxialmf,
  title   = {Aerodynamic optimization of a coaxial rotor system using a deep learning-based multi-fidelity surrogate model},
  author  = {Qin, Shu-Hui and Yang, Ai-Ming},
  journal = {Engineering Applications of Computational Fluid Mechanics},
  volume  = {19}, number = {1}, year = {2025},
  doi     = {10.1080/19942060.2025.2528120},
  url     = {https://doi.org/10.1080/19942060.2025.2528120}
}

@inproceedings{ErhardAlonso2024mfbo,
  title     = {Multi-Fidelity {Bayesian} Optimization of a Coaxial Rotor for e{VTOL} Aircraft},
  author    = {Erhard, Racheal M. and Alonso, Juan J.},
  booktitle = {AIAA SCITECH 2024 Forum},
  year      = {2024},
  doi       = {10.2514/6.2024-2506},
  url       = {https://doi.org/10.2514/6.2024-2506}
}

@article{CorneliusEtAl2025mfm,
  title   = {Multi-Fidelity Modeling of Isolated Hovering Rotors},
  author  = {Cornelius, Jason and Peters, Nicholas and {\'A}gren, Tove and Hjelm, Hugo},
  journal = {Aerospace},
  volume  = {12}, number = {8}, pages = {650}, year = {2025},
  doi     = {10.3390/aerospace12080650},
  url     = {https://doi.org/10.3390/aerospace12080650}
}

@inproceedings{HighFidelityRotorBEM2025,
  title     = {High-Fidelity Rotor Design Based on Blade Element Momentum Theory},
  author    = {Mise, Ritsuki and Ikami, Tsubasa and Nagai, Hiroki},
  booktitle = {AIAA SCITECH 2025 Forum},
  year      = {2025},
  doi       = {10.2514/6.2025-1635},
  url       = {https://doi.org/10.2514/6.2025-1635}
}

@misc{IterativeBEMTCoaxial2025,
  title  = {Iterative {BEMT} Analysis Extended to Model Coaxial Rotor Aerodynamic Performance in Hover},
  author = {Dai, Ziqi and Symons, Digby and Jung, Riul and Kingan, Michael Joseph},
  year   = {2025},
  doi    = {10.2139/ssrn.5135051},
  url    = {https://doi.org/10.2139/ssrn.5135051}
}

@inproceedings{RuhEtAl2023ganparam,
  title     = {Airfoil shape parameterization using reconstruction-error-minimizing generative adversarial networks},
  author    = {Ruh, Marius L. and Liu, Xiangbei and Yu, Rose and Hwang, John T.},
  booktitle = {AIAA AVIATION 2023 Forum},
  year      = {2023},
  doi       = {10.2514/6.2023-3722},
  url       = {https://doi.org/10.2514/6.2023-3722}
}

@article{YiDeng2023compactparam,
  title   = {Cooperation of Thin-Airfoil Theory and Deep Learning for a Compact Airfoil Shape Parameterization},
  author  = {Yi, Jianmiao and Deng, Feng},
  journal = {Aerospace},
  volume  = {10}, number = {7}, year = {2023},
  doi     = {10.3390/aerospace10070650},
  url     = {https://doi.org/10.3390/aerospace10070650}
}

@inproceedings{KimSethLiem2025gp,
  title     = {Geometric Programming for Airfoil Shape Optimization With Geometric Constraints},
  author    = {Kim, Dajung and Seth, Arjit and Liem, Rhea P.},
  booktitle = {AIAA SCITECH 2025 Forum},
  year      = {2025},
  doi       = {10.2514/6.2025-0653},
  url       = {https://doi.org/10.2514/6.2025-0653}
}

@misc{CSTLLM2025,
  title  = {{CST-LLM}: Enhancing Airfoil Parameterization Method with Large Language Model},
  author = {Zheng, Kefeng and Wang, Yiheng and Liu, Fei and Zhang, Qingfu and Song, Wenping},
  year   = {2025},
  doi    = {10.2139/ssrn.5125589},
  url    = {https://doi.org/10.2139/ssrn.5125589}
}

@article{ChenEtAl2020beziergan,
  title   = {Airfoil Design Parameterization and Optimization Using B{\'e}zier Generative Adversarial Networks},
  author  = {Chen, Wei and Chiu, Kevin and Fuge, Mark D.},
  journal = {AIAA Journal},
  volume  = {58}, number = {11}, pages = {4723--4735}, year = {2020},
  doi     = {10.2514/1.J059317},
  url     = {https://doi.org/10.2514/1.J059317}
}

@article{ChenEtAl2022inverse,
  title   = {Inverse Design of Two-Dimensional Airfoils Using Conditional Generative Models and Surrogate Log-Likelihoods},
  author  = {Chen, Qiuyi and Wang, Jun and Pope, Phillip and Chen, Wei and Fuge, Mark},
  journal = {Journal of Mechanical Design},
  volume  = {144}, number = {2}, year = {2022},
  doi     = {10.1115/1.4052846},
  url     = {https://doi.org/10.1115/1.4052846}
}

@article{WangEtAl2025cvae,
  title   = {A generative design method of airfoil based on conditional variational autoencoder},
  author  = {Wang, Xu and Qian, Weiqi and Zhao, Tun and Chen, Hai and He, Lei and Sun, Haisheng},
  journal = {Engineering Applications of Artificial Intelligence},
  volume  = {139}, pages = {109461}, year = {2025},
  doi     = {10.1016/j.engappai.2024.109461},
  url     = {https://doi.org/10.1016/j.engappai.2024.109461}
}

@misc{GravesFarimani2024diffusion,
  title         = {Airfoil Diffusion: Denoising Diffusion Model For Conditional Airfoil Generation},
  author        = {Graves, Reid and Barati Farimani, Amir},
  year          = {2024},
  eprint        = {2408.15898}, archivePrefix = {arXiv}, primaryClass = {cs.LG},
  url           = {https://arxiv.org/abs/2408.15898}
}

@misc{WangEtAl2024aerodit,
  title         = {{AeroDiT}: Diffusion Transformers for Reynolds-Averaged Navier-Stokes Simulations of Airfoil Flows},
  author        = {Wang, Chunyang and Pan, Biyue and Dai, Zhibo and Cai, Yudi and Ma, Yuhao},
  year          = {2024},
  eprint        = {2412.17394}, archivePrefix = {arXiv}, primaryClass = {physics.flu-dyn},
  url           = {https://arxiv.org/abs/2412.17394}
}

@misc{ChenEtAl2025manifold,
  title         = {Adjoint-Based Aerodynamic Shape Optimization with a Manifold Constraint Learned by Diffusion Models},
  author        = {Chen, Long and Oezkaya, Emre and Rottmayer, Jan and Gauger, Nicolas R. and Shen, Zebang},
  year          = {2025},
  eprint        = {2507.23443}, archivePrefix = {arXiv},
  url           = {https://arxiv.org/abs/2507.23443}
}

@inproceedings{HealyEtAl2024deepairfoil,
  title     = {Multi-Fidelity Training Data Generation for the Development of {DeepAirfoil}, an Airfoil Design and Analysis Tool},
  author    = {Healy, Richard C. and Beals, Nathan and Anusonti-Inthra, Phuriwat},
  booktitle = {AIAA AVIATION Forum and ASCEND 2024},
  year      = {2024},
  doi       = {10.2514/6.2024-4140},
  url       = {https://doi.org/10.2514/6.2024-4140}
}

@article{LiEtAl2022lowre,
  title   = {Low-{Reynolds}-number airfoil design optimization using deep-learning-based tailored airfoil modes},
  author  = {Li, Jichao and Zhang, Mengqi and Tay, Chien Ming Jonathan and Liu, Ningyu and Cui, Yongdong and Chew, Siou Chye},
  journal = {Aerospace Science and Technology},
  volume  = {121}, pages = {107309}, year = {2022},
  doi     = {10.1016/j.ast.2021.107309},
  url     = {https://doi.org/10.1016/j.ast.2021.107309}
}

@article{HoFarhat2023diffembed,
  title   = {Aerodynamic optimization with large shape and topology changes using a differentiable embedded boundary method},
  author  = {Ho, Jonathan and Farhat, Charbel},
  journal = {Journal of Computational Physics},
  volume  = {488}, pages = {112191}, year = {2023},
  doi     = {10.1016/j.jcp.2023.112191},
  url     = {https://doi.org/10.1016/j.jcp.2023.112191}
}

@misc{FengEtAl2024dgadjoint,
  title         = {Investigation of discontinuous {Galerkin} methods in adjoint gradient-based aerodynamic shape optimization},
  author        = {Feng, Yiwei and Lv, Lili and Liu, Tiegang and Wang, Kun and Ai, Bangcheng},
  year          = {2024},
  eprint        = {2407.18963}, archivePrefix = {arXiv},
  url           = {https://arxiv.org/abs/2407.18963}
}

@inproceedings{Drela1989xfoil,
  title     = {{XFOIL}: An Analysis and Design System for Low Reynolds Number Airfoils},
  author    = {Drela, Mark},
  booktitle = {Low Reynolds Number Airfoil Design},
  series    = {Lecture Notes in Engineering},
  pages     = {1--12},
  year      = {1989},
  doi       = {10.1007/978-3-642-84010-4_1},
  url       = {https://doi.org/10.1007/978-3-642-84010-4_1}
}

@misc{QBladeOcean2023,
  title  = {Verifying {QBlade-Ocean}: A Hydrodynamic Extension to the Wind Turbine Simulation Tool {QBlade}},
  author = {Behrens de Luna, Robert and Perez-Becker, Sebastian and Saverin, Joseph and Marten, David},
  year   = {2023},
  doi    = {10.5194/wes-2023-117},
  url    = {https://doi.org/10.5194/wes-2023-117}
}

@misc{MultiRotorReference2025,
  title  = {Design optimisation of an open-source reference rotor library for multi-rotor development and innovation},
  author = {Sheik Hassan, Abdirahman and Chandarana, Neha and Groh, Rainer and Macquart, Terence},
  year   = {2025},
  doi    = {10.5194/wes-2025-274},
  url    = {https://doi.org/10.5194/wes-2025-274}
}

@article{Hansen2016cmaes,
  title         = {The {CMA} Evolution Strategy: A Tutorial},
  author        = {Hansen, Nikolaus},
  year          = {2016},
  eprint        = {1604.00772}, archivePrefix = {arXiv}, primaryClass = {math.OC},
  url           = {https://arxiv.org/abs/1604.00772}
}

@article{VirtanenEtAl2020scipy,
  title   = {{SciPy} 1.0: fundamental algorithms for scientific computing in {Python}},
  author  = {Virtanen, Pauli and Gommers, Ralf and Oliphant, Travis E. and Haberland, Matt and Reddy, Tyler and Cournapeau, David and others},
  journal = {Nature Methods},
  volume  = {17}, pages = {261--272}, year = {2020},
  doi     = {10.1038/s41592-019-0686-2},
  url     = {https://doi.org/10.1038/s41592-019-0686-2}
}

@article{Jme2023blunt3d,
  title   = {Efficiency Analysis of 3D Wind Turbine Blade Performance Based on Blunt Trailing Edge Airfoil Blade},
  author  = {{Journal of Mechanical Engineering (see Crossref record)}},
  journal = {Journal of Mechanical Engineering},
  year    = {2023},
  doi     = {10.3901/jme.2023.06.123},
  url     = {https://doi.org/10.3901/jme.2023.06.123}
}

@article{Jafm2025hdarrieus,
  title   = {Numerical Study on Aerodynamic Characteristics of {H-Darrieus} Wind Turbine with Blunt Trailing-edge Airfoil},
  author  = {{Journal of Applied Fluid Mechanics (see publisher record)}},
  journal = {Journal of Applied Fluid Mechanics},
  volume  = {18}, number = {4}, year = {2025},
  doi     = {10.47176/jafm.18.4.3019},
  url     = {https://doi.org/10.47176/jafm.18.4.3019}
}
```

### Chinese-language work (中文检索, 计划.md 第 7.2 节 中文检索词)

Neither arXiv nor Crossref indexes the Chinese literature found through the plan's 中文检索词, so these
entries were located through the journals' own sites and are listed with their resolver URLs rather than
API-verified records — treat them as leads for a follow-up pass (task T008 limitation, also noted in
`docs/literature_search/queries.md`):

* 覃锴, 伊鹏辉, 刘兆方, 黄典贵. 基于多岛遗传算法的钝尾缘翼型多目标优化设计 (Multi-objective optimization
  design for blunt trailing-edge airfoil based on multi-island genetic algorithm). 太阳能学报, 2022,
  DOI [10.19912/j.0254-0096.tynxb.2021-0253](https://doi.org/10.19912/j.0254-0096.tynxb.2021-0253).
  Combines a Hicks-Henne-type function with a blunt-edge shaping function — the same "explicit edge term"
  idea our CST clamp implements.
* 王少聪, 田红艳, 汤志豪, 朱申安. 考虑结构特性的钝尾缘翼型气动优化设计 (Aerodynamic optimization design of
  blunt trailing edge airfoil considering structural characteristics). 太阳能学报, 2024,
  DOI [10.19912/j.0254-0096.tynxb.2024-0150](https://doi.org/10.19912/j.0254-0096.tynxb.2024-0150).
  Builds the airfoil parametric model by combining CST parameterization with blunt-trailing-edge
  modification — the closest published match to this project's geometry layer.
