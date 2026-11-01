"""Reference runs: the same stimuli are replayed on every implementation of the design (the SCADE scenario scripts,
the C code, the browser engine, the OpenModelica model) and compared scan by scan.

Each run starts at power-up.  For every scan it holds the inputs applied and the value of every variable, computed
by the NuSCR requirements model with exact arithmetic.

    runs = standard_runs(spec)                the 7 runs every implementation is checked on (decimal stimuli)
    runs = full_runs(spec)                    the exact back-to-back runs of tools/design_verify.py
    run.target                                "BP", "CP" or "SYSTEM"
    run.scans                                 list of (inputs, values); in a SYSTEM run both are {instance: {name: value}}
    write_stimulus_csv(spec, run, path)       the run's inputs in the scenario CSV format (tools/nuscr_sim.py)
    trace_columns(design, run)                the signals an implementation has to report
    read_trace_csv(path)                      an implementation's trace: one {signal: number} per scan
    reference = reference_traces(design, run) the run replayed on the exported Scade model, exactly and in doubles
    check_trace(design, run, trace, reference)  the comparison

Implementations compute in IEEE-754 doubles, the requirements model exactly.  The reference is the exported Scade
model run by tools/scade_subset.py twice: with exact rationals, which must equal the requirements model in every
value (this checks the translation of the design that every export shares), and with Python floats.  A difference
of an implementation from the exact value is accepted only where the exact replay is right and the double replay
shows the same value; it is then listed as a rounding effect.
"""
from __future__ import annotations

import csv
import random
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_common import design_signals, signal_type                 # noqa: E402
from nuscr_lang import format_number                                  # noqa: E402
from nuscr_sim import ModuleSim, SystemSim, _read_scenario            # noqa: E402
from paths import SCENARIOS_DIR                                       # noqa: E402

REAL_TOLERANCE = Fraction(1, 10 ** 9)       # absolute, on real-valued signals; flags and counts compare exactly
BP_SEEDS = tuple(range(1, 9))               # the seeds tools/design_verify.py uses for its back-to-back runs
SYSTEM_SEEDS = (1, 2)
MAX_LISTED = 20                             # failures listed per run (counts are always complete); every rounding
                                            # effect is listed


class Run:
    def __init__(self, name, target, title, scans):
        self.name, self.target, self.title, self.scans = name, target, title, scans

    def view(self, instance):
        """One instance's (inputs, values) per scan of a SYSTEM run."""
        return [(i[instance], v[instance]) for i, v in self.scans]


def _rounded(changes):
    # stimuli rounded to 6 decimals so that every value has an exact decimal literal in scripts and CSV files
    return {n: Fraction(round(v * 10 ** 6), 10 ** 6) for n, v in changes.items()}


# ---------------------------------------------------------------------------
# The runs
# ---------------------------------------------------------------------------

def bp_scenario_run(spec, name):
    """A single-BP scenario file."""
    events, last = _read_scenario(SCENARIOS_DIR / f"{name}.csv")
    sim, scans = ModuleSim(spec, "BP"), []
    for c in range(last + 1):
        res = sim.step({col: val for col, val in events.get(c, [])})
        scans.append((dict(sim.inputs), res))
    return Run(f"bp_{name}", "BP", f"BP scenario {name} (5_simulation/scenarios/{name}.csv)", scans)


def system_scenario_run(spec, name):
    """A four-channel scenario file."""
    events, last = _read_scenario(SCENARIOS_DIR / f"{name}.csv")
    sim, scans = SystemSim(spec), []
    for c in range(last + 1):
        changes = {}
        for col, val in events.get(c, []):
            inst, var = col.split(".", 1) if "." in col else ("BP", col)
            changes.setdefault(inst, {})[var] = val
        res = sim.step(changes)
        scans.append(({i: dict(s.inputs) for i, s in sim.instances.items()}, res))
    return Run(f"system_{name}", "SYSTEM", f"Four-channel scenario {name} (5_simulation/scenarios/{name}.csv)", scans)


def boundary_run(spec):
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
    return Run("bp_boundaries", "BP", "BP comparator boundaries: every analog trip and pre-trip threshold and "
                                      "reset threshold hit exactly", cycles)


def random_bp_run(spec, seed, scans=1000, coverage=None, rounded=True):
    """Boundary-biased random BP run (the input generator of tools/design_verify.py); `coverage` collects the
    (variable, transition) pairs fired.  With `rounded` the stimuli are rounded to 6 decimals; without, they are
    the exact rationals tools/design_verify.py uses."""
    from design_verify import BoundaryInputs
    sim, gen, req_values, out = ModuleSim(spec, "BP"), BoundaryInputs(spec, seed), {}, []
    for _ in range(scans):
        ch = gen.next(req_values)
        req_values = sim.step(_rounded(ch) if rounded else ch)
        if coverage is not None:
            coverage.update(sim.last_transition.items())
        out.append((dict(sim.inputs), req_values))
    if rounded:
        return Run(f"bp_random_boundary_seed{seed}", "BP",
                   f"BP boundary-biased random run, seed {seed}, {scans} cycles", out)
    return Run(f"bp_random_seed{seed}_exact", "BP",
               f"BP boundary-biased random run, seed {seed}, {scans} scans, exact stimuli of tools/design_verify.py", out)


def random_cp_run(spec, seed, scans=1000):
    """Random run over every trip, bypass and link input of the CP."""
    rng, cp, out = random.Random(seed), ModuleSim(spec, "CP"), []
    for _ in range(scans):
        ch = {n: (0 if rng.random() < 0.15 else 1) if n.endswith("_Trip") else (1 if rng.random() < 0.1 else 0)
              for n in spec.modules["CP"].inputs}
        res = cp.step(ch)
        out.append((dict(cp.inputs), res))
    return Run(f"cp_random_seed{seed}", "CP", f"CP random run, seed {seed}, {scans} cycles", out)


def random_system_run(spec, seed, scans=1000, rounded=True):
    """Four channels with independent boundary-biased inputs, bypass switches and link failures (the generator of
    tools/design_verify.py; `rounded` as in random_bp_run)."""
    import build_spec as B
    from design_verify import BoundaryInputs
    sim = SystemSim(spec)
    gens = {i: BoundaryInputs(spec, seed * 100 + k) for k, i in enumerate(n for n in sim.instances
                                                                         if sim.modules[n] == "BP")}
    rng = random.Random(seed)
    links = {f"f_{c}_Link_Error": Fraction(0) for c in B.CHANNELS}
    req, out = {}, []
    for _ in range(scans):
        changes = {inst: g.next(req.get(inst, {})) for inst, g in gens.items()}
        if rounded:
            changes = {inst: _rounded(ch) for inst, ch in changes.items()}
        for n in links:
            if rng.random() < (0.01 if links[n] == 0 else 0.2):
                links[n] = 1 - links[n]
        changes["CP_A"] = dict(links)
        req = sim.step(changes)
        out.append(({i: dict(s.inputs) for i, s in sim.instances.items()}, req))
    if rounded:
        return Run(f"system_random_seed{seed}", "SYSTEM", f"Four-channel random run, seed {seed}, {scans} scans", out)
    return Run(f"system_random_seed{seed}_exact", "SYSTEM",
               f"Four-channel random run, seed {seed}, {scans} scans, exact stimuli of tools/design_verify.py", out)


def standard_runs(spec, coverage=None):
    """The runs every implementation is checked on.  Every stimulus is a decimal with at most 6 decimals."""
    return [bp_scenario_run(spec, "pzr_pressure_excursion"), bp_scenario_run(spec, "cooldown_manual_reset"),
            system_scenario_run(spec, "loss_of_flow_4ch"), boundary_run(spec),
            random_bp_run(spec, 1, 1000, coverage), random_cp_run(spec, 1), random_system_run(spec, 1, 500)]


def full_runs(spec, coverage=None):
    """The back-to-back runs of tools/design_verify.py with their exact stimuli: together the BP runs fire every
    transition of the requirements model.  Some stimuli have no finite decimal expansion (see stimulus_cell)."""
    return ([random_bp_run(spec, s, 3000, coverage, rounded=False) for s in BP_SEEDS]
            + [random_system_run(spec, s, 1500, rounded=False) for s in SYSTEM_SEEDS])


# ---------------------------------------------------------------------------
# Exchanging a run with an implementation
# ---------------------------------------------------------------------------

def external_inputs(spec, instance, module):
    """The inputs of a system instance that no connection drives."""
    driven = {c["to"].split(".", 1)[1] for c in spec.system["connections"] if c["to"].startswith(instance + ".")}
    return [n for n in spec.modules[module].inputs if n not in driven]


def _flat(run, per_instance):
    if run.target != "SYSTEM":
        return per_instance
    return {f"{inst}.{n}": v for inst, vals in per_instance.items() for n, v in vals.items()}


def stimulus_columns(spec, run):
    if run.target != "SYSTEM":
        return list(spec.modules[run.target].inputs)
    return [f"{i['name']}.{n}" for i in spec.system["instances"] for n in external_inputs(spec, i["name"], i["module"])]


def stimulus_cell(v):
    """Text of a stimulus value: its exact decimal, or, when it has no finite decimal expansion, the shortest decimal
    that reads back as the nearest double.  That double is the value float64 code computes with (float64_trace
    converts the exact value the same way); the run itself keeps the exact value for the comparison."""
    v = Fraction(v)
    text = format_number(v)
    return repr(float(v)) if text.startswith("(") else text


def write_stimulus_csv(spec, run, path):
    """The run's inputs in the scenario CSV format.  Every input is set in cycle 0 and afterwards only when it
    changes; an empty cell keeps the input's value.  SYSTEM columns are INSTANCE.var, inputs driven by a connection
    are left out.  The standard runs have decimal stimuli only; some exact stimuli of the full runs are written as
    the nearest double (stimulus_cell), so those files are exact for double-precision readers only."""
    cols = stimulus_columns(spec, run)
    last = {}
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["cycle"] + cols)
        for k, (inputs, _) in enumerate(run.scans):
            flat = _flat(run, inputs)
            row = [k]
            for c in cols:
                v = Fraction(flat[c])
                row.append(stimulus_cell(v) if last.get(c) != v else "")
                last[c] = v
            w.writerow(row)


def trace_columns(design, run):
    """The signals an implementation reports for a run: everything the design computes (INSTANCE.var in a SYSTEM
    run)."""
    if run.target != "SYSTEM":
        return design_signals(design, run.target)
    return [f"{i['name']}.{s}" for i in design.spec.system["instances"] for s in design_signals(design, i["module"])]


def read_trace_csv(path):
    """A trace written by an implementation: a header with 'cycle', optionally 'time_ms', then one column per
    signal.  Returns one {signal: float} per scan."""
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    header = rows[0]
    if header[0] != "cycle":
        raise ValueError(f"{path}: the first trace column must be 'cycle'")
    out = []
    for k, r in enumerate(rows[1:]):
        if int(r[0]) != k:
            raise ValueError(f"{path}: row {k} holds cycle {r[0]}")
        out.append({h: float(v) for h, v in zip(header, r) if h not in ("cycle", "time_ms")})
    return out


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------

_programs = {}


def _program(design):
    from export_scade import model_text
    from scade_subset import parse_program
    if id(design) not in _programs:
        _programs[id(design)] = parse_program(model_text(design))
    return _programs[id(design)]


def float64_trace(design, run):
    """The run replayed on the exported Scade model with IEEE-754 double arithmetic: what a correct float64
    implementation computes.  One {signal: float} per scan."""
    return _replay(design, run, float)


def reference_traces(design, run):
    """(exact, float64): the run replayed on the exported Scade model with exact rational arithmetic (one
    {signal: Fraction} per scan) and with IEEE-754 double arithmetic (one {signal: float} per scan)."""
    return _replay(design, run, Fraction), _replay(design, run, float)


def _replay(design, run, num):
    from scade_subset import ScadeSim
    spec, prog = design.spec, _program(design)
    conv = Fraction if num is Fraction else float

    def sim(module):
        m = spec.modules[module]
        return ScadeSim(prog, module, {n: Fraction(str(m.variables[n]["nominal"])) for n in m.inputs}, num=num)

    cols = trace_columns(design, run)
    out = []
    if run.target != "SYSTEM":
        s = sim(run.target)
        for inputs, _ in run.scans:
            res = s.step(inputs)
            out.append({c: conv(res[c]) for c in cols})
        return out
    sims = {i["name"]: sim(i["module"]) for i in spec.system["instances"]}
    links = {}
    for c in spec.system["connections"]:
        ti, tv = c["to"].split(".", 1)
        si, sv = c["from"].split(".", 1)
        links.setdefault(ti, []).append((tv, si, sv))
    for inputs, _ in run.scans:
        results = {}
        for name in spec.system["evaluation_order"]:
            ins = dict(inputs[name])
            for tv, si, sv in links.get(name, []):
                ins[tv] = Fraction(results[si][sv])
            results[name] = sims[name].step(ins)
        flat = _flat(run, results)
        out.append({c: conv(flat[c]) for c in cols})
    return out


def _signal_kinds(design, run):
    """{column: bool | int32 | float64}."""
    spec = design.spec
    if run.target != "SYSTEM":
        v = spec.modules[run.target].variables
        return {s: signal_type(spec.types, v[s]["type"]) for s in design_signals(design, run.target)}
    out = {}
    for i in spec.system["instances"]:
        v = spec.modules[i["module"]].variables
        for s in design_signals(design, i["module"]):
            out[f"{i['name']}.{s}"] = signal_type(spec.types, v[s]["type"])
    return out


def check_trace(design, run, trace, reference=None, bit_exact=True):
    """Compares an implementation's trace with the exact requirements model, signal by signal, scan by scan.

    `reference` is reference_traces(design, run).  Its exact replay must equal the requirements model in every value,
    or the run fails (reference errors: the shared translation of the design is wrong).  A difference of the
    implementation from the requirements model is a failure unless the exact replay is right there and the double
    replay holds the same value; it is then a rounding effect.  With `bit_exact`, every value must also equal the
    double replay exactly.  Without a reference every difference is a failure.  Returns a report dictionary;
    report["passed"] is the verdict."""
    exact, float64 = reference if reference is not None else (None, None)
    kinds = _signal_kinds(design, run)
    report = {"run": run.name, "title": run.title, "scans": len(run.scans), "comparisons": 0, "failures": 0,
              "listed_failures": [], "rounding_effects": 0, "listed_rounding_effects": [],
              "float64_differences": 0, "listed_float64_differences": [],
              "reference_errors": 0, "listed_reference_errors": []}

    def note(kind, k, c, expected, got):
        report[kind] += 1
        listed = report["listed_" + kind]
        if len(listed) < MAX_LISTED or kind == "rounding_effects":
            listed.append({"scan": k, "signal": c, "expected": str(expected), "got": repr(got)})

    if len(trace) != len(run.scans):
        report["passed"] = False
        report["length_error"] = f"{len(trace)} scans in the trace, {len(run.scans)} in the run"
        return report
    for k, (_, values) in enumerate(run.scans):
        flat = _flat(run, values)
        for c, kind in kinds.items():
            if c not in trace[k]:
                note("failures", k, c, flat[c], "missing")
                continue
            got = trace[k][c]
            report["comparisons"] += 1
            ref_right = exact is not None and exact[k][c] == flat[c]
            if exact is not None and not ref_right:
                note("reference_errors", k, c, flat[c], exact[k][c])
            diff = abs(Fraction(got) - flat[c])
            if diff > (REAL_TOLERANCE if kind == "float64" else 0):
                if ref_right and float64[k][c] == got:
                    note("rounding_effects", k, c, flat[c], got)
                else:
                    note("failures", k, c, flat[c], got)
            if float64 is not None and bit_exact and float64[k][c] != got:
                note("float64_differences", k, c, float64[k][c], got)
    report["passed"] = (report["failures"] == 0 and report["float64_differences"] == 0
                        and report["reference_errors"] == 0)
    return report
