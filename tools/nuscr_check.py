"""Static analysis of a NuSCR specification (nuscr-json).

Checks performed (all proofs are exact: finite-domain case splitting plus
Fourier-Motzkin elimination over the reals, see nuscr_lang.py):

  Structure   naming prefixes, one definition and one FOD node per computed
              variable, FOD data-flow edges match the expressions, acyclic
              data flow, execution order respects dependencies, outputs exist.
  SDT         completeness (some rule always applies), disjointness (no two
              rules apply together), dead rules, action values in type/range.
  FSM / TTS   determinism (guards leaving a state are pairwise disjoint), dead
              transitions, unreachable states, states with no exit,
              state/action value consistency, initial value, timing bounds
              realisable at the scan period, for every timed guard that the
              untimed exits of its source state cover its negation
              (continuous-persistence semantics), declared must-exit
              conditions (the state is left, not merely re-entered, whenever
              the condition holds) and declared forced values (whenever the
              condition holds, the variable has that value after the scan,
              from every state).
  Invariants  declared ranges proved inductively; proof obligations proved.
  Constants   assertions over constants evaluated; unused constants reported.
  System      instance/connection consistency and type compatibility.
  Traceability every trace names a declared requirement; every requirement is
              implemented by a node or has an explicit allocation.

Usage:  python tools/nuscr_check.py [spec.json] [--json report.json] [--quiet]
Exit status is 1 if any ERROR is found.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nuscr_lang import (TRUE, conj, disj, evaluate, find_model, format_number,   # noqa: E402
                        neg, substitute, to_text, variables)
from nuscr_model import (DEF_KIND_FOR_CLASS, PREFIX_FOR_CLASS, SpecError,        # noqa: E402
                         default_spec_path, load, topological_order)


@dataclass
class Finding:
    severity: str          # ERROR | WARNING
    where: str
    message: str


@dataclass
class Report:
    findings: list = field(default_factory=list)
    stats: Counter = field(default_factory=Counter)

    def error(self, where, msg):
        self.findings.append(Finding("ERROR", where, msg))

    def warn(self, where, msg):
        self.findings.append(Finding("WARNING", where, msg))

    @property
    def errors(self):
        return [f for f in self.findings if f.severity == "ERROR"]

    @property
    def warnings(self):
        return [f for f in self.findings if f.severity == "WARNING"]


class _Solver:
    """Satisfiability queries for one module, with constants substituted and
    proved range invariants added as assumptions."""

    def __init__(self, module):
        self.m = module
        self.consts = module.spec.constants
        self.domains = module.finite_domains()

    def assumptions(self, names):
        parts, seen, todo = [], set(), [n for n in names if n in self.m.ranges]
        while todo:
            n = todo.pop()
            if n in seen:
                continue
            seen.add(n)
            lo, hi = self.m.ranges[n]
            parts.append(conj(("cmp", "<=", lo, ("var", n)), ("cmp", "<=", ("var", n), hi)))
            todo += [v for v in variables(lo) | variables(hi) if v in self.m.ranges]
        return conj(*parts)

    def model(self, formula):
        names = {n for n in variables(formula) if n not in self.consts}
        f = substitute(conj(self.assumptions(names), formula), self.consts)
        return find_model(f, self.domains)

    def describe(self, model):
        if not model:
            return "(any values)"
        parts = []
        for n in sorted(model):
            v = model[n]
            label = ""
            if n in self.m.variables:
                labels = self.m.spec.types[self.m.variables[n]["type"]].get("labels")
                if labels and format_number(v) in labels:
                    label = f" ({labels[format_number(v)]})"
            parts.append(f"{n} = {format_number(v)}{label}")
        return ", ".join(parts)


def check_spec(spec) -> Report:
    rep = Report()
    _check_constants(spec, rep)
    for module in spec.modules.values():
        _check_module(module, rep)
    _check_system(spec, rep)
    _check_traceability(spec, rep)
    return rep


def _check_traceability(spec, rep):
    """Every trace names a declared requirement; every requirement is implemented by at least one
    node or has an explicit allocation to another part of the specification."""
    traced = set()
    for m in spec.modules.values():
        for g in m.groups:
            for rid in g.raw.get("traces", []):
                if rid not in spec.requirements:
                    rep.error(f"{m.name} FOD group {g.name}", f"traces to undeclared requirement {rid}")
        for var, d in m.definitions.items():
            for rid in d.raw.get("traces", []):
                rep.stats["trace links"] += 1
                if rid not in spec.requirements:
                    rep.error(f"{m.name}.{var}", f"traces to undeclared requirement {rid}")
                traced.add(rid)
    for rid, r in spec.requirements.items():
        rep.stats["requirements"] += 1
        if rid not in traced and not r.get("allocated_to"):
            rep.error(f"requirement {rid}", "is not implemented by any node and has no allocation")


# ---------------------------------------------------------------------------
def _check_constants(spec, rep):
    for name, meta in spec.const_meta.items():
        if not name.startswith("k_"):
            rep.error(f"constant {name}", "constants must use the prefix k_")
        if not meta.get("unit"):
            rep.warn(f"constant {name}", "no unit given")
    for a, ast in spec.assertions:
        rep.stats["assertions"] += 1
        try:
            ok = evaluate(ast, spec.constants)
        except KeyError as e:
            rep.error(f"assertion {a['id']}", f"refers to an undeclared constant {e}")
            continue
        if not ok:
            rep.error(f"assertion {a['id']}", f"violated: {a['expr']}  ({a.get('rationale', '')})")
    used = set()
    for meta in spec.const_meta.values():
        if "expr" in meta:
            used |= _names(meta["expr"])
    for a, ast in spec.assertions:
        used |= variables(ast)
    for m in spec.modules.values():
        for d in m.definitions.values():
            used |= d.references()
        for lo, hi in m.ranges.values():
            used |= variables(lo) | variables(hi)
    for name in spec.const_meta:
        if name not in used:
            rep.warn(f"constant {name}", "declared but never used")


def _names(text):
    from nuscr_lang import parse
    return set(variables(parse(text)))


# ---------------------------------------------------------------------------
def _check_module(m, rep):
    M = m.name
    solver = _Solver(m)

    # --- naming and definitions -------------------------------------------------
    for name, v in m.variables.items():
        cls = v["class"]
        if not name.startswith(PREFIX_FOR_CLASS[cls]) or (cls != "timed_history" and name.startswith("th_")):
            rep.error(f"{M}.{name}", f"a {cls} variable must use the prefix {PREFIX_FOR_CLASS[cls]}")
        if cls == "input":
            if name in m.definitions:
                rep.error(f"{M}.{name}", "input variables must not have a definition")
            continue
        d = m.definitions.get(name)
        if d is None:
            rep.error(f"{M}.{name}", f"{cls} variable has no definition")
            continue
        if d.kind != DEF_KIND_FOR_CLASS[cls]:
            rep.error(f"{M}.{name}", f"a {cls} variable must be defined by a {DEF_KIND_FOR_CLASS[cls]}, not {d.kind}")
        node = m.nodes.get(name)
        if node is None:
            rep.error(f"{M}.{name}", "does not appear in the FOD")
        else:
            if node.type != d.kind:
                rep.error(f"{M}.{name}", f"FOD node {node.no} has type {node.type} but the definition is {d.kind}")
            declared = set(node.raw.get("inputs", []))
            actual = m.data_inputs(name)
            if declared != actual:
                rep.error(f"{M}.{name}", "FOD node inputs do not match the definition: missing "
                          f"{sorted(actual - declared)}, extra {sorted(declared - actual)}")
        if d.kind == "SDT" and name in d.references():
            rep.error(f"{M}.{name}", "an SDT (function variable) must not reference itself")
    for name in m.nodes:
        if name not in m.variables:
            rep.error(f"{M} FOD", f"node {m.nodes[name].no} names undeclared variable {name}")
    for g in m.groups:
        if not g.name.startswith("g_"):
            rep.error(f"{M} FOD", f"group {g.no} {g.name} must use the prefix g_")
    for o in m.outputs:
        if o not in m.variables:
            rep.error(f"{M} outputs", f"output {o} is not a declared variable")

    # --- data flow ----------------------------------------------------------------
    order, cycle = topological_order(m)
    if cycle:
        rep.error(f"{M} FOD", f"data-flow cycle among {', '.join(cycle)}")
    declared = m.execution_order
    if Counter(declared) != Counter(list(m.definitions)):
        rep.error(f"{M} execution_order", "must list every defined variable exactly once")
    else:
        pos = {v: i for i, v in enumerate(declared)}
        for v in declared:
            for dep in m.dependencies(v):
                if pos.get(dep, -1) > pos[v]:
                    rep.error(f"{M} execution_order", f"{v} is evaluated before {dep}, which it reads")
    read = set()
    for d in m.definitions.values():
        read |= d.references() - {d.var}
    for name, v in m.variables.items():
        if name not in read and name not in m.outputs:
            rep.warn(f"{M}.{name}", f"{v['class']} variable is neither read nor an output")

    # --- range invariants: initial values and actions are checked below ------------
    for name in m.ranges:
        if m.variables[name]["class"] == "input":
            rep.error(f"{M}.{name}", "ranges on inputs would be unproved assumptions; validate inputs with an SDT")

    # --- definitions --------------------------------------------------------------
    for d in m.definitions.values():
        if d.kind == "SDT":
            _check_sdt(m, d, solver, rep)
        else:
            _check_machine(m, d, solver, rep)

    # --- obligations --------------------------------------------------------------
    for ob, ast in m.obligations:
        rep.stats["obligations"] += 1
        cex = solver.model(neg(ast))
        if cex is not None:
            rep.error(f"{M} obligation {ob['id']}", f"not provable: {ob['formula']}; counterexample: "
                      f"{solver.describe(cex)}")
        else:
            rep.stats["obligations proved"] += 1


def _check_value(m, d, solver, rep, guard, value, where):
    """Prove that `value` (assigned under `guard`) lies in the variable's type and range."""
    var = d.var
    dom = m.domain(var)
    if dom is not None:
        rep.stats["type proofs"] += 1
        in_dom = disj(*[("cmp", "=", value, ("num", v)) for v in dom])
        cex = solver.model(conj(guard, neg(in_dom)))
        if cex is not None:
            rep.error(f"{m.name}.{var} {where}", f"value {to_text(value)} can leave the type "
                      f"{m.variables[var]['type']}: {solver.describe(cex)}")
    if var in m.ranges:
        rep.stats["range proofs"] += 1
        lo, hi = m.ranges[var]
        in_rng = conj(("cmp", "<=", lo, value), ("cmp", "<=", value, hi))
        cex = solver.model(conj(guard, neg(in_rng)))
        if cex is not None:
            rep.error(f"{m.name}.{var} {where}", f"value {to_text(value)} can violate the range invariant "
                      f"[{to_text(lo)}, {to_text(hi)}]: {solver.describe(cex)}")


def _check_sdt(m, d, solver, rep):
    where = f"{m.name}.{d.var} (SDT)"
    rep.stats["SDTs"] += 1
    rep.stats["SDT rules"] += len(d.rules)
    preds = [r.pred for r in d.rules]
    cex = solver.model(neg(disj(*preds)))
    rep.stats["completeness proofs"] += 1
    if cex is not None:
        rep.error(where, f"incomplete: no rule applies when {solver.describe(cex)}")
    for i, ri in enumerate(d.rules):
        for rj in d.rules[i + 1:]:
            rep.stats["disjointness proofs"] += 1
            cex = solver.model(conj(ri.pred, rj.pred))
            if cex is not None:
                rep.error(where, f"inconsistent: rules {ri.index} and {rj.index} both apply when "
                          f"{solver.describe(cex)}")
    for r in d.rules:
        if solver.model(r.pred) is None:
            rep.warn(where, f"rule {r.index} can never apply")
        _check_value(m, d, solver, rep, r.pred, d.actions[r.action][1], f"rule {r.index}")
    used = {r.action for r in d.rules}
    for i, (text, _) in enumerate(d.actions):
        if i not in used:
            rep.warn(where, f"action {text!r} is not selected by any rule")


def _check_machine(m, d, solver, rep):
    where = f"{m.name}.{d.var} ({d.kind})"
    consts = m.spec.constants
    period = consts.get("k_Scan_Period")
    rep.stats[f"{d.kind}s"] += 1
    rep.stats["transitions"] += len(d.transitions)

    # initial value and state values
    _check_value(m, d, solver, rep, TRUE, d.initial_value[1], "initial value")
    sv = d.state_values.get(d.initial_state)
    if sv is not None and evaluate(substitute(d.initial_value[1], consts), {}) != evaluate(substitute(sv[1], consts), {}):
        rep.error(where, f"initial value {d.initial_value[0]} differs from the value of initial state {d.initial_state}")

    enabled_somewhere = set()
    for t in d.transitions:
        tw = f"transition {t.id} ({t.source} -> {t.target})"
        if solver.model(t.guard) is None:
            rep.warn(where, f"{tw} can never be taken (guard unsatisfiable)")
        else:
            enabled_somewhere.add(t.id)
        _check_value(m, d, solver, rep, t.guard, t.action, tw)
        tv = d.state_values.get(t.target)
        if tv is not None:
            cex = solver.model(conj(t.guard, neg(("cmp", "=", t.action, tv[1]))))
            if cex is not None:
                rep.error(where, f"{tw} assigns {t.action_text} but state {t.target} has value {tv[0]}")
        if t.timed:
            rep.stats["timed transitions"] += 1
            try:
                lo = evaluate(substitute(t.lower, consts), {})
                hi = evaluate(substitute(t.upper, consts), {})
            except KeyError as e:
                rep.error(where, f"{tw}: timing bounds must be constant expressions ({e} is a variable)")
                continue
            if not (0 < lo <= hi):
                rep.error(where, f"{tw}: timing bounds must satisfy 0 < lower <= upper "
                          f"(got {format_number(lo)}, {format_number(hi)})")
            elif period is None or period <= 0:
                rep.error(where, "timed transitions need a positive constant k_Scan_Period")
            elif math.ceil(lo / period) * period > hi:
                rep.error(where, f"{tw}: no scan instant (multiple of k_Scan_Period = {format_number(period)}) "
                          f"lies in [{format_number(lo)}, {format_number(hi)}]")
            others = [u.guard for u in d.outgoing(t.source) if u is not t and not u.timed]
            rep.stats["persistence proofs"] += 1
            cex = solver.model(conj(neg(t.guard), neg(disj(*others))))
            if cex is not None:
                rep.error(where, f"{tw}: its guard can become false without {t.source} being left, so the "
                          f"persistence time is not well defined: {solver.describe(cex)}")

    # states that can never be left
    for s in d.states:
        if not d.outgoing(s):
            rep.error(where, f"state {s} has no outgoing transition (it can never be left)")
    for state, text, cond in d.must_exit:
        rep.stats["exit proofs"] += 1
        exits = [t.guard for t in d.outgoing(state) if not t.timed and t.target != state]   # self-loops stay
        cex = solver.model(conj(cond, neg(disj(*exits))))
        if cex is not None:
            rep.error(where, f"state {state} is not left although '{text}' holds: {solver.describe(cex)}")
    for text, cond, vtext, value in d.forced_value:
        # Whenever `cond` holds, the variable must equal `value` after the scan, whatever the current state.
        for s in d.states:
            rep.stats["forced-value proofs"] += 1
            outgoing = d.outgoing(s)
            for t in outgoing:
                cex = solver.model(conj(cond, t.guard, neg(("cmp", "=", t.action, value))))
                if cex is not None:
                    rep.error(where, f"when '{text}' holds, {t.id} ({s} -> {t.target}) assigns {t.action_text} "
                              f"instead of {vtext}: {solver.describe(cex)}")
            sv = d.state_values.get(s)
            holds = sv is not None and solver.model(neg(("cmp", "=", sv[1], value))) is None
            if not holds:
                exits = [t.guard for t in outgoing if not t.timed and t.target != s]
                cex = solver.model(conj(cond, neg(disj(*exits))))
                if cex is not None:
                    rep.error(where, f"when '{text}' holds, state {s} is kept, so the value is not forced to "
                              f"{vtext}: {solver.describe(cex)}")

    # determinism
    for s in d.states:
        out = d.outgoing(s)
        for i, a in enumerate(out):
            for b in out[i + 1:]:
                rep.stats["determinism proofs"] += 1
                cex = solver.model(conj(a.guard, b.guard))
                if cex is not None:
                    rep.error(where, f"non-deterministic in state {s}: {a.id} and {b.id} are both enabled when "
                              f"{solver.describe(cex)}")

    # reachability (through transitions whose guard is satisfiable)
    reach, todo = {d.initial_state}, [d.initial_state]
    while todo:
        s = todo.pop()
        for t in d.outgoing(s):
            if t.id in enabled_somewhere and t.target not in reach:
                reach.add(t.target)
                todo.append(t.target)
    for s in d.states:
        if s not in reach:
            rep.warn(where, f"state {s} is unreachable from {d.initial_state}")
    if d.kind == "TTS" and not any(t.timed for t in d.transitions):
        rep.warn(where, "TTS without timed transitions (an FSM would suffice)")


# ---------------------------------------------------------------------------
def _check_system(spec, rep):
    sysd = spec.system
    if not sysd:
        return
    inst = {}
    for i in sysd.get("instances", []):
        if i["module"] not in spec.modules:
            rep.error(f"system instance {i['name']}", f"unknown module {i['module']}")
        else:
            inst[i["name"]] = spec.modules[i["module"]]
    order = sysd.get("evaluation_order", list(inst))
    if sorted(order) != sorted(inst):
        rep.error("system", "evaluation_order must list every instance exactly once")
    pos = {n: k for k, n in enumerate(order)}
    targets = Counter()
    for c in sysd.get("connections", []):
        rep.stats["connections"] += 1
        try:
            ti, tv = c["to"].split(".", 1)
            si, sv = c["from"].split(".", 1)
        except ValueError:
            rep.error("system", f"malformed connection {c}")
            continue
        if ti not in inst or si not in inst:
            rep.error("system", f"connection {c['from']} -> {c['to']} names an unknown instance")
            continue
        tm, sm = inst[ti], inst[si]
        if tv not in tm.variables or tm.variables[tv]["class"] != "input":
            rep.error("system", f"{c['to']} is not an input of module {tm.name}")
            continue
        if sv not in sm.variables:
            rep.error("system", f"{c['from']} is not a variable of module {sm.name}")
            continue
        if spec.domains[tm.variables[tv]["type"]] != spec.domains[sm.variables[sv]["type"]]:
            rep.error("system", f"type mismatch {c['from']} ({sm.variables[sv]['type']}) -> {c['to']} "
                      f"({tm.variables[tv]['type']})")
        if pos.get(si, 0) >= pos.get(ti, 0):
            rep.error("system", f"{si} must be evaluated before {ti} (connection {c['from']} -> {c['to']})")
        targets[c["to"]] += 1
    for t, n in targets.items():
        if n > 1:
            rep.error("system", f"{t} is driven by {n} connections")


# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="Static analysis of a NuSCR specification.")
    ap.add_argument("spec", nargs="?", default=str(default_spec_path()))
    ap.add_argument("--json", help="write the report as JSON to this file")
    ap.add_argument("--quiet", action="store_true", help="print only the summary")
    args = ap.parse_args(argv)
    try:
        spec = load(args.spec)
    except SpecError as e:
        print(f"STRUCTURAL ERROR: {e}")
        return 1
    rep = check_spec(spec)
    if not args.quiet:
        for f in rep.findings:
            print(f"{f.severity:7s} {f.where}: {f.message}")
    print(f"Checked {args.spec}")
    for k in sorted(rep.stats):
        print(f"  {k:24s} {rep.stats[k]}")
    print(f"Result: {len(rep.errors)} error(s), {len(rep.warnings)} warning(s)")
    if args.json:
        Path(args.json).write_text(json.dumps({
            "spec": args.spec, "errors": len(rep.errors), "warnings": len(rep.warnings),
            "stats": dict(rep.stats),
            "findings": [f.__dict__ for f in rep.findings]}, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
