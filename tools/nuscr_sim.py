"""Cycle-accurate reference simulator for NuSCR specifications (nuscr-json).

Implements the execution semantics recorded in the specification's
"semantics" section with exact rational arithmetic.

Library use:
    spec = nuscr_model.load(path)
    bp = ModuleSim(spec, "BP")            # inputs start at their nominal values
    out = bp.step({"f_PZR_P": 2400})      # changed inputs persist until changed again
    out["th_PZR_P_HI_Trip"], bp.states["th_PZR_P_HI_Trip"]

    plant = SystemSim(spec)               # BP_A..BP_D -> CP_A
    res = plant.step({"BP": {"f_PZR_P": 2400}})          # applied to every BP
    res = plant.step({"BP_B": {"f_Module_Error": 1}})    # one instance only

Command line (scenario replay):
    python tools/nuscr_sim.py scenarios/<file>.csv [--system] [--until N] [--watch a,b] [--out trace.csv]

Scenario CSV: a 'cycle' column plus one column per input.  An empty cell leaves
the input unchanged.  In --system mode a column is either 'INSTANCE.var',
'BP.var' (all four bistable processors) or a bare BP input name (all four).
"""
from __future__ import annotations

import argparse
import csv
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nuscr_lang import compile_expr, format_number     # noqa: E402
from nuscr_model import default_spec_path, load         # noqa: E402


class SimulationError(RuntimeError):
    pass


def to_fraction(v):
    if isinstance(v, Fraction):
        return v
    if isinstance(v, float):
        return Fraction(repr(v))
    return Fraction(str(v).strip())


class ModuleSim:
    def __init__(self, spec, module_name, instance_name=None):
        self.spec = spec
        self.mod = spec.modules[module_name]
        self.name = instance_name or module_name
        self.period = spec.constants["k_Scan_Period"]
        self.consts = dict(spec.constants)
        self._compile()
        self.reset()

    # ------------------------------------------------------------------
    def _compile(self):
        self.plan = []
        for var in self.mod.execution_order:
            d = self.mod.definitions[var]
            if d.kind == "SDT":
                rules = [(r.index, compile_expr(r.pred), compile_expr(d.actions[r.action][1])) for r in d.rules]
                self.plan.append((var, "SDT", rules))
            else:
                out = {}
                for s in d.states:
                    ts = []
                    for t in d.outgoing(s):
                        lo = hi = None
                        if t.timed:
                            lo = compile_expr(t.lower)(self.consts)
                            hi = compile_expr(t.upper)(self.consts)
                        ts.append((t.id, t.target, compile_expr(t.guard), compile_expr(t.action), lo, hi))
                    out[s] = ts
                self.plan.append((var, d.kind, out))
        self.domains = {n: self.mod.domain(n) for n in self.mod.variables}

    def nominal_inputs(self):
        out = {}
        for n in self.mod.inputs:
            nom = self.mod.variables[n].get("nominal")
            if nom is None:
                raise SimulationError(f"{self.name}: input {n} has no nominal value")
            out[n] = to_fraction(nom)
        return out

    def reset(self):
        """Power-up: nominal inputs, machines in their initial states (entered at cycle -1)."""
        self.cycle = -1
        self.inputs = self.nominal_inputs()
        self.values = {}
        self.states = {}
        self.entered = {}
        self.last_transition = {}
        for var, kind, _ in self.plan:
            if kind != "SDT":
                d = self.mod.definitions[var]
                self.values[var] = Fraction(compile_expr(d.initial_value[1])(self.consts))
                self.states[var] = d.initial_state
                self.entered[var] = -1

    def set_inputs(self, changes):
        for n, v in (changes or {}).items():
            if n not in self.mod.variables or self.mod.variables[n]["class"] != "input":
                raise SimulationError(f"{self.name}: {n} is not an input of module {self.mod.name}")
            v = to_fraction(v)
            dom = self.domains[n]
            if dom is not None and v not in dom:
                raise SimulationError(f"{self.name}: value {format_number(v)} is outside the domain of {n}")
            self.inputs[n] = v

    def step(self, changes=None):
        """Execute one scan; returns a dict of every variable's value in this scan."""
        self.set_inputs(changes)
        self.cycle += 1
        env = dict(self.consts)
        env.update(self.values)
        env.update(self.inputs)
        fired = {}
        for var, kind, plan in self.plan:
            if kind == "SDT":
                hits = [(i, act) for i, pred, act in plan if pred(env)]
                if len(hits) != 1:
                    raise SimulationError(f"{self.name}.{var}: {len(hits)} SDT rules apply in cycle {self.cycle} "
                                          f"(rules {[i for i, _ in hits]})")
                env[var] = Fraction(hits[0][1](env))
            else:
                state = self.states[var]
                tau = (self.cycle - self.entered[var]) * self.period
                enabled = [t for t in plan[state]
                           if t[2](env) and (t[4] is None or t[4] <= tau <= t[5])]
                if len(enabled) > 1:
                    raise SimulationError(f"{self.name}.{var}: transitions {[t[0] for t in enabled]} enabled "
                                          f"together in state {state}, cycle {self.cycle}")
                if enabled:
                    tid, target, _, action, _, _ = enabled[0]
                    env[var] = Fraction(action(env))
                    self.states[var] = target
                    self.entered[var] = self.cycle
                    fired[var] = tid
        self.last_transition = fired
        self.values = {var: env[var] for var, _, _ in self.plan}
        out = dict(self.inputs)
        out.update(self.values)
        return out

    @property
    def time_ms(self):
        return self.cycle * self.period


class SystemSim:
    """All module instances of the specification's 'system' section, evaluated
    in evaluation_order each scan, with connections applied in the same scan."""

    def __init__(self, spec):
        sysd = spec.system
        self.instances = {i["name"]: ModuleSim(spec, i["module"], i["name"]) for i in sysd["instances"]}
        self.order = sysd["evaluation_order"]
        self.links = {}
        for c in sysd["connections"]:
            ti, tv = c["to"].split(".", 1)
            si, sv = c["from"].split(".", 1)
            self.links.setdefault(ti, []).append((tv, si, sv))
        self.modules = {i["name"]: i["module"] for i in sysd["instances"]}

    def reset(self):
        for s in self.instances.values():
            s.reset()

    def step(self, changes=None):
        """changes: {instance name | module name: {input: value}}; a module name applies to all its instances."""
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

    @property
    def cycle(self):
        return next(iter(self.instances.values())).cycle


# ---------------------------------------------------------------------------
def _read_scenario(path):
    with open(path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.reader(line for line in f if not line.lstrip().startswith("#")) if r]
    header, events, last = rows[0], {}, 0
    if header[0].strip() != "cycle":
        raise SimulationError("the first scenario column must be 'cycle'")
    for r in rows[1:]:
        c = int(r[0])
        last = max(last, c)                   # a row with no values still extends the run
        for col, cell in zip(header[1:], r[1:]):
            if cell.strip():
                events.setdefault(c, []).append((col.strip(), cell.strip()))
    return events, last


def run_scenario(spec, path, system=False, until=None, watch=None):
    """Replay a scenario file up to its last row (or `until`); returns (header, rows)."""
    events, last = _read_scenario(path)
    until = max(until or 0, last)
    if system:
        sim = SystemSim(spec)
        watch = watch or ["CP_A." + o for o in sim.instances["CP_A"].mod.outputs]
    else:
        sim = ModuleSim(spec, "BP")
        watch = watch or sim.mod.outputs
    header = ["cycle", "time_ms"] + watch
    rows = []
    for c in range(until + 1):
        changes = {}
        for col, val in events.get(c, []):
            if system:
                inst, var = col.split(".", 1) if "." in col else ("BP", col)
                changes.setdefault(inst, {})[var] = val
            else:
                changes[col] = val
        res = sim.step(changes)
        row = [c, format_number(c * spec.constants["k_Scan_Period"])]
        for w in watch:
            if system:
                inst, var = w.split(".", 1)
                row.append(format_number(res[inst][var]))
            else:
                row.append(format_number(res[w]))
        rows.append(row)
    return header, rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="Replay a scenario CSV against the NuSCR specification.")
    ap.add_argument("scenario")
    ap.add_argument("--spec", default=str(default_spec_path()))
    ap.add_argument("--system", action="store_true", help="simulate BP_A..BP_D and CP_A together")
    ap.add_argument("--until", type=int, help="last cycle to simulate (default: last scenario row)")
    ap.add_argument("--watch", help="comma-separated variables to report (INSTANCE.var in --system mode)")
    ap.add_argument("--out", help="write the trace to this CSV file instead of stdout")
    ap.add_argument("--changes-only", action="store_true", help="print only rows where a watched value changes")
    args = ap.parse_args(argv)
    spec = load(args.spec)
    watch = [w.strip() for w in args.watch.split(",")] if args.watch else None
    header, rows = run_scenario(spec, args.scenario, args.system, args.until, watch)
    if args.changes_only:
        kept, prev = [], None
        for r in rows:
            if prev is None or r[2:] != prev:
                kept.append(r)
            prev = r[2:]
        rows = kept
    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(header)
            w.writerows(rows)
        print(f"wrote {len(rows)} rows to {args.out}")
    else:
        w = csv.writer(sys.stdout, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
