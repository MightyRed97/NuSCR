"""The function-block design implements the NuSCR requirements (exact proofs plus back-to-back tests), and the
static check and proofs detect wrong designs (fault injection)."""
import copy
import json
import unittest
from functools import lru_cache

import helpers
from design_model import Design, check_design, load_design
from design_verify import (BP_SEEDS, back_to_back_bp, back_to_back_system, back_to_back_system_random,
                           verify_edge_and_bypass_blocks, verify_instances, verify_trip_bistable)


@lru_cache(maxsize=1)
def design():
    return load_design(spec=helpers.spec())


def with_block(block, old, new):
    raw = copy.deepcopy(design().raw)
    text = json.dumps(raw["blocks"][block])
    assert text.count(old) == 1, old
    raw["blocks"][block] = json.loads(text.replace(old, new))
    return Design(raw, helpers.spec())


def with_instance(module, name, fn):
    raw = copy.deepcopy(design().raw)
    m = next(x for x in raw["modules"] if x["name"] == module)
    fn(next(i for i in m["instances"] if i["name"] == name))
    return Design(raw, helpers.spec())


def failed_proofs(d, name=None):
    results, _ = verify_instances(d)
    return [r for r in results if not r["passed"] and (name is None or f".{name} (" in r["check"])]


class DesignIsCorrect(unittest.TestCase):
    def test_static_check_clean(self):
        self.assertEqual(check_design(design()), [])

    def test_block_equivalence(self):
        for r in verify_trip_bistable(design()) + verify_edge_and_bypass_blocks(design()):
            self.assertTrue(r["passed"], r)

    def test_every_instance_proved_and_every_definition_covered(self):
        results, covered = verify_instances(design())
        self.assertEqual([r for r in results if not r["passed"]], [])
        self.assertEqual(len(results), 89)
        defs = {v for m in helpers.spec().modules.values() for v in m.definitions}
        self.assertEqual(defs - covered, set())

    def test_bp_back_to_back_and_transition_coverage(self):
        coverage = set()
        for seed in BP_SEEDS:
            r = back_to_back_bp(design(), seed, 3000, coverage)
            self.assertTrue(r["passed"], r)
        all_t = {(v, t.id) for v, d in helpers.spec().modules["BP"].definitions.items() for t in d.transitions}
        self.assertEqual(sorted(all_t - coverage), [])

    def test_system_back_to_back(self):
        self.assertTrue(back_to_back_system_random(design(), 3, 1000)["passed"])
        for sc in sorted((helpers.ROOT / "scenarios").glob("*.csv")):
            r = back_to_back_system(design(), sc)
            self.assertTrue(r["passed"], r)


class ProofsDetectWrongBlocks(unittest.TestCase):
    def assertBlockCaught(self, d):
        results = verify_trip_bistable(d, (2,)) + verify_edge_and_bypass_blocks(d)
        self.assertTrue(any(not r["passed"] for r in results))

    def test_off_by_one_delay(self):
        self.assertBlockCaught(with_block("FB_TRIP_BISTABLE", "last_CNT + 1 >= DELAY", "last_CNT + 1 > DELAY"))

    def test_bypass_ignored_on_release(self):
        self.assertBlockCaught(with_block("FB_TRIP_BISTABLE", "OK = 1 && (BYP = 1 || RESET = 1)", "OK = 1 && RESET = 1"))

    def test_process_trip_forgotten_during_error(self):
        self.assertBlockCaught(with_block("FB_TRIP_BISTABLE", '["OK = 0", "last_LATCH"]', '["OK = 0", "0"]'))

    def test_edge_detector_firing_at_power_up(self):
        self.assertBlockCaught(with_block("FB_EDGE", '"init": "1"', '"init": "0"'))

    def test_bypass_not_removed_automatically(self):
        self.assertBlockCaught(with_block("FB_OPERATING_BYPASS", "PERM = 1 && (last_MEM = 1 || PULSE = 1)",
                                          "last_MEM = 1 || PERM = 1 && PULSE = 1"))

    def test_bypass_interlock_wrong(self):
        d = with_block("FB_VOTE_2OO4", "(BYP_A = 1 && BYP_B = 0 && BYP_C = 0 && BYP_D = 0)", "(BYP_A = 1)")
        self.assertTrue(failed_proofs(d, "VOPT_VOTE"))

    def test_rate_limit_wrong(self):
        d = with_block("FB_SP_RATE_UP", '"SP0 + STEP"', '"SP0 + 2 * STEP"')
        self.assertTrue(failed_proofs(d, "VOPT_SP"))

    def test_rate_limit_final_approach_wrong(self):
        d = with_block("FB_SP_RATE_UP", '["TGT - SP0 <= STEP", "TGT"]', '["TGT - SP0 <= STEP", "SP0 + STEP"]')
        self.assertTrue(failed_proofs(d, "VOPT_SP"))

    def test_manual_reset_ceiling_off_by_one(self):
        d = with_block("FB_SP_MANUAL_RESET", "D > SP0 && D <= MAX", "D > SP0 && D < MAX")
        self.assertTrue(failed_proofs(d, "PZR_P_LO_SP"))

    def test_manual_reset_floor_off_by_one(self):
        d = with_block("FB_SP_MANUAL_RESET", "D < SP0 && D >= MIN", "D < SP0 && D > MIN")
        self.assertTrue(failed_proofs(d, "PZR_P_LO_SP"))


class ProofsDetectWrongWiring(unittest.TestCase):
    def assertCaught(self, d, name):
        static = [f for f in check_design(d) if name in f[1]]
        self.assertTrue(static or failed_proofs(d, name), f"{name} not caught")

    def test_comparator_on_variable_setpoint(self):
        d = with_instance("BP", "VOPT_TRIP", lambda i: i["inputs"].update(COND="f_NPWR_LIN > h_VOPT_Trip_SP"))
        self.assertTrue(failed_proofs(d, "VOPT_TRIP"))

    def test_falling_comparator(self):
        d = with_instance("BP", "SG1_F_LO_TRIP", lambda i: i["inputs"].update(COND="f_SG1_F < h_SG1_F_LO_Trip_SP"))
        self.assertTrue(failed_proofs(d, "SG1_F_LO_TRIP"))

    def test_pretrip_comparator(self):
        d = with_instance("BP", "PZR_P_LO_PRETRIP", lambda i: i["inputs"].update(
            COND="f_PZR_P < h_PZR_P_LO_Trip_SP + k_PZR_P_LO_Pretrip_Margin"))
        self.assertTrue(failed_proofs(d, "PZR_P_LO_PRETRIP"))

    def test_reset_threshold(self):
        d = with_instance("BP", "PZR_P_LO_TRIP", lambda i: i["inputs"].update(
            RESET="f_PZR_P >= h_PZR_P_LO_Trip_SP + k_PZR_P_LO_Trip_Hys"))
        self.assertTrue(failed_proofs(d, "PZR_P_LO_TRIP"))

    def test_initiation_missing_a_function(self):
        def f(i):
            i["inputs"]["TRIPPED"] = i["inputs"]["TRIPPED"].replace(" || f_CTMT_P_HI_Coinc = 0", "")
        self.assertCaught(with_instance("CP", "RT_INIT", f), "RT_INIT")

    def test_link_input_tied_off(self):
        self.assertCaught(with_instance("CP", "DNBR_LO_VOTE", lambda i: i["inputs"].update(LINK_A="0")),
                          "DNBR_LO_VOTE")

    def test_link_input_from_wrong_channel(self):
        self.assertCaught(with_instance("CP", "CTMT_P_HI_VOTE",
                                        lambda i: i["inputs"].update(LINK_B="f_C_Link_Error")), "CTMT_P_HI_VOTE")

    def test_bypass_from_wrong_function(self):
        self.assertCaught(with_instance("CP", "SG2_L_HI_VOTE",
                                        lambda i: i["inputs"].update(BYP_A="f_A_SG1_L_HI_Byp")), "SG2_L_HI_VOTE")

    def test_channels_permuted(self):
        def f(i):
            i["inputs"]["TRIP_A"], i["inputs"]["TRIP_B"] = i["inputs"]["TRIP_B"], i["inputs"]["TRIP_A"]
        d = with_instance("CP", "VOPT_VOTE", f)
        self.assertEqual([x for x in check_design(d) if "VOPT_VOTE" in x[1]], [])   # same variables read ...
        self.assertTrue(failed_proofs(d, "VOPT_VOTE"))                                # ... caught by the proof

    def test_delay_constant_swapped(self):
        d = with_instance("BP", "VOPT_TRIP", lambda i: i["params"].update(DELAY="k_PZR_P_HI_Pretrip_Delay / k_Scan_Period"))
        self.assertTrue([x for x in check_design(d) if "VOPT_TRIP" in x[1] and "constants differ" in x[2]])

    def test_limit_constant_swapped(self):
        d = with_instance("BP", "VAL_SG1_L", lambda i: i["params"].update(MAX="k_SG2_L_Valid_Max"))
        self.assertTrue([x for x in check_design(d) if "VAL_SG1_L" in x[1]])

    def test_non_integer_delay(self):
        d = with_instance("BP", "PZR_P_HI_TRIP", lambda i: i["params"].update(DELAY="k_PZR_P_HI_Trip_Delay / 40"))
        self.assertTrue([x for x in check_design(d) if "parameter DELAY" in x[2]])


class StaticCheckDetectsNotationErrors(unittest.TestCase):
    def messages(self, block, old, new):
        return " ".join(m for _, w, m in check_design(with_block(block, old, new)) if block in w)

    def test_double_assignment(self):
        self.assertIn("assigned more than once", self.messages("FB_EDGE", '{"set": "PREV", "expr": "PB"}',
                                                               '{"set": "Q", "expr": "PB"}, {"set": "PREV", "expr": "PB"}'))

    def test_read_before_assignment(self):
        self.assertIn("before its assignment", self.messages("FB_EDGE", "last_PREV = 0", "PREV = 0"))

    def test_parameter_dependent_initial_value(self):
        self.assertIn("must be a constant", self.messages("FB_SP_MANUAL_RESET", '"init": "0"}, {"name": "STARTED"',
                                                          '"init": "MAX"}, {"name": "STARTED"'))

    def test_unimplemented_requirement_node(self):
        d = with_instance("BP", "LP_PERM", lambda i: i.update(implements=[]))
        msgs = [m for _, _, m in check_design(d)]
        self.assertIn("NuSCR definition f_LP_OB_Perm is not implemented by any instance", msgs)


if __name__ == "__main__":
    unittest.main()
