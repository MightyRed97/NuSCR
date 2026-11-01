"""Renders START_HERE.html, the entry page of the package: what each folder holds and what to open first, the
virtual system (the browser engine 4_code/js/rps_engine.js, embedded, running BP_A..BP_D and CP_A scan by scan), each
trip function followed from its requirements to its design, code and models, and the verification results.  The
page is self-contained: it loads nothing from the network or from other files.

    python -B tools/render_start_here.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_spec as B                                                     # noqa: E402
from design_model import load_design                                      # noqa: E402
from paths import (BROWSER_REPORT, C_REPORT, DESIGN_REPORT, JS_ENGINE,      # noqa: E402
                   MODELICA_REPORT, REQUIREMENTS_REPORT, ROOT, SCENARIOS_DIR, START_HERE)

FOLDERS = [
    ("1_requirements", "Requirements",
     "What the software must do, in NuSCR: inputs, outputs, function tables (SDT), state machines (FSM, TTS), "
     "grouped in function overview diagrams (FOD), and 43 system requirements.",
     ("1_requirements/NuSCR_RPS_SRS.md", "Requirements specification"),
     [("1_requirements/README.md", "What is here"), ("1_requirements/rps_nuscr_spec.json", "Machine-readable specification"),
      ("1_requirements/tables/requirements.csv", "Requirements table"),
      ("1_requirements/tables/trace_requirements_to_nuscr.csv", "Requirements to NuSCR nodes")]),
    ("2_design", "Design",
     "How the software does it: 12 reusable function blocks with explicit state, wired into 89 instances, every "
     "instance proved equivalent to the NuSCR definition it implements.",
     ("2_design/RPS_Software_Design.md", "Software design specification"),
     [("2_design/README.md", "What is here"), ("2_design/rps_design.json", "Machine-readable design"),
      ("2_design/tables/instances.csv", "Block instances"),
      ("2_design/tables/trace_nuscr_to_design.csv", "NuSCR nodes to design instances")]),
    ("3_case_tool", "CASE tool models",
     "The design as models for modelling tools: OpenModelica (open and simulate it in OMEdit, free) and SCADE Suite "
     "(licensed).",
     ("3_case_tool/openmodelica/README.md", "OpenModelica model: how to open and simulate"),
     [("3_case_tool/README.md", "What is here"), ("3_case_tool/openmodelica/RPS.mo", "OpenModelica package RPS"),
      ("3_case_tool/scade/README.md", "SCADE Suite model"), ("3_case_tool/scade/RPS_Design.scade", "Scade 6 model")]),
    ("4_code", "Code",
     "C code generated from the design, with a command-line simulator, and the JavaScript engine that runs the "
     "virtual system on this page.",
     ("4_code/README.md", "How to build and run the code"),
     [("4_code/src/rps_bp.c", "Bistable processor (C)"), ("4_code/src/rps_cp.c", "Coincidence processor (C)"),
      ("4_code/src/rps_blocks.c", "Function blocks (C)"), ("4_code/app/rps_sim.c", "Command-line simulator"),
      ("4_code/js/rps_engine.js", "Browser engine (JavaScript)")]),
    ("5_simulation", "Simulation",
     "Scenarios to replay: a pressure excursion, a cooldown with manual setpoint resets, and a loss of flow seen by "
     "all four channels.",
     ("5_simulation/README.md", "Scenarios and how to replay them"),
     [("5_simulation/scenarios/pzr_pressure_excursion.csv", "Pressure excursion"),
      ("5_simulation/scenarios/cooldown_manual_reset.csv", "Cooldown with manual resets"),
      ("5_simulation/scenarios/loss_of_flow_4ch.csv", "Four-channel loss of flow")]),
    ("6_verification", "Verification",
     "The evidence: static checks of the requirements, proofs of the design, scan-by-scan comparisons of the C code, "
     "the browser engine and the OpenModelica model with the requirements, and the test suite.",
     ("6_verification/README.md", "What was verified and how"),
     [("6_verification/requirements_check_report.json", "Requirements checks"),
      ("6_verification/design_verification_report.json", "Design proofs"),
      ("6_verification/c_verification_report.json", "C code against the requirements"),
      ("6_verification/openmodelica_verification_report.json", "OpenModelica model against the requirements"),
      ("6_verification/tests", "Test suite")]),
]

SCENARIO_TITLES = {
    "pzr_pressure_excursion": "Pressurizer pressure excursion (all four channels)",
    "cooldown_manual_reset": "Cooldown with manual setpoint resets (all four channels)",
    "loss_of_flow_4ch": "Loss of reactor coolant flow (all four channels)",
}


def _check_links():
    for _, _, _, first, files in FOLDERS:
        for path, _ in [first] + files:
            if not (ROOT / path).exists():
                raise SystemExit(f"START_HERE.html would link to {path}, which does not exist")


def trip_data(design):
    spec = design.spec
    bp = spec.modules["BP"]
    c = spec.constants
    reqs = {r["id"]: r for r in spec.raw["requirements"]}
    fod = {m["name"]: {g["name"]: g for g in m["fod"]["nodes"]} for m in spec.raw["modules"]}
    insts = {m["name"]: m["instances"] for m in design.raw["modules"]}
    scen_cols = {}
    for p in sorted(SCENARIOS_DIR.glob("*.csv")):
        header = next(ln for ln in p.read_text(encoding="utf-8").splitlines() if ln and not ln.startswith("#"))
        scen_cols[p.stem] = header.split(",")[1:]
    out = []
    for t in B.TRIPS:
        x, logic = t["id"], t["logic"]
        rising = B.LOGIC[logic]["rising"]
        g, cg = fod["BP"][f"g_{x}"], fod["CP"][f"g_{x}_Coincidence"]
        ids = []
        for node in [g] + g["nodes"] + [cg] + cg["nodes"]:
            ids += [i for i in node.get("traces", []) if i not in ids]
        sig = f"f_{t['signal']}" if t.get("signal") else None
        if logic in ("VSRT", "RLVSFT_OB", "MRVSFT_OB"):
            margin = float(c[f"k_{x}_Pretrip_Margin"])
            kind, trip, pre = "variable", {"signal": f"h_{x}_Trip_SP"}, {"signal": f"h_{x}_Trip_SP",
                                                                         "offset": -margin if rising else margin}
        elif logic == "DT_OB":
            kind, trip, pre = "digital", None, None
        else:
            kind, trip, pre = "fixed", {"const": float(c[f"k_{x}_Trip_SP"])}, {"const": float(c[f"k_{x}_Pretrip_SP"])}

        def opt(name):
            return name if name in bp.variables else None

        ob = next((i for i in insts["BP"] if i.get("group") == f"g_{x}" and i["block"] == "FB_OPERATING_BYPASS"), None)
        perm = ob["inputs"].get("PERM") if ob else None              # the permissive the design wires to the bypass

        touched = [s for s, cols in scen_cols.items()
                   if any(col.split(".")[-1] in {sig, f"f_{x}_OB_Ini", f"f_{x}_SP_Reset", f"f_{x}_Byp_Sw"}
                          for col in cols)]
        out.append({
            "id": x, "title": t["title"], "logic": logic, "logicType": g.get("logic_type", ""),
            "category": g.get("category", ""), "kind": kind, "rising": rising, "signal": sig,
            "unit": bp.variables[sig].get("unit", "") if sig else "", "trip": trip, "pretrip": pre,
            "tripSignal": f"th_{x}_Trip", "pretripSignal": f"th_{x}_Pretrip", "coinc": f"f_{x}_Coinc",
            "obButton": opt(f"f_{x}_OB_Ini"), "obStatus": opt(f"h_{x}_OB_Sta"), "obPerm": opt(perm) if perm else None,
            "resetButton": opt(f"f_{x}_SP_Reset"), "bypassSwitch": f"f_{x}_Byp_Sw",
            "digitalInputs": [n for n in (f"f_{x}_Trip_In", f"f_{x}_Pretrip_In") if n in bp.variables],
            "requirements": [{"id": i, "text": reqs[i]["text"]} for i in ids if i in reqs],
            "fod": {"no": g["no"], "name": g["name"], "title": g["title"], "description": g.get("description", ""),
                    "nodes": [{"no": n["no"], "name": n["name"], "type": n.get("type", "")} for n in g["nodes"]]},
            "cpFod": {"no": cg["no"], "name": cg["name"], "title": cg["title"],
                      "nodes": [{"no": n["no"], "name": n["name"], "type": n.get("type", "")} for n in cg["nodes"]]},
            "instances": [{"name": i["name"], "block": i["block"], "params": i["params"], "implements": i["implements"]}
                          for i in insts["BP"] if i.get("group") == f"g_{x}"],
            "cpInstances": [{"name": i["name"], "block": i["block"], "implements": i["implements"]}
                            for i in insts["CP"] if i.get("group") == f"g_{x}_Coincidence"],
            "scenarios": touched,
        })
    return out


def signal_data(design):
    bp = design.spec.modules["BP"]
    out = []
    for s, info in B.SIGNALS.items():
        name = f"f_{s}"
        lo, hi = float(info["vmin"]), float(info["vmax"])
        span = hi - lo
        out.append({"name": name, "description": bp.variables[name]["description"], "unit": info.get("unit", ""),
                    "min": lo - 0.05 * span if s != "NPWR_LOG" else lo / 10, "max": hi + 0.05 * span,
                    "nominal": float(info["nominal"]), "log": s == "NPWR_LOG",
                    "validMin": float(design.spec.constants[f"k_{s}_Valid_Min"]),
                    "validMax": float(design.spec.constants[f"k_{s}_Valid_Max"])})
    return out


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def verification_data():
    rows = []
    r = _load(REQUIREMENTS_REPORT)
    if r:
        s = r.get("stats", {})
        rows.append({"what": "Requirements: static analysis", "tool": "tools/nuscr_check.py",
                     "how": f"completeness ({s.get('completeness proofs', '?')} tables), disjointness "
                            f"({s.get('disjointness proofs', '?')} rule pairs), determinism "
                            f"({s.get('determinism proofs', '?')} transition pairs), fail-safe, invariants, "
                            f"{s.get('assertions', '?')} constant assertions",
                     "result": f"{r['errors']} errors, {r['warnings']} warnings", "ok": r["errors"] == 0})
    r = _load(DESIGN_REPORT)
    if r:
        pc, tc = r["proof_coverage"], r["transition_coverage"]
        rows.append({"what": "Design against the requirements", "tool": "tools/design_verify.py",
                     "how": "block equivalence by exhaustive exploration, one proof per instance, back-to-back runs",
                     "result": f"{pc['proved']} of {pc['definitions']} NuSCR definitions proved; "
                               f"{tc['fired']} of {tc['transitions']} transitions exercised",
                     "ok": bool(r["passed"])})
    for path, what, tool in ((C_REPORT, "C code against the requirements", "tools/verify_c.py"),
                             (BROWSER_REPORT, "Browser engine against the requirements", "tools/verify_js.py")):
        r = _load(path)
        if r:
            tc = r["transition_coverage"]
            rows.append({"what": what, "tool": tool,
                         "how": f"{len(r['runs'])} runs, every signal in every scan, bit for bit against the "
                                f"double-arithmetic reference ({r.get('compiler') or r.get('runtime')})",
                         "result": f"{r['comparisons']:,} comparisons, "
                                   f"{'all equal to the reference' if r['passed'] else 'FAILURES'}; "
                                   f"{r['rounding_effects']} rounding effects (differences from the exact model that the "
                                   f"reference shows too); {tc['fired']} of {tc['transitions']} transitions exercised",
                         "ok": bool(r["passed"])})
    r = _load(MODELICA_REPORT)
    if r:
        tc = r.get("transition_coverage")
        cover = f"; {tc['fired']} of {tc['transitions']} transitions exercised" if tc and tc["fired"] else ""
        rows.append({"what": "OpenModelica model against the requirements", "tool": "tools/run_openmodelica.py",
                     "how": f"{len(r['runs'])} runs simulated by {r['tool']}, every signal in every scan: flags "
                            "exactly, reals within 1e-9 (the result file is decimal)",
                     "result": f"{r['comparisons']:,} comparisons, {'all passing' if r['passed'] else 'FAILURES'}; "
                               f"{r['rounding_effects']} rounding effects that the double-arithmetic reference shows "
                               f"too{cover}",
                     "ok": bool(r["passed"])})
    else:
        rows.append({"what": "OpenModelica model against the requirements", "tool": "tools/run_openmodelica.py",
                     "how": "needs OpenModelica", "result": "not run yet", "ok": None})
    rows.append({"what": "SCADE Suite model", "tool": "tools/export_scade.py",
                 "how": "checked with a local parser and simulator of the exported Scade subset",
                 "result": "not yet loaded in SCADE Suite (no licence)", "ok": None})
    return rows


def page_data(design):
    spec = design.spec
    return {
        "status": design.raw["document"]["status"],
        "scanMs": float(spec.constants["k_Scan_Period"]),
        "trips": trip_data(design), "signals": signal_data(design),
        "scenarios": {p.stem: {"title": SCENARIO_TITLES.get(p.stem, p.stem), "csv": p.read_text(encoding="utf-8")}
                      for p in sorted(SCENARIOS_DIR.glob("*.csv"))},
        "verification": verification_data(),
        "folders": [{"folder": f, "title": t, "purpose": p, "first": first, "files": files}
                    for f, t, p, first, files in FOLDERS],
    }


def page_text(design):
    _check_links()
    data = json.dumps(page_data(design), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    engine = JS_ENGINE.read_text(encoding="utf-8")
    if "</script" in engine.lower():
        raise SystemExit("the engine contains </script")
    return TEMPLATE.replace("__DATA__", data).replace("__ENGINE__", engine)


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NuSCR RPS: Start Here</title>
<!-- Generated by tools/render_start_here.py - do not edit by hand.  Self-contained: loads nothing else. -->
<style>
:root {
  --bg: #f6f7f9; --panel: #ffffff; --text: #1b1f24; --muted: #59636e; --border: #dde2e8; --soft: #eef1f5;
  --accent: #2456c4; --accent-soft: #e7eefc; --ok: #1d7d46; --ok-soft: #e3f4ea; --pre: #9a6200; --pre-soft: #fdf0d5;
  --trip: #c0262d; --trip-soft: #fbe3e4; --byp: #5146c9; --byp-soft: #ebe9fb; --warn: #8a5a00; --warn-soft: #fff6e0;
  --mono: ui-monospace, "Cascadia Mono", Consolas, "SF Mono", Menlo, monospace;
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f1216; --panel: #171b21; --text: #e5e8ec; --muted: #9aa4af; --border: #2b323b; --soft: #1e242c;
    --accent: #7ea2ff; --accent-soft: #1d2945; --ok: #4fc681; --ok-soft: #14301f; --pre: #f2b84b; --pre-soft: #352a12;
    --trip: #ff6b70; --trip-soft: #3a1719; --byp: #a39bff; --byp-soft: #25223f; --warn: #f2c35b; --warn-soft: #2f2611;
    color-scheme: dark;
  }
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body { margin: 0; background: var(--bg); color: var(--text); font: 15px/1.5 var(--sans); }
a { color: var(--accent); }
code, .mono { font-family: var(--mono); font-size: 0.92em; }
.wrap { max-width: 1240px; margin: 0 auto; padding: 0 16px; }
header.top { background: var(--panel); border-bottom: 1px solid var(--border); }
header.top .wrap { padding-top: 22px; padding-bottom: 14px; }
h1 { font-size: 1.6rem; margin: 0 0 4px; letter-spacing: -0.01em; }
h2 { font-size: 1.25rem; margin: 0 0 6px; }
h3 { font-size: 1.02rem; margin: 0 0 6px; }
.lede { color: var(--muted); margin: 0 0 14px; max-width: 900px; }
.banner { margin: 12px 0 10px; padding: 10px 14px; border-radius: 8px; background: var(--warn-soft); color: var(--warn);
  border: 1px solid color-mix(in srgb, var(--warn) 30%, transparent); font-weight: 600; font-size: 0.9rem; }
nav.toc { display: flex; flex-wrap: wrap; gap: 6px 16px; font-size: 0.92rem; }
nav.toc a { text-decoration: none; }
section { padding: 26px 0 8px; }
.panel { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }
.flow { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 10px; margin: 6px 0 4px; }
.flow .step { position: relative; background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; }
.flow .num { font: 700 0.8rem var(--mono); color: var(--accent); }
.flow .step b { display: block; margin: 2px 0; }
.flow .step span { color: var(--muted); font-size: 0.86rem; }
.cards { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.card h3 a { text-decoration: none; color: var(--text); }
.card .folder { font-family: var(--mono); color: var(--accent); font-size: 0.85rem; }
.card p { margin: 4px 0 8px; color: var(--muted); font-size: 0.9rem; }
.card .first { display: inline-block; margin-bottom: 8px; padding: 4px 10px; border-radius: 6px; background: var(--accent-soft); font-weight: 600; font-size: 0.88rem; text-decoration: none; }
.card ul { margin: 0; padding-left: 18px; font-size: 0.88rem; }
.card li { overflow-wrap: anywhere; }
.card li .kv { display: block; }
.toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 8px 0 10px; }
.toolbar .sep { width: 1px; height: 24px; background: var(--border); margin: 0 4px; }
button, select, input[type=number] { font: inherit; font-size: 0.9rem; color: var(--text); background: var(--panel);
  border: 1px solid var(--border); border-radius: 7px; padding: 5px 10px; }
button { cursor: pointer; }
button:hover { border-color: var(--accent); }
select { max-width: 100%; }
label { display: inline-flex; align-items: center; gap: 6px; max-width: 100%; }
.scroll-x { overflow-x: auto; }
button.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
@media (prefers-color-scheme: dark) { button.primary { color: #0f1216; } }
button:focus-visible, select:focus-visible, input:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.clock { font-family: var(--mono); color: var(--muted); margin-left: auto; }
.rt { display: flex; align-items: center; gap: 12px; padding: 10px 14px; border-radius: 10px; font-weight: 700; margin-bottom: 10px; border: 1px solid var(--border); }
.rt.ok { background: var(--ok-soft); color: var(--ok); }
.rt.trip { background: var(--trip-soft); color: var(--trip); }
.rt small { font-weight: 400; color: var(--muted); }
.simgrid { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr); gap: 12px; align-items: start; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 5px 6px; border-bottom: 1px solid var(--border); vertical-align: middle; }
th { font-size: 0.8rem; color: var(--muted); font-weight: 600; }
table.status td.fn { font-size: 0.88rem; }
.fnbtn { background: none; border: none; padding: 0; color: inherit; font: inherit; text-align: left; cursor: pointer; }
.fnbtn:hover { color: var(--accent); }
table.status tr.sel .fnbtn { font-weight: 700; color: var(--accent); }
td.st { text-align: center; font: 600 0.78rem var(--mono); border-radius: 5px; padding: 3px 2px; min-width: 58px; }
td.st.ok { color: var(--ok); } td.st.pre { color: var(--pre); background: var(--pre-soft); } td.st.trip { color: var(--trip); background: var(--trip-soft); }
.badge { display: inline-block; margin-left: 3px; padding: 0 4px; border-radius: 4px; font: 600 0.68rem var(--mono); color: var(--byp); background: var(--byp-soft); }
.tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border); margin: -4px 0 10px; }
.tabs button { border: none; border-bottom: 2px solid transparent; border-radius: 0; background: none; padding: 6px 10px; color: var(--muted); }
.tabs button[aria-selected=true] { color: var(--accent); border-bottom-color: var(--accent); font-weight: 600; }
.ctl { padding: 7px 0; border-bottom: 1px dashed var(--border); }
.ctl:last-child { border-bottom: none; }
.ctl .row1 { display: flex; justify-content: space-between; gap: 8px; font-size: 0.86rem; }
.ctl .row2 { display: flex; gap: 8px; align-items: center; }
.ctl input[type=range] { flex: 1; min-width: 0; accent-color: var(--accent); }
.ctl input[type=number] { width: 112px; padding: 3px 6px; }
.ctl .per { font: 0.74rem var(--mono); color: var(--muted); }
.seg { display: inline-flex; border: 1px solid var(--border); border-radius: 7px; overflow: hidden; }
.seg button { border: none; border-radius: 0; padding: 4px 10px; }
.seg button[aria-pressed=true] { background: var(--accent); color: #fff; }
@media (prefers-color-scheme: dark) { .seg button[aria-pressed=true] { color: #0f1216; } }
.hint { color: var(--muted); font-size: 0.84rem; margin: 6px 0; }
canvas { width: 100%; height: 260px; display: block; }
.legend { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 0.8rem; color: var(--muted); margin-top: 6px; }
.legend i { display: inline-block; width: 18px; height: 3px; vertical-align: middle; margin-right: 5px; border-radius: 2px; }
.log { max-height: 260px; overflow: auto; margin: 0; padding: 0; list-style: none; font-size: 0.84rem; }
.log li { padding: 3px 0; border-bottom: 1px solid var(--soft); }
.log li .c { font-family: var(--mono); color: var(--muted); margin-right: 6px; }
.log li.trip { color: var(--trip); } .log li.ok { color: var(--ok); } .log li.pre { color: var(--pre); }
.bottom { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr); gap: 12px; margin-top: 12px; }
.trace { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.trace .panel p, .trace .panel li { font-size: 0.88rem; }
.trace ul { padding-left: 18px; margin: 4px 0; }
.kv { font: 0.82rem var(--mono); color: var(--muted); }
.res-ok { color: var(--ok); font-weight: 600; } .res-bad { color: var(--trip); font-weight: 600; } .res-na { color: var(--muted); }
pre.cmd { background: var(--soft); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; overflow: auto; font: 0.84rem/1.55 var(--mono); margin: 6px 0 12px; }
footer { color: var(--muted); font-size: 0.82rem; padding: 26px 0 40px; }
.chk { display: inline-flex; align-items: center; gap: 4px; font-size: 0.82rem; }
@media (max-width: 1000px) {
  .simgrid, .bottom, .trace { grid-template-columns: minmax(0, 1fr); }
  .cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .flow { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (max-width: 640px) {
  .cards, .flow { grid-template-columns: minmax(0, 1fr); }
  .clock { margin-left: 0; width: 100%; }
  td.st { min-width: 0; font-size: 0.7rem; }
  #scenario, #trace-sel { width: 100%; }
}
</style>
</head>
<body>
<header class="top"><div class="wrap">
  <h1>Notional four-channel reactor protection system</h1>
  <p class="lede">NuSCR requirements, a function-block design that implements them, the design as an OpenModelica model, C code generated from it, and a virtual system that runs the design on this page. Everything is checked against the requirements scan by scan.</p>
  <div class="banner" id="status"></div>
  <nav class="toc"><a href="#overview">How the package fits together</a><a href="#folders">Folders</a><a href="#sim">Virtual system</a><a href="#trace">Follow a trip function</a><a href="#verification">Verification</a><a href="#commands">Commands</a></nav>
</div></header>
<main class="wrap">
<section id="overview">
  <h2>How the package fits together</h2>
  <p class="lede">Read the folders in their numbered order. Each stage is generated from the one before by the scripts in <code>tools/</code> and checked against the requirements.</p>
  <div class="flow" id="flow"></div>
</section>
<section id="folders">
  <h2>Folders</h2>
  <p class="lede">What each folder holds and the one file to open first. Markdown files read best in VS Code's preview; the links below open them as text in the browser.</p>
  <div class="cards" id="cards"></div>
</section>
<section id="sim">
  <h2>Virtual system</h2>
  <p class="lede">The four bistable processors (channels A&ndash;D) and the coincidence processor, executing the design scan by scan (50&nbsp;ms per scan) in this page. Change an input, press a button or replay a scenario, and watch the channel trips, the 2-out-of-4 votes and the reactor trip initiation.</p>
  <div class="toolbar">
    <button class="primary" id="run">Run</button>
    <button id="step">Step one scan</button>
    <label>Speed <select id="speed"><option value="1">real time</option><option value="10">10&times;</option><option value="100">100&times;</option></select></label>
    <span class="sep"></span>
    <button id="atpower" title="Power-up, then the operator places the high log power trip in operating bypass in every channel">Power-up at power</button>
    <button id="cold" title="Every block in its initial state, nominal inputs, no operator action">Cold power-up</button>
    <span class="sep"></span>
    <label>Scenario <select id="scenario"></select></label>
    <span class="clock" id="clock"></span>
  </div>
  <div class="rt" id="rt"></div>
  <div class="simgrid">
    <div class="panel scroll-x">
      <table class="status" id="table"><thead><tr><th>Trip function</th><th>A</th><th>B</th><th>C</th><th>D</th><th>2&nbsp;of&nbsp;4</th></tr></thead><tbody></tbody></table>
      <p class="hint">OK = normal, PRE = pre-trip alarm, TRIP = channel trip. <span class="badge">OB</span> operating bypass active, <span class="badge">BYP</span> trip channel bypass switch on. Select a row to see it in detail.</p>
    </div>
    <div class="panel">
      <div class="tabs" role="tablist">
        <button role="tab" id="t-fn" aria-controls="tab-fn" aria-selected="true" data-tab="tab-fn">Selected function</button>
        <button role="tab" id="t-proc" aria-controls="tab-proc" aria-selected="false" data-tab="tab-proc">Process inputs</button>
        <button role="tab" id="t-diag" aria-controls="tab-diag" aria-selected="false" data-tab="tab-diag">Diagnostics</button>
      </div>
      <div id="tab-fn" role="tabpanel" aria-labelledby="t-fn"></div>
      <div id="tab-proc" role="tabpanel" aria-labelledby="t-proc" hidden></div>
      <div id="tab-diag" role="tabpanel" aria-labelledby="t-diag" hidden></div>
    </div>
  </div>
  <div class="bottom">
    <div class="panel">
      <h3 id="chart-title"></h3>
      <div class="toolbar" style="margin:4px 0 6px"><span class="hint" style="margin:0">Channel</span><span class="seg" id="chart-ch"></span></div>
      <canvas id="chart" aria-label="Recent values of the selected trip function"></canvas>
      <div class="legend" id="legend"></div>
    </div>
    <div class="panel"><h3>Events</h3><ul class="log" id="log"></ul></div>
  </div>
</section>
<section id="trace">
  <h2>Follow a trip function</h2>
  <p class="lede">From the system requirements to the NuSCR specification, the design, the code and the models. <label>Trip function <select id="trace-sel"></select></label></p>
  <div class="trace" id="trace-body"></div>
</section>
<section id="verification">
  <h2>Verification</h2>
  <p class="lede">Read from the reports in <code>6_verification/</code> when this page was generated. These results show that each stage matches the synthetic requirements; they do not show that the requirements are right for any plant.</p>
  <div class="panel scroll-x"><table id="ver"><thead><tr><th>What</th><th>How</th><th>Result</th><th>Script</th></tr></thead><tbody></tbody></table></div>
</section>
<section id="commands">
  <h2>Commands</h2>
  <p class="lede">Run from the package folder. Python 3.10 or later; no other packages.</p>
  <div class="panel">
    <h3>Regenerate and check everything</h3>
    <pre class="cmd">python -B tools/build_all.py</pre>
    <h3>Build the C simulator and run it</h3>
    <pre class="cmd">python -B tools/build_c.py
4_code\build\rps_sim.exe run SYSTEM 5_simulation\scenarios\loss_of_flow_4ch.csv trace.csv
4_code\build\rps_sim.exe interactive SYSTEM</pre>
    <h3>Simulate the OpenModelica model</h3>
    <pre class="cmd">OMEdit: File &gt; Open Model/Library File &gt; 3_case_tool\openmodelica\RPS.mo, then RPS.Scenarios &gt; Simulate
python -B tools/run_openmodelica.py</pre>
    <h3>Compare the code with the requirements</h3>
    <pre class="cmd">python -B tools/verify_c.py
python -B tools/verify_js.py
python -B -m unittest discover -s 6_verification/tests</pre>
  </div>
</section>
<footer>Generated by <code>tools/render_start_here.py</code> from the design and the verification reports; do not edit by hand.</footer>
</main>
<script type="application/json" id="page-data">__DATA__</script>
<script>
__ENGINE__
</script>
<script>
(function () {
  "use strict";
  const D = JSON.parse(document.getElementById("page-data").textContent);
  const META = RPS.META;
  const CH = ["A", "B", "C", "D"];
  const T = D.trips;
  const LABEL = ["OK", "PRE", "TRIP"], CLS = ["ok", "pre", "trip"];
  const HIST = 600;
  const sim = new RPS.Simulator("SYSTEM");
  let hist = [], logItems = [], prev = null, presses = [], lastPressed = new Set(), scenario = null;
  let running = false, speed = 1, acc = 0, lastTs = 0, dirty = true;
  let selected = "PZR_P_HI", target = "BP", chartCh = "A";
  const $ = (id) => document.getElementById(id);
  const hasOwn = (o, k) => Object.prototype.hasOwnProperty.call(o, k);

  function h(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") e.className = v;
      else if (k === "text") e.textContent = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else if (v !== null && v !== undefined && v !== false) e.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid !== null && kid !== undefined) e.append(kid.nodeType ? kid : String(kid));
    return e;
  }
  const fmt = (v) => (typeof v !== "number" ? String(v) : Math.abs(v) >= 1e5 || (v !== 0 && Math.abs(v) < 1e-3) ? v.toExponential(3) : String(Number(v.toPrecision(6))));
  const bp = (c) => "BP_" + c;
  const get = (name) => sim.get(name);

  // ---- static content ---------------------------------------------------------------------------------------
  $("status").textContent = "STATUS: " + D.status + ". Every setpoint, delay and range is an illustrative placeholder.";
  const flow = $("flow");
  D.folders.forEach((f, k) => flow.append(h("div", { class: "step" }, h("span", { class: "num", text: String(k + 1) }), h("b", { text: f.title }), h("span", { text: f.purpose.split(":")[0].split(".")[0] }))));
  const cards = $("cards");
  for (const f of D.folders) {
    cards.append(h("div", { class: "panel card" },
      h("div", { class: "folder", text: f.folder + "/" }), h("h3", {}, h("a", { href: f.first[0], text: f.title })),
      h("p", { text: f.purpose }), h("a", { class: "first", href: f.first[0], text: "Open first: " + f.first[1] }),
      h("ul", {}, f.files.map(([p, l]) => h("li", {}, h("a", { href: p, text: l }), " ", h("span", { class: "kv", text: p }))))));
  }

  // ---- simulation -------------------------------------------------------------------------------------------
  function stateOf(c, t) {
    if (get(bp(c) + "." + t.tripSignal) === false) return 2;
    if (get(bp(c) + "." + t.pretripSignal) === false) return 1;
    return 0;
  }
  function setpoints(c, t) {
    if (!t.trip) return [null, null];
    const tsp = t.trip.const !== undefined ? t.trip.const : get(bp(c) + "." + t.trip.signal);
    const psp = t.pretrip.const !== undefined ? t.pretrip.const : get(bp(c) + "." + t.pretrip.signal) + t.pretrip.offset;
    return [tsp, psp];
  }
  function snapshot() {
    const s = { cycle: sim.cycle, st: {}, co: {}, v: {}, sp: {}, rt: get("CP_A.f_RT_Init") };
    for (const t of T) {
      s.st[t.id] = CH.map((c) => stateOf(c, t));
      s.co[t.id] = get("CP_A." + t.coinc) ? 0 : 2;
      if (t.signal) {
        s.v[t.id] = CH.map((c) => get(bp(c) + "." + t.signal));
        s.sp[t.id] = CH.map((c) => setpoints(c, t));
      }
    }
    return s;
  }
  function note(text, cls, cycle) {
    logItems.unshift({ cycle: cycle === undefined ? sim.cycle : cycle, text, cls });
    if (logItems.length > 300) logItems.length = 300;
  }
  function diff(a, b) {
    for (const t of T) {
      CH.forEach((c, k) => {
        if (a.st[t.id][k] !== b.st[t.id][k]) note("Channel " + c + " · " + t.title + ": " + LABEL[a.st[t.id][k]] + " → " + LABEL[b.st[t.id][k]], CLS[b.st[t.id][k]]);
      });
      if (a.co[t.id] !== b.co[t.id]) note("2 of 4 · " + t.title + ": " + (b.co[t.id] ? "coincidence TRIP" : "coincidence cleared"), b.co[t.id] ? "trip" : "ok");
    }
    if (a.rt !== b.rt) note(b.rt ? "Reactor trip initiation reset (all coincidences normal)" : "REACTOR TRIP INITIATED", b.rt ? "ok" : "trip");
  }
  // One note per button pressed in the scan just executed, naming the channels whose button was 1 in it.
  function logPresses(now) {
    const byName = new Map();
    for (const p of now) {
      const dot = p.indexOf("."), name = p.slice(dot + 1);
      if (!byName.has(name)) byName.set(name, new Set());
      byName.get(name).add(p.slice(3, dot));
    }
    for (const [name, chs] of byName) {
      const list = [...chs].sort();
      note("Operator: " + name + " pressed in " + (list.length === CH.length ? "every channel" : (list.length === 1 ? "channel " : "channels ") + list.join(", ")) + ".", "");
    }
  }
  function scan() {
    if (scenario) {
      const ev = scenario.events.get(sim.cycle + 1);
      if (ev) for (const [col, val] of ev) sim.set(col, val);
    }
    // a press is 1 for exactly one scan; a button that was 1 in the previous scan first stays one scan at 0
    const now = presses.filter((p) => !lastPressed.has(p)), later = presses.filter((p) => lastPressed.has(p));
    for (const p of now) sim.set(p, 1);
    sim.step();
    for (const p of now) sim.set(p, 0);
    lastPressed = new Set(now);
    presses = later;
    logPresses(now);
    const s = snapshot();
    if (prev) diff(prev, s);
    prev = s;
    hist.push(s);
    if (hist.length > HIST) hist.shift();
    if (scenario && sim.cycle >= scenario.last) {
      note("Scenario finished: " + scenario.title + ". Inputs keep their last values.", "");
      scenario = null;
      $("scenario").value = "";
      setRunning(false);
    }
    dirty = true;
  }
  function powerUp(atPower) {
    sim.reset();
    hist = []; logItems = []; prev = null; presses = []; lastPressed = new Set(); scenario = null;
    $("scenario").value = "";
    note("Power-up: every block in its initial state, every input at its nominal value. Trips start tripped and clear in the first error-free scan without a trip condition.", "", 0);
    scan();
    if (atPower) {
      note("The high log power trip trips at power by design, so the operator places it in operating bypass in every channel.", "", 1);
      presses = CH.map((c) => bp(c) + ".f_LOGP_HI_OB_Ini");
      scan();
      scan();
    }
    syncControls();
  }
  function parseScenario(csv) {
    const events = new Map();
    let header = null, last = 0;
    for (const line of csv.split(/\r?\n/)) {
      if (/^\s*(#|$)/.test(line)) continue;
      const cells = line.split(",").map((x) => x.trim());
      if (!header) { header = cells; continue; }
      const c = Number(cells[0]);
      last = Math.max(last, c);
      for (let j = 1; j < cells.length; j++) {
        if (cells[j] === "") continue;
        if (!events.has(c)) events.set(c, []);
        events.get(c).push([header[j], cells[j]]);
      }
    }
    return { events, last };
  }
  function loadScenario(name) {
    if (!name || !hasOwn(D.scenarios, name)) return;
    const sc = D.scenarios[name];
    sim.reset();
    hist = []; logItems = []; prev = null; presses = []; lastPressed = new Set();
    scenario = Object.assign({ title: sc.title }, parseScenario(sc.csv));
    note("Scenario: " + sc.title + " (5_simulation/scenarios/" + name + ".csv), from a cold power-up. Columns without a channel apply to all four channels.", "", 0);
    scan();
    syncControls();
    setRunning(true);
  }
  function setRunning(on) {
    running = on;
    $("run").textContent = on ? "Pause" : "Run";
    $("run").classList.toggle("primary", !on);
  }
  function frame(ts) {
    if (running) {
      if (lastTs) acc += ((ts - lastTs) / D.scanMs) * speed;
      lastTs = ts;
      let n = Math.min(Math.floor(acc), 2000);
      acc -= Math.floor(acc);
      while (n-- > 0 && running) scan();
    } else {
      lastTs = 0;
      acc = 0;
    }
    if (dirty) { render(); dirty = false; }
    requestAnimationFrame(frame);
  }

  // ---- status table -----------------------------------------------------------------------------------------
  const tbody = $("table").tBodies[0];
  const cells = {};
  for (const t of T) {
    const tr = h("tr", { "data-id": t.id });
    tr.append(h("td", { class: "fn" }, h("button", { class: "fnbtn", type: "button", text: t.title, title: t.category + " · " + t.logicType, onclick: () => { selected = t.id; buildFnTab(); dirty = true; } })));
    cells[t.id] = CH.map(() => { const td = h("td", { class: "st" }); tr.append(td); return td; });
    cells[t.id].push(tr.appendChild(h("td", { class: "st" })));
    tbody.append(tr);
  }
  function renderTable() {
    const s = prev;
    for (const t of T) {
      const row = cells[t.id];
      CH.forEach((c, k) => {
        const st = s.st[t.id][k];
        const ob = t.obStatus && get(bp(c) + "." + t.obStatus);
        const byp = get(bp(c) + "." + t.bypassSwitch);
        row[k].className = "st " + CLS[st];
        row[k].replaceChildren(LABEL[st], ob ? h("span", { class: "badge", text: "OB" }) : "", byp ? h("span", { class: "badge", text: "BYP" }) : "");
      });
      const co = s.co[t.id];
      row[4].className = "st " + CLS[co];
      row[4].textContent = co ? "TRIP" : "OK";
      row[0].parentNode.classList.toggle("sel", t.id === selected);
    }
  }

  // ---- control tabs -----------------------------------------------------------------------------------------
  for (const b of document.querySelectorAll(".tabs button")) {
    b.addEventListener("click", () => {
      for (const o of document.querySelectorAll(".tabs button")) { o.setAttribute("aria-selected", o === b); $(o.dataset.tab).hidden = o !== b; }
    });
  }
  function targetSeg(onChange) {
    const seg = h("span", { class: "seg target-seg", role: "group", "aria-label": "Channels" });
    for (const [val, lab] of [["BP", "All"], ["BP_A", "A"], ["BP_B", "B"], ["BP_C", "C"], ["BP_D", "D"]]) {
      seg.append(h("button", { type: "button", "aria-pressed": String(target === val), "data-target": val, text: lab, onclick: () => {
        target = val;
        for (const b of document.querySelectorAll(".target-seg button")) b.setAttribute("aria-pressed", String(b.dataset.target === target));
        onChange();
        syncControls();
        dirty = true;
      } }));
    }
    return seg;
  }
  function targets() { return target === "BP" ? CH.map(bp) : [target]; }
  function press(name) {
    presses.push(...targets().map((b) => b + "." + name));          // logged by the scan that applies it
    if (!running) { scan(); while (presses.length) scan(); }
    dirty = true;
  }

  function buildFnTab() {
    const t = T.find((x) => x.id === selected);
    const box = $("tab-fn");
    box.replaceChildren();
    box.append(h("h3", { text: t.title }), h("p", { class: "hint", text: t.category + " · NuSCR logic type " + t.logicType + (t.signal ? " · process signal " + t.signal + " [" + t.unit + "]" : "") }));
    const head = h("tr", {}, h("th", { text: "Ch." }), t.signal ? [h("th", { text: "Value" }), h("th", { text: "Pre-trip SP" }), h("th", { text: "Trip SP" })] : [], h("th", { text: "State" }), t.obStatus ? h("th", { text: "Op. bypass" }) : null, h("th", { text: "Channel bypass" }));
    const tb = h("tbody");
    fnRows = CH.map((c) => {
      const cellsRow = { v: h("td", { class: "mono" }), p: h("td", { class: "mono" }), s: h("td", { class: "mono" }), st: h("td", { class: "st" }), ob: h("td", { class: "mono" }) };
      const chk = h("input", { type: "checkbox", "aria-label": "Trip channel bypass switch, channel " + c, onchange: (e) => { sim.set(bp(c) + "." + t.bypassSwitch, e.target.checked ? 1 : 0); note("Maintenance: trip channel bypass switch of " + t.title + " " + (e.target.checked ? "ON" : "off") + " in channel " + c + ".", ""); dirty = true; } });
      cellsRow.chk = chk;
      tb.append(h("tr", {}, h("td", { text: c }), t.signal ? [cellsRow.v, cellsRow.p, cellsRow.s] : [], cellsRow.st, t.obStatus ? cellsRow.ob : null, h("td", {}, h("label", { class: "chk" }, chk, "BYP"))));
      return cellsRow;
    });
    box.append(h("div", { class: "scroll-x" }, h("table", {}, h("thead", {}, head), tb)));
    const acts = h("div", { class: "toolbar" }, h("span", { class: "hint", style: "margin:0", text: "Buttons act on" }), targetSeg(buildFnTab));
    if (t.obButton) acts.append(h("button", { text: "Press operating-bypass button", onclick: () => press(t.obButton) }));
    if (t.resetButton) acts.append(h("button", { text: "Press setpoint reset", onclick: () => press(t.resetButton) }));
    box.append(acts);
    const why = [];
    if (t.obButton) why.push("An operating bypass needs a fresh press while its permissive holds" + (t.obPerm ? " (" + t.obPerm + ")" : "") + "; it is removed automatically when the permissive is lost.");
    if (t.resetButton) why.push("Each setpoint-reset press lowers the setpoint to the process value minus the band, once per press.");
    if (t.kind === "digital") why.push("Digital trip from the core protection calculator: set its trip or pre-trip demand in the Diagnostics tab.");
    why.push("The coincidence processor honours a trip channel bypass only when exactly one channel requests it; a failed link counts as a trip vote.");
    box.append(h("p", { class: "hint", text: why.join(" ") }));
    box.append(h("p", { class: "hint" }, h("a", { href: "#trace", onclick: () => { $("trace-sel").value = t.id; renderTrace(); }, text: "Requirements, design and code of this function ↓" })));
    renderFn();
    renderChart();
  }
  let fnRows = [];
  function renderFn() {
    const t = T.find((x) => x.id === selected);
    const s = prev;
    CH.forEach((c, k) => {
      const r = fnRows[k];
      if (!r) return;
      if (t.signal) {
        r.v.textContent = fmt(s.v[t.id][k]);
        const [tsp, psp] = s.sp[t.id][k];
        r.p.textContent = fmt(psp);
        r.s.textContent = fmt(tsp);
      }
      const st = s.st[t.id][k];
      r.st.className = "st " + CLS[st];
      r.st.textContent = LABEL[st];
      if (t.obStatus) r.ob.textContent = get(bp(c) + "." + t.obStatus) ? "ACTIVE" : (t.obPerm && get(bp(c) + "." + t.obPerm) ? "permitted" : "—");
      r.chk.checked = !!get(bp(c) + "." + t.bypassSwitch);
    });
  }

  let procCtl = [];
  function buildProcTab() {
    const box = $("tab-proc");
    box.replaceChildren(h("div", { class: "toolbar" }, h("span", { class: "hint", style: "margin:0", text: "Apply changes to" }), targetSeg(buildProcTab),
      h("button", { text: "Back to nominal", onclick: () => { for (const g of D.signals) for (const b of targets()) sim.set(b + "." + g.name, g.nominal); syncControls(); dirty = true; } })));
    procCtl = D.signals.map((g) => {
      const toSlider = (v) => (g.log ? Math.log10(Math.max(v, 1e-12)) : v);
      const fromSlider = (x) => (g.log ? Math.pow(10, x) : x);
      const lo = toSlider(g.min), hi = toSlider(g.max);
      const range = h("input", { type: "range", min: lo, max: hi, step: (hi - lo) / 1000, "aria-label": g.description });
      const num = h("input", { type: "number", step: "any", "aria-label": g.description + " value" });
      const per = h("div", { class: "per" });
      const set = (v) => { for (const b of targets()) sim.set(b + "." + g.name, v); dirty = true; syncControls(); };
      range.addEventListener("input", () => set(Number(fromSlider(Number(range.value)).toPrecision(6))));
      num.addEventListener("change", () => { const v = num.valueAsNumber; if (num.value.trim() === "" || !Number.isFinite(v)) { syncControls(); return; } set(v); });
      box.append(h("div", { class: "ctl" }, h("div", { class: "row1" }, h("span", { text: g.description }), h("span", { class: "kv", text: g.name + " [" + g.unit + "] valid " + fmt(g.validMin) + "…" + fmt(g.validMax) })), h("div", { class: "row2" }, range, num), per));
      return { g, range, num, per, toSlider };
    });
    box.append(h("p", { class: "hint", text: "A value outside its valid range makes the input invalid: every function that uses it as a process input trips without delay (fail-safe); a bypass permissive that uses it is lost, which removes that bypass." }));
    syncControls();
  }
  function syncControls() {
    for (const c of procCtl) {
      const ref = target === "BP" ? "BP_A" : target;
      const v = get(ref + "." + c.g.name);
      if (document.activeElement !== c.range) c.range.value = c.toSlider(v);
      if (document.activeElement !== c.num) c.num.value = fmt(v);
      c.per.textContent = CH.map((x) => x + " " + fmt(get(bp(x) + "." + c.g.name))).join("   ");
    }
    for (const d of diagCtl) d.el.checked = d.read();
  }

  let diagCtl = [];
  function buildDiagTab() {
    const box = $("tab-diag");
    box.replaceChildren();
    const cols = [["f_Module_Error", "Module error", false], ["f_Channel_Error", "Channel error", false], ["f_CPC_Link_Error", "CPC link error", false],
      ["f_LPD_HI_Trip_In", "CPC LPD trip demand", true], ["f_LPD_HI_Pretrip_In", "CPC LPD pre-trip demand", true], ["f_DNBR_LO_Trip_In", "CPC DNBR trip demand", true], ["f_DNBR_LO_Pretrip_In", "CPC DNBR pre-trip demand", true]];
    diagCtl = [];
    const tb = h("tbody");
    for (const c of CH) {
      const tr = h("tr", {}, h("td", { text: c }));
      for (const [name, label, demand] of cols) {
        const full = bp(c) + "." + name;
        const read = () => (demand ? !get(full) : !!get(full));
        const el = h("input", { type: "checkbox", "aria-label": label + ", channel " + c, onchange: (e) => { sim.set(full, demand ? (e.target.checked ? 0 : 1) : (e.target.checked ? 1 : 0)); note("Channel " + c + ": " + name + " = " + get(full) + ".", ""); dirty = true; } });
        diagCtl.push({ el, read });
        tr.append(h("td", {}, el));
      }
      const link = "CP_A.f_" + c + "_Link_Error";
      const el = h("input", { type: "checkbox", "aria-label": "Link from channel " + c + " to the CP failed", onchange: (e) => { sim.set(link, e.target.checked ? 1 : 0); note("Link from channel " + c + " to the CP: " + (e.target.checked ? "FAILED" : "restored") + ".", ""); dirty = true; } });
      diagCtl.push({ el, read: () => !!get(link) });
      tr.append(h("td", {}, el));
      tb.append(tr);
    }
    box.append(h("div", { class: "scroll-x" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", { text: "Ch." }), cols.map(([, lab]) => h("th", { text: lab })), h("th", { text: "Link to CP failed" }))), tb)));
    box.append(h("p", { class: "hint", text: "Module, channel and CPC link errors trip the affected functions at once; an operating bypass does not hide them, but a trip channel bypass (one channel at a time) still removes that channel from the vote. CPC demands come from the core protection calculator (digital trips). A failed link counts as a trip vote in the coincidence processor." }));
    syncControls();
  }

  // ---- chart ------------------------------------------------------------------------------------------------
  const seg = $("chart-ch");
  for (const c of CH) seg.append(h("button", { "aria-pressed": String(c === chartCh), text: c, onclick: () => { chartCh = c; for (const b of seg.children) b.setAttribute("aria-pressed", b.textContent === c); renderChart(); } }));
  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function renderChart() {
    const t = T.find((x) => x.id === selected);
    $("chart-title").textContent = t.title + (t.signal ? ": " + t.signal + " and setpoints" : ": channel states");
    const cv = $("chart"), dpr = window.devicePixelRatio || 1;
    const W = cv.clientWidth, H = cv.clientHeight;
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    const g = cv.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    const k = CH.indexOf(chartCh), n = hist.length;
    const strips = 5, sh = 9, top = 8, left = 54, right = 10;
    const plotH = t.signal ? H - top - strips * (sh + 3) - 26 : 0;
    const span = Math.max(n - 1, 40);                   // fills the width as the history grows, then scrolls
    const x = (i) => left + (i / span) * (W - left - right);
    g.font = "11px " + css("--mono"); g.fillStyle = css("--muted"); g.strokeStyle = css("--border"); g.lineWidth = 1;
    const leg = $("legend"); leg.replaceChildren();
    if (t.signal && n) {
      let lo = Infinity, hi = -Infinity;
      for (const s of hist) { for (const v of [s.v[t.id][k], ...s.sp[t.id][k]]) { if (v !== null && Number.isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } } }
      if (lo === hi) { lo -= 1; hi += 1; }
      const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
      const y = (v) => top + (1 - (v - lo) / (hi - lo)) * plotH;
      for (let j = 0; j <= 4; j++) {
        const v = lo + ((hi - lo) * j) / 4, yy = y(v);
        g.beginPath(); g.moveTo(left, yy); g.lineTo(W - right, yy); g.stroke();
        g.fillText(fmt(v), 2, yy + 4);
      }
      const series = [[css("--pre"), (s) => s.sp[t.id][k][1], [5, 4], "Pre-trip setpoint"], [css("--trip"), (s) => s.sp[t.id][k][0], [5, 4], "Trip setpoint"], [css("--accent"), (s) => s.v[t.id][k], [], t.signal + " (channel " + chartCh + ")"]];
      for (const [col, fn, dash, lab] of series) {
        g.strokeStyle = col; g.lineWidth = 2; g.setLineDash(dash); g.beginPath();
        hist.forEach((s, i) => { const yy = y(fn(s)); i ? g.lineTo(x(i), yy) : g.moveTo(x(i), yy); });
        g.stroke(); g.setLineDash([]);
        leg.append(h("span", {}, h("i", { style: "background:" + col }), lab));
      }
    }
    const y0 = top + plotH + (t.signal ? 18 : 4);
    const names = ["A", "B", "C", "D", "2oo4"];
    const colors = [css("--ok"), css("--pre"), css("--trip")];
    for (let r = 0; r < strips; r++) {
      const yy = y0 + r * (sh + 3);
      g.fillStyle = css("--muted"); g.fillText(names[r], 2, yy + sh - 1);
      hist.forEach((s, i) => {
        const st = r < 4 ? s.st[t.id][r] : s.co[t.id];
        g.fillStyle = colors[st];
        g.fillRect(x(i), yy, Math.max(1, x(i + 1) - x(i) + 0.5), sh);
      });
    }
    leg.append(h("span", { text: "Strips: green OK, amber pre-trip, red trip; last " + HIST + " scans (" + (HIST * D.scanMs / 1000) + " s)" }));
  }

  // ---- rendering --------------------------------------------------------------------------------------------
  function render() {
    if (!prev) return;
    $("clock").textContent = "cycle " + sim.cycle + " · t = " + (sim.cycle * D.scanMs / 1000).toFixed(3) + " s";
    const rt = $("rt");
    const tripped = prev.rt === false;
    rt.className = "rt " + (tripped ? "trip" : "ok");
    const coincs = T.filter((t) => prev.co[t.id]).map((t) => t.title);
    rt.replaceChildren(tripped ? "REACTOR TRIP INITIATED" : "Reactor trip not initiated", h("small", { text: tripped ? "coincidence trip: " + coincs.join(", ") : "every 2-out-of-4 coincidence is normal (CP_A.f_RT_Init = 1)" }));
    renderTable();
    renderFn();
    syncControls();
    renderChart();
    const log = $("log");
    log.replaceChildren(...logItems.slice(0, 150).map((e) => h("li", { class: e.cls }, h("span", { class: "c", text: "#" + e.cycle }), e.text)));
  }

  // ---- trace ------------------------------------------------------------------------------------------------
  const tsel = $("trace-sel");
  for (const t of T) tsel.append(h("option", { value: t.id, text: t.title }));
  tsel.value = selected;
  tsel.addEventListener("change", renderTrace);
  function renderTrace() {
    const t = T.find((x) => x.id === tsel.value);
    const box = $("trace-body");
    const panel = (title, ...kids) => h("div", { class: "panel" }, h("h3", { text: title }), ...kids);
    box.replaceChildren(
      panel("1 · Requirements", h("ul", {}, t.requirements.map((r) => h("li", {}, h("b", { class: "mono", text: r.id }), " " + r.text))),
        h("p", { class: "kv" }, h("a", { href: "1_requirements/tables/requirements.csv", text: "1_requirements/tables/requirements.csv" }))),
      panel("2 · NuSCR specification", h("p", {}, "FOD group " + t.fod.no + " ", h("code", { text: t.fod.name }), ": " + t.fod.title + "."),
        h("ul", {}, t.fod.nodes.map((nd) => h("li", {}, h("span", { class: "kv", text: nd.no + " " + nd.type + " " }), h("code", { text: nd.name })))),
        h("p", {}, "Coincidence: CP group " + t.cpFod.no + " ", h("code", { text: t.cpFod.name }), " (" + t.cpFod.nodes.length + " nodes)."),
        h("p", { class: "kv" }, h("a", { href: "1_requirements/NuSCR_RPS_SRS.md", text: "1_requirements/NuSCR_RPS_SRS.md" }), " · group " + t.fod.no)),
      panel("3 · Design", h("ul", {}, t.instances.map((i) => h("li", {}, h("code", { text: i.name }), " = ", h("code", { text: i.block }),
          Object.keys(i.params).length ? h("div", { class: "kv", text: Object.entries(i.params).map(([p, v]) => p + " = " + v).join(", ") }) : null,
          h("div", { class: "kv", text: "implements " + i.implements.join(", ") })))),
        h("p", {}, "Coincidence: ", t.cpInstances.map((i) => h("code", { text: i.name + " = " + i.block }))),
        h("p", { class: "kv" }, h("a", { href: "2_design/RPS_Software_Design.md", text: "2_design/RPS_Software_Design.md" }))),
      panel("4 · Code", h("p", {}, "C: one call per instance in ", h("a", { href: "4_code/src/rps_bp.c", text: "4_code/src/rps_bp.c" }), " under ", h("code", { text: "---- g_" + t.id + " ----" }), "; coincidence in ", h("a", { href: "4_code/src/rps_cp.c", text: "4_code/src/rps_cp.c" }), "."),
        h("p", {}, "JavaScript (this page): ", h("a", { href: "4_code/js/rps_engine.js", text: "4_code/js/rps_engine.js" }), ", function ", h("code", { text: "BP_step" }), ".")),
      panel("5 · CASE tool models", h("p", {}, "OpenModelica: ", h("code", { text: "RPS.BP_Groups." + t.fod.name }), " and ", h("code", { text: "RPS.CP_Groups." + t.cpFod.name }), " in ", h("a", { href: "3_case_tool/openmodelica/RPS.mo", text: "RPS.mo" }), "."),
        h("p", {}, "SCADE Suite: instances in operator ", h("code", { text: "RPS::BP" }), " of ", h("a", { href: "3_case_tool/scade/RPS_Design.scade", text: "RPS_Design.scade" }), ".")),
      panel("6 · Scenarios and checks", t.scenarios.length ? h("ul", {}, t.scenarios.map((s) => h("li", {}, h("a", { href: "5_simulation/scenarios/" + s + ".csv", text: s + ".csv" })))) : h("p", { class: "hint", text: t.kind === "digital" ? "No scenario file changes its inputs; the random runs of 6_verification exercise it." : "No scenario file changes its inputs; the random and boundary runs of 6_verification exercise it." }),
        h("p", { class: "hint", text: (t.kind === "digital" ? "Its trip and pre-trip demands come from the core protection calculator; " : "Every analog trip and pre-trip threshold and reset threshold of this function is held exactly in the boundary run; ") + "every transition of its state machines fires in the long back-to-back runs." })));
  }

  // ---- verification -----------------------------------------------------------------------------------------
  const vt = $("ver").tBodies[0];
  for (const r of D.verification) {
    vt.append(h("tr", {}, h("td", { text: r.what }), h("td", { class: "hint", text: r.how }), h("td", { class: r.ok === true ? "res-ok" : r.ok === false ? "res-bad" : "res-na", text: r.result }), h("td", {}, h("code", { text: r.tool }))));
  }

  // ---- wiring -----------------------------------------------------------------------------------------------
  const ssel = $("scenario");
  ssel.append(h("option", { value: "", text: "choose…" }));
  for (const [name, sc] of Object.entries(D.scenarios)) ssel.append(h("option", { value: name, text: sc.title }));
  ssel.addEventListener("change", () => loadScenario(ssel.value));
  $("run").addEventListener("click", () => setRunning(!running));
  $("step").addEventListener("click", () => { setRunning(false); scan(); });
  $("speed").addEventListener("change", (e) => { speed = Number(e.target.value); });
  $("atpower").addEventListener("click", () => powerUp(true));
  $("cold").addEventListener("click", () => powerUp(false));
  window.addEventListener("resize", () => { dirty = true; });
  // A link can start a scenario: START_HERE.html#scenario=loss_of_flow_4ch&fn=SG1_F_LO&scans=31&speed=10
  const args = new URLSearchParams(location.hash.slice(1));
  if (args.get("fn") && T.some((t) => t.id === args.get("fn"))) { selected = args.get("fn"); tsel.value = selected; }
  if (args.get("speed")) { speed = Number(args.get("speed")) || 1; $("speed").value = String(speed); }
  powerUp(true);
  buildFnTab(); buildProcTab(); buildDiagTab(); renderTrace();
  setRunning(true);
  if (args.get("scenario") && hasOwn(D.scenarios, args.get("scenario"))) {
    ssel.value = args.get("scenario");
    loadScenario(args.get("scenario"));
  }
  const n0 = Number(args.get("scans"));
  if (n0 > 0) {
    setRunning(false);
    for (let k = sim.cycle + 1; k < n0; k++) scan();
    dirty = true;
  }
  // For automated checks of this page (tools/check_page.js): the simulator and the operator's actions.
  window.RPS_PAGE = { sim, scan, press, setRunning, render, log: () => logItems.slice(), pending: () => presses.slice() };
  requestAnimationFrame(frame);
})();
</script>
</body>
</html>
"""


def main():
    design = load_design()
    text = page_text(design)
    START_HERE.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {START_HERE} ({len(text) // 1024} KB)")


if __name__ == "__main__":
    main()
