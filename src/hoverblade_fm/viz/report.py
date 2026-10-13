"""Self-contained single-page HTML report (plan section 12.1 / task T067).

The output embeds CSS and every PNG as a data URI, so one file can be mailed or opened
from a USB stick with no network access.  All dynamic text is HTML-escaped and every
non-finite number is rendered as ``n/a``.
"""

from __future__ import annotations

import base64
import html
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hoverblade_fm.models import SECTION_COUNT, Design, Performance

if TYPE_CHECKING:
    from hoverblade_fm.config.schema import ResolvedConfig

PLOT_CAPTIONS: tuple[tuple[str, str], ...] = (
    ("chord_twist", "Chord and twist distribution"),
    ("airfoil_stack", f"All {SECTION_COUNT} sections at their radial stations"),
    ("trailing_edge_zoom", "Blunt trailing-edge zoom, x/c 0.90-1.02 per station"),
    ("fm_convergence", "Figure-of-Merit and objective convergence"),
    ("radial_performance", "Spanwise hover loading"),
    ("sections_preview", "Generated section outlines"),
)

_RESOLVED_ROWS: tuple[tuple[str, str, str, int], ...] = (
    ("Air density", "air_density_kg_m3", "kg/m^3", 4),
    ("Dynamic viscosity", "dynamic_viscosity_pa_s", "Pa.s", 3),
    ("Speed of sound", "sound_speed_m_s", "m/s", 2),
    ("Blade count", "blade_count", "", 0),
    ("Rotor speed", "rpm", "RPM", 1),
    ("Tip speed", "tip_speed_m_s", "m/s", 2),
    ("Tip Mach (resolved)", "tip_mach", "", 3),
    ("Tip Mach limit", "tip_mach_limit", "", 3),
    ("Disk area", "disk_area_m2", "m^2", 4),
    ("Disk loading", "disk_loading_n_m2", "N/m^2", 2),
    ("Thrust coefficient", "thrust_coefficient", "", 5),
    ("Reference solidity", "solidity_ref", "", 4),
    ("Ideal induced power", "power_ideal_w", "W", 1),
)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def format_number(value: Any, digits: int = 4, *, scale: float = 1.0, unit: str = "") -> str:
    """Format a number for HTML, mapping None/NaN/inf to ``n/a``."""
    if not _is_number(value):
        return "n/a"
    number = float(value) * scale
    text = f"{number:.{digits}f}" if digits > 0 else f"{number:.0f}"
    return f"{text} {unit}".strip() if unit else text


def _sci(value: Any, digits: int = 2) -> str:
    if not _is_number(value):
        return "n/a"
    return f"{float(value):.{digits}e}"


def escape_text(value: Any) -> str:
    """HTML-escape any dynamic string coming from user input or provenance payloads."""
    return html.escape("" if value is None else str(value), quote=True)


def _badge(ok: bool, *, yes: str = "PASS", no: str = "FAIL") -> str:
    return f'<span class="badge {"ok" if ok else "bad"}">{escape_text(yes if ok else no)}</span>'


def _rows(pairs: Iterable[tuple[str, str]]) -> str:
    return "".join(
        f"<tr><th scope='row'>{escape_text(name)}</th><td>{value}</td></tr>" for name, value in pairs
    )


def _table(caption: str, headers: Sequence[str], body: Sequence[Sequence[str]], *, css: str = "") -> str:
    head = "".join(f"<th>{escape_text(h)}</th>" for h in headers)
    rows = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in body)
    cap = f"<caption>{escape_text(caption)}</caption>" if caption else ""
    return f'<table class="data {css}">{cap}<thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>'


def _provenance_dict(provenance: Any) -> dict[str, Any]:
    if provenance is None:
        return {}
    if isinstance(provenance, Mapping):
        return dict(provenance)
    to_dict = getattr(provenance, "to_dict", None)
    if callable(to_dict):
        try:
            return dict(to_dict())
        except Exception:
            return {}
    return {}


def _resolved_dict(provenance: Mapping[str, Any], resolved: ResolvedConfig | None) -> dict[str, Any]:
    if resolved is not None:
        to_dict = getattr(resolved, "to_dict", None)
        if callable(to_dict):
            data = dict(to_dict())
            data.setdefault("auto_reasons", list(getattr(resolved, "reasons", ()) or ()))
            data.setdefault("warnings", list(getattr(resolved, "warnings", ()) or ()))
            return data
    nested = provenance.get("resolved_config")
    return dict(nested) if isinstance(nested, Mapping) else {}


def _relative_link(asset: Path, base: Path) -> str:
    """Relative href from the report file to an asset, falling back to the file name."""
    try:
        return asset.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return asset.name


def _image_block(key: str, caption: str, plot_paths: Mapping[str, Path]) -> str:
    path = plot_paths.get(key)
    if path is None or not Path(path).is_file():
        return (
            f'<figure class="plot missing"><figcaption>{escape_text(caption)}</figcaption>'
            f"<p class='note'>Plot <code>{escape_text(key)}</code> was not generated; the pipeline reported no file for it.</p></figure>"
        )
    raw = Path(path).read_bytes()
    uri = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    return (
        f'<figure class="plot"><figcaption>{escape_text(caption)} '
        f"<span class='file'>{escape_text(Path(path).name)}</span></figcaption>"
        f'<img src="{uri}" alt="{escape_text(caption)}" loading="lazy"></figure>'
    )


def _te_proof(design: Design, constraints: Any) -> tuple[str, list[str]]:
    """Build the headline blunt-trailing-edge table plus any degradation notes."""
    notes: list[str] = []
    required = design.te_min_m if _is_number(design.te_min_m) else float("nan")
    if constraints is None:
        notes.append(
            "No ConstraintReport was supplied, so the trailing-edge proof below is measured "
            "directly from the generated contour geometry."
        )

    body: list[list[str]] = []
    all_ok = True
    for section in design.sections:
        measured = section.points.te_thickness * section.chord_m
        contour_ok = _is_number(required) and measured >= required - 1e-12
        nominal_ok = _is_number(required) and section.te_thickness_m >= required - 1e-12
        angle = section.te_included_angle_deg
        angle_ok = _is_number(angle) and angle > 0.0
        ok = bool(contour_ok and nominal_ok and angle_ok)
        all_ok = all_ok and ok
        margin = measured - required if _is_number(required) else float("nan")
        body.append(
            [
                escape_text(section.station_id),
                format_number(section.r_R, 3),
                format_number(section.chord_m, 4),
                format_number(required, 5),
                format_number(section.te_thickness_m, 5),
                format_number(measured, 5),
                format_number(margin, 5),
                format_number(margin / required, 2) if _is_number(required) and required > 0 else "n/a",
                format_number(angle, 2),
                _badge(ok),
            ]
        )

    headers = [
        "#",
        "r/R",
        "chord [m]",
        "required t_TE [m]",
        "nominal t_TE [m]",
        "contour t_TE [m]",
        "margin [m]",
        "margin / required",
        "TE angle [deg]",
        "status",
    ]
    table = _table("", headers, body, css="stations")

    min_actual = design.min_te_thickness()
    violation = getattr(constraints, "te_violation_m", None)
    summary = (
        "<ul class='summary'>"
        f"<li>Required minimum trailing-edge thickness: <b>{format_number(required, 5, unit='m')}</b> "
        f"({format_number(required, 3, scale=1000.0, unit='mm')})</li>"
        f"<li>Smallest generated trailing edge: <b>{format_number(min_actual, 5, unit='m')}</b> "
        f"({format_number(min_actual, 3, scale=1000.0, unit='mm')})</li>"
        f"<li>Global margin: <b>{format_number(design.te_margin(), 5, unit='m')}</b></li>"
        f"<li>Constraint violation: <b>{format_number(violation, 5, unit='m')}</b></li>"
        f"<li>Every station blunt: <b>{'YES' if all_ok else 'NO'}</b> {_badge(all_ok)}</li>"
        "</ul>"
    )
    banner = (
        f"<div class='proof {'pass' if all_ok else 'fail'}'><div class='proof-head'>"
        f"<span>Blunt trailing edge — hard constraint</span>{_badge(all_ok, yes='CONSTRAINT SATISFIED', no='CONSTRAINT VIOLATED')}</div>"
        f"{summary}{table}</div>"
    )
    if constraints is not None and constraints.geometry_issues:
        notes.append(
            "Geometry issues reported by the constraint check: "
            + "; ".join(str(i) for i in constraints.geometry_issues)
        )
    return banner, notes


_CSS = """
:root{--ink:#1f2430;--muted:#6b7280;--line:#dfe3ea;--panel:#f7f8fa;--chord:#2b6cb0;--ok:#2f855a;--bad:#c53030;--warn:#b7791f}
*{box-sizing:border-box}
body{margin:0;padding:28px 20px 60px;background:#fff;color:var(--ink);
 font-family:"Segoe UI",DejaVu Sans,Verdana,Geneva,sans-serif;font-size:14px;line-height:1.5;max-width:1120px}
h1{font-size:25px;margin:0 0 4px}
h2{font-size:18px;margin:34px 0 10px;padding-bottom:5px;border-bottom:2px solid var(--line)}
h3{font-size:15px;margin:20px 0 6px}
.sub{color:var(--muted);margin:0 0 18px}
.kpis{display:flex;flex-wrap:wrap;gap:10px;margin:14px 0 6px}
.kpi{border:1px solid var(--line);border-radius:8px;padding:10px 14px;min-width:150px;background:var(--panel)}
.kpi b{display:block;font-size:20px;line-height:1.2}
.kpi span{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
table.data{border-collapse:collapse;width:100%;margin:8px 0 4px;font-size:13px}
table.data caption{caption-side:top;text-align:left;font-weight:600;padding-bottom:4px;color:var(--muted)}
table.data th,table.data td{border:1px solid var(--line);padding:5px 8px;text-align:right;font-variant-numeric:tabular-nums}
table.data thead th{background:var(--panel);text-align:center}
table.data tbody th[scope=row]{text-align:left;font-weight:500;background:#fff;width:250px}
table.stations td,table.stations th{text-align:right;font-size:12px}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;font-weight:700;letter-spacing:.03em;color:#fff}
.badge.ok{background:var(--ok)}.badge.bad{background:var(--bad)}.badge.warn{background:var(--warn)}
.proof{border:3px solid var(--ok);border-radius:10px;padding:14px 16px;margin:10px 0 6px;background:#f5fbf7}
.proof.fail{border-color:var(--bad);background:#fdf5f5}
.proof-head{display:flex;justify-content:space-between;align-items:center;font-size:16px;font-weight:700;margin-bottom:6px}
ul.summary{margin:6px 0 12px;padding-left:20px}
ul.summary li{margin:2px 0}
ul.reasons{margin:6px 0;padding-left:20px;color:var(--ink)}
ul.reasons li{margin:2px 0}
ul.warnings{margin:6px 0;padding-left:20px;color:var(--warn)}
.note{background:var(--panel);border-left:4px solid var(--muted);padding:8px 10px;margin:8px 0;color:var(--ink);font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:16px}
figure.plot{margin:0;border:1px solid var(--line);border-radius:8px;padding:8px;background:#fff}
figure.plot img{width:100%;height:auto;display:block}
figure.plot figcaption{font-weight:600;font-size:13px;margin-bottom:6px}
figure.missing{background:var(--panel)}
.file{color:var(--muted);font-weight:400;font-size:11px;float:right}
footer{margin-top:38px;border-top:2px solid var(--line);padding-top:14px;color:var(--muted);font-size:12px}
footer table.data{font-size:12px}
footer th[scope=row]{width:200px}
code{background:var(--panel);padding:1px 4px;border-radius:3px;font-size:12px}
details{margin:10px 0}
summary{cursor:pointer;color:var(--muted);font-size:12px}
pre{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:10px;overflow:auto;font-size:11px}
@media print{body{max-width:none;padding:0}figure.plot{break-inside:avoid}}
"""


def render_report(
    design: Design,
    performance: Performance,
    constraints: Any | None = None,
    provenance: Mapping[str, Any] | Any | None = None,
    plot_paths: Mapping[str, Path] | None = None,
    out_path: str | Path = Path("output/report.html"),
    *,
    resolved: ResolvedConfig | None = None,
) -> Path:
    """Write one self-contained HTML page and return its path."""
    plots = dict(plot_paths or {})
    target = Path(out_path)
    prov = _provenance_dict(provenance)
    cfg = _resolved_dict(prov, resolved)
    notes: list[str] = []

    if len(design.sections) != SECTION_COUNT:
        notes.append(f"Expected {SECTION_COUNT} stations, found {len(design.sections)}.")

    proof_html, proof_notes = _te_proof(design, constraints)
    notes.extend(proof_notes)
    if performance.radial is None:
        notes.append(
            "The BEMT solver returned no radial distribution, so the spanwise loading panel is a placeholder."
        )
    if not plots.get("fm_convergence"):
        notes.append("No optimisation history was provided, so the FM convergence plot is a placeholder.")

    fm = performance.figure_of_merit
    blunt = design.is_blunt()
    parts: list[str] = []

    parts.append(
        "<header><h1>HoverBlade-FM — hover rotor blade report</h1>"
        f"<p class='sub'>Design <b>{escape_text(design.label)}</b> · {SECTION_COUNT} radial stations · "
        f"blunt-trailing-edge hard constraint</p>"
        "<div class='kpis'>"
        f"<div class='kpi'><span>Figure of Merit</span><b>{format_number(fm, 4)}</b></div>"
        f"<div class='kpi'><span>Thrust target</span><b>{format_number(performance.thrust_target_n, 2, unit='N')}</b></div>"
        f"<div class='kpi'><span>Radius</span><b>{format_number(design.radius_m, 3, unit='m')}</b></div>"
        f"<div class='kpi'><span>Required t<sub>TE</sub></span><b>{format_number(design.te_min_m * 1000.0, 3, unit='mm')}</b></div>"
        f"<div class='kpi'><span>Smallest t<sub>TE</sub></span><b>{format_number(design.min_te_thickness() * 1000.0, 3, unit='mm')}</b> "
        f"{_badge(blunt, yes='BLUNT', no='SHARP')}</div>"
        f"<div class='kpi'><span>Power / thrust</span><b>{format_number(performance.figure_power, 4, unit='W/N')}</b></div>"
        "</div></header>"
    )

    if notes:
        parts.append("<div class='note'>" + "<br>".join(escape_text(n) for n in notes) + "</div>")

    inputs = _rows(
        [
            ("Thrust (input)", format_number(performance.thrust_target_n, 3, unit="N")),
            ("Radius (input)", format_number(design.radius_m, 4, unit="m")),
            ("Minimum trailing-edge thickness (input)", format_number(design.te_min_m, 5, unit="m")),
        ]
    )
    resolved_rows = [
        (label, format_number(cfg.get(key), digits, unit=unit))
        for label, key, unit, digits in _RESOLVED_ROWS
        if key in cfg
    ]
    stations = cfg.get("r_stations") or [section.r_R for section in design.sections]
    stations_text = ", ".join(f"{s:.3f}" for s in stations if _is_number(s)) or "n/a"
    resolved_rows.append(("Radial stations r/R", escape_text(stations_text)))
    parts.append(
        "<h2>1 · Inputs and automatically resolved parameters</h2>"
        f"<table class='data'><caption>The three required numbers</caption><tbody>{inputs}</tbody></table>"
        + (
            f"<table class='data'><caption>Resolved configuration</caption><tbody>{_rows(resolved_rows)}</tbody></table>"
            if resolved_rows
            else "<p class='note'>No resolved configuration was recorded in the provenance payload.</p>"
        )
    )
    reasons = [str(r) for r in (cfg.get("auto_reasons") or cfg.get("reasons") or [])]
    if reasons:
        parts.append(
            "<h3>Why these values were chosen</h3><ul class='reasons'>"
            + "".join(f"<li>{escape_text(r)}</li>" for r in reasons)
            + "</ul>"
        )

    perf_rows = _rows(
        [
            ("Figure of Merit FM = P_ideal / P_total", format_number(performance.figure_of_merit, 4)),
            ("Ideal induced power P_ideal", format_number(performance.power_ideal_w, 2, unit="W")),
            ("Induced power P_induced", format_number(performance.power_induced_w, 2, unit="W")),
            ("Profile power P_profile", format_number(performance.power_profile_w, 2, unit="W")),
            ("Swirl power P_swirl", format_number(performance.power_swirl_w, 2, unit="W")),
            ("Miscellaneous power P_misc", format_number(performance.power_misc_w, 2, unit="W")),
            ("Total power P_total", format_number(performance.power_actual_w, 2, unit="W")),
            ("Shaft torque Q", format_number(performance.torque_nm, 3, unit="N·m")),
            (
                "Thrust target / calculated",
                f"{format_number(performance.thrust_target_n, 2)} / {format_number(performance.thrust_calc_n, 2)} N",
            ),
            ("Thrust error", format_number(performance.thrust_error * 100.0, 3, unit="%")),
            ("Rotor speed", format_number(performance.rpm, 2, unit="RPM")),
            ("Tip Mach", format_number(performance.tip_mach, 4)),
            ("Solidity", format_number(performance.solidity, 4)),
            ("Blade count", format_number(performance.blade_count, 0)),
            ("Collective trim", format_number(design.collective_trim_deg, 2, unit="deg")),
            ("Tip loss factor", format_number(performance.tip_loss_factor, 4)),
            ("Power loading P/T", format_number(performance.figure_power, 4, unit="W/N")),
        ]
    )
    constraint_rows: list[tuple[str, str]] = []
    if constraints is not None:
        constraint_rows = [
            ("Feasible overall", _badge(bool(constraints.feasible))),
            ("Trailing-edge violation", format_number(constraints.te_violation_m, 6, unit="m")),
            ("Trailing-edge angle check", _badge(bool(constraints.te_included_angle_ok))),
            ("Geometry validity", _badge(bool(constraints.geometry_valid))),
            ("Smoothness penalty", format_number(constraints.smoothness_penalty, 6)),
        ]
    parts.append(
        "<h2>2 · Hover performance</h2>"
        f"<table class='data'><tbody>{perf_rows}</tbody></table>"
        + (
            f"<h3>Constraint report</h3><table class='data'><tbody>{_rows(constraint_rows)}</tbody></table>"
            if constraint_rows
            else ""
        )
    )

    parts.append("<h2>3 · Blunt trailing-edge proof</h2>" + proof_html)

    headers = [
        "#",
        "r/R",
        "r [m]",
        "chord [m]",
        "twist [deg]",
        "t/c",
        "camber [m]",
        "t_TE [m]",
        "TE angle [deg]",
        "Re",
        "M",
        "c_l",
        "c_d",
        "dT/dr [N/m]",
        "alpha_eff [deg]",
    ]
    body = [
        [
            escape_text(s.station_id),
            format_number(s.r_R, 3),
            format_number(s.r_m, 4),
            format_number(s.chord_m, 4),
            format_number(s.twist_deg, 2),
            format_number(s.thickness_to_chord, 4),
            format_number(s.camber_m, 5),
            format_number(s.te_thickness_m, 5),
            format_number(s.te_included_angle_deg, 2),
            _sci(s.reynolds),
            format_number(s.mach, 3),
            format_number(s.cl_design, 3),
            format_number(s.cd_design, 5),
            format_number(s.loading_n_m, 2),
            format_number(s.alpha_effective_deg, 2),
        ]
        for s in design.sections
    ]
    parts.append("<h2>4 · Radial station data</h2>" + _table("", headers, body, css="stations"))

    parts.append("<h2>5 · Plots</h2><div class='grid'>")
    for key, caption in PLOT_CAPTIONS:
        parts.append(_image_block(key, caption, plots))
    parts.append("</div>")
    interactive = plots.get("interactive")
    if interactive is not None and Path(interactive).is_file():
        href = escape_text(_relative_link(Path(interactive), target.parent))
        parts.append(
            f"<p class='note'>A rotating 3D view of the same geometry ships alongside this report as "
            f"<a href='{href}'>{href}</a> (offline, no external scripts).</p>"
        )

    warnings = [str(w) for w in (cfg.get("warnings") or [])]
    if constraints is not None:
        warnings += [f"geometry: {i}" for i in getattr(constraints, "geometry_issues", ())]
    if not blunt:
        warnings.append(
            "At least one station violates the blunt trailing-edge hard constraint — this design is not manufacturable."
        )
    parts.append(
        "<h2>6 · Warnings</h2>"
        + (
            f"<ul class='warnings'>{''.join(f'<li>{escape_text(w)}</li>' for w in warnings)}</ul>"
            if warnings
            else "<p>No warnings recorded.</p>"
        )
    )

    footer_rows = _rows(
        [
            ("Program version", escape_text(prov.get("program_version", "n/a"))),
            ("Git commit", escape_text(prov.get("git_commit", "n/a"))),
            ("Input hash", escape_text(prov.get("input_hash", "n/a"))),
            ("Random seed", escape_text(prov.get("random_seed", "n/a"))),
            ("Timestamp (UTC)", escape_text(prov.get("timestamp", "n/a"))),
            (
                "Python / NumPy / SciPy",
                f"{escape_text(prov.get('python', 'n/a'))} / {escape_text(prov.get('numpy', 'n/a'))} / {escape_text(prov.get('scipy', 'n/a'))}",
            ),
            (
                "Optimisation settings",
                escape_text(json.dumps(prov.get("optimization_settings", {}), sort_keys=True)),
            ),
        ]
    )
    parts.append(
        "<footer><h2>7 · Provenance</h2>"
        f"<table class='data'><tbody>{footer_rows}</tbody></table>"
        + (
            f"<details><summary>Resolved configuration JSON</summary><pre>{escape_text(json.dumps(cfg, indent=2, sort_keys=True, default=str))}</pre></details>"
            if cfg
            else "<p class='note'>No provenance payload was supplied with this run.</p>"
        )
        + "<p>Generated by HoverBlade-FM — hover-only blade design with a blunt trailing-edge hard constraint.</p></footer>"
    )

    document = (
        "<!DOCTYPE html>\n<html lang='en'>\n<head>\n<meta charset='utf-8'>\n"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>\n"
        "<title>HoverBlade-FM report</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n" + "\n".join(parts) + "\n</body>\n</html>\n"
    )

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(document, encoding="utf-8")
    return target


__all__ = ["PLOT_CAPTIONS", "escape_text", "format_number", "render_report"]
