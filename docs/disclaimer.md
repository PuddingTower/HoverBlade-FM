# Disclaimer (免责声明)

**Scope of what HoverBlade-FM produces.**  The program outputs aerodynamic shape design and
manufacturing geometry data packages (气动外形设计与制造几何数据包).  It does **not** constitute, and
must not be used as:

* flight-safety certification (飞行安全认证),
* structural-strength certification (结构强度认证),
* airworthiness certification (适航认证).

Real engineering use additionally requires structural, fatigue, dynamics (airworthiness),
manufacturing-inspection and test verification (结构、疲劳、动力学、制造检验和试验验证).
— 计划.md 第 28 节.

## What the tool does claim

| Claim | Basis |
|---|---|
| Ten radial sections at `r/R = 0.10 … 0.97` with a blunt trailing edge at every station | geometry is constructed with an explicit trailing-edge gap that is hard-clamped at decode time; checked by unit tests and by the CI assertion on `sections.csv` |
| Hover performance figures (thrust, torque, induced / profile / swirl power, FM) from a blade-element-momentum solver with Prandtl tip and root losses and an analytic polar | a low-fidelity, quasi-steady model.  It is reproducible and fast; it is **not** validated against flight test data in this repository |
| A higher FM means a more hover-efficient blade *within this model* | the optimiser maximises the model's FM, subject to a thrust-matching penalty and geometric constraints |
| STEP / STL / DXF and a manufacturing report describe the lofted geometry | they describe geometry and nominal tolerances.  They say nothing about stresses, natural frequencies, balance, tolerance stack-up, material properties, or process capability |

## What the tool does not claim

1. **No structure.** There is no spar, skin, root fitting, centrifugal, bending, or fatigue
   analysis.  Planform and airfoil shapes produced here are not structurally sized.
   A blade that looks manufacturable here may still be structurally inadmissible.
2. **No aeroelasticity or dynamics.** No trim, stability, coning, flapping, ground resonance,
   or vibration prediction.
3. **No forward flight.** The aerodynamic model is hover-only: `forward_speed = 0`,
   `climb_rate = 0`.  Advance-ratio performance, retreating-side stall, and compressibility in
   forward flight are out of scope (计划.md 第 2.2 节).
4. **No acoustic or wake modelling.** Noise, blade-vortex interaction, and unsteady wake effects
   are not evaluated.
5. **No flight-readiness.** Nothing here demonstrates that a rotor built from these files will
   produce the predicted thrust, that it will not diverge, or that it is safe to fly.
6. **Low-fidelity polar.** Airfoil coefficients come from an analytic polar with Reynolds, Mach
   and blunt-trailing-edge corrections, not from wind-tunnel or RANS data for the specific
   shapes generated here.  Section polars away from the calibration range are extrapolations.
7. **Literature figures are not ours.** Any number quoted in the documentation about *other*
   work belongs to the cited source; performance figures of this repository's own runs are only
   valid for the exact input, preset, seed and version recorded in `provenance.json`.

## Intended use

Conceptual hover-rotor design studies, education, aerodynamic shape exploration, generating
candidate geometry for a downstream structural and manufacturing process owned by a qualified
engineer, and as a reproducible baseline for optimisation research.

## No warranty

Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except
in compliance with the License.  You may obtain a copy of the License at
<http://www.apache.org/licenses/LICENSE-2.0>.

Unless required by applicable law or agreed to in writing, software distributed under the
License is distributed on an **"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND**,
either express or implied.  See the License for the specific language governing permissions and
limitations under the License.  The maintainers of HoverBlade-FM assume no liability for the use
of generated geometry in hardware.

## If you fly it

Anything that lifts a person or a payload requires an independent engineering review, a
structural design and a test programme.  Treat HoverBlade-FM output as a starting geometry,
not as an approved part definition.
