"""Generates the JavaScript implementation of the function-block design, 4_code/js/rps_engine.js.  It runs the
virtual system in START_HERE.html and under Node.js (tools/js_runner.js, checked by tools/verify_js.py).

The engine follows the design one to one, like the C code (tools/export_c.py): one function per block, one call
per instance in the design's execution order, signals named after the NuSCR variables, last_S read before state S
is updated, and the same thresholds folded at generation time, so that it computes exactly what IEEE-754 double
arithmetic gives.

    const sim = new RPS.Simulator("SYSTEM");     // or "BP", "CP"; inputs start at their nominal values
    sim.set("BP.f_PZR_P", 2400);                  // BP_A.f_PZR_P, BP.f_PZR_P (all BPs) or f_PZR_P (all BPs)
    sim.step();                                   // one scan
    sim.get("CP_A.f_RT_Init");                    // signals and inputs by name; flags are booleans
    RPS.META                                      // inputs, signals, types, units, labels, system wiring

    python -B tools/export_js.py
"""
from __future__ import annotations

import json
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from design_model import load_design                                     # noqa: E402
from export_common import (Translator, delay_const, design_signals,      # noqa: E402
                           literal, module_outputs, signal_type, used_constants, value_type)
from nuscr_lang import format_number, parse, variables                   # noqa: E402
from nuscr_sim import to_fraction                                        # noqa: E402
from paths import JS_ENGINE                                              # noqa: E402

JS_TYPE = {"bool": "bool", "int32": "int32", "float64": "real"}


class JSTranslator(Translator):
    AND, OR = "&&", "||"
    CMP = {"=": "===", "!=": "!==", "<": "<", "<=": "<=", ">": ">", ">=": ">="}

    def __init__(self, types, state=(), consts=None, derived=None, names=None):
        super().__init__(types, state, consts, derived)
        self.names = names or {}

    def last(self, name):
        return f"last_{name}"

    def negate(self, text):
        return f"(!{text})"

    def name(self, name):
        return self.names.get(name, name)


def _reads(st):
    texts = [st["expr"]] if "expr" in st else [t for case in st["cases"] for t in case] + [st["else"]]
    return set().union(*(variables(parse(t)) for t in texts))


def block_js(name, b):
    ins = [(p["name"], value_type(p["type"])) for p in b["inputs"] + b["params"]]
    out, out_t = b["outputs"][0]["name"], value_type(b["outputs"][0]["type"])
    state = {p["name"]: value_type(p["type"]) for p in b["state"]}
    types = dict(ins)
    types.update({out: out_t}, **state, **{p["name"]: value_type(p["type"]) for p in b["locals"]})
    tr = JSTranslator(types, state)
    lines = [f"  // {name}: {' '.join(b['description'].split())}"]
    if state:
        inits = ", ".join(f"{p['name']}: {literal(Fraction(str(p['init'])), state[p['name']])}" for p in b["state"])
        lines.append(f"  function {name}_init() {{ return {{ {inits} }}; }}")
    lines.append(f"  function {name}({', '.join((['s'] if state else []) + [n for n, _ in ins])}) {{")
    read_last = {v[5:] for st in b["body"] for v in _reads(st) if v.startswith("last_") and v[5:] in state}
    lines += [f"    const last_{n} = s.{n};" for n in state if n in read_last]
    for st in b["body"]:
        v, target = st["set"], types[st["set"]]
        if "expr" in st:
            lines.append(f"    const {v} = {tr.value(parse(st['expr']), target)};")
            continue
        lines.append(f"    const {v} =")
        lines += [f"      {tr.bool(parse(g))} ? {tr.value(parse(e), target)} :" for g, e in st["cases"]]
        lines.append(f"      {tr.value(parse(st['else']), target)};")
    lines += [f"    s.{n} = {n};" for n in state]
    lines += [f"    return {out};", "  }", ""]
    return lines


def module_js(design, mname, derived):
    spec = design.spec
    req = spec.modules[mname]
    vtype = {n: signal_type(spec.types, v["type"]) for n, v in req.variables.items()}
    signals = design_signals(design, mname)
    names = {n: f"i.{n}" for n in req.inputs}
    names.update({s: f"o.{s}" for s in signals})
    types = dict(vtype)
    types.update({k: "float64" for k in spec.constants})
    tr = JSTranslator(types, consts=spec.constants, derived=derived, names=names)
    stateful = [i for i in design.order[mname] if i.block.state]
    lines = [f"  function {mname}_init() {{", "    return {"]
    lines += [f"      {i.name}: {i.block.name}_init()," for i in stateful]
    lines += ["    };", "  }", "", f"  function {mname}_nominal_inputs() {{", "    return {"]
    lines += [f"      {n}: {literal(to_fraction(req.variables[n]['nominal']), vtype[n])}," for n in req.inputs]
    lines += ["    };", "  }", "", f"  function {mname}_step(st, i, o) {{"]
    for inst in design.order[mname]:
        b = inst.block.raw
        args = [tr.value(inst.binding_asts[p["name"]], value_type(p["type"])) for p in b["inputs"]]
        for p in b["params"]:
            args.append(delay_const(inst) if p["name"] == "DELAY"
                        else tr.value(parse(inst.raw["params"][p["name"]]), value_type(p["type"])))
        if inst.block.state:
            args.insert(0, f"st.{inst.name}")
        (_, sig), = inst.outputs.items()
        lines.append(f"    o.{sig} = {inst.block.name}({', '.join(args)});  // {inst.name}")
    lines += ["    return o;", "  }", ""]
    return lines


def system_js(design):
    sysd = design.spec.system
    module = {i["name"]: i["module"] for i in sysd["instances"]}
    links = {}
    for con in sysd["connections"]:
        ti, tv = con["to"].split(".", 1)
        si, sv = con["from"].split(".", 1)
        links.setdefault(ti, []).append((tv, si, sv))
    lines = ["  function System_init() {", "    return {"]
    lines += [f"      {n}: {m}_init()," for n, m in module.items()]
    lines += ["    };", "  }", "", "  function System_nominal_inputs() {", "    return {"]
    lines += [f"      {n}: {m}_nominal_inputs()," for n, m in module.items()]
    lines += ["    };", "  }", "", "  function System_step(st, i, o) {"]
    for n in sysd["evaluation_order"]:
        m = module[n]
        if n in links:
            lines.append(f"    const {n}_in = Object.assign({{}}, i.{n});")
            for tv, si, sv in links[n]:
                where = "i" if sv in design.spec.modules[module[si]].inputs else "o"
                lines.append(f"    {n}_in.{tv} = {where}.{si}.{sv};")
            lines.append(f"    {m}_step(st.{n}, {n}_in, o.{n});")
        else:
            lines.append(f"    {m}_step(st.{n}, i.{n}, o.{n});")
    lines += ["    return o;", "  }", ""]
    return lines


def meta(design):
    spec = design.spec

    def field(m, n, extra=None):
        v = spec.modules[m].variables[n]
        t = signal_type(spec.types, v["type"])
        f = {"name": n, "type": JS_TYPE[t], "nuscrType": v["type"], "unit": v.get("unit", ""),
             "description": v["description"]}
        labels = spec.types[v["type"]].get("labels")
        if t == "bool" and labels:
            f["labels"] = [labels["0"], labels["1"]]
        if "nominal" in v:
            f["nominal"] = float(to_fraction(v["nominal"])) if t == "float64" else int(to_fraction(v["nominal"]))
        f.update(extra or {})
        return f

    modules = {}
    for m in ("BP", "CP"):
        raw_mod = next(x for x in design.raw["modules"] if x["name"] == m)
        outs = set(module_outputs(design, m))
        producer = {sig: inst for inst in design.order[m] for sig in inst.outputs.values()}
        modules[m] = {
            "title": raw_mod["title"], "description": raw_mod["description"],
            "inputs": [field(m, n, {"source": spec.modules[m].variables[n].get("source", "")})
                       for n in spec.modules[m].inputs],
            "signals": [field(m, s, {"instance": producer[s].name, "block": producer[s].block.name,
                                     "group": producer[s].raw.get("group", ""), "output": s in outs,
                                     "implements": producer[s].raw["implements"]})
                        for s in design_signals(design, m)]}
    doc = design.raw["document"]
    return {"document": {"id": doc["id"], "revision": doc["revision"], "status": doc["status"]},
            "scanPeriodMs": float(spec.constants["k_Scan_Period"]), "modules": modules,
            "system": {"instances": spec.system["instances"], "order": spec.system["evaluation_order"],
                       "connections": spec.system["connections"]}}


RUNTIME = r"""
  // ---- convenience simulator: BP, CP or SYSTEM ----------------------------------------------------------------
  const MODULES = {
    BP: { init: BP_init, nominal_inputs: BP_nominal_inputs, step: BP_step },
    CP: { init: CP_init, nominal_inputs: CP_nominal_inputs, step: CP_step },
  };
  // Lookups use own properties only, so names such as "constructor" are never taken for inputs or signals.
  const hasOwn = (o, k) => o !== null && o !== undefined && Object.prototype.hasOwnProperty.call(o, k);
  const FIELDS = Object.create(null);
  for (const m of Object.keys(META.modules)) {
    FIELDS[m] = Object.create(null);
    for (const f of META.modules[m].inputs) FIELDS[m][f.name] = f;
    for (const f of META.modules[m].signals) FIELDS[m][f.name] = f;
  }
  const SOURCE = new Map(META.system.connections.map((c) => [c.to, c.from]));

  function coerce(field, value) {
    if (field.type === "bool") {
      if (value === true || value === false) return value;
      const n = Number(value);
      if (n !== 0 && n !== 1) throw new Error(field.name + ": a flag takes 0 or 1, not " + value);
      return n === 1;
    }
    const n = Number(value);
    if (!Number.isFinite(n)) throw new Error(field.name + ": " + value + " is not a number");
    if (field.type === "int32" && !Number.isInteger(n)) throw new Error(field.name + ": a whole number is needed");
    return n;
  }

  class Simulator {
    constructor(target) {
      this.target = target || "SYSTEM";
      if (this.target !== "SYSTEM" && !hasOwn(MODULES, this.target)) throw new Error("unknown target " + target);
      this.reset();
    }

    // Power-up: initial states, nominal inputs, no scan run yet.
    reset() {
      if (this.target === "SYSTEM") {
        this.state = System_init();
        this.inputs = System_nominal_inputs();
        this.signals = {};
        for (const i of META.system.instances) this.signals[i.name] = {};
      } else {
        this.state = MODULES[this.target].init();
        this.inputs = MODULES[this.target].nominal_inputs();
        this.signals = {};
      }
      this.cycle = -1;
    }

    // One scan.
    step() {
      if (this.target === "SYSTEM") System_step(this.state, this.inputs, this.signals);
      else MODULES[this.target].step(this.state, this.inputs, this.signals);
      this.cycle += 1;
      return this.signals;
    }

    get timeMs() {
      return this.cycle * META.scanPeriodMs;
    }

    moduleOf(instance) {
      const i = META.system.instances.find((x) => x.name === instance);
      return i ? i.module : null;
    }

    // [container, key, field] for every input a name stands for: in SYSTEM INSTANCE.var, BP.var or a bare BP
    // input name (all BPs).
    resolveInput(name) {
      if (this.target !== "SYSTEM") {
        const f = FIELDS[this.target][name];
        return f && hasOwn(this.inputs, name) ? [[this.inputs, name, f]] : [];
      }
      const dot = name.indexOf(".");
      let instances, v;
      if (dot < 0 || name.slice(0, dot) === "BP") {
        v = dot < 0 ? name : name.slice(dot + 1);
        instances = META.system.instances.filter((x) => x.module === "BP").map((x) => x.name);
      } else {
        v = name.slice(dot + 1);
        instances = [name.slice(0, dot)];
      }
      const out = [];
      for (const inst of instances) {
        const m = this.moduleOf(inst);
        if (m && hasOwn(this.inputs, inst) && hasOwn(this.inputs[inst], v)) out.push([this.inputs[inst], v, FIELDS[m][v]]);
      }
      return out;
    }

    // Sets an input; it applies from the next scan and keeps its value.
    set(name, value) {
      const slots = this.resolveInput(name);
      if (!slots.length) throw new Error(name + " is not an input of " + this.target);
      for (const [obj, key, f] of slots) obj[key] = coerce(f, value);
    }

    // In SYSTEM: true for an input that a connection sets in every scan (INSTANCE.input).
    isDriven(name) {
      return this.target === "SYSTEM" && SOURCE.has(name);
    }

    // A signal or input by name (INSTANCE.name in SYSTEM); a connection-driven input gives the value it receives.
    get(name) {
      let sig = this.signals, inp = this.inputs, v = name;
      if (this.target === "SYSTEM" && SOURCE.has(name) && this.cycle >= 0) return this.get(SOURCE.get(name));
      if (this.target === "SYSTEM") {
        const dot = name.indexOf(".");
        const inst = name.slice(0, dot);
        v = name.slice(dot + 1);
        sig = hasOwn(this.signals, inst) ? this.signals[inst] : {};
        inp = hasOwn(this.inputs, inst) ? this.inputs[inst] : {};
      }
      if (hasOwn(sig, v)) return sig[v];
      if (hasOwn(inp, v)) return inp[v];
      throw new Error("no signal or input " + name);
    }

    // Every computed signal in trace order: names (INSTANCE.signal in SYSTEM) and values.
    signalNames() {
      if (this.target !== "SYSTEM") return META.modules[this.target].signals.map((f) => f.name);
      const out = [];
      for (const i of META.system.instances) for (const f of META.modules[i.module].signals) out.push(i.name + "." + f.name);
      return out;
    }
  }

  const RPS = {
    META, Simulator,
    BP: { init: BP_init, nominal_inputs: BP_nominal_inputs, step: BP_step },
    CP: { init: CP_init, nominal_inputs: CP_nominal_inputs, step: CP_step },
    System: { init: System_init, nominal_inputs: System_nominal_inputs, step: System_step },
  };
  if (typeof module === "object" && module.exports) module.exports = RPS;
  else root.RPS = RPS;
})(typeof globalThis !== "undefined" ? globalThis : this);
"""


def engine_text(design):
    spec = design.spec
    doc = design.raw["document"]
    derived, body = {}, []
    for name, b in design.raw["blocks"].items():
        body += block_js(name, b)
    modules = []
    for m in ("BP", "CP"):                       # same order as the SCADE and C exports: same derived constants
        modules += module_js(design, m, derived)
    used, delays = used_constants(design)
    consts = [f"  const {k} = {literal(spec.constants[k], 'float64')};" for k in sorted(set(used) | {'k_Scan_Period'})]
    consts += [f"  const {n} = {literal(v, 'int32')};  // = {e}" for n, (v, e) in sorted(delays.items())]
    consts += [f"  const {n} = {literal(v, 'float64')};  // = {e}" for n, (v, e) in sorted(derived.items())]
    head = ["// rps_engine.js - the function-block design of the notional four-channel reactor protection system,",
            "// executable in a browser or Node.js.",
            f"// Generated by tools/export_js.py from 2_design/rps_design.json ({doc['id']} revision {doc['revision']}).",
            "// Do not edit by hand: change the design and regenerate.",
            f"// STATUS: {doc['status']}.",
            "// Every constant is an ILLUSTRATIVE placeholder.",
            "(function (root) {", '  "use strict";', "",
            "  // ---- constants -----------------------------------------------------------------------------------------"]
    meta_text = json.dumps(meta(design), indent=None, separators=(",", ":"), ensure_ascii=False)
    lines = head + consts + ["", "  // ---- function blocks ---------------------------------------------------------------------------------"]
    lines += body + ["  // ---- modules -----------------------------------------------------------------------------------------"]
    lines += modules + system_js(design)
    lines += ["  // ---- names, types, units and labels of every input and signal -------------------------------------------",
              f"  const META = {meta_text};"]
    return "\n".join(lines) + RUNTIME


def main():
    design = load_design()
    JS_ENGINE.parent.mkdir(parents=True, exist_ok=True)
    text = engine_text(design)
    JS_ENGINE.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {JS_ENGINE} ({text.count(chr(10))} lines, {len(text) // 1024} KB)")


if __name__ == "__main__":
    main()
