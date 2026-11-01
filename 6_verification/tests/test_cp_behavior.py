"""Coincidence processor: exhaustive comparison with an oracle written directly
from SYS-CP-01..04, plus end-to-end four-channel scenarios."""
import itertools
import unittest

from helpers import at_power_plant, spec, trip_ids
from nuscr_sim import ModuleSim

CH = "ABCD"


def oracle(trip, byp, link):
    """SYS-CP-01..03: 0 = coincidence trip."""
    requests = [c for c in CH if byp[c]]
    bypassed = requests[0] if len(requests) == 1 else None
    votes = [c for c in CH if c != bypassed and (trip[c] == 0 or link[c] == 1)]
    return 0 if len(votes) >= 2 else 1


class CoincidenceLogic(unittest.TestCase):
    def check_function(self, x, cp):
        for bits in itertools.product((0, 1), repeat=12):
            trip = dict(zip(CH, bits[0:4]))
            byp = dict(zip(CH, bits[4:8]))
            link = dict(zip(CH, bits[8:12]))
            changes = {}
            for c in CH:
                changes[f"f_{c}_{x}_Trip"] = trip[c]
                changes[f"f_{c}_{x}_Byp"] = byp[c]
                changes[f"f_{c}_Link_Error"] = link[c]
            out = cp.step(changes)
            expected = oracle(trip, byp, link)
            self.assertEqual(out[f"f_{x}_Coinc"], expected, f"{x} trip={trip} byp={byp} link={link}")
            # every other function sees nominal trip/bypass inputs but the same link failures
            other = oracle(dict.fromkeys(CH, 1), dict.fromkeys(CH, 0), link)
            for y in trip_ids():
                if y != x:
                    self.assertEqual(out[f"f_{y}_Coinc"], other, f"{y} with link={link}")
            self.assertEqual(out["f_RT_Init"], 0 if 0 in (expected, other) else 1)

    def test_exhaustive_for_representative_functions(self):
        cp = ModuleSim(spec(), "CP")
        for x in ("VOPT", "PZR_P_LO", "SG2_F_LO"):
            self.check_function(x, cp)
            cp.reset()

    def test_every_function_initiates_reactor_trip(self):
        for x in trip_ids():
            cp = ModuleSim(spec(), "CP")
            self.assertEqual(cp.step()["f_RT_Init"], 1)
            out = cp.step({f"f_A_{x}_Trip": 0, f"f_D_{x}_Trip": 0})
            self.assertEqual((out[f"f_{x}_Coinc"], out["f_RT_Init"]), (0, 0), x)


class FourChannelScenarios(unittest.TestCase):
    def test_single_channel_excursion_does_not_trip_the_reactor(self):
        plant = at_power_plant()
        for _ in range(10):
            res = plant.step({"BP_B": {"f_PZR_P": 2450}})
        self.assertEqual(res["BP_B"]["th_PZR_P_HI_Trip"], 0)
        self.assertEqual(res["CP_A"]["f_RT_Init"], 1)

    def test_two_channel_excursion_trips_the_reactor(self):
        plant = at_power_plant()
        results = [plant.step({"BP_A": {"f_SG1_L": 20}, "BP_C": {"f_SG1_L": 20}})]
        results += [plant.step() for _ in range(3)]
        self.assertEqual([int(r["CP_A"]["f_RT_Init"]) for r in results], [1, 1, 0, 0])

    def test_one_bypassed_channel_gives_two_out_of_three(self):
        plant = at_power_plant()
        plant.step({"BP_A": {"f_CTMT_P_HI_Byp_Sw": 1}})
        res = [plant.step({"BP_A": {"f_CTMT_P": 5}, "BP_B": {"f_CTMT_P": 5}}) for _ in range(4)][-1]
        self.assertEqual(res["CP_A"]["f_RT_Init"], 1)          # A bypassed, only B votes
        res = [plant.step({"BP_C": {"f_CTMT_P": 5}}) for _ in range(4)][-1]
        self.assertEqual(res["CP_A"]["f_RT_Init"], 0)          # B and C

    def test_second_bypass_request_is_ignored(self):
        plant = at_power_plant()
        plant.step({"BP_A": {"f_CTMT_P_HI_Byp_Sw": 1}, "BP_B": {"f_CTMT_P_HI_Byp_Sw": 1}})
        res = [plant.step({"BP_A": {"f_CTMT_P": 5}, "BP_B": {"f_CTMT_P": 5}}) for _ in range(4)][-1]
        self.assertEqual(res["CP_A"]["f_RT_Init"], 0)

    def test_link_failure_counts_as_a_trip_vote(self):
        plant = at_power_plant()
        res = plant.step({"CP_A": {"f_C_Link_Error": 1}})
        self.assertEqual(res["CP_A"]["f_RT_Init"], 1)          # one vote only
        res = plant.step({"BP_D": {"f_Module_Error": 1}})
        self.assertEqual(res["CP_A"]["f_RT_Init"], 0)          # C (link) + D (module error)


if __name__ == "__main__":
    unittest.main()
