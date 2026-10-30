"""The shipped specification must pass every static check, and the checker must
detect each class of defect it claims to detect (fault injection)."""
import copy
import unittest

import helpers
from nuscr_check import check_spec
from nuscr_model import Spec


def mutate(fn):
    raw = copy.deepcopy(helpers.spec().raw)
    fn(raw)
    return check_spec(Spec(raw))


def bp(raw):
    return next(m for m in raw["modules"] if m["name"] == "BP")


def cp(raw):
    return next(m for m in raw["modules"] if m["name"] == "CP")


def const(raw, name):
    return next(c for c in raw["constants"] if c["name"] == name)


def transition(d, tid):
    return next(t for t in d["transitions"] if t["id"] == tid)


class ShippedSpecIsClean(unittest.TestCase):
    def test_no_errors_or_warnings(self):
        rep = check_spec(helpers.spec())
        self.assertEqual([f"{f.where}: {f.message}" for f in rep.findings], [])
        self.assertEqual(rep.stats["obligations"], rep.stats["obligations proved"])
        self.assertGreater(rep.stats["completeness proofs"], 100)


class FaultInjection(unittest.TestCase):
    def assertDetected(self, rep, *fragments):
        text = "\n".join(f"{f.severity} {f.where}: {f.message}" for f in rep.errors)
        for frag in fragments:
            self.assertIn(frag, text, msg=f"expected an error mentioning {frag!r}; got:\n{text}")

    def test_overlapping_sdt_rules(self):
        def f(raw):
            bp(raw)["definitions"]["f_PZR_P_Valid"]["rules"][0]["cells"] = ["T", "-"]
        self.assertDetected(mutate(f), "f_PZR_P_Valid (SDT)", "inconsistent: rules 1 and 2")

    def test_syntactic_overlap_that_is_infeasible_is_not_reported(self):
        # rules (-,F) and (F,-) overlap syntactically, but f < Valid_Min && f > Valid_Max is impossible
        def f(raw):
            bp(raw)["definitions"]["f_PZR_P_Valid"]["rules"][1]["cells"] = ["-", "F"]
        self.assertEqual(mutate(f).findings, [])

    def test_incomplete_sdt(self):
        def f(raw):
            del bp(raw)["definitions"]["f_PZR_P_Valid"]["rules"][2]
        self.assertDetected(mutate(f), "f_PZR_P_Valid (SDT)", "incomplete")

    def test_incomplete_clamp_when_limits_swapped(self):
        def f(raw):
            const(raw, "k_VOPT_SP_Min")["value"] = "120"      # now above SP_Max: rule (T,T) becomes feasible
        self.assertDetected(mutate(f), "f_VOPT_SP_Target (SDT)", "incomplete", "AS-VOPT")

    def test_nondeterministic_transitions(self):
        def f(raw):
            t = transition(bp(raw)["definitions"]["h_VOPT_Trip_SP"], "T4")
            t["guard"] = t["guard"].replace("> k_VOPT_SP_Step", ">= k_VOPT_SP_Step")
        self.assertDetected(mutate(f), "h_VOPT_Trip_SP (FSM)", "non-deterministic in state TRACKING: T3 and T4")

    def test_range_invariant_violation(self):
        def f(raw):
            transition(bp(raw)["definitions"]["h_VOPT_Trip_SP"], "T4")["action"] = "h_VOPT_Trip_SP + 2 * k_VOPT_SP_Step"
        self.assertDetected(mutate(f), "h_VOPT_Trip_SP", "range invariant")

    def test_negative_hysteresis(self):
        def f(raw):
            const(raw, "k_PZR_P_HI_Trip_Hys")["value"] = "-5"
        self.assertDetected(mutate(f), "obligation PO-PZR_P_HI-TRIP-HYS", "AS-PZR_P_HI")

    def test_delay_not_realisable_at_scan_period(self):
        def f(raw):
            const(raw, "k_SG1_L_LO_Trip_Delay")["value"] = "75"
        self.assertDetected(mutate(f), "th_SG1_L_LO_Trip (TTS)", "no scan instant")

    def test_timed_guard_without_exit(self):
        def f(raw):
            d = bp(raw)["definitions"]["th_CTMT_P_HI_Trip"]
            d["transitions"] = [t for t in d["transitions"] if t["id"] != "W2"]
        self.assertDetected(mutate(f), "th_CTMT_P_HI_Trip (TTS)", "persistence time is not well defined")

    def test_state_value_mismatch(self):
        def f(raw):
            transition(bp(raw)["definitions"]["th_CTMT_P_HI_Trip"], "E1")["action"] = "1"
        self.assertDetected(mutate(f), "th_CTMT_P_HI_Trip (TTS)", "state TRIP_PENDING has value 0")

    def test_latched_state_without_exits(self):
        def f(raw):
            d = bp(raw)["definitions"]["th_LPD_HI_Trip"]
            d["transitions"] = [t for t in d["transitions"] if t["id"] not in ("H1", "H2")]
        self.assertDetected(mutate(f), "th_LPD_HI_Trip (TTS)", "TRIP_BY_ERROR_LATCHED has no outgoing transition",
                            "TRIP_BY_ERROR_LATCHED is not left")

    def test_latched_release_turned_into_self_loop(self):
        def f(raw):
            t = transition(bp(raw)["definitions"]["th_SG1_F_LO_Trip"], "H2")
            t["to"], t["action"] = "TRIP_BY_ERROR_LATCHED", "0"
        self.assertDetected(mutate(f), "th_SG1_F_LO_Trip (TTS)", "TRIP_BY_ERROR_LATCHED is not left")

    def test_missing_error_transition_breaks_fail_safe(self):
        def f(raw):
            d = bp(raw)["definitions"]["th_PZR_P_HI_Trip"]
            d["transitions"] = [t for t in d["transitions"] if t["id"] != "N1"]
        self.assertDetected(mutate(f), "th_PZR_P_HI_Trip (TTS)", "state NORMAL is kept, so the value is not forced to 0")

    def test_latched_state_missing_release(self):
        def f(raw):
            d = bp(raw)["definitions"]["th_SG1_F_LO_Trip"]
            d["transitions"] = [t for t in d["transitions"] if t["id"] != "H2"]
        self.assertDetected(mutate(f), "th_SG1_F_LO_Trip (TTS)", "TRIP_BY_ERROR_LATCHED is not left")

    def test_level_triggered_bypass_is_detected_as_unused_pulse(self):
        def f(raw):
            t = transition(bp(raw)["definitions"]["h_LOGP_HI_OB_Sta"], "T1")
            t["guard"] = "f_LOGP_HI_OB_Perm = 1 && f_LOGP_HI_OB_Ini = 1"
        rep = mutate(f)
        self.assertTrue(any(w.where == "BP.h_LOGP_HI_OB_Pulse" and "neither read nor an output" in w.message
                            for w in rep.warnings), [f.message for f in rep.findings])

    def test_value_outside_type(self):
        def f(raw):
            cp(raw)["definitions"]["f_VOPT_Vote_A"]["actions"][1] = "2"
        self.assertDetected(mutate(f), "f_VOPT_Vote_A", "can leave the type vote")

    def test_fod_inputs_out_of_date(self):
        def f(raw):
            node = bp(raw)["fod"]["nodes"][0]["nodes"][0]
            node["inputs"] = node["inputs"] + ["f_PZR_P"]
        self.assertDetected(mutate(f), "FOD node inputs do not match")

    def test_bad_execution_order(self):
        def f(raw):
            order = bp(raw)["execution_order"]
            i, j = order.index("f_PZR_P_Valid"), order.index("th_PZR_P_HI_Trip")
            order[i], order[j] = order[j], order[i]
        self.assertDetected(mutate(f), "th_PZR_P_HI_Trip is evaluated before f_PZR_P_Valid")

    def test_unallocated_requirement(self):
        def f(raw):
            next(r for r in raw["requirements"] if r["id"] == "SYS-GEN-10").pop("allocated_to")
        self.assertDetected(mutate(f), "requirement SYS-GEN-10", "no allocation")

    def test_trace_to_undeclared_requirement(self):
        def f(raw):
            bp(raw)["definitions"]["th_CTMT_P_HI_Trip"]["traces"].append("SYS-XYZ-99")
        self.assertDetected(mutate(f), "BP.th_CTMT_P_HI_Trip", "undeclared requirement SYS-XYZ-99")

    def test_naming_convention(self):
        def f(raw):
            m = bp(raw)
            for v in m["variables"]:
                if v["name"] == "h_PZR_P_LO_OB_Sta":
                    v["class"] = "timed_history"
        self.assertDetected(mutate(f), "h_PZR_P_LO_OB_Sta", "prefix th_")


if __name__ == "__main__":
    unittest.main()
