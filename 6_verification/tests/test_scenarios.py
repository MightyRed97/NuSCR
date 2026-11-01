"""The example scenario files replay with the documented outcome."""
import unittest

from helpers import spec
from nuscr_sim import run_scenario
from paths import SCENARIOS_DIR as SC


def column(header, rows, name):
    i = header.index(name)
    return {int(r[0]): r[i] for r in rows}


class Scenarios(unittest.TestCase):
    def test_pzr_pressure_excursion(self):
        h, rows = run_scenario(spec(), SC / "pzr_pressure_excursion.csv")
        trip, pre = column(h, rows, "th_PZR_P_HI_Trip"), column(h, rows, "th_PZR_P_HI_Pretrip")
        self.assertEqual([c for c in trip if trip[c] == "0"], list(range(22, 50)))
        self.assertEqual([c for c in pre if pre[c] == "0"], list(range(12, 50)))

    def test_loss_of_flow_trips_the_reactor(self):
        h, rows = run_scenario(spec(), SC / "loss_of_flow_4ch.csv", system=True)
        rt = column(h, rows, "CP_A.f_RT_Init")
        self.assertEqual(rt[0], "0")                           # all four channels tripped until bypassed
        self.assertEqual(min(c for c in rt if c > 0 and rt[c] == "0"), 15)
        self.assertEqual(rt[30], "0")

    def test_cooldown_never_trips(self):
        h, rows = run_scenario(spec(), SC / "cooldown_manual_reset.csv")
        trip, ob = column(h, rows, "th_PZR_P_LO_Trip"), column(h, rows, "h_PZR_P_LO_OB_Sta")
        self.assertTrue(all(v == "1" for v in trip.values()))
        self.assertEqual(ob[125], "1")
        self.assertEqual(ob[150], "0")


if __name__ == "__main__":
    unittest.main()
