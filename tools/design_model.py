"""Loader, static checker and cycle simulator for the function-block design (rps-design-json/2.0).

The design refers to the NuSCR requirements specification for its module interfaces, constants and the
variables it implements.  Signals are named after NuSCR variables, so the design can be simulated side by side
with the requirements model (nuscr_sim.py) and compared scan by scan.

Block body notation (single assignment):
    {"set": V, "expr": E}                                  V := E
    {"set": V, "cases": [[G1, E1], ...], "else": E}         V := E1 if G1, else E2 if G2, ..., else E
Every output, state variable and local is assigned exactly once, in statement order.  last_S is the value of
state variable S at the end of the previous scan; S read after its assignment is the current value.  Boolean
expressions assigned to a variable yield 1 (TRUE) or 0 (FALSE).

    python -B tools/design_model.py            static check of design/rps_design.json
"""
from __future__ import annotations

import heapq
import json
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nuscr_lang import ExprError, compile_expr, evaluate, parse, variables   # noqa: E402
from nuscr_model import default_spec_path, load                              # noqa: E402
from nuscr_sim import SimulationError, to_fraction                          # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FORMAT = "rps-design-json/2.0"
BOOL_TAGS = {"bool", "cmp", "and", "or", "not"}
ONE, ZERO = Fraction(1), Fraction(0)


class DesignError(Exception):
    pass


def default_design_path():
    return ROOT / "design" / "rps_design.json"


def is_bool(ast):
    return ast[0] in BOOL_TAGS


def _value_fn(ast):
    f = compile_expr(ast)
    if is_bool(ast):
        return lambda env: ONE if f(env) else ZERO
    return lambda env: Fraction(f(env))


def _parse(text, where, kind=None):
    try:
        return parse(text, kind)
    except ExprError as e:
        raise DesignError(f"{where}: {e}") from None


class Statement:
    """One single assignment; `branches` is [(guard ast | None, value ast)] with None for the default."""

    def __init__(self, raw, where):
        self.var = raw["set"]
        if "expr" in raw:
            self.branches = [(None, _parse(raw["expr"], f"{where} {self.var} :="))]
        else:
            self.branches = [(_parse(g, f"{where} {self.var} guard", "bool"), _parse(e, f"{where} {self.var} :="))
                             for g, e in raw["cases"]]
            self.branches.append((None, _parse(raw["else"], f"{where} {self.var} else")))
        self.reads = set()
        for g, e in self.branches:
            self.reads |= variables(e) | (variables(g) if g is not None else set())
        self._fns = [(compile_expr(g) if g is not None else None, _value_fn(e)) for g, e in self.branches]

    def run(self, env):
        for g, f in self._fns:
            if g is None or g(env):
                env[self.var] = f(env)
                return


class Block:
    def __init__(self, name, raw):
        self.name, self.raw = name, raw
        self.inputs = [p["name"] for p in raw["inputs"]]
        self.params = {p["name"]: p for p in raw["params"]}
        self.state = {p["name"]: p for p in raw["state"]}
        self.locals = [p["name"] for p in raw.get("locals", [])]
        self.outputs = [p["name"] for p in raw["outputs"]]
        self.statements = [Statement(st, f"block {name}") for st in raw["body"]]
        self.inits = {n: _parse(p["init"], f"block {name} initial value of {n}") for n, p in self.state.items()}

    def check(self, report):
        where = f"block {self.name}"
        must_assign = set(self.outputs) | set(self.state) | set(self.locals)
        seen = []
        for st in self.statements:
            if st.var in seen:
                report.append(("ERROR", where, f"{st.var} is assigned more than once"))
            if st.var not in must_assign:
                report.append(("ERROR", where, f"assigns {st.var}, which is not an output, state variable or "
                                               f"declared local"))
            for r in st.reads:
                ok = (r in self.inputs or r in self.params or r in seen
                      or (r.startswith("last_") and r[5:] in self.state))
                if not ok:
                    what = ("before its assignment" if r in must_assign
                            else "which is not declared" if not r.startswith("last_") else "(no such state)")
                    report.append(("ERROR", where, f"{st.var} reads {r} {what}"))
            seen.append(st.var)
        for v in sorted(must_assign - set(seen)):
            report.append(("ERROR", where, f"{v} is never assigned"))
        for n, ast in self.inits.items():
            if variables(ast):
                report.append(("ERROR", where, f"initial value of {n} must be a constant"))

    def initial_state(self, params=None):
        return {n: Fraction(evaluate(ast, {})) for n, ast in self.inits.items()}

    def step(self, params, state, inputs):
        """Run one scan: returns (outputs, new_state)."""
        env = dict(params)
        env.update(inputs)
        for n, v in state.items():
            env["last_" + n] = v
        for st in self.statements:
            st.run(env)
        return {o: env[o] for o in self.outputs}, {n: env[n] for n in self.state}


class Instance:
    def __init__(self, raw, block, consts):
        self.raw, self.block = raw, block
        self.name = raw["name"]
        self.params = {}
        for p, text in raw["params"].items():
            self.params[p] = Fraction(evaluate(_parse(text, f"instance {self.name} parameter {p}"), consts))
        self.binding_asts = {}
        self.bindings = {}
        self.reads = set()
        for port, text in raw["inputs"].items():
            ast = _parse(text, f"instance {self.name} input {port}")
            self.binding_asts[port] = ast
            self.bindings[port] = _value_fn(ast)
            self.reads |= variables(ast)
        self.outputs = dict(raw["outputs"])


class Design:
    def __init__(self, raw, spec):
        if raw.get("format") != FORMAT:
            raise DesignError(f"unsupported design format {raw.get('format')!r}")
        self.raw, self.spec = raw, spec
        self.blocks = {n: Block(n, b) for n, b in raw["blocks"].items()}
        self.modules = {}
        consts = spec.constants
        for m in raw["modules"]:
            insts = []
            for i in m["instances"]:
                if i["block"] not in self.blocks:
                    raise DesignError(f"{m['name']}.{i['name']}: unknown block {i['block']}")
                insts.append(Instance(i, self.blocks[i["block"]], consts))
            self.modules[m["name"]] = insts
        self.order = {n: self._order(n) for n in self.modules}

    def instance(self, module, name):
        return next(i for i in self.modules[module] if i.name == name)

    def _order(self, module):
        insts = self.modules[module]
        producer = {sig: k for k, inst in enumerate(insts) for sig in inst.outputs.values()}
        deps = {k: {producer[s] for s in inst.reads if s in producer} - {k} for k, inst in enumerate(insts)}
        users = {k: set() for k in deps}
        for k, ds in deps.items():
            for d in ds:
                users[d].add(k)
        remaining = {k: len(ds) for k, ds in deps.items()}
        heap = [k for k, n in remaining.items() if n == 0]
        heapq.heapify(heap)
        order = []
        while heap:
            k = heapq.heappop(heap)
            order.append(k)
            for u in users[k]:
                remaining[u] -= 1
                if remaining[u] == 0:
                    heapq.heappush(heap, u)
        if len(order) != len(insts):
            raise DesignError(f"{module}: cyclic data flow between instances")
        return [insts[k] for k in order]


def load_design(path=None, spec=None):
    spec = spec or load(default_spec_path())
    with open(path or default_design_path(), encoding="utf-8") as f:
        return Design(json.load(f), spec)


# ---------------------------------------------------------------------------
def check_design(design):
    """Static checks; returns a list of (severity, where, message)."""
    rep = []
    spec = design.spec
    for b in design.blocks.values():
        b.check(rep)
        for n, p in b.state.items():
            if p["type"] not in design.raw.get("types", {}):
                rep.append(("ERROR", f"block {b.name}", f"state {n} has undeclared type {p['type']}"))
    for mname, insts in design.modules.items():
        if mname not in spec.modules:
            rep.append(("ERROR", f"module {mname}", "not a module of the requirements specification"))
            continue
        req = spec.modules[mname]
        produced, implemented = {}, {}
        for inst in insts:
            where = f"{mname}.{inst.name}"
            b = inst.block
            if set(inst.raw["params"]) != set(b.params):
                rep.append(("ERROR", where, f"parameters {sorted(inst.raw['params'])} do not match block "
                            f"{b.name} {sorted(b.params)}"))
            for p, spec_p in b.params.items():
                v = inst.params.get(p)
                if spec_p.get("integer") and v is not None and (v.denominator != 1 or v < spec_p.get("minimum", v)):
                    rep.append(("ERROR", where, f"parameter {p} = {v} must be an integer >= {spec_p.get('minimum')}"))
            if set(inst.bindings) != set(b.inputs):
                rep.append(("ERROR", where, f"inputs {sorted(inst.bindings)} do not match block {b.name} "
                            f"{sorted(b.inputs)}"))
            if set(inst.outputs) != set(b.outputs):
                rep.append(("ERROR", where, f"outputs {sorted(inst.outputs)} do not match block {b.name}"))
            impl = set(inst.raw.get("implements", []))
            for sig in inst.outputs.values():
                if sig in produced:
                    rep.append(("ERROR", where, f"signal {sig} also produced by {produced[sig]}"))
                produced[sig] = inst.name
                if sig not in req.variables or req.variables[sig]["class"] == "input":
                    rep.append(("ERROR", where, f"output signal {sig} is not a computed NuSCR variable of {mname}"))
                elif sig not in impl:
                    rep.append(("ERROR", where, f"output signal {sig} is not listed in 'implements'"))
            for name in inst.reads:
                if name not in req.variables and name not in spec.constants:
                    rep.append(("ERROR", where, f"reads {name}, which is neither a signal nor a constant"))
            for v in impl:
                if v not in req.definitions:
                    rep.append(("ERROR", where, f"implements unknown NuSCR definition {v}"))
                implemented.setdefault(v, []).append(inst.name)
            # Exact wiring: the instance reads exactly the NuSCR variables, and uses exactly the constants, that
            # the nodes it implements read (k_Scan_Period may additionally convert delays into scans).
            known = [v for v in impl if v in req.definitions]
            if known:
                need = set().union(*(req.data_inputs(v) for v in known)) - impl
                got = {r for r in inst.reads if r in req.variables}
                if need != got:
                    rep.append(("ERROR", where, f"wiring differs from the requirements: missing {sorted(need - got)}, "
                                                f"extra {sorted(got - need)}"))
                need_k = set().union(*(req.definitions[v].references() for v in known)) & set(spec.constants)
                got_k = {r for r in inst.reads if r in spec.constants}
                for text in inst.raw["params"].values():
                    got_k |= variables(parse(text)) & set(spec.constants)
                got_k.discard("k_Scan_Period")
                if need_k != got_k:
                    rep.append(("ERROR", where, f"constants differ from the requirements: missing "
                                                f"{sorted(need_k - got_k)}, extra {sorted(got_k - need_k)}"))
        for inst in insts:
            for name in inst.reads:
                if name in req.variables and req.variables[name]["class"] != "input" and name not in produced:
                    rep.append(("ERROR", f"{mname}.{inst.name}", f"reads {name}, which no instance produces"))
        for v in req.definitions:
            if v not in implemented:
                rep.append(("ERROR", f"module {mname}", f"NuSCR definition {v} is not implemented by any instance"))
            elif len(implemented[v]) > 1:
                rep.append(("ERROR", f"module {mname}", f"NuSCR definition {v} is implemented by "
                                                        f"{len(implemented[v])} instances"))
        for o in req.outputs:
            if o not in produced and req.variables[o]["class"] != "input":
                rep.append(("ERROR", f"module {mname}", f"module output {o} is not produced by the design"))
    return rep


# ---------------------------------------------------------------------------
class DesignModuleSim:
    """Cycle simulator of one module of the design; same input interface as nuscr_sim.ModuleSim."""

    def __init__(self, design, module_name, instance_name=None):
        self.design = design
        self.req = design.spec.modules[module_name]
        self.name = instance_name or module_name
        self.plan = design.order[module_name]
        self.consts = design.spec.constants
        self.domains = {n: self.req.domain(n) for n in self.req.variables}
        self.reset()

    def nominal_inputs(self):
        return {n: to_fraction(self.req.variables[n]["nominal"]) for n in self.req.inputs}

    def reset(self):
        self.cycle = -1
        self.inputs = self.nominal_inputs()
        self.states = {inst.name: inst.block.initial_state() for inst in self.plan}
        self.signals = {}

    def set_inputs(self, changes):
        for n, v in (changes or {}).items():
            if n not in self.req.variables or self.req.variables[n]["class"] != "input":
                raise SimulationError(f"{self.name}: {n} is not an input of module {self.req.name}")
            v = to_fraction(v)
            dom = self.domains[n]
            if dom is not None and v not in dom:
                raise SimulationError(f"{self.name}: value {v} is outside the domain of {n}")
            self.inputs[n] = v

    def step(self, changes=None):
        self.set_inputs(changes)
        self.cycle += 1
        env = dict(self.consts)
        env.update(self.inputs)
        for inst in self.plan:
            ins = {port: f(env) for port, f in inst.bindings.items()}
            outs, self.states[inst.name] = inst.block.step(inst.params, self.states[inst.name], ins)
            for port, sig in inst.outputs.items():
                env[sig] = outs[port]
        self.signals = {k: v for k, v in env.items() if k not in self.consts}
        return dict(self.signals)


class DesignSystemSim:
    """All module instances of the requirements 'system' section, simulated with the design."""

    def __init__(self, design):
        sysd = design.spec.system
        self.instances = {i["name"]: DesignModuleSim(design, i["module"], i["name"]) for i in sysd["instances"]}
        self.modules = {i["name"]: i["module"] for i in sysd["instances"]}
        self.order = sysd["evaluation_order"]
        self.links = {}
        for c in sysd["connections"]:
            ti, tv = c["to"].split(".", 1)
            si, sv = c["from"].split(".", 1)
            self.links.setdefault(ti, []).append((tv, si, sv))

    def reset(self):
        for s in self.instances.values():
            s.reset()

    @property
    def cycle(self):
        return next(iter(self.instances.values())).cycle

    def step(self, changes=None):
        changes = changes or {}
        per = {n: {} for n in self.instances}
        for key, vals in changes.items():
            targets = [key] if key in self.instances else [n for n, m in self.modules.items() if m == key]
            if not targets:
                raise SimulationError(f"unknown instance or module {key}")
            for t in targets:
                per[t].update(vals)
        results = {}
        for name in self.order:
            ins = per[name]
            for tv, si, sv in self.links.get(name, []):
                ins[tv] = results[si][sv]
            results[name] = self.instances[name].step(ins)
        return results


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Static check of the function-block design.")
    ap.add_argument("design", nargs="?", default=str(default_design_path()))
    args = ap.parse_args(argv)
    try:
        design = load_design(args.design)
    except DesignError as e:
        print(f"STRUCTURAL ERROR: {e}")
        return 1
    rep = check_design(design)
    for sev, where, msg in rep:
        print(f"{sev:7s} {where}: {msg}")
    n_inst = sum(len(v) for v in design.modules.values())
    print(f"Checked {args.design}: {len(design.blocks)} block types, {n_inst} instances; "
          f"{sum(1 for r in rep if r[0] == 'ERROR')} error(s)")
    return 1 if any(r[0] == "ERROR" for r in rep) else 0


if __name__ == "__main__":
    sys.exit(main())
