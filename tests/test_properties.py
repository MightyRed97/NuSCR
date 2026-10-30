"""Randomised property tests of the bistable processor (fixed seeds, so runs are
reproducible).  Inputs wander around every setpoint, leave their valid ranges,
and diagnostics, pushbuttons, bypass switches and CPC signals toggle.  After
every scan the safety properties below must hold.

P1  module/channel error            => every trip and pre-trip output = 0      (SYS-GEN-02)
P2  input of a function invalid     => its trip and pre-trip = 0              (SYS-GEN-02/03)
P3  variable setpoints stay within [SP_Min, SP_Max] once initialised           (SYS-x-02)
P4  arming condition held for delay => output = 0 (no missed trip)            (SYS-GEN-06)
P5  fixed-setpoint trip output = 0  => error, or process beyond reset level    (SYS-GEN-07)
P6  manual-reset setpoint decreases => a reset pulse occurred in that scan     (SYS-x-02)
P7  operating bypass active         => its permissive holds in that scan       (SYS-GEN-09)
P8  reset pulse                     => button pressed now, released before     (SYS-x-02)
P9  rate-limited setpoints move by at most k_x_SP_Step per scan in the limited direction
P10 an output tripped by its process condition returns to NORMAL only in an error-free scan in which the
    process has recovered past the reset threshold or the operating bypass is active, even across errors
    (SYS-GEN-07); the release condition is computed from the constants, not from the specification's guards
P11 an operating bypass is established only in a scan in which its pushbutton goes from released to
    pressed while the permissive holds (SYS-GEN-09)

A coverage check guarantees the properties are not satisfied vacuously: every
state of every trip/pre-trip TTS must be visited.
"""
import random
import unittest

from helpers import F, k, spec
from nuscr_lang import compile_expr
from nuscr_sim import ModuleSim

import build_spec as B

SCANS = 3000
SEEDS = (1, 2, 3)
LOG_POWER_POINTS = ["1e-7", "1e-5", "5e-5", "0.0001", "0.0002", "0.004", "0.005", "0.01", "0.02", "1", "50"]


def thresholds(signal):
    """Every constant an analog signal is compared with."""
    vals = [k(f"k_{signal}_Valid_Min"), k(f"k_{signal}_Valid_Max")]
    for t in B.TRIPS:
        if t.get("signal") == signal:
            for key in ("Trip_SP", "Pretrip_SP", "OB_Perm", "SP_Min", "SP_Max"):
                if key in t["c"]:
                    vals.append(k(f"k_{t['id']}_{key}"))
    return vals


def toggle_rates(name):
    """(probability of leaving the nominal value, probability of returning)."""
    if name in ("f_Module_Error", "f_Channel_Error", "f_CPC_Link_Error"):
        return 0.004, 0.2
    if name.endswith("_Trip_In") or name.endswith("_Pretrip_In"):
        return 0.02, 0.1
    if name.endswith("_Byp_Sw"):
        return 0.01, 0.1
    return 0.03, 0.3                                   # pushbuttons


def released(t, which, out):
    """Independent oracle for the SYS-GEN-07 release condition of trip function t ('Trip' or 'Pretrip')."""
    x, logic, s = t["id"], t["logic"], t.get("signal")
    errors = out["f_Module_Error"] == 1 or out["f_Channel_Error"] == 1
    if logic == "DT_OB":
        ok = not errors and out["f_CPC_Link_Error"] == 0
        recovered = out[f"f_{x}_{which}_In"] == 1
    else:
        ok = not errors and out[f"f_{s}_Valid"] == 1
        rising, v, hys = B.LOGIC[logic]["rising"], out[f"f_{s}"], k(f"k_{x}_{which}_Hys")
        if logic in ("VSRT", "RLVSFT_OB", "MRVSFT_OB"):
            sp = out[f"h_{x}_Trip_SP"]
            if which == "Pretrip":
                margin = k(f"k_{x}_Pretrip_Margin")
                sp = sp - margin if rising else sp + margin
        else:
            sp = k(f"k_{x}_{which}_SP")
        recovered = v < sp - hys if rising else v > sp + hys
    bypassed = logic.endswith("_OB") and out[f"h_{x}_OB_Sta"] == 1
    return ok and (bypassed or recovered)


class RandomInputs:
    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.bp = ModuleSim(spec(), "BP")
        self.nominal = self.bp.nominal_inputs()
        self.values = dict(self.nominal)

    def next(self):
        r = self.rng
        ch = {}
        for s, sig in B.SIGNALS.items():
            name = f"f_{s}"
            lo, hi = F(sig["vmin"]), F(sig["vmax"])
            span = hi - lo
            p = r.random()
            if p < 0.02:
                ch[name] = r.choice(thresholds(s)) + r.choice([-1, 0, 1]) * span / 1000
            elif p < 0.03:
                ch[name] = lo - span / 20 if r.random() < 0.5 else hi + span / 20      # out of range
            elif p < 0.35:
                ch[name] = self.values[name] + F(r.randint(-20, 20)) * span / 2000       # drift
        if r.random() < 0.02:
            ch["f_NPWR_LOG"] = F(r.choice(LOG_POWER_POINTS))
        for n, nominal in self.nominal.items():
            if self.bp.domains[n] is None:
                continue
            p_on, p_off = toggle_rates(n)
            if self.values[n] == nominal:
                if r.random() < p_on:
                    ch[n] = 1 - nominal
            elif r.random() < p_off:
                ch[n] = nominal
        self.values.update(ch)
        return ch


class Properties(unittest.TestCase):
    def run_seed(self, seed, visited):
        m = spec().modules["BP"]
        gen = RandomInputs(seed)
        bp = gen.bp
        period = k("k_Scan_Period")
        machines = [v for v, d in m.definitions.items() if d.kind == "TTS"]
        arm = {v: compile_expr(next(t.guard for t in m.definitions[v].transitions if t.id == "W3"))
               for v in machines}
        latched = dict.fromkeys(machines, False)
        function_of = {f"th_{t['id']}_{w}": (t, w) for t in B.TRIPS for w in ("Trip", "Pretrip")}
        need = {v: int(k(f"k_{v[3:].rsplit('_', 1)[0]}_{v.rsplit('_', 1)[1]}_Delay") / period) + 1
                for v in machines}
        held = dict.fromkeys(machines, 0)
        prev = prev_states = None
        for _ in range(SCANS):
            out = bp.step(gen.next())
            env = dict(spec().constants)
            env.update(out)
            err = out["f_Module_Error"] == 1 or out["f_Channel_Error"] == 1
            where = f"(seed {seed}, scan {bp.cycle})"
            for t in B.TRIPS:
                x, logic = t["id"], t["logic"]
                trip, pre = out[f"th_{x}_Trip"], out[f"th_{x}_Pretrip"]
                if err:
                    self.assertEqual((trip, pre), (0, 0), f"P1 {x} {where}")
                s = t.get("signal")
                invalid = out[f"f_{s}_Valid"] == 0 if s else out["f_CPC_Link_Error"] == 1
                if invalid:
                    self.assertEqual((trip, pre), (0, 0), f"P2 {x} {where}")
                sp = f"h_{x}_Trip_SP"
                if sp in out and bp.states[sp] != "INIT":
                    self.assertTrue(k(f"k_{x}_SP_Min") <= out[sp] <= k(f"k_{x}_SP_Max"), f"P3 {x} {where}")
                if logic in ("FSRT", "FSFT") and trip == 0:
                    v, spv, hys = out[f"f_{s}"], k(f"k_{x}_Trip_SP"), k(f"k_{x}_Trip_Hys")
                    beyond = v >= spv - hys if B.LOGIC[logic]["rising"] else v <= spv + hys
                    self.assertTrue(err or invalid or beyond, f"P5 {x} {where}")
                if logic == "MRVSFT_OB" and prev is not None:
                    if out[sp] < prev[sp]:
                        self.assertEqual(out[f"h_{x}_Reset_Pulse"], 1, f"P6 {x} {where}")
                    if out[f"h_{x}_Reset_Pulse"] == 1:
                        self.assertEqual((out[f"f_{x}_SP_Reset"], prev[f"f_{x}_SP_Reset"]), (1, 0), f"P8 {x} {where}")
                if logic.endswith("_OB"):
                    perm = "f_LP_OB_Perm" if logic in ("DT_OB", "RLVSFT_OB") else f"f_{x}_OB_Perm"
                    if out[f"h_{x}_OB_Sta"] == 1:
                        self.assertEqual(out[perm], 1, f"P7 {x} {where}")
                    if prev is not None and prev[f"h_{x}_OB_Sta"] == 0 and out[f"h_{x}_OB_Sta"] == 1:
                        pb = f"f_{x}_OB_Ini"
                        self.assertEqual((out[pb], prev[pb], out[perm]), (1, 0, 1), f"P11 {x} {where}")
                if logic in ("VSRT", "RLVSFT_OB") and prev_states and prev_states[sp] == "TRACKING":
                    delta, step = out[sp] - prev[sp], k(f"k_{x}_SP_Step")
                    self.assertTrue(delta <= step if logic == "VSRT" else delta >= -step, f"P9 {x} {where}")
            for v in machines:
                held[v] = held[v] + 1 if arm[v](env) else 0
                if held[v] >= need[v]:
                    self.assertEqual(out[v], 0, f"P4 {v} armed for {held[v]} scans {where}")
                if latched[v] and out[v] == 1:
                    self.assertTrue(released(*function_of[v], out), f"P10 {v} released early {where}")
                latched[v] = out[v] == 0 and (latched[v] or bp.states[v].endswith("_BY_LOGIC"))
                visited.setdefault(v, set()).add(bp.states[v])
            prev, prev_states = out, dict(bp.states)

    def test_properties_and_coverage(self):
        visited = {}
        for seed in SEEDS:
            with self.subTest(seed=seed):
                self.run_seed(seed, visited)
        m = spec().modules["BP"]
        missing = {v: sorted(set(m.definitions[v].states) - s) for v, s in visited.items()
                   if set(m.definitions[v].states) - s}
        self.assertEqual(missing, {}, "TTS states never visited by the random runs")


if __name__ == "__main__":
    unittest.main()
