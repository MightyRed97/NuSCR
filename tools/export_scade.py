"""Exports the function-block design for Ansys SCADE Suite:

  3_case_tool/scade/RPS_Design.scade   Scade 6 textual model: constants, the 12 blocks as operators, and the BP and
                                  CP modules as root-able operators (insert into a SCADE Suite project with
                                  File View > Model Files > Insert Files)
  3_case_tool/scade/scenarios/*.sss    simulator scenario scripts: every input set in cycle 0 and afterwards only
                                  the inputs that change; SSM::check of every output in every cycle, with the
                                  expected values computed by the NuSCR requirements model

The generated text is checked locally by tools/scade_subset.py (parser, type checker and simulator for the
subset of Scade 6 emitted here) against the design model; SCADE Suite's own Checker is the final authority.

    python -B tools/export_scade.py
"""
from __future__ import annotations

import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from design_model import load_design                                    # noqa: E402
from export_common import (Translator, delay_const, literal, module_outputs,  # noqa: E402
                           signal_type, used_constants, value_type)
from nuscr_lang import parse                                            # noqa: E402
from nuscr_sim import to_fraction                                       # noqa: E402
from paths import SCADE_DIR as OUT_DIR                                  # noqa: E402
from vectors import (boundary_run, bp_scenario_run, random_bp_run,      # noqa: E402
                     random_cp_run, system_scenario_run)

PACKAGE = "RPS"


# ---------------------------------------------------------------------------
# Model file
# ---------------------------------------------------------------------------

def block_operator(name, b):
    ins = [(p["name"], value_type(p["type"])) for p in b["inputs"]] + \
          [(p["name"], value_type(p["type"])) for p in b["params"]]
    outs = [(p["name"], value_type(p["type"])) for p in b["outputs"]]
    state = {p["name"]: value_type(p["type"]) for p in b["state"]}
    locs = {p["name"]: value_type(p["type"]) for p in b["locals"]}
    types = dict(ins)
    types.update(dict(outs))
    types.update(state)
    types.update(locs)
    tr = Translator(types, state)
    kind = "node" if b["state"] else "function"
    lines = [f"  -- {b['description']}", f"  {kind} {name}"] + interface(ins, outs)
    decls = [f"    {n} : {t} last = {literal(Fraction(str(p['init'])), t)};"
             for p in b["state"] for n, t in [(p["name"], state[p["name"]])]]
    decls += [f"    {n} : {t};" for n, t in locs.items()]
    if decls:
        lines.append("  var")
        lines += decls
    lines.append("  let")
    for st in b["body"]:
        v, target = st["set"], types[st["set"]]
        if "expr" in st:
            rhs = tr.value(parse(st["expr"]), target)
        else:
            rhs = tr.value(parse(st["else"]), target)
            for guard, expr in reversed(st["cases"]):
                rhs = f"(if {tr.bool(parse(guard))} then {tr.value(parse(expr), target)} else {rhs})"
        lines.append(f"    {v} = {rhs};")
    lines += ["  tel", ""]
    return lines


def interface(ins, outs, width=100):
    """Parameter and return lists, wrapped so that no line is longer than about `width` characters."""
    def group(open_, params):
        if not params:
            return [open_ + ")"]
        lines, cur = [], open_
        for k, (n, t) in enumerate(params):
            item = f"{n} : {t}" + (";" if k < len(params) - 1 else ")")
            if len(cur) + len(item) + 1 > width and cur.strip() not in ("", open_.strip()):
                lines.append(cur.rstrip())
                cur = "      "
            cur += item + " "
        return lines + [cur.rstrip()]
    return group("    (", ins) + group("    returns (", outs)


def module_operator(design, mname, derived):
    spec = design.spec
    req = spec.modules[mname]
    raw_mod = next(m for m in design.raw["modules"] if m["name"] == mname)
    vtype = {n: signal_type(spec.types, v["type"]) for n, v in req.variables.items()}
    types = dict(vtype)
    types.update({k: "float64" for k in spec.constants})
    tr = Translator(types, consts=spec.constants, derived=derived)
    produced = [sig for inst in design.order[mname] for sig in inst.outputs.values()]
    outputs = [o for o in req.outputs if o in produced]
    locals_ = [s for s in produced if s not in outputs]
    ins = [(n, vtype[n]) for n in req.inputs]
    lines = [f"  -- {raw_mod['title']}: {raw_mod['description']}", f"  node {mname}"]
    lines += interface(ins, [(n, vtype[n]) for n in outputs])
    if locals_:
        lines.append("  var")
        lines += [f"    {n} : {vtype[n]};" for n in locals_]
    lines.append("  let")
    for inst in design.order[mname]:
        b = inst.block.raw
        args = [tr.value(inst.binding_asts[p["name"]], value_type(p["type"])) for p in b["inputs"]]
        for p in b["params"]:
            if p["name"] == "DELAY":
                args.append(f"{delay_const(inst)}")
            else:
                args.append(tr.value(parse(inst.raw["params"][p["name"]]), value_type(p["type"])))
        (port, sig), = inst.outputs.items()
        lines.append(f"    {sig} = {inst.block.name}({', '.join(args)});  -- {inst.name}")
    lines += ["  tel", ""]
    return lines


def model_text(design):
    spec = design.spec
    doc = design.raw["document"]
    used, delays = used_constants(design)
    lines = [f"-- {doc['title']}",
             f"-- {doc['id']} revision {doc['revision']}, implements {doc['implements']['id']}.",
             f"-- STATUS: {doc['status']}",
             "-- Generated by tools/export_scade.py from 2_design/rps_design.json - do not edit by hand.",
             "-- Every constant is an ILLUSTRATIVE placeholder.",
             "",
             f"package {PACKAGE}", "", "  const"]
    for k in sorted(used):
        meta = spec.const_meta[k]
        lines.append(f"    {k} : float64 = {literal(spec.constants[k], 'float64')};  -- {meta.get('unit', '')}")
    for name, (value, expr) in sorted(delays.items()):
        lines.append(f"    {name} : int32 = {literal(value, 'int32')};  -- scans = {expr}")
    derived, modules = {}, []
    for mname in ("BP", "CP"):
        modules += module_operator(design, mname, derived)
    for name, (value, expr) in sorted(derived.items()):
        lines.append(f"    {name} : float64 = {literal(value, 'float64')};  -- = {expr}, evaluated exactly")
    lines.append("")
    for name, b in design.raw["blocks"].items():
        lines += block_operator(name, b)
    lines += modules
    lines.append("end;")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Scenario scripts (.sss)
# ---------------------------------------------------------------------------

def _value(v, typ):
    return literal(to_fraction(v), typ)


def sss_text(title, root, module, spec, cycles, real_tolerance="1e-9"):
    """cycles: list of (inputs dict for this cycle, expected outputs dict)."""
    req = spec.modules[module]
    vtype = {n: signal_type(spec.types, v["type"]) for n, v in req.variables.items()}
    lines = [f"# {title}",
             "# Generated by tools/export_scade.py.  Every input is set in cycle 0 and afterwards only when it",
             "# changes (the simulator keeps an input's last set value).  Every output is checked in every cycle",
             "# against the value computed by the NuSCR requirements model (1_requirements/rps_nuscr_spec.json).",
             "# Cycles are numbered from 0 in this file; SCADE's cycle counter shows file cycle k as k+1.",
             "# Start each scenario in a fresh simulation session, from the initial state.",
             f"SSM::set_tolerance real={real_tolerance}"]
    last = {}
    for k, (inputs, expected) in enumerate(cycles):
        lines.append(f"# cycle {k}")
        for n in req.inputs:
            v = inputs[n]
            if last.get(n) != v:
                lines.append(f"SSM::set {PACKAGE}::{root}/{n} {_value(v, vtype[n])}")
                last[n] = v
        for n, v in expected.items():
            lines.append(f"SSM::check {PACKAGE}::{root}/{n} {_value(v, vtype[n])}")
        lines.append("SSM::cycle")
    return "\n".join(lines) + "\n"


def scenarios(design):
    spec = design.spec
    bp_outs, cp_outs = module_outputs(design, "BP"), module_outputs(design, "CP")

    def script(title, module, scans, outs):
        return sss_text(title, module, module, spec, [(i, {o: r[o] for o in outs}) for i, r in scans])

    files = {}
    for name in ("pzr_pressure_excursion", "cooldown_manual_reset"):
        run = bp_scenario_run(spec, name)
        files[f"{run.name}.sss"] = script(run.title, "BP", run.scans, bp_outs)
    # four-channel loss of flow: channel A bistable processor and the coincidence processor
    run = system_scenario_run(spec, "loss_of_flow_4ch")
    files["bp_loss_of_flow_channel_A.sss"] = script("BP_A in the four-channel loss-of-flow scenario", "BP",
                                                    run.view("BP_A"), bp_outs)
    files["cp_loss_of_flow.sss"] = script("CP_A in the four-channel loss-of-flow scenario", "CP",
                                          run.view("CP_A"), cp_outs)
    # every analog comparator exactly at its threshold and reset threshold
    run = boundary_run(spec)
    files["bp_boundaries.sss"] = script(run.title, "BP", run.scans, bp_outs)
    # boundary-biased random BP run
    run = random_bp_run(spec, 1)
    files["bp_random_boundary_seed1.sss"] = script(run.title, "BP", run.scans, bp_outs)
    # random CP run over every trip, bypass and link input
    run = random_cp_run(spec, 1)
    files["cp_random_seed1.sss"] = script(run.title, "CP", run.scans, cp_outs)
    return files


def main():
    design = load_design()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "RPS_Design.scade").write_text(model_text(design), encoding="utf-8", newline="\n")
    sdir = OUT_DIR / "scenarios"
    sdir.mkdir(exist_ok=True)
    files = scenarios(design)
    for name, text in files.items():
        (sdir / name).write_text(text, encoding="utf-8", newline="\n")
    report = float64_report(design)
    (sdir / "expected_float64_deviations.txt").write_text(report, encoding="utf-8", newline="\n")
    print(f"wrote {OUT_DIR / 'RPS_Design.scade'}, {len(files)} scenario scripts and the float64 deviation list "
          f"in {sdir}")


def float64_report(design):
    """Replays every scenario on the exported model with IEEE-754 double arithmetic (as SCADE's float64
    simulation computes) and lists the checks whose exact-arithmetic expectation is not met."""
    from scade_subset import parse_program, run_sss
    spec = design.spec
    prog = parse_program(model_text(design))
    noms = {m: {n: Fraction(str(spec.modules[m].variables[n]["nominal"])) for n in spec.modules[m].inputs}
            for m in ("BP", "CP")}
    lines = ["# Checks expected to fail when the scenarios run in SCADE's float64 simulation.",
             "# Generated by tools/export_scade.py: every scenario is replayed on RPS_Design.scade with IEEE-754",
             "# double arithmetic.  Each entry is a one-rounding-step effect at a threshold computed at run time",
             "# (a variable setpoint compared with a process value that equals it exactly in decimal).  Any other",
             "# failed check in SCADE is not explained by floating point and must be investigated.",
             "# 'file cycle' is the '# cycle' comment in the scenario (from 0); 'SCADE cycle' is the simulator's",
             "# cycle counter (from 1).", ""]
    total = 0
    for path in sorted((OUT_DIR / "scenarios").glob("*.sss")):
        _, checks, fails = run_sss(prog, path, noms["CP" if path.name.startswith("cp") else "BP"], num=float)
        total += len(fails)
        lines.append(f"{path.name}: {checks} checks, {len(fails)} expected float64 deviation(s)")
        lines += [f"    {f}" for f in fails]
    lines += ["", f"Total: {total}"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
