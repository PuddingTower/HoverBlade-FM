"""Command line interface (plan section 18).

Exit codes: 0 success, 2 invalid input, 3 infeasible design or blocking manufacturing failure
(``--strict``), 4 unexpected internal error.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

from hoverblade_fm import __version__
from hoverblade_fm.config.schema import (
    AirfoilBounds,
    InputValidationError,
    OptimizationSettings,
    SolverSettings,
    load_input,
    resolve,
)
from hoverblade_fm.io.readers import RunDataError
from hoverblade_fm.io.summary import summarise_run
from hoverblade_fm.pipeline import RunOutcome, design_from_run_dir, generate

EXIT_OK = 0
EXIT_INVALID_INPUT = 2
EXIT_INFEASIBLE = 3
EXIT_INTERNAL = 4


class Console:
    """Small stdout logger honouring --quiet and --json."""

    def __init__(self, quiet: bool = False, json_mode: bool = False) -> None:
        self.quiet = quiet or json_mode
        self.json_mode = json_mode

    def info(self, message: str) -> None:
        if not self.quiet:
            print(message, file=sys.stderr if self.json_mode else sys.stdout)

    def warning(self, message: str) -> None:
        if not self.quiet:
            print(f"warning: {message}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hoverblade-fm", description="Hover-only rotor generator with a blunt trailing edge constraint"
    )
    parser.add_argument("--version", action="version", version=f"HoverBlade-FM {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="generate a complete blade design from three inputs")
    gen.add_argument(
        "input_json",
        nargs="?",
        type=Path,
        default=None,
        help="JSON file with thrust_n / radius_m / trailing_edge_thickness_m",
    )
    gen.add_argument("--thrust-n", type=float, default=None, help="target thrust [N]")
    gen.add_argument("--radius-m", type=float, default=None, help="rotor radius [m]")
    gen.add_argument(
        "--trailing-edge-thickness-m",
        type=float,
        default=None,
        help="minimum blunt trailing-edge thickness [m]",
    )
    gen.add_argument("--out", type=Path, required=True, help="output directory")
    gen.add_argument("--preset", choices=("fast", "standard", "full", "custom"), default=None)
    gen.add_argument("--seed", type=int, default=None)
    gen.add_argument("--blade-count", type=int, default=None)
    gen.add_argument("--rpm", type=float, default=None)
    gen.add_argument("--tip-mach-limit", type=float, default=None)
    gen.add_argument("--rho", type=float, default=None, help="air density override [kg/m^3]")
    gen.add_argument("--mu", type=float, default=None, help="dynamic viscosity override [Pa.s]")
    gen.add_argument("--a-sound", type=float, default=None, help="speed of sound override [m/s]")
    gen.add_argument("--max-evaluations", type=int, default=None)
    gen.add_argument("--popsize", type=int, default=None)
    gen.add_argument("--no-optimize", action="store_true", help="report the reference design only")
    gen.add_argument("--no-cad", action="store_true")
    gen.add_argument("--no-viz", action="store_true")
    gen.add_argument("--mesh-resolution", choices=("coarse", "standard", "fine"), default="standard")
    gen.add_argument("--thickness-min", type=float, default=None, help="airfoil t/c lower bound")
    gen.add_argument("--thickness-max", type=float, default=None, help="airfoil t/c upper bound")
    gen.add_argument("--quiet", action="store_true")
    gen.add_argument("--json", action="store_true", help="print the run summary as JSON on stdout")

    viz = sub.add_parser("viz", help="regenerate plots and reports for an existing run directory")
    viz.add_argument("out_dir", type=Path)
    viz.add_argument("--interactive-only", action="store_true")
    viz.add_argument("--quiet", action="store_true")

    check = sub.add_parser(
        "check-manufacturing", help="run the manufacturing / trailing-edge checks of a run"
    )
    check.add_argument("out_dir", type=Path)
    check.add_argument("--json", action="store_true")
    check.add_argument("--strict", action="store_true", help="exit 3 when any blocking check fails")

    summary = sub.add_parser("summary", help="print a run summary")
    summary.add_argument("out_dir", type=Path)
    summary.add_argument("--json", action="store_true")

    lit = sub.add_parser("literature-search", help="run the literature retrieval script")
    lit.add_argument("--out", type=Path, default=Path("docs/literature_search"))
    lit.add_argument("--limit", type=int, default=25)
    lit.add_argument("--offline", action="store_true")
    lit.add_argument("--years", type=str, default="2022-2025")

    sub.add_parser("version", help="print the program version")
    return parser


def _overrides(args: argparse.Namespace) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if args.preset is not None:
        data.setdefault("optimization", {})["preset"] = args.preset
    optimization: dict[str, Any] = {}
    if args.seed is not None:
        optimization["seed"] = args.seed
    if args.max_evaluations is not None:
        optimization["max_evaluations"] = args.max_evaluations
        optimization["preset"] = "custom"
    if args.popsize is not None:
        optimization["popsize"] = args.popsize
        optimization["preset"] = "custom"
    if optimization:
        data.setdefault("optimization", {}).update(optimization)
    if args.blade_count is not None:
        data["blade_count"] = args.blade_count
    if args.rpm is not None:
        data["rpm"] = args.rpm
    if args.tip_mach_limit is not None:
        data["tip_mach_limit"] = args.tip_mach_limit
    if args.rho is not None:
        data["air_density_kg_m3"] = args.rho
    if args.mu is not None:
        data["dynamic_viscosity_pa_s"] = args.mu
    if args.a_sound is not None:
        data["sound_speed_m_s"] = args.a_sound
    airfoil: dict[str, Any] = {}
    if args.thickness_min is not None:
        airfoil["thickness_to_chord_min"] = args.thickness_min
    if args.thickness_max is not None:
        airfoil["thickness_to_chord_max"] = args.thickness_max
    if airfoil:
        data.setdefault("airfoil", {}).update(airfoil)
    return data


def _resolved_config(args: argparse.Namespace):
    env_seed = os.environ.get("HOVERBLADE_FM_SEED")
    if env_seed is not None and args.seed is None:
        args.seed = int(env_seed)
    user_input = load_input(
        args.input_json,
        thrust_n=args.thrust_n,
        radius_m=args.radius_m,
        te_min_m=args.trailing_edge_thickness_m,
        overrides=_overrides(args),
    )
    if args.no_optimize:
        user_input = user_input.model_copy(
            update={
                "optimization": OptimizationSettings(
                    preset="custom", max_evaluations=0, local_refine=False, popsize=8
                )
            }
        )
    return resolve(user_input)


def _search_solver(cfg) -> SolverSettings | None:
    """Use a cheaper radial resolution during the search; the winner is re-checked at full resolution."""
    if cfg.optimization.preset == "full" or cfg.optimization.max_evaluations == 0:
        return None
    return SolverSettings(n_radial=81, n_itmax=120)


def cmd_generate(args: argparse.Namespace, console: Console) -> int:
    cfg = _resolved_config(args)
    console.info(
        f"inputs: T={cfg.thrust_n:.1f} N, R={cfg.radius_m:.3f} m, t_TE_min={cfg.te_min_m * 1000:.3f} mm"
        f" -> {cfg.blade_count} blades @ {cfg.rpm:.1f} RPM (tip Mach {cfg.tip_mach:.3f})"
    )
    for reason in cfg.reasons:
        console.info(f"  {reason}")
    outcome = generate(
        cfg,
        args.out,
        optimize=cfg.optimization.max_evaluations > 0,
        with_cad=not args.no_cad,
        with_viz=not args.no_viz,
        mesh_resolution=args.mesh_resolution,
        seed=args.seed,
        search_solver=_search_solver(cfg),
        log=console,
    )
    _report(outcome, console, json_mode=args.json)
    return outcome.exit_code


def _initial_fm(outcome: RunOutcome) -> float | None:
    if outcome.optimization is None or outcome.optimization.initial_performance is None:
        return None
    return float(outcome.optimization.initial_performance.figure_of_merit)


def _round4(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def _report(outcome: RunOutcome, console: Console, *, json_mode: bool) -> None:
    perf, cons = outcome.performance, outcome.constraints
    payload = {
        "out": str(outcome.out_dir),
        "inputs": {
            "thrust_n": outcome.config.thrust_n,
            "radius_m": outcome.config.radius_m,
            "trailing_edge_thickness_m": outcome.config.te_min_m,
        },
        "resolved": {
            "blade_count": outcome.config.blade_count,
            "rpm": round(outcome.config.rpm, 3),
            "tip_mach": round(outcome.config.tip_mach, 4),
            "solidity": round(perf.solidity, 4),
        },
        "performance": {
            "figure_of_merit": round(perf.figure_of_merit, 4),
            "thrust_calc_n": round(perf.thrust_calc_n, 2),
            "thrust_error": round(perf.thrust_error, 5),
            "power_ideal_w": round(perf.power_ideal_w, 1),
            "power_actual_w": round(perf.power_actual_w, 1),
            "power_induced_w": round(perf.power_induced_w, 1),
            "power_profile_w": round(perf.power_profile_w, 1),
            "power_swirl_w": round(perf.power_swirl_w, 1),
            "torque_nm": round(perf.torque_nm, 1),
            "figure_power_w_per_n": round(perf.figure_power, 3),
            "collective_trim_deg": round(perf.collective_trim_deg, 3),
        },
        "trailing_edge": {
            "required_mm": round(outcome.config.te_min_m * 1000, 4),
            "min_actual_mm": round(cons.te_min_actual_m * 1000, 4),
            "margin_mm": round((cons.te_min_actual_m - cons.te_min_required_m) * 1000, 4),
            "violations": cons.te_violation_m,
            "blunt_everywhere": bool(outcome.design.is_blunt()),
        },
        "optimisation": {
            "performed": outcome.optimization is not None,
            "evaluations": outcome.optimization.evaluations if outcome.optimization else 0,
            "initial_fm": _round4(_initial_fm(outcome)),
            "final_fm": round(perf.figure_of_merit, 4),
            "strategy": outcome.optimization.strategy if outcome.optimization else None,
            "seed": outcome.optimization.seed if outcome.optimization else outcome.config.optimization.seed,
        },
        "feasible": bool(cons.feasible),
        "geometry_issues": list(cons.geometry_issues)[:8],
        "notes": outcome.notes,
        "exit_code": outcome.exit_code,
        "wall_time_s": round(outcome.wall_time_s, 2),
    }
    if json_mode:
        print(json.dumps(payload, indent=2))
        return
    console.info("")
    console.info(
        f"FM = {perf.figure_of_merit:.4f}   P = {perf.power_actual_w / 1000:.2f} kW "
        f"(ideal {perf.power_ideal_w / 1000:.2f} kW)   T = {perf.thrust_calc_n:.1f} N "
        f"({perf.thrust_error:+.2%})"
    )
    console.info(
        f"blunt trailing edge: min {cons.te_min_actual_m * 1000:.3f} mm >= required "
        f"{cons.te_min_required_m * 1000:.3f} mm  ->  {'PASS' if outcome.design.is_blunt() else 'FAIL'}"
    )
    initial_fm = _initial_fm(outcome)
    if outcome.optimization is not None and initial_fm is not None:
        console.info(
            f"optimisation: FM {initial_fm:.4f} -> {perf.figure_of_merit:.4f} in "
            f"{outcome.optimization.evaluations} evaluations"
        )
    for note in outcome.notes:
        console.info(f"note: {note}")


def cmd_viz(args: argparse.Namespace, console: Console) -> int:
    from hoverblade_fm.io.readers import read_performance, read_provenance

    cfg = _config_from_design_json(Path(args.out_dir) / "design.json")
    design = design_from_run_dir(args.out_dir, cfg)
    performance = read_performance(Path(args.out_dir) / "performance.json")
    provenance = read_provenance(Path(args.out_dir) / "provenance.json")
    if args.interactive_only:
        from hoverblade_fm.viz.interactive import build_interactive_html

        path = build_interactive_html(
            design, _performance_dict_to_model(performance), Path(args.out_dir) / "interactive" / "blade.html"
        )
        console.info(f"wrote {path}")
        return EXIT_OK
    from hoverblade_fm.viz import generate_all_visuals

    paths = generate_all_visuals(
        design,
        _performance_dict_to_model(performance),
        None,
        None,
        provenance,
        Path(args.out_dir),
    )
    for key, value in paths.items():
        console.info(f"{key}: {value}")
    return EXIT_OK


def cmd_check_manufacturing(args: argparse.Namespace, console: Console) -> int:
    from hoverblade_fm.cad import run_manufacturing_checks
    from hoverblade_fm.io.readers import read_design

    out_dir = Path(args.out_dir)
    design_data = read_design(out_dir / "design.json")
    cfg = _config_from_design_json(out_dir / "design.json")
    design = design_from_run_dir(out_dir, cfg)
    checks = run_manufacturing_checks(design, cfg)
    payload = checks.to_dict()
    blocking = list(payload.get("blocking_failures", []) or [])
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        for key, value in payload.items():
            console.info(f"{key}: {value}")
    required = design_data.get("blade", {}).get("trailing_edge_thickness_m")
    if required is not None and design.min_te_thickness() < float(required) - 1e-12:
        blocking.append("trailing-edge thickness below requirement")
    if blocking and args.strict:
        console.warning(f"{len(blocking)} blocking manufacturing failure(s)")
        return EXIT_INFEASIBLE
    return EXIT_OK


def cmd_summary(args: argparse.Namespace, console: Console) -> int:
    try:
        summary = summarise_run(Path(args.out_dir))
    except RunDataError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        for key, value in summary.items():
            if isinstance(value, (list, dict)):
                console.info(f"{key}: {json.dumps(value, default=str)}")
            else:
                console.info(f"{key}: {value}")
    return EXIT_OK if not summary.get("missing_required") else EXIT_INVALID_INPUT


def cmd_literature_search(args: argparse.Namespace, console: Console) -> int:
    offline = args.offline or os.environ.get("HOVERBLADE_FM_OFFLINE", "") not in ("", "0")
    script = Path(__file__).resolve().parents[2] / "scripts" / "literature_search.py"
    if not script.is_file():
        print(f"error: literature search script not found at {script}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    spec = importlib.util.spec_from_file_location("hoverblade_literature_search", script)
    if spec is None or spec.loader is None:
        print("error: could not load the literature search script", file=sys.stderr)
        return EXIT_INTERNAL
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    argv = ["--out", str(args.out), "--limit", str(args.limit), "--years", args.years]
    if offline:
        argv.append("--offline")
    runner = getattr(module, "main", None)
    if callable(runner):
        return int(runner(argv) or 0)
    print("error: literature script exposes no main(argv)", file=sys.stderr)
    return EXIT_INTERNAL


def _config_from_design_json(path: Path):
    """Rebuild a ResolvedConfig from a stored ``design.json`` (used by viz / check commands)."""
    from hoverblade_fm.config.schema import UserInput

    data = json.loads(path.read_text(encoding="utf-8"))
    resolved = data.get("config") or data.get("resolved_config") or {}
    rpm = resolved.get("rpm") or data.get("rpm")
    airfoil_fields = AirfoilBounds.model_fields
    solver_fields = SolverSettings.model_fields
    opt_fields = OptimizationSettings.model_fields
    user_input = UserInput(
        thrust_n=float(resolved.get("thrust_n", data.get("thrust_n", 1.0))),
        radius_m=float(resolved.get("radius_m", data.get("radius_m", 1.0))),
        trailing_edge_thickness_m=float(
            resolved.get("trailing_edge_thickness_m", data.get("te_min_m", 0.001))
        ),
        blade_count=int(resolved.get("blade_count", data.get("blade_count", 2))),
        rpm=float(rpm) if rpm else None,
        air_density_kg_m3=float(resolved.get("air_density_kg_m3", 1.225)),
        dynamic_viscosity_pa_s=float(resolved.get("dynamic_viscosity_pa_s", 1.81e-5)),
        sound_speed_m_s=float(resolved.get("sound_speed_m_s", 340.29)),
        tip_mach_limit=float(resolved.get("tip_mach_limit", 0.65)),
        r_stations=list(resolved.get("r_stations", [])) or None,
        airfoil=AirfoilBounds(
            **{k: v for k, v in (resolved.get("airfoil") or {}).items() if k in airfoil_fields}
        ),
        solver=SolverSettings(
            **{k: v for k, v in (resolved.get("solver") or {}).items() if k in solver_fields}
        ),
        optimization=OptimizationSettings(
            **{k: v for k, v in (resolved.get("optimization") or {}).items() if k in opt_fields}
        ),
    )
    return resolve(user_input)


def _performance_dict_to_model(data: dict[str, Any]):
    from hoverblade_fm.models import Performance

    fields = {f.name for f in dataclasses.fields(Performance)}
    kwargs = {k: v for k, v in data.items() if k in fields and k != "radial"}
    return Performance(**kwargs)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    console = Console(quiet=getattr(args, "quiet", False), json_mode=getattr(args, "json", False))
    handlers = {
        "generate": cmd_generate,
        "viz": cmd_viz,
        "check-manufacturing": cmd_check_manufacturing,
        "summary": cmd_summary,
        "literature-search": cmd_literature_search,
        "version": lambda a, c: (print(f"HoverBlade-FM {__version__}"), EXIT_OK)[1],
    }
    handler = handlers.get(args.command)
    if handler is None:  # pragma: no cover - argparse enforces the choice
        parser.error(f"unknown command {args.command}")
        return EXIT_INVALID_INPUT
    try:
        return int(handler(args, console))
    except (InputValidationError, RunDataError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    except Exception as exc:  # pragma: no cover - last-resort guard
        print(f"internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        if os.environ.get("HOVERBLADE_FM_DEBUG"):
            raise
        return EXIT_INTERNAL


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
