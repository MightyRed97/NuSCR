"""Verification of the function-block design against the NuSCR requirements.

Proofs (exact, for all inputs):
  1. Block equivalence: each stateful pattern block (FB_TRIP_BISTABLE, FB_EDGE, FB_OPERATING_BYPASS) is run in
     lock-step with the NuSCR pattern it implements, over every reachable pair of states and every input
     combination (breadth-first search of the product), for every delay value used by the design.
  2. Instance proofs, one per instance, so that every NuSCR definition is covered:
     - pattern instances: every transition guard of the requirement machine equals the pattern guard with the
       instance's input bindings substituted (Fourier-Motzkin decision procedure), with identical states,
       targets, actions and delay; together with 1 this gives instance-level equivalence;
     - stateless BP instances: the block output, evaluated symbolically, equals the SDT value for every input;
     - setpoint instances: inductive refinement proof (requirement state and value <-> block state);
     - CP instances: exhaustive enumeration of all inputs of the instance (4,096 per vote block, 32,768 for
       the initiation block) against the requirement SDTs.
  Because every signal is named after the NuSCR variable it implements, and the static check proves that each
  instance reads exactly the variables its requirement node reads, the instance proofs compose into module
  equivalence.
Tests (additional evidence):
  3. Back-to-back: BP design and requirements model driven with randomised sequences that include values
     placed relative to the moving setpoints; every computed signal is compared in every scan, and the
     transition coverage of the requirements model is reported.  The four-channel system is compared with
     independent random inputs per channel, bypass switches and link failures, and on the example scenarios.

    python -B tools/design_verify.py [--scans N] [--seeds 1,2] [--out 6_verification/design_verification_report.json]
"""
from __future__ import annotations

import argparse
import itertools
import json
import random
import sys
from collections import deque
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_spec as B                                                         # noqa: E402
from design_model import DesignModuleSim, DesignSystemSim, is_bool, load_design  # noqa: E402
from nuscr_check import _Solver                                                # noqa: E402
from nuscr_lang import (TRUE, compile_expr, conj, disj, evaluate, neg, parse,   # noqa: E402
                        simplify, variables)
from nuscr_model import FORMAT, Spec, topological_order                        # noqa: E402
from nuscr_sim import ModuleSim, SystemSim, _read_scenario                     # noqa: E402
from paths import DESIGN_REPORT, SCENARIOS_DIR                                 # noqa: E402

F = Fraction
ONE_AST, ZERO_AST = ("num", F(1)), ("num", F(0))


# ===========================================================================
# Abstract NuSCR machines, built with the same pattern functions as the specification
# ===========================================================================

def _abstract_spec(build, inputs, delay=1):
    consts = [{"name": "k_Scan_Period", "value": "1", "unit": "scan", "status": "DERIVED", "description": ""},
              {"name": "k_ABS_Trip_Delay", "value": str(delay), "unit": "scan", "status": "DERIVED",
               "description": ""}]
    mb = B.ModuleBuilder("ABS", "abstract pattern", "", {c["name"] for c in consts})
    for n in inputs:
        mb.var(n, "input", "bool", n, nominal="0")
    g = mb.group("g_ABS", "abstract", "abstract")
    build(mb, g)
    raw = {"format": FORMAT, "types": B.TYPES, "constants": consts, "modules": [mb.to_json()]}
    order, _ = topological_order(Spec(raw).modules["ABS"])
    raw["modules"][0]["execution_order"] = order
    return Spec(raw)


def trip_pattern_spec(delay, with_bypass):
    def build(mb, g):
        mb.var("th_ABS_Trip", "timed_history", "trip", "abstract trip")
        B.trip_machine(mb, g, "ABS", "Trip", "OK = 0", "OK = 1", "COND = 1", "COND = 0", "RESET = 1",
                       "RESET = 0", "BYP" if with_bypass else None, [], "abstract")
    return _abstract_spec(build, ["OK", "COND", "RESET"] + (["BYP"] if with_bypass else []), delay)


def edge_pattern_spec():
    def build(mb, g):
        mb.var("h_ABS_Pulse", "history", "pulse", "abstract pulse")
        B.pulse_machine(g, "h_ABS_Pulse", "PB", [], "abstract")
    return _abstract_spec(build, ["PB"])


def bypass_pattern_spec():
    def build(mb, g):
        mb.var("h_ABS_OB_Sta", "history", "bypass_status", "abstract bypass")
        B.ob_machine(g, "ABS", "PERM", "PULSE", [], "abstract")
    return _abstract_spec(build, ["PERM", "PULSE"])


# ===========================================================================
# 1. Block equivalence by product exploration
# ===========================================================================

def _req_key(sim, cap):
    return tuple((v, sim.states[v], sim.values[v], min(sim.cycle + 1 - sim.entered[v], cap))
                 for v in sorted(sim.states))


def _req_restore(sim, key):
    sim.cycle = 1000
    for v, state, value, tau in key:
        sim.states[v], sim.values[v], sim.entered[v] = state, value, 1001 - tau


def _freeze(d):
    return tuple(sorted(d.items()))


def explore(req_sim, design_step, design_init, input_space, compare, cap):
    """BFS over the product of the requirements machine and a design block.  Returns (pairs, steps, cex)."""
    start = (_req_key(req_sim, cap), design_init)
    parent = {start: None}
    queue = deque([start])
    steps = 0
    while queue:
        node = queue.popleft()
        rk, ds = node
        for inp in input_space:
            _req_restore(req_sim, rk)
            out_r = req_sim.step(dict(inp))
            out_d, nds = design_step(ds, inp)
            steps += 1
            for rv, dv in compare:
                if out_r[rv] != out_d[dv]:
                    path, n = [inp], node
                    while parent[n] is not None:
                        n, i = parent[n]
                        path.append(i)
                    return len(parent), steps, {"inputs": [dict(i) for i in reversed(path)], "signal": rv,
                                                "requirements": str(out_r[rv]), "design": str(out_d[dv])}
            nxt = (_req_key(req_sim, cap), nds)
            if nxt not in parent:
                parent[nxt] = (node, inp)
                queue.append(nxt)
    return len(parent), steps, None


def _space(names):
    return [tuple(sorted({n: F(v) for n, v in zip(names, bits)}.items()))
            for bits in itertools.product((0, 1), repeat=len(names))]


def verify_trip_bistable(design, delays=(1, 2, 3, 5)):
    block = design.blocks["FB_TRIP_BISTABLE"]
    results = []
    for with_bypass in (True, False):
        for delay in delays:
            sim = ModuleSim(trip_pattern_spec(delay, with_bypass), "ABS")
            params = {"DELAY": F(delay)}
            names = ["OK", "COND", "RESET"] + (["BYP"] if with_bypass else [])

            def step(ds, inp, params=params):
                ins = dict(inp)
                ins.setdefault("BYP", F(0))
                out, ns = block.step(params, dict(ds), ins)
                return out, _freeze(ns)
            pairs, steps, cex = explore(sim, step, _freeze(block.initial_state()), _space(names),
                                        [("th_ABS_Trip", "Q")], delay + 1)
            results.append({"check": f"FB_TRIP_BISTABLE == trip TTS pattern (delay {delay} scan"
                                     f"{'s' if delay != 1 else ''}, {'with' if with_bypass else 'without'} bypass)",
                            "size": f"{pairs} state pairs, {steps} steps", "passed": cex is None,
                            "counterexample": cex})
    return results


def verify_edge_and_bypass_blocks(design):
    results = []
    for block_name, spec, var, port, names in (
            ("FB_EDGE", edge_pattern_spec(), "h_ABS_Pulse", "Q", ["PB"]),
            ("FB_OPERATING_BYPASS", bypass_pattern_spec(), "h_ABS_OB_Sta", "Q", ["PERM", "PULSE"])):
        block = design.blocks[block_name]
        sim = ModuleSim(spec, "ABS")

        def step(ds, inp, block=block):
            out, ns = block.step({}, dict(ds), dict(inp))
            return out, _freeze(ns)
        pairs, steps, cex = explore(sim, step, _freeze(block.initial_state()), _space(names), [(var, port)], 2)
        results.append({"check": f"{block_name} == {'pulse' if block_name == 'FB_EDGE' else 'operating-bypass'} "
                                 f"FSM pattern (all input sequences)",
                        "size": f"{pairs} state pairs, {steps} steps", "passed": cex is None, "counterexample": cex})
    return results


# ===========================================================================
# 2. Instance proofs
# ===========================================================================

def subst_ast(e, mapping):
    """Replace variables by ASTs."""
    tag = e[0]
    if tag == "var":
        return mapping.get(e[1], e)
    if tag in ("num", "bool"):
        return e
    if tag in ("neg", "not"):
        return (tag, subst_ast(e[1], mapping))
    if tag == "cmp":
        return ("cmp", e[1], subst_ast(e[2], mapping), subst_ast(e[3], mapping))
    return (tag, subst_ast(e[1], mapping), subst_ast(e[2], mapping))


def bind_atoms(e, bindings):
    """Substitute block-port atoms of an abstract guard by the instance bindings.  'P = 1' / 'P = 0' with a
    boolean binding b become b / !b; a numeric binding replaces the variable itself."""
    tag = e[0]
    if tag == "cmp" and e[2][0] == "var" and e[2][1] in bindings and e[3][0] == "num":
        b = bindings[e[2][1]]
        if is_bool(b):
            k = e[3][1]
            if e[1] == "=" and k in (0, 1):
                return b if k == 1 else neg(b)
            raise ValueError(f"boolean port {e[2][1]} used in {e}")
        return ("cmp", e[1], b, e[3])
    if tag == "var" and e[1] in bindings:
        b = bindings[e[1]]
        if is_bool(b):
            raise ValueError(f"boolean port {e[1]} used as a number")
        return b
    if tag in ("num", "bool"):
        return e
    if tag in ("neg", "not"):
        return (tag, bind_atoms(e[1], bindings))
    if tag == "cmp":
        return ("cmp", e[1], bind_atoms(e[2], bindings), bind_atoms(e[3], bindings))
    return (tag, bind_atoms(e[1], bindings), bind_atoms(e[2], bindings))


def _equivalent(solver, a, b):
    """None if a <=> b for all inputs, otherwise a counterexample."""
    return solver.model(conj(a, neg(b))) or solver.model(conj(neg(a), b))


def prove_pattern_instance(design, inst, solver, abstract_def, port_map, word_map=None):
    """Prove that the requirement machine implemented by `inst` equals the abstract pattern machine with the
    instance bindings substituted: same states, initial state, transitions (ids, source, target, action,
    timing) and equivalent guards."""
    req = design.spec.modules["BP"]
    var = inst.raw["implements"][0]
    d = req.definitions[var]
    bindings = {abs_name: inst.binding_asts[port] for abs_name, port in port_map.items()}
    rename = word_map or (lambda s: s)
    problems = []
    if sorted(rename(s) for s in abstract_def.states) != sorted(d.states):
        problems.append(f"states {d.states} differ from the pattern")
    if rename(abstract_def.initial_state) != d.initial_state:
        problems.append("initial state differs from the pattern")
    if evaluate(abstract_def.initial_value[1], {}) != evaluate(d.initial_value[1], design.spec.constants):
        problems.append("initial value differs from the pattern")
    concrete = {t.id: t for t in d.transitions}
    if set(concrete) != {t.id for t in abstract_def.transitions}:
        problems.append(f"transition ids {sorted(concrete)} differ from the pattern")
    consts = design.spec.constants
    for at in abstract_def.transitions:
        ct = concrete.get(at.id)
        if ct is None:
            continue
        if (rename(at.source), rename(at.target)) != (ct.source, ct.target):
            problems.append(f"{at.id}: {ct.source}->{ct.target} differs from the pattern")
        if evaluate(at.action, {}) != evaluate(ct.action, consts):
            problems.append(f"{at.id}: action {ct.action_text} differs from the pattern")
        if at.timed != ct.timed:
            problems.append(f"{at.id}: timing differs from the pattern")
        elif ct.timed:
            lo, hi = evaluate(ct.lower, consts), evaluate(ct.upper, consts)
            if lo != hi or inst.params.get("DELAY") != lo / consts["k_Scan_Period"]:
                problems.append(f"{at.id}: DELAY {inst.params.get('DELAY')} differs from the requirement delay "
                                f"[{lo}, {hi}] / k_Scan_Period")
        try:
            sg = bind_atoms(at.guard, bindings)
        except ValueError as e:
            problems.append(f"{at.id}: {e}")
            continue
        cex = _equivalent(solver, sg, ct.guard)
        if cex is not None:
            problems.append(f"{at.id}: guard not equivalent to the requirement ({solver.describe(cex)})")
    return problems


# --- symbolic evaluation of block bodies (guarded values) ---------------------

def _gv_input(ast):
    return [(ast, ONE_AST), (neg(ast), ZERO_AST)] if is_bool(ast) else [(TRUE, ast)]


def _prune(gv):
    out = []
    for c, v in gv:
        c = simplify(c)
        if c != ("bool", False):
            out.append((c, v))
    return out


def _gv_combos(ast, env):
    names = [n for n in sorted(variables(ast)) if n in env]
    for combo in itertools.product(*(env[n] for n in names)):
        yield conj(*(c for c, _ in combo)), subst_ast(ast, {n: v for n, (_, v) in zip(names, combo)})


def _gv_expr(ast, env):
    out = []
    for cond, e in _gv_combos(ast, env):
        out += [(conj(cond, e), ONE_AST), (conj(cond, neg(e)), ZERO_AST)] if is_bool(ast) else [(cond, e)]
    return _prune(out)


def _gv_bool(ast, env):
    return disj(*[conj(c, e) for c, e in _gv_combos(ast, env)])


def symbolic_block(block, inputs, params, last):
    """Guarded values of every variable of a block body.  inputs: port -> AST; params: name -> Fraction;
    last: state -> AST (value at the end of the previous scan)."""
    env = {p: [(TRUE, ("num", v))] for p, v in params.items()}
    env.update({p: _gv_input(a) for p, a in inputs.items()})
    env.update({"last_" + s: [(TRUE, a)] for s, a in last.items()})
    for st in block.statements:
        gv, prior = [], TRUE
        for guard, value in st.branches:
            if guard is None:
                path = prior
            else:
                g = _gv_bool(guard, env)
                path, prior = conj(prior, g), conj(prior, neg(g))
            gv += [(conj(path, c), v) for c, v in _gv_expr(value, env)]
        env[st.var] = _prune(gv)
    return env


def gv_equal(solver, gv1, gv2):
    for c1, v1 in gv1:
        for c2, v2 in gv2:
            cex = solver.model(conj(c1, c2, neg(("cmp", "=", v1, v2))))
            if cex is not None:
                return cex
    return None


def prove_sdt_instance(design, inst, solver):
    req = design.spec.modules["BP"]
    var = inst.raw["implements"][0]
    d = req.definitions[var]
    port = next(p for p, s in inst.outputs.items() if s == var)
    env = symbolic_block(inst.block, inst.binding_asts, inst.params, {})
    req_gv = [(r.pred, d.actions[r.action][1]) for r in d.rules]
    cex = gv_equal(solver, env[port], req_gv)
    return [] if cex is None else [f"output differs from SDT {var}: {solver.describe(cex)}"]


def _fsm_next(d, state, h_value):
    """Guarded next value of an FSM variable from `state`, with the variable's previous value h_value."""
    hv = {d.var: h_value}
    out = [(subst_ast(t.guard, hv), subst_ast(t.action, hv)) for t in d.outgoing(state)]
    out.append((subst_ast(neg(disj(*[t.guard for t in d.outgoing(state)])), hv), h_value))
    return out, [(subst_ast(t.guard, hv), t.target) for t in d.outgoing(state)]


def prove_setpoint_instance(design, inst, solver):
    """Inductive refinement: (requirement state, value) <-> (block state), checked for every case."""
    req = design.spec.modules["BP"]
    var = inst.raw["implements"][0]
    d = req.definitions[var]
    init_value = d.initial_value[1]
    h = ("var", var)
    port = next(p for p, s in inst.outputs.items() if s == var)
    problems = []
    if inst.block.name == "FB_SP_MANUAL_RESET":
        cases = [("first scan", "ACTIVE", init_value, {"SP": ZERO_AST, "STARTED": ZERO_AST}),
                 ("later scans", "ACTIVE", h, {"SP": h, "STARTED": ONE_AST})]
    else:
        cases = [("first scan", "INIT", init_value, {"SP": ZERO_AST, "READY": ZERO_AST, "STARTED": ZERO_AST}),
                 ("before first valid scan", "INIT", init_value,
                  {"SP": init_value, "READY": ZERO_AST, "STARTED": ONE_AST}),
                 ("tracking", "TRACKING", h, {"SP": h, "READY": ONE_AST, "STARTED": ONE_AST})]
    for label, state, h_value, last in cases:
        env = symbolic_block(inst.block, inst.binding_asts, inst.params, last)
        value_gv, targets = _fsm_next(d, state, h_value)
        cex = gv_equal(solver, env[port], value_gv)
        if cex is not None:
            problems.append(f"{label}: value differs from {var}: {solver.describe(cex)}")
        if "READY" in env:
            # block READY <-> requirement state TRACKING, after this scan
            def ind(s):
                return ONE_AST if s == "TRACKING" else ZERO_AST
            want = [(g, ind(tgt)) for g, tgt in targets]
            want.append((neg(disj(*[g for g, _ in targets])), ind(state)))
            cex = gv_equal(solver, env["READY"], want)
            if cex is not None:
                problems.append(f"{label}: READY does not track the requirement state: {solver.describe(cex)}")
    return problems


def _sdt_evaluator(module, names):
    plan = []
    for v in module.execution_order:
        if v in names:
            d = module.definitions[v]
            plan.append((v, [(compile_expr(r.pred), compile_expr(d.actions[r.action][1])) for r in d.rules]))

    def run(env):
        for v, rules in plan:
            env[v] = next(F(a(env)) for p, a in rules if p(env))
        return env
    return run


def prove_cp_instance(design, inst):
    """Exhaustive: every combination of the instance's inputs, requirement SDTs vs block."""
    cp = design.spec.modules["CP"]
    implemented = set(inst.raw["implements"])
    inputs = sorted(inst.reads)
    run_req = _sdt_evaluator(cp, implemented)
    out_var = next(iter(inst.outputs.values()))
    port = next(iter(inst.outputs))
    consts = design.spec.constants
    for bits in itertools.product((0, 1), repeat=len(inputs)):
        env = dict(consts)
        env.update({n: F(b) for n, b in zip(inputs, bits)})
        want = run_req(dict(env))[out_var]
        got = inst.block.step(inst.params, {}, {p: f(env) for p, f in inst.bindings.items()})[0][port]
        if want != got:
            return 2 ** len(inputs), [f"{out_var} differs for {dict(zip(inputs, bits))}: requirements {want}, "
                                      f"design {got}"]
    return 2 ** len(inputs), []


def verify_instances(design):
    """One proof per instance; returns (results, covered NuSCR definitions)."""
    solver = _Solver(design.spec.modules["BP"])
    word = lambda w: (lambda s: w + s[4:] if s.startswith("TRIP_") else s)          # noqa: E731
    abstract = {(wb): trip_pattern_spec(1, wb).modules["ABS"].definitions["th_ABS_Trip"] for wb in (True, False)}
    edge_def = edge_pattern_spec().modules["ABS"].definitions["h_ABS_Pulse"]
    ob_def = bypass_pattern_spec().modules["ABS"].definitions["h_ABS_OB_Sta"]
    results, covered = [], set()
    for mname in ("BP", "CP"):
        for inst in design.modules[mname]:
            b = inst.block.name
            if mname == "CP":
                size, problems = prove_cp_instance(design, inst)
                kind, size = "exhaustive", f"{size} input combinations"
            elif b == "FB_TRIP_BISTABLE":
                with_bypass = inst.raw["inputs"]["BYP"] != "0"
                var = inst.raw["implements"][0]
                w = "PRETRIP" if var.endswith("_Pretrip") else "TRIP"
                port_map = {"OK": "OK", "COND": "COND", "RESET": "RESET"}
                if with_bypass:
                    port_map["BYP"] = "BYP"
                problems = prove_pattern_instance(design, inst, solver, abstract[with_bypass], port_map, word(w))
                kind, size = "pattern", f"{len(abstract[with_bypass].transitions)} guards"
            elif b == "FB_EDGE":
                problems = prove_pattern_instance(design, inst, solver, edge_def, {"PB": "PB"})
                kind, size = "pattern", f"{len(edge_def.transitions)} guards"
            elif b == "FB_OPERATING_BYPASS":
                problems = prove_pattern_instance(design, inst, solver, ob_def, {"PERM": "PERM", "PULSE": "PULSE"})
                kind, size = "pattern", f"{len(ob_def.transitions)} guards"
            elif b in ("FB_SP_RATE_UP", "FB_SP_RATE_DOWN", "FB_SP_MANUAL_RESET"):
                problems = prove_setpoint_instance(design, inst, solver)
                kind, size = "refinement", "inductive"
            elif not inst.block.state:
                problems = prove_sdt_instance(design, inst, solver)
                kind, size = "symbolic", "all inputs"
            else:
                problems = [f"no proof method for block {b}"]
                kind, size = "none", ""
            if not problems:
                covered |= set(inst.raw["implements"])
            results.append({"check": f"{mname}.{inst.name} ({b}) == {', '.join(inst.raw['implements'])}"
                            if len(inst.raw["implements"]) == 1
                            else f"{mname}.{inst.name} ({b}) == {len(inst.raw['implements'])} NuSCR definitions",
                            "method": kind, "size": size, "passed": not problems, "problems": problems})
    return results, covered


# ===========================================================================
# 3. Back-to-back tests
# ===========================================================================

def _signal_candidates(spec, s):
    sig = B.SIGNALS[s]
    c = spec.constants
    vals = [F(sig["vmin"]), F(sig["vmax"])]
    for t in B.TRIPS:
        if t.get("signal") != s:
            continue
        x = t["id"]
        for k in ("Trip_SP", "Pretrip_SP", "OB_Perm", "SP_Min", "SP_Max"):
            if k in t["c"]:
                vals.append(c[f"k_{x}_{k}"])
        if "Band" in t["c"]:
            for lim in ("SP_Min", "SP_Max"):
                vals += [c[f"k_{x}_{lim}"] + c[f"k_{x}_Band"], c[f"k_{x}_{lim}"] - c[f"k_{x}_Band"]]
        if "Pretrip_Margin" in t["c"]:
            vals += [c[f"k_{x}_SP_Max"] + c[f"k_{x}_Pretrip_Margin"], c[f"k_{x}_SP_Min"] - c[f"k_{x}_Pretrip_Margin"]]
        if "Trip_SP" in t["c"]:
            vals += [c[f"k_{x}_Trip_SP"] - c[f"k_{x}_Trip_Hys"], c[f"k_{x}_Trip_SP"] + c[f"k_{x}_Trip_Hys"],
                     c[f"k_{x}_Pretrip_SP"] - c[f"k_{x}_Pretrip_Hys"], c[f"k_{x}_Pretrip_SP"] + c[f"k_{x}_Pretrip_Hys"]]
    return vals


def _relative_candidates(spec, values, s):
    """Values placed relative to the current variable setpoints that compare with f_<s>."""
    c = spec.constants
    out = []
    for t in B.TRIPS:
        x = t["id"]
        if t.get("signal") != s or f"h_{x}_Trip_SP" not in values:
            continue
        h = values[f"h_{x}_Trip_SP"]
        m, th, ph = c[f"k_{x}_Pretrip_Margin"], c[f"k_{x}_Trip_Hys"], c[f"k_{x}_Pretrip_Hys"]
        band = c[f"k_{x}_Band"]
        step = c.get(f"k_{x}_SP_Step", F(0))
        rising = B.LOGIC[t["logic"]]["rising"]
        sgn = 1 if rising else -1
        out += [h, h - sgn * th, h - sgn * (m), h - sgn * (m + ph), h + sgn * step, h - sgn * step,
                h - sgn * band, h - sgn * (band - step), h + sgn * (band + step), h + band, h - band]
    return out


class BoundaryInputs:
    """Randomised BP inputs: drift, jumps to fixed thresholds and to values relative to the moving setpoints
    (read from the requirements model), holds, out-of-range values, toggling diagnostics and pushbuttons."""

    def __init__(self, spec, seed):
        self.spec, self.rng = spec, random.Random(seed)
        m = spec.modules["BP"]
        self.m = m
        self.nominal = {n: F(str(m.variables[n]["nominal"])) for n in m.inputs}
        self.values = dict(self.nominal)
        self.fixed = {s: _signal_candidates(spec, s) for s in B.SIGNALS}
        self.hold = {}

    def next(self, req_values):
        r, ch = self.rng, {}
        for s, sig in B.SIGNALS.items():
            name = f"f_{s}"
            if self.hold.get(name, 0) > 0:
                self.hold[name] -= 1
                continue
            lo, hi = F(sig["vmin"]), F(sig["vmax"])
            span, p = hi - lo, r.random()
            if p < 0.04:
                rel = _relative_candidates(self.spec, req_values, s)
                if rel:
                    ch[name] = r.choice(rel)
                    self.hold[name] = r.randint(0, 4)
            elif p < 0.07:
                ch[name] = r.choice(self.fixed[s]) + r.choice((-1, 0, 0, 1)) * span / 10000
                self.hold[name] = r.randint(0, 4)
            elif p < 0.08:
                ch[name] = lo - span / 20 if r.random() < 0.5 else hi + span / 20
            elif p < 0.30:
                ch[name] = self.values[name] + F(r.randint(-40, 40)) * span / 4001
        if r.random() < 0.02:
            ch["f_NPWR_LOG"] = F(r.choice(["1e-7", "5e-5", "0.0001", "0.0002", "0.005", "0.01", "0.02", "50"]))
        for n, nom in self.nominal.items():
            if self.m.domain(n) is None:
                continue
            p_on, p_off = ((0.02, 0.35) if n.endswith("_Error") else (0.02, 0.1) if n.endswith("_In")
                           else (0.03, 0.3))
            if self.values[n] == nom:
                if r.random() < p_on:
                    ch[n] = 1 - nom
            elif r.random() < p_off:
                ch[n] = nom
        self.values.update(ch)
        return ch


def _computed(module, name):
    return name in module.variables and module.variables[name]["class"] != "input"


def back_to_back_bp(design, seed, scans, coverage=None):
    spec = design.spec
    ds, rs = DesignModuleSim(design, "BP"), ModuleSim(spec, "BP")
    gen = BoundaryInputs(spec, seed)
    m = spec.modules["BP"]
    compared, first = 0, None
    req_values = {}
    for i in range(scans):
        ch = gen.next(req_values)
        a, b = ds.step(ch), rs.step(ch)
        req_values = b
        if coverage is not None:
            coverage.update((v, t) for v, t in rs.last_transition.items())
        for k, v in a.items():
            if _computed(m, k):
                compared += 1
                if b[k] != v and first is None:
                    first = {"scan": i, "signal": k, "requirements": str(b[k]), "design": str(v)}
    return {"check": f"BP back-to-back, boundary-biased random, seed {seed}, {scans} scans",
            "size": f"{compared} computed-signal comparisons", "passed": first is None, "first_mismatch": first}


def back_to_back_system_random(design, seed, scans):
    spec = design.spec
    ds, rs = DesignSystemSim(design), SystemSim(spec)
    gens = {i: BoundaryInputs(spec, seed * 100 + k) for k, i in enumerate(n for n in rs.instances
                                                                          if rs.modules[n] == "BP")}
    rng = random.Random(seed)
    links = {f"f_{c}_Link_Error": F(0) for c in B.CHANNELS}
    compared, first, req = 0, None, {}
    for i in range(scans):
        changes = {inst: g.next(req.get(inst, {})) for inst, g in gens.items()}
        for n in links:
            if rng.random() < (0.01 if links[n] == 0 else 0.2):
                links[n] = 1 - links[n]
        changes["CP_A"] = dict(links)
        a, req = ds.step(changes), rs.step(changes)
        for inst in a:
            mod = spec.modules[rs.modules[inst]]
            for k, v in a[inst].items():
                if _computed(mod, k):
                    compared += 1
                    if req[inst][k] != v and first is None:
                        first = {"scan": i, "signal": f"{inst}.{k}", "requirements": str(req[inst][k]),
                                 "design": str(v)}
    return {"check": f"system back-to-back, independent random channels with bypass switches and link failures, "
                     f"seed {seed}, {scans} scans",
            "size": f"{compared} computed-signal comparisons", "passed": first is None, "first_mismatch": first}


def back_to_back_system(design, scenario):
    spec = design.spec
    events, last = _read_scenario(scenario)
    ds, rs = DesignSystemSim(design), SystemSim(spec)
    compared, first = 0, None
    for c in range(last + 1):
        changes = {}
        for col, val in events.get(c, []):
            inst, var = col.split(".", 1) if "." in col else ("BP", col)
            changes.setdefault(inst, {})[var] = val
        a, b = ds.step(changes), rs.step(changes)
        for inst in a:
            mod = spec.modules[rs.modules[inst]]
            for k, v in a[inst].items():
                if _computed(mod, k):
                    compared += 1
                    if b[inst][k] != v and first is None:
                        first = {"scan": c, "signal": f"{inst}.{k}", "requirements": str(b[inst][k]),
                                 "design": str(v)}
    return {"check": f"system back-to-back, scenario {Path(scenario).name}",
            "size": f"{compared} computed-signal comparisons", "passed": first is None, "first_mismatch": first}


# ===========================================================================

BP_SEEDS = tuple(range(1, 9))         # together these fire every transition of the requirements model
SYSTEM_SEEDS = (1, 2)


def run_all(design, seeds=BP_SEEDS, scans=3000, system_scans=1500, system_seeds=SYSTEM_SEEDS):
    delays = sorted({int(i.params["DELAY"]) for i in design.modules["BP"] if i.block.name == "FB_TRIP_BISTABLE"})
    instances, covered = verify_instances(design)
    report = {"block_equivalence": verify_trip_bistable(design, tuple(sorted(set(delays) | {1, 2, 3, 5})))
              + verify_edge_and_bypass_blocks(design),
              "instance_proofs": instances}
    missing = [f"{m}.{v}" for m, mod in design.spec.modules.items() for v in mod.definitions if v not in covered]
    report["proof_coverage"] = {"definitions": sum(len(m.definitions) for m in design.spec.modules.values()),
                                "proved": len(covered), "missing": missing}
    coverage = set()
    tests = [back_to_back_bp(design, s, scans, coverage) for s in seeds]
    tests += [back_to_back_system_random(design, s, system_scans) for s in system_seeds]
    tests += [back_to_back_system(design, sc) for sc in sorted(SCENARIOS_DIR.glob("*.csv"))]
    report["back_to_back"] = tests
    all_t = {(v, t.id) for v, d in design.spec.modules["BP"].definitions.items() for t in d.transitions}
    report["transition_coverage"] = {"transitions": len(all_t), "fired": len(all_t & coverage),
                                     "not_fired": sorted(f"{v}.{t}" for v, t in all_t - coverage)}
    report["passed"] = (all(r["passed"] for part in ("block_equivalence", "instance_proofs", "back_to_back")
                            for r in report[part]) and not missing
                        and not report["transition_coverage"]["not_fired"])
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="Verify the design against the NuSCR requirements.")
    ap.add_argument("--seeds", default=",".join(map(str, BP_SEEDS)))
    ap.add_argument("--scans", type=int, default=3000)
    ap.add_argument("--system-scans", type=int, default=1500)
    ap.add_argument("--out", default=str(DESIGN_REPORT))
    args = ap.parse_args(argv)
    design = load_design()
    report = run_all(design, tuple(int(s) for s in args.seeds.split(",")), args.scans, args.system_scans)
    Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    for part in ("block_equivalence", "instance_proofs", "back_to_back"):
        ok = sum(r["passed"] for r in report[part])
        print(f"{part}: {ok} of {len(report[part])} passed")
        for r in report[part]:
            if not r["passed"]:
                print(f"  FAIL {r['check']}: {r.get('problems') or r.get('counterexample') or r.get('first_mismatch')}")
    pc, tc = report["proof_coverage"], report["transition_coverage"]
    print(f"NuSCR definitions proved: {pc['proved']} of {pc['definitions']}"
          + (f"; missing {pc['missing']}" if pc["missing"] else ""))
    print(f"Transition coverage of the back-to-back runs: {tc['fired']} of {tc['transitions']}"
          + (f"; not fired: {tc['not_fired']}" if tc["not_fired"] else ""))
    print(f"Result: {'all checks passed' if report['passed'] else 'FAILURES'}  -> {args.out}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
