"""Exports the function-block design for Ansys SCADE Suite:

  design/scade/RPS_Design.scade   Scade 6 textual model: constants, the 12 blocks as operators, and the BP and
                                  CP modules as root-able operators (insert into a SCADE Suite project with
                                  File View > Model Files > Insert Files)
  design/scade/scenarios/*.sss    simulator scenario scripts: every input set in cycle 0 and afterwards only
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
from nuscr_lang import format_number, parse, variables                  # noqa: E402
from nuscr_sim import ModuleSim, SystemSim, _read_scenario, to_fraction  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "design" / "scade"
PACKAGE = "RPS"
DELAY_SUFFIX = "_Scans"


class ExportError(Exception):
    pass


# ---------------------------------------------------------------------------
# Types and literals
# ---------------------------------------------------------------------------

def scade_type(design_type):
    if design_type == "real":
        return "float64"
    if design_type in ("count", "count_0_4"):
        return "int32"
    return "bool"


def nuscr_scade_type(spec_types, type_name):
    vals = spec_types[type_name].get("values")
    if vals is None:
        return "float64"
    if sorted(vals) == [0, 1]:
        return "bool"
    return "int32"


def literal(q, typ):
    q = Fraction(q)
    if typ == "bool":
        if q not in (0, 1):
            raise ExportError(f"literal {q} used as a flag")
        return "true" if q == 1 else "false"
    if typ == "int32":
        if q.denominator != 1:
            raise ExportError(f"literal {q} used as an integer")
        return str(q.numerator)
    s = format_number(q)
    if s.startswith("("):
        raise ExportError(f"constant {q} has no finite decimal representation")
    return s if "." in s else s + ".0"


# ---------------------------------------------------------------------------
# Typed translation of design expressions into Scade 6
# ---------------------------------------------------------------------------

_OPS = {"add": "+", "sub": "-", "mul": "*", "div": "/"}
_CMP = {"=": "=", "!=": "<>", "<": "<", "<=": "<=", ">": ">", ">=": ">="}


class Translator:
    """Translates design expressions (flags as 1/0) into typed Scade 6 text (flags as bool).

    With `consts`, arithmetic subexpressions made only of constants are evaluated exactly at export time and
    emitted as one named constant (registered in `derived`), so that float64 code rounds each threshold once
    instead of computing it at run time (for example SP - HYS becomes k_<X>_<W>_Reset)."""

    def __init__(self, types, state=(), consts=None, derived=None):
        self.types = types                      # name -> bool | int32 | float64
        self.state = set(state)
        self.consts = consts or {}
        self.derived = derived if derived is not None else {}

    # returns (text, type) where type is bool / int32 / float64 / ("lit", Fraction)
    def tr(self, e):
        tag = e[0]
        if tag == "num":
            return None, ("lit", e[1])
        if tag == "bool":
            return ("true" if e[1] else "false"), "bool"
        if tag == "var":
            name = e[1]
            if name.startswith("last_") and name[5:] in self.state:
                return f"last '{name[5:]}", self.types[name[5:]]
            if name not in self.types:
                raise ExportError(f"unknown name {name}")
            return name, self.types[name]
        if tag == "neg":
            t, ty = self.tr(e[1])
            if isinstance(ty, tuple):
                return None, ("lit", -ty[1])
            return f"(-{t})", ty
        if tag in _OPS and self.consts and variables(e) and variables(e) <= set(self.consts):
            return self._fold(e), "float64"
        if tag in _OPS:
            (a, ta), (b, tb) = self.tr(e[1]), self.tr(e[2])
            typ = self._unify(ta, tb)
            return f"({self._as(a, ta, typ)} {_OPS[tag]} {self._as(b, tb, typ)})", typ
        if tag == "cmp":
            op, l, r = e[1], e[2], e[3]
            (a, ta), (b, tb) = self.tr(l), self.tr(r)
            for (x, tx), (_, ty) in (((a, ta), (b, tb)), ((b, tb), (a, ta))):
                if tx == "bool" and isinstance(ty, tuple) and op in ("=", "!=") and ty[1] in (0, 1):
                    positive = (op == "=") == (ty[1] == 1)
                    return (x if positive else f"(not {x})"), "bool"
            typ = self._unify(ta, tb)
            if typ == "bool" and op not in ("=", "!="):
                raise ExportError(f"ordering comparison on flags: {e}")
            return f"({self._as(a, ta, typ)} {_CMP[op]} {self._as(b, tb, typ)})", "bool"
        if tag in ("and", "or"):
            a, b = self.bool(e[1]), self.bool(e[2])
            return f"({a} {tag} {b})", "bool"
        if tag == "not":
            return f"(not {self.bool(e[1])})", "bool"
        raise ExportError(f"cannot translate {e}")

    def _fold(self, e):
        from nuscr_lang import evaluate, to_text
        value = Fraction(evaluate(e, self.consts))
        name = None
        if e[0] in ("add", "sub") and e[1][0] == "var" and e[2][0] == "var":
            a, b = e[1][1], e[2][1]
            if a.endswith("_SP") and b.endswith("_Hys") and a[:-3] == b[:-4]:
                name = a[:-3] + "_Reset"
        name = name or f"k_Derived_{len(self.derived) + 1}"
        prev = self.derived.get(name)
        if prev is not None and prev[0] != value:
            raise ExportError(f"derived constant {name} defined with two values")
        self.derived[name] = (value, to_text(e))
        return name

    def bool(self, e):
        t, ty = self.tr(e)
        if ty != "bool":
            raise ExportError(f"expected a flag expression: {e}")
        return t

    def value(self, e, target):
        t, ty = self.tr(e)
        return self._as(t, ty, target)

    @staticmethod
    def _unify(ta, tb):
        if isinstance(ta, tuple) and isinstance(tb, tuple):
            raise ExportError("expression between two literals")
        if isinstance(ta, tuple):
            return tb
        if isinstance(tb, tuple) or ta == tb:
            return ta
        raise ExportError(f"type mismatch {ta} / {tb}")

    @staticmethod
    def _as(text, typ, target):
        if isinstance(typ, tuple):
            return literal(typ[1], target)
        if typ != target:
            raise ExportError(f"type {typ} where {target} is expected ({text})")
        return text


# ---------------------------------------------------------------------------
# Model file
# ---------------------------------------------------------------------------

def block_operator(name, b):
    ins = [(p["name"], scade_type(p["type"])) for p in b["inputs"]] + \
          [(p["name"], scade_type(p["type"])) for p in b["params"]]
    outs = [(p["name"], scade_type(p["type"])) for p in b["outputs"]]
    state = {p["name"]: scade_type(p["type"]) for p in b["state"]}
    locs = {p["name"]: scade_type(p["type"]) for p in b["locals"]}
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
    vtype = {n: nuscr_scade_type(spec.types, v["type"]) for n, v in req.variables.items()}
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
        args = [tr.value(inst.binding_asts[p["name"]], scade_type(p["type"])) for p in b["inputs"]]
        for p in b["params"]:
            if p["name"] == "DELAY":
                args.append(f"{delay_const(inst)}")
            else:
                args.append(tr.value(parse(inst.raw["params"][p["name"]]), scade_type(p["type"])))
        (port, sig), = inst.outputs.items()
        lines.append(f"    {sig} = {inst.block.name}({', '.join(args)});  -- {inst.name}")
    lines += ["  tel", ""]
    return lines


def delay_const(inst):
    return inst.raw["params"]["DELAY"].split("/")[0].strip() + DELAY_SUFFIX


def model_text(design):
    spec = design.spec
    doc = design.raw["document"]
    used = set()
    for insts in design.modules.values():
        for inst in insts:
            used |= {r for r in inst.reads if r in spec.constants}
            for p, text in inst.raw["params"].items():
                if p != "DELAY":
                    used |= {v for v in variables(parse(text)) if v in spec.constants}
    delays = {}
    for inst in design.modules["BP"]:
        if "DELAY" in inst.params:
            delays[delay_const(inst)] = (inst.params["DELAY"], inst.raw["params"]["DELAY"])
    lines = [f"-- {doc['title']}",
             f"-- {doc['id']} revision {doc['revision']}, implements {doc['implements']['id']}.",
             f"-- STATUS: {doc['status']}",
             "-- Generated by tools/export_scade.py from design/rps_design.json - do not edit by hand.",
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
    vtype = {n: nuscr_scade_type(spec.types, v["type"]) for n, v in req.variables.items()}
    lines = [f"# {title}",
             "# Generated by tools/export_scade.py.  Every input is set in cycle 0 and afterwards only when it",
             "# changes (the simulator keeps an input's last set value).  Every output is checked in every cycle",
             "# against the value computed by the NuSCR requirements model (spec/rps_nuscr_spec.json).",
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


def module_outputs(design, mname):
    req = design.spec.modules[mname]
    produced = {sig for inst in design.modules[mname] for sig in inst.outputs.values()}
    return [o for o in req.outputs if o in produced]


def bp_trace(spec, events, last_cycle):
    sim = ModuleSim(spec, "BP")
    out = []
    for c in range(last_cycle + 1):
        res = sim.step({col: val for col, val in events.get(c, [])})
        out.append((dict(sim.inputs), res))
    return out


def boundary_bp_cycles(spec):
    """Deterministic run that holds every analog trip and pre-trip comparator exactly at its threshold, exactly
    at its reset threshold and just beyond it.  Variable setpoints are probed where they are stationary: VOPT at
    its ceiling, the manual-reset setpoints at their maximum, the low-flow setpoints at their floor."""
    import build_spec as B
    c = spec.constants
    sim = ModuleSim(spec, "BP")
    cycles = []
    eps = Fraction(1, 1000)

    def step(ch=None, n=1):
        for _ in range(n):
            res = sim.step(ch or {})
            ch = None
            cycles.append((dict(sim.inputs), res))

    step()
    step({"f_LOGP_HI_OB_Ini": 1})
    step({"f_LOGP_HI_OB_Ini": 0}, 2)
    for t in B.TRIPS:
        x, logic, s = t["id"], t["logic"], t.get("signal")
        if logic == "DT_OB":
            continue
        sig, nominal = f"f_{s}", Fraction(B.SIGNALS[s]["nominal"])
        rising = B.LOGIC[logic]["rising"]
        if logic == "FSRT_OB":                          # log power: leave the bypass first
            step({sig: Fraction("0.00005")}, 2)
        if logic == "RLVSFT_OB":                        # bring the rate-limited setpoint down to its floor
            v, floor = nominal, c[f"k_{x}_SP_Min"] + c[f"k_{x}_Band"]
            while v > floor:
                v -= c[f"k_{x}_SP_Step"]
                step({sig: v})
            step(None, 3)
        for which in ("Pretrip", "Trip"):
            delay = int(c[f"k_{x}_{which}_Delay"] / c["k_Scan_Period"])
            hys = c[f"k_{x}_{which}_Hys"]
            if logic in ("VSRT", "RLVSFT_OB", "MRVSFT_OB"):
                sp = sim.values[f"h_{x}_Trip_SP"]
                if which == "Pretrip":
                    sp = sp - c[f"k_{x}_Pretrip_Margin"] if rising else sp + c[f"k_{x}_Pretrip_Margin"]
            else:
                sp = c[f"k_{x}_{which}_SP"]
            reset = sp - hys if rising else sp + hys
            step({sig: sp}, delay + 2)                   # exactly at the threshold
            step({sig: reset}, 2)                        # exactly at the reset threshold: still tripped
            step({sig: reset - eps if rising else reset + eps}, 2)   # just beyond: released
        step({sig: nominal}, 3)
        if logic == "FSRT_OB":
            step({"f_LOGP_HI_OB_Ini": 1})
            step({"f_LOGP_HI_OB_Ini": 0}, 2)
    return cycles


def scenarios(design):
    import random
    from design_verify import BoundaryInputs
    spec = design.spec
    bp_outs, cp_outs = module_outputs(design, "BP"), module_outputs(design, "CP")
    files = {}
    for name in ("pzr_pressure_excursion", "cooldown_manual_reset"):
        events, last = _read_scenario(ROOT / "scenarios" / f"{name}.csv")
        trace = bp_trace(spec, events, last)
        files[f"bp_{name}.sss"] = sss_text(f"BP scenario {name} (scenarios/{name}.csv)", "BP", "BP", spec,
                                           [(i, {o: r[o] for o in bp_outs}) for i, r in trace])
    # four-channel loss of flow: channel A bistable processor and the coincidence processor
    events, last = _read_scenario(ROOT / "scenarios" / "loss_of_flow_4ch.csv")
    sysim = SystemSim(spec)
    bp_cycles, cp_cycles = [], []
    for c in range(last + 1):
        changes = {}
        for col, val in events.get(c, []):
            inst, var = col.split(".", 1) if "." in col else ("BP", col)
            changes.setdefault(inst, {})[var] = val
        res = sysim.step(changes)
        bp_cycles.append((dict(sysim.instances["BP_A"].inputs), {o: res["BP_A"][o] for o in bp_outs}))
        cp_cycles.append((dict(sysim.instances["CP_A"].inputs), {o: res["CP_A"][o] for o in cp_outs}))
    files["bp_loss_of_flow_channel_A.sss"] = sss_text("BP_A in the four-channel loss-of-flow scenario", "BP", "BP",
                                                      spec, bp_cycles)
    files["cp_loss_of_flow.sss"] = sss_text("CP_A in the four-channel loss-of-flow scenario", "CP", "CP", spec,
                                            cp_cycles)
    # every analog comparator exactly at its threshold and reset threshold
    files["bp_boundaries.sss"] = sss_text("BP comparator boundaries: every analog trip and pre-trip threshold and "
                                          "reset threshold hit exactly", "BP", "BP", spec,
                                          [(i, {o: r[o] for o in bp_outs}) for i, r in boundary_bp_cycles(spec)])
    # boundary-biased random BP run
    sim, gen, req_values, cycles = ModuleSim(spec, "BP"), BoundaryInputs(spec, 1), {}, []
    for _ in range(1000):
        # stimuli rounded to 6 decimals so that every value has an exact decimal literal in the script
        ch = {n: Fraction(round(v * 10 ** 6), 10 ** 6) for n, v in gen.next(req_values).items()}
        req_values = sim.step(ch)
        cycles.append((dict(sim.inputs), {o: req_values[o] for o in bp_outs}))
    files["bp_random_boundary_seed1.sss"] = sss_text("BP boundary-biased random run, seed 1, 1000 cycles", "BP",
                                                     "BP", spec, cycles)
    # random CP run over every trip, bypass and link input
    rng, cp, cycles = random.Random(1), ModuleSim(spec, "CP"), []
    for _ in range(1000):
        ch = {n: (0 if rng.random() < 0.15 else 1) if n.endswith("_Trip") else (1 if rng.random() < 0.1 else 0)
              for n in spec.modules["CP"].inputs}
        res = cp.step(ch)
        cycles.append((dict(cp.inputs), {o: res[o] for o in cp_outs}))
    files["cp_random_seed1.sss"] = sss_text("CP random run, seed 1, 1000 cycles", "CP", "CP", spec, cycles)
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
