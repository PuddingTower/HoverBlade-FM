"""Dependency-free interactive 3D blade viewer (plan section 12.2 / task T068).

``blade.html`` embeds the lofted section polylines as JSON and draws them with a small
hand-written canvas renderer: drag to rotate, wheel to zoom, wireframe/solid toggle,
orthographic/perspective switch, a station selector and a trailing-edge colour ramp.
No script, font or stylesheet is fetched, so the file works fully offline.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from hoverblade_fm.models import SECTION_COUNT, AirfoilPoints, Design, Performance
from hoverblade_fm.viz.theme import ramp_color

#: Points kept per surface after decimation (plan section 12.2 payload budget).
POINTS_PER_SURFACE = 40
ROUND_DIGITS = 4

_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--ink:#1f2430;--muted:#6b7280;--line:#dfe3ea;--panel:#f7f8fa;--accent:#b83232;--ok:#2f855a}
*{box-sizing:border-box}
body{margin:0;padding:16px;background:#fff;color:var(--ink);
 font-family:"Segoe UI",DejaVu Sans,Verdana,sans-serif;font-size:14px;line-height:1.45}
h1{font-size:19px;margin:0 0 6px}
.bar{display:flex;flex-wrap:wrap;gap:14px;align-items:center;margin:0 0 12px;padding:8px 12px;
 background:var(--panel);border:1px solid var(--line);border-radius:8px}
.bar b{font-variant-numeric:tabular-nums}
.wrap{display:flex;flex-wrap:wrap;gap:16px}
.stage{flex:1 1 520px;min-width:320px}
canvas{display:block;width:100%;height:auto;background:#fff;border:1px solid var(--line);border-radius:8px;cursor:grab}
canvas.drag{cursor:grabbing}
.side{flex:0 1 300px;min-width:250px}
button,select{font:inherit;color:var(--ink);background:#fff;border:1px solid var(--line);
 border-radius:6px;padding:5px 10px;margin:2px 4px 2px 0;cursor:pointer}
button.on{background:var(--ink);color:#fff;border-color:var(--ink)}
label{display:block;margin:10px 0 2px;font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
dl{margin:10px 0 0;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--panel)}
dl div{display:flex;justify-content:space-between;gap:10px;padding:2px 0}
dt{color:var(--muted)}dd{margin:0;font-variant-numeric:tabular-nums}
.ramp{height:12px;border:1px solid var(--line);border-radius:6px;margin-top:4px}
.hint{color:var(--muted);font-size:12px;margin-top:8px}
.accent{color:var(--accent)}
footer{margin-top:18px;color:var(--muted);font-size:12px}
</style>
</head>
<body>
<h1>__TITLE__</h1>
<div class="bar">__HEADER__</div>
<div class="wrap">
  <div class="stage">
    <canvas id="view" width="900" height="640" aria-label="Interactive 3D blade view"></canvas>
    <p class="hint">Drag to rotate &middot; wheel to zoom &middot; sections are coloured by trailing-edge thickness
    &middot; red segments mark the blunt trailing edge.</p>
  </div>
  <div class="side">
    <div>
      <button id="mode" type="button">Solid</button>
      <button id="proj" type="button">Perspective</button>
      <button id="reset" type="button">Reset view</button>
    </div>
    <label for="station">Station</label>
    <select id="station"></select>
    <label>Trailing-edge thickness</label>
    <div class="ramp" id="ramp"></div>
    <div id="rampLabels" class="hint"></div>
    <dl id="readout"></dl>
  </div>
</div>
<footer>__FOOTER__</footer>
<script id="blade-data" type="application/json">__DATA__</script>
<script>
(function(){
"use strict";
var DATA = JSON.parse(document.getElementById("blade-data").textContent);
var SEC = DATA.sec, NS = SEC.length, NP = SEC[0].up.length;
var canvas = document.getElementById("view"), ctx = canvas.getContext("2d");
var DEFAULTS = {yaw:-0.62, pitch:0.30, zoom:1, solid:true, persp:false, sel:0};
var view = {yaw:DEFAULTS.yaw, pitch:DEFAULTS.pitch, zoom:DEFAULTS.zoom, solid:DEFAULTS.solid, persp:DEFAULTS.persp, sel:0};

var centre=[0,0,0], ext=1e-6;
(function(){
  var sx=0, sy=0, sz=0, n=0, i, j, p;
  for(i=0;i<NS;i++){ for(j=0;j<NP;j++){ p=SEC[i].up[j]; sx+=p[0]; sy+=p[1]; sz+=p[2]; n++; }
                     for(j=0;j<NP;j++){ p=SEC[i].lo[j]; sx+=p[0]; sy+=p[1]; sz+=p[2]; n++; } }
  centre=[sx/n, sy/n, sz/n];
  for(i=0;i<NS;i++){ for(j=0;j<NP;j++){ p=SEC[i].up[j];
      ext=Math.max(ext, Math.abs(p[0]-centre[0]), Math.abs(p[1]-centre[1]), Math.abs(p[2]-centre[2])); } }
})();

function hexRGB(h){ return [parseInt(h.slice(1,3),16), parseInt(h.slice(3,5),16), parseInt(h.slice(5,7),16)]; }
SEC.forEach(function(s){ s.rgb = hexRGB(s.col); });

function rot(p){
  var dx=p[0]-centre[0], dy=p[1]-centre[1], dz=p[2]-centre[2];
  var cy=Math.cos(view.yaw), sy=Math.sin(view.yaw);
  var x1=dx*cy+dz*sy, z1=-dx*sy+dz*cy;
  var cp=Math.cos(view.pitch), sp=Math.sin(view.pitch);
  return [x1, dy*cp - z1*sp, dy*sp + z1*cp];
}
function scale(){ return 0.42*Math.min(canvas.width, canvas.height)/ext*view.zoom; }
function toScreen(v){
  var s=scale(), k=1;
  if(view.persp){ var f=4*ext; k=f/Math.max(1e-3, f-v[2]); }
  return [canvas.width*0.5 + v[0]*s*k, canvas.height*0.5 - v[1]*s*k, v[2]];
}
function shade(rgb, i){
  return "rgb(" + Math.round(Math.min(255,rgb[0]*i)) + "," + Math.round(Math.min(255,rgb[1]*i)) +
         "," + Math.round(Math.min(255,rgb[2]*i)) + ")";
}
function normal(a,b,c){
  var u=[b[0]-a[0],b[1]-a[1],b[2]-a[2]], w=[c[0]-a[0],c[1]-a[1],c[2]-a[2]];
  var nx=u[1]*w[2]-u[2]*w[1], ny=u[2]*w[0]-u[0]*w[2], nz=u[0]*w[1]-u[1]*w[0];
  var len=Math.sqrt(nx*nx+ny*ny+nz*nz) || 1;
  return [nx/len, ny/len, nz/len];
}
var LIGHT=(function(){ var l=[0.36,0.48,0.80], n=Math.sqrt(0.36*0.36+0.48*0.48+0.8*0.8); return [l[0]/n,l[1]/n,l[2]/n]; })();

function draw(){
  ctx.setTransform(1,0,0,1,0,0);
  ctx.clearRect(0,0,canvas.width,canvas.height);
  var R=[], S=[], i, j;
  for(i=0;i<NS;i++){
    var up=[], lo=[];
    for(j=0;j<NP;j++){ up.push(rot(SEC[i].up[j])); lo.push(rot(SEC[i].lo[j])); }
    R.push({up:up, lo:lo});
    S.push({up:up.map(toScreen), lo:lo.map(toScreen)});
  }
  if(view.solid){
    var faces=[];
    for(i=0;i<NS-1;i++){
      for(j=0;j<NP-1;j++){
        faces.push(mkFace(i,j,"up",R,S));
        faces.push(mkFace(i,j,"lo",R,S));
      }
      faces.push(teFace(i,R,S));
    }
    faces.sort(function(a,b){ return a.d-b.d; });
    for(i=0;i<faces.length;i++){ paintFace(faces[i]); }
  } else {
    ctx.lineWidth=1.1;
    for(i=0;i<NS;i++){
      stroke(S[i].up, SEC[i].col, 1.1);
      stroke(S[i].lo, SEC[i].col, 1.1);
    }
    ctx.globalAlpha=0.55;
    for(j=0;j<NP;j+=4){
      ctx.beginPath();
      for(i=0;i<NS;i++){ var p=S[i].up[j]; if(i===0){ctx.moveTo(p[0],p[1]);}else{ctx.lineTo(p[0],p[1]);} }
      ctx.strokeStyle="#6b7280"; ctx.stroke();
      ctx.beginPath();
      for(i=0;i<NS;i++){ var q=S[i].lo[j]; if(i===0){ctx.moveTo(q[0],q[1]);}else{ctx.lineTo(q[0],q[1]);} }
      ctx.stroke();
    }
    ctx.globalAlpha=1;
  }
  for(i=0;i<NS;i++){
    var a=S[i].lo[NP-1], b=S[i].up[NP-1];
    ctx.beginPath(); ctx.moveTo(a[0],a[1]); ctx.lineTo(b[0],b[1]);
    ctx.strokeStyle = i===view.sel ? "#b83232" : "rgba(184,50,50,0.55)";
    ctx.lineWidth = i===view.sel ? 3.2 : 1.8;
    ctx.stroke();
  }
  var sel=S[view.sel];
  ctx.beginPath();
  for(j=0;j<NP;j++){ var u=sel.up[j]; if(j===0){ctx.moveTo(u[0],u[1]);}else{ctx.lineTo(u[0],u[1]);} }
  for(j=NP-1;j>=0;j--){ var l=sel.lo[j]; ctx.lineTo(l[0],l[1]); }
  ctx.closePath();
  ctx.strokeStyle="#1f2430"; ctx.lineWidth=2.2; ctx.stroke();
  readout();
}

function mkFace(i,j,key,R,S){
  var A=R[i][key], B=S[i][key], C=R[i+1][key], D=S[i+1][key];
  var pts=[[B[j][0],B[j][1]],[B[j+1][0],B[j+1][1]],[D[j+1][0],D[j+1][1]],[D[j][0],D[j][1]]];
  var n=normal(A[j], A[j+1], C[j+1]);
  return {pts:pts, d:(A[j][2]+A[j+1][2]+C[j+1][2]+C[j][2])/4, rgb:SEC[i].rgb,
          i:Math.abs(n[0]*LIGHT[0]+n[1]*LIGHT[1]+n[2]*LIGHT[2])};
}
function teFace(i,R,S){
  var A=R[i].up[NP-1], B=R[i].lo[NP-1], C=R[i+1].lo[NP-1], D=R[i+1].up[NP-1];
  var n=normal(A,B,C);
  return {pts:[[S[i].up[NP-1][0],S[i].up[NP-1][1]],[S[i].lo[NP-1][0],S[i].lo[NP-1][1]],
               [S[i+1].lo[NP-1][0],S[i+1].lo[NP-1][1]],[S[i+1].up[NP-1][0],S[i+1].up[NP-1][1]]],
          d:(A[2]+B[2]+C[2]+D[2])/4, rgb:SEC[i].rgb,
          i:Math.abs(n[0]*LIGHT[0]+n[1]*LIGHT[1]+n[2]*LIGHT[2])};
}
function paintFace(f){
  ctx.beginPath();
  ctx.moveTo(f.pts[0][0], f.pts[0][1]);
  for(var k=1;k<f.pts.length;k++){ ctx.lineTo(f.pts[k][0], f.pts[k][1]); }
  ctx.closePath();
  ctx.fillStyle = shade(f.rgb, 0.38+0.62*f.i);
  ctx.fill();
  ctx.strokeStyle = shade(f.rgb, 0.30+0.45*f.i);
  ctx.lineWidth = 0.5;
  ctx.stroke();
}
function stroke(pts, color, w){
  ctx.beginPath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  for(var k=1;k<pts.length;k++){ ctx.lineTo(pts[k][0], pts[k][1]); }
  ctx.strokeStyle=color; ctx.lineWidth=w; ctx.stroke();
}
function readout(){
  var s=SEC[view.sel], el=document.getElementById("readout");
  el.innerHTML =
    row("Station", "#"+s.i+" &middot; r/R "+fmt(s.rR,3)) +
    row("Radius", fmt(s.r,4)+" m") +
    row("Chord", fmt(s.c,4)+" m") +
    row("Twist", fmt(s.tw,2)+" deg") +
    row("t<sub>TE</sub>", fmt(s.te*1000,3)+" mm") +
    row("t<sub>TE</sub> required", fmt(DATA.teMin*1000,3)+" mm") +
    row("t/c", fmt(s.tc,4)) +
    row("c<sub>l</sub> design", fmt(s.cl,3)) +
    row("c<sub>d</sub> design", fmt(s.cd,5));
}
function row(k,v){ return "<div><dt>"+k+"</dt><dd>"+v+"</dd></div>"; }
function fmt(v,d){ return (v===null || typeof v!=="number" || !isFinite(v)) ? "n/a" : v.toFixed(d); }

var select=document.getElementById("station");
SEC.forEach(function(s, idx){
  var o=document.createElement("option");
  o.value=String(idx);
  o.textContent="#"+s.i+"  r/R="+fmt(s.rR,3)+"  c="+fmt(s.c*1000,1)+" mm  t_TE="+fmt(s.te*1000,2)+" mm";
  select.appendChild(o);
});
select.addEventListener("change", function(){ view.sel=parseInt(select.value,10)||0; draw(); });

var colors=SEC.map(function(s){ return s.col; });
document.getElementById("ramp").style.background="linear-gradient(90deg,"+colors.join(",")+")";
document.getElementById("rampLabels").textContent =
  "min "+fmt(DATA.teMinRamp*1000,3)+" mm  \u2192  max "+fmt(DATA.teMaxRamp*1000,3)+" mm";

function setModeButtons(){
  document.getElementById("mode").textContent = view.solid ? "Solid" : "Wireframe";
  document.getElementById("mode").classList.toggle("on", view.solid);
  document.getElementById("proj").textContent = view.persp ? "Perspective" : "Orthographic";
  document.getElementById("proj").classList.toggle("on", view.persp);
}
document.getElementById("mode").addEventListener("click", function(){ view.solid=!view.solid; setModeButtons(); draw(); });
document.getElementById("proj").addEventListener("click", function(){ view.persp=!view.persp; setModeButtons(); draw(); });
document.getElementById("reset").addEventListener("click", function(){
  view.yaw=DEFAULTS.yaw; view.pitch=DEFAULTS.pitch; view.zoom=DEFAULTS.zoom; draw();
});

var drag=null;
canvas.addEventListener("pointerdown", function(e){ drag=[e.clientX,e.clientY]; canvas.classList.add("drag"); canvas.setPointerCapture(e.pointerId); });
canvas.addEventListener("pointermove", function(e){
  if(!drag){ return; }
  view.yaw += (e.clientX-drag[0])*0.008;
  view.pitch += (e.clientY-drag[1])*0.008;
  view.pitch = Math.max(-1.55, Math.min(1.55, view.pitch));
  drag=[e.clientX,e.clientY];
  draw();
});
function endDrag(e){ if(drag){ drag=null; canvas.classList.remove("drag"); if(canvas.releasePointerCapture){ try{canvas.releasePointerCapture(e.pointerId);}catch(err){} } } }
canvas.addEventListener("pointerup", endDrag);
canvas.addEventListener("pointercancel", endDrag);
canvas.addEventListener("wheel", function(e){
  e.preventDefault();
  view.zoom *= (e.deltaY<0) ? 1.08 : 1/1.08;
  view.zoom = Math.max(0.25, Math.min(8, view.zoom));
  draw();
}, {passive:false});

function resize(){
  var box=canvas.getBoundingClientRect();
  var dpr=Math.max(1, Math.min(2, window.devicePixelRatio||1));
  var w=Math.max(320, Math.round(box.width*dpr)), h=Math.round(w*0.72);
  if(canvas.width!==w || canvas.height!==h){ canvas.width=w; canvas.height=h; draw(); }
}
window.addEventListener("resize", resize);

setModeButtons();
resize();
draw();
})();
</script>
</body>
</html>
"""


def _round(value: float) -> float | None:
    number = float(value)
    if not math.isfinite(number):
        return None
    return round(number, ROUND_DIGITS)


def _coord(value: float) -> float:
    """Geometry slots must stay numeric JSON; non-finite input collapses to the origin."""
    number = float(value)
    return round(number, ROUND_DIGITS) if math.isfinite(number) else 0.0


def _decimated_points(section: Any, count: int = POINTS_PER_SURFACE) -> AirfoilPoints:
    """Resample both surfaces onto a shared cosine chord grid of ``count`` points."""
    x_lower, y_lower, x_upper, y_upper = section.points.surface_pairs()
    j = np.arange(count, dtype=float)
    xq = 0.5 * (1.0 - np.cos(np.pi * j / (count - 1)))
    lower = np.interp(xq, x_lower, y_lower)
    upper = np.interp(xq, x_upper, y_upper)
    return AirfoilPoints(
        x=np.concatenate([xq[::-1], xq]),
        y=np.concatenate([lower[::-1], upper]),
        n_per_surface=count,
    )


def _as_triplets(frame: np.ndarray) -> list[list[float]]:
    return [[_coord(v) for v in row] for row in frame]


def _surface_lists(
    section: Any, count: int = POINTS_PER_SURFACE
) -> tuple[list[list[float]], list[list[float]]]:
    """Lower and upper surface polylines in the blade frame, LE to TE, in metres."""
    frame = _decimated_points(section, count).in_blade_frame(section.chord_m, section.twist_deg, section.r_m)
    return _as_triplets(frame[:count][::-1]), _as_triplets(frame[count:])


def blade_payload(design: Design, performance: Performance | None = None) -> dict[str, Any]:
    """Compact JSON-ready payload: header summary plus decimated section polylines."""
    te_values = np.array([s.points.te_thickness * s.chord_m for s in design.sections], dtype=float)
    low, high = float(np.min(te_values)), float(np.max(te_values))
    sections: list[dict[str, Any]] = []
    for index, section in enumerate(design.sections):
        lower, upper = _surface_lists(section)
        te_measured = float(te_values[index])
        sections.append(
            {
                "i": section.station_id,
                "rR": _round(section.r_R),
                "r": _round(section.r_m),
                "c": _round(section.chord_m),
                "tw": _round(section.twist_deg),
                "te": _round(te_measured),
                "tc": _round(section.thickness_to_chord),
                "cl": _round(section.cl_design),
                "cd": _round(section.cd_design),
                "col": ramp_color(te_measured, low, high),
                "lo": lower,
                "up": upper,
            }
        )
    return {
        "stations": SECTION_COUNT,
        "radius": _round(design.radius_m),
        "teMin": _round(design.te_min_m),
        "teMinRamp": _round(low),
        "teMaxRamp": _round(high),
        "fm": _round(performance.figure_of_merit) if performance is not None else None,
        "thrust": _round(performance.thrust_calc_n) if performance is not None else None,
        "rpm": _round(design.rpm),
        "blades": int(design.blade_count),
        "sec": sections,
    }


def _header_html(design: Design, performance: Performance | None) -> str:
    from hoverblade_fm.viz.report import escape_text, format_number

    cells = [
        (
            "Figure of Merit",
            format_number(performance.figure_of_merit, 4) if performance is not None else "n/a",
        ),
        (
            "Thrust",
            format_number(performance.thrust_calc_n, 2, unit="N") if performance is not None else "n/a",
        ),
        ("Radius", format_number(design.radius_m, 3, unit="m")),
        ("RPM", format_number(design.rpm, 1)),
        ("Blades", format_number(design.blade_count, 0)),
        ("Required t<sub>TE</sub>", format_number(design.te_min_m * 1000.0, 3, unit="mm")),
        ("Smallest t<sub>TE</sub>", format_number(design.min_te_thickness() * 1000.0, 3, unit="mm")),
    ]
    badge = (
        escape_text("blunt-TE constraint satisfied")
        if design.is_blunt()
        else escape_text("blunt-TE constraint violated")
    )
    return "".join(f"<span><b>{value}</b> {escape_text(name)}</span>" for name, value in cells) + (
        f"<span class='accent'><b>{badge}</b></span>"
    )


def build_interactive_html(
    design: Design,
    performance: Performance | None = None,
    out_path: str | Path = Path("output/interactive/blade.html"),
) -> Path:
    """Write the offline 3D viewer and return its path (``blade.html`` inside a directory)."""
    target = Path(out_path)
    if target.suffix.lower() not in (".html", ".htm"):
        target = target / "blade.html"
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(blade_payload(design, performance), separators=(",", ":"), ensure_ascii=True)
    document = (
        _TEMPLATE.replace("__TITLE__", "HoverBlade-FM — interactive blade viewer")
        .replace("__HEADER__", _header_html(design, performance))
        .replace(
            "__FOOTER__",
            f"10 stations, {POINTS_PER_SURFACE} points per surface, {len(payload) // 1024} KB of geometry",
        )
        .replace("__DATA__", payload.replace("</", "<\\/"))
    )
    target.write_text(document, encoding="utf-8")
    _try_plotly(design, performance, target)
    return target


def _try_plotly(design: Design, performance: Performance | None, target: Path) -> Path | None:
    """Optional bonus file; never required and never allowed to break ``blade.html``."""
    try:
        import plotly.graph_objects as go
    except Exception:
        return None
    try:
        figure = go.Figure()
        for section in blade_payload(design, performance)["sec"]:
            lo = [p for p in section["lo"] if p[0] is not None]
            up = [p for p in section["up"] if p[0] is not None]
            pts = lo[::-1] + up
            figure.add_trace(
                go.Scatter3d(
                    x=[p[0] for p in pts],
                    y=[p[1] for p in pts],
                    z=[p[2] for p in pts],
                    mode="lines",
                    name=f"r/R={section['rR']}",
                    line={"color": section["col"], "width": 4},
                )
            )
        figure.update_layout(title="HoverBlade-FM blade", margin={"l": 0, "r": 0, "t": 40, "b": 0})
        bonus = target.parent / "blade_plotly.html"
        bonus.write_text(figure.to_html(include_plotlyjs="inline", full_html=True), encoding="utf-8")
        return bonus
    except Exception:
        return None


__all__ = [
    "POINTS_PER_SURFACE",
    "blade_payload",
    "build_interactive_html",
]
