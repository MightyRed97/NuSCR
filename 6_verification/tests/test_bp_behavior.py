"""Scenario tests of the bistable processor, one class per NuSCR trip-logic type.
Requirement IDs refer to the 'requirements' section of the specification."""
import unittest

from helpers import F, at_power_bp, k, scans, spec, trip_ids
from nuscr_sim import ModuleSim


def run(bp, changes, n):
    """Apply `changes` and run n scans; return the list of outputs."""
    out = [bp.step(changes)]
    for _ in range(n - 1):
        out.append(bp.step())
    return out


class Startup(unittest.TestCase):
    """SYS-GEN-04: fail-safe initialisation."""

    def test_nominal_at_power_start_all_normal_after_bypass(self):
        bp = at_power_bp()
        out = bp.step()
        for x in trip_ids():
            self.assertEqual(out[f"th_{x}_Trip"], 1, x)
            self.assertEqual(out[f"th_{x}_Pretrip"], 1, x)

    def test_high_log_power_stays_tripped_from_first_scan_without_bypass(self):
        bp = ModuleSim(spec(), "BP")          # at power, operator has not bypassed
        n = scans("k_LOGP_HI_Trip_Delay")
        outs = run(bp, None, n + 1)
        self.assertEqual([int(o["th_LOGP_HI_Trip"]) for o in outs], [0] * (n + 1))      # no NORMAL glitch
        self.assertEqual(bp.states["th_LOGP_HI_Trip"], "TRIP_BY_LOGIC")               # confirmed after delay
        out = bp.step({"f_LOGP_HI_OB_Ini": 1})
        self.assertEqual(out["th_LOGP_HI_Trip"], 1)

    def test_bypass_button_held_from_power_up_is_ignored(self):
        """SYS-GEN-09: a held or stuck button does not establish a bypass."""
        bp = ModuleSim(spec(), "BP")
        outs = run(bp, {"f_LOGP_HI_OB_Ini": 1}, 10)
        self.assertTrue(all(o["h_LOGP_HI_OB_Sta"] == 0 and o["th_LOGP_HI_Trip"] == 0 for o in outs))
        bp.step({"f_LOGP_HI_OB_Ini": 0})
        self.assertEqual(bp.step({"f_LOGP_HI_OB_Ini": 1})["h_LOGP_HI_OB_Sta"], 1)      # a real press works

    def test_start_with_module_error_keeps_everything_tripped(self):
        bp = ModuleSim(spec(), "BP")
        for out in run(bp, {"f_Module_Error": 1}, 5):
            self.assertTrue(all(out[f"th_{x}_{w}"] == 0 for x in trip_ids() for w in ("Trip", "Pretrip")))

    def test_initial_values_before_first_scan(self):
        bp = ModuleSim(spec(), "BP")
        self.assertEqual(bp.values["th_PZR_P_HI_Trip"], 0)
        self.assertEqual(bp.values["h_LOGP_HI_OB_Sta"], 0)
        self.assertEqual(bp.values["h_PZR_P_LO_Trip_SP"], k("k_PZR_P_LO_SP_Max"))


class FixedSetpointRisingTrip(unittest.TestCase):
    """High pressurizer pressure: SYS-PZR_P_HI-01, SYS-GEN-06/07/08."""

    def setUp(self):
        self.bp = at_power_bp()
        self.sp, self.hys = k("k_PZR_P_HI_Trip_SP"), k("k_PZR_P_HI_Trip_Hys")
        self.n = scans("k_PZR_P_HI_Trip_Delay")

    def test_trip_exactly_after_delay_at_setpoint(self):
        outs = run(self.bp, {"f_PZR_P": self.sp}, self.n + 1)
        self.assertEqual([int(o["th_PZR_P_HI_Trip"]) for o in outs], [1] * self.n + [0])

    def test_below_setpoint_no_trip(self):
        outs = run(self.bp, {"f_PZR_P": self.sp - F(1, 100)}, 20)
        self.assertTrue(all(o["th_PZR_P_HI_Trip"] == 1 for o in outs))

    def test_excursion_shorter_than_delay_is_filtered(self):
        run(self.bp, {"f_PZR_P": self.sp + 50}, self.n)
        outs = run(self.bp, {"f_PZR_P": 2250}, 10)
        self.assertTrue(all(o["th_PZR_P_HI_Trip"] == 1 for o in outs))
        self.assertEqual(self.bp.states["th_PZR_P_HI_Trip"], "NORMAL")

    def test_hysteresis(self):
        run(self.bp, {"f_PZR_P": self.sp + 20}, self.n + 1)
        self.assertEqual(self.bp.step({"f_PZR_P": self.sp - self.hys})["th_PZR_P_HI_Trip"], 0)   # still in band
        self.assertEqual(self.bp.step({"f_PZR_P": self.sp - self.hys - F(1, 100)})["th_PZR_P_HI_Trip"], 1)

    def test_pretrip_precedes_trip(self):
        outs = run(self.bp, {"f_PZR_P": k("k_PZR_P_HI_Pretrip_SP")}, 20)
        self.assertEqual(outs[-1]["th_PZR_P_HI_Pretrip"], 0)
        self.assertEqual(outs[-1]["th_PZR_P_HI_Trip"], 1)


class FixedSetpointFallingTrip(unittest.TestCase):
    """Low SG1 level: SYS-SG1_L_LO-01."""

    def test_trip_delay_hysteresis_and_independence_of_sg2(self):
        bp = at_power_bp()
        sp, hys, n = k("k_SG1_L_LO_Trip_SP"), k("k_SG1_L_LO_Trip_Hys"), scans("k_SG1_L_LO_Trip_Delay")
        outs = run(bp, {"f_SG1_L": sp}, n + 1)
        self.assertEqual([int(o["th_SG1_L_LO_Trip"]) for o in outs], [1] * n + [0])
        self.assertEqual(outs[-1]["th_SG2_L_LO_Trip"], 1)
        self.assertEqual(bp.step({"f_SG1_L": sp + hys})["th_SG1_L_LO_Trip"], 0)
        self.assertEqual(bp.step({"f_SG1_L": sp + hys + F(1, 10)})["th_SG1_L_LO_Trip"], 1)


class InputValidityAndDiagnostics(unittest.TestCase):
    """SYS-GEN-02, SYS-GEN-03."""

    def test_out_of_range_input_trips_its_functions_without_delay(self):
        bp = at_power_bp()
        out = bp.step({"f_PZR_P": k("k_PZR_P_Valid_Max") + F(1, 10)})
        for x in ("PZR_P_HI", "PZR_P_LO"):
            self.assertEqual(out[f"th_{x}_Trip"], 0)
            self.assertEqual(out[f"th_{x}_Pretrip"], 0)
            self.assertEqual(bp.states[f"th_{x}_Trip"], "TRIP_BY_ERROR")
        self.assertEqual(out["th_SG1_L_LO_Trip"], 1)
        out = bp.step({"f_PZR_P": 2250})
        self.assertEqual((out["th_PZR_P_HI_Trip"], out["th_PZR_P_LO_Trip"]), (1, 1))

    def test_module_and_channel_errors_trip_everything_in_the_same_scan(self):
        for flag in ("f_Module_Error", "f_Channel_Error"):
            bp = at_power_bp()
            out = bp.step({flag: 1})
            self.assertTrue(all(out[f"th_{x}_{w}"] == 0 for x in trip_ids() for w in ("Trip", "Pretrip")), flag)

    def test_cpc_link_error_trips_only_the_cpc_functions(self):
        bp = at_power_bp()
        out = bp.step({"f_CPC_Link_Error": 1})
        tripped = {x for x in trip_ids() if out[f"th_{x}_Trip"] == 0}
        self.assertEqual(tripped, {"LPD_HI", "DNBR_LO"})

    def test_error_trips_are_not_inhibited_by_operating_bypass(self):
        bp = at_power_bp()           # high log power trip is in operating bypass
        self.assertEqual(bp.values["h_LOGP_HI_OB_Sta"], 1)
        self.assertEqual(bp.step({"f_Channel_Error": 1})["th_LOGP_HI_Trip"], 0)

    def test_process_trip_keeps_its_hysteresis_across_an_error(self):
        """SYS-GEN-07: an error during a process trip must not release the trip inside the hysteresis band."""
        bp = at_power_bp()
        sp, hys = k("k_PZR_P_HI_Trip_SP"), k("k_PZR_P_HI_Trip_Hys")
        run(bp, {"f_PZR_P": sp + 20}, scans("k_PZR_P_HI_Trip_Delay") + 1)
        self.assertEqual(bp.states["th_PZR_P_HI_Trip"], "TRIP_BY_LOGIC")
        bp.step({"f_Module_Error": 1, "f_PZR_P": sp - hys / 2})             # into the band during the error
        self.assertEqual(bp.states["th_PZR_P_HI_Trip"], "TRIP_BY_ERROR_LATCHED")
        out = bp.step({"f_Module_Error": 0})                                # error clears inside the band
        self.assertEqual((out["th_PZR_P_HI_Trip"], bp.states["th_PZR_P_HI_Trip"]), (0, "TRIP_BY_LOGIC"))
        out = bp.step({"f_PZR_P": sp - hys - 1})                            # recovered past the hysteresis
        self.assertEqual(out["th_PZR_P_HI_Trip"], 1)

    def test_latched_error_releases_when_error_clears_after_recovery(self):
        bp = at_power_bp()
        sp, hys = k("k_PZR_P_HI_Trip_SP"), k("k_PZR_P_HI_Trip_Hys")
        run(bp, {"f_PZR_P": sp + 20}, scans("k_PZR_P_HI_Trip_Delay") + 1)
        bp.step({"f_Channel_Error": 1, "f_PZR_P": 2250})
        out = bp.step({"f_Channel_Error": 0})
        self.assertEqual((out["th_PZR_P_HI_Trip"], bp.states["th_PZR_P_HI_Trip"]), (1, "NORMAL"))

    def test_error_cleared_while_condition_present_keeps_trip_and_confirms_after_delay(self):
        """SYS-GEN-04/06: the output stays tripped and becomes a process trip after the persistence delay."""
        bp = at_power_bp()
        bp.step({"f_Module_Error": 1, "f_PZR_P": 2400})
        n = scans("k_PZR_P_HI_Trip_Delay")
        outs = run(bp, {"f_Module_Error": 0}, n + 1)
        self.assertTrue(all(o["th_PZR_P_HI_Trip"] == 0 for o in outs))
        self.assertEqual(bp.states["th_PZR_P_HI_Trip"], "TRIP_BY_LOGIC")

    def test_condition_seen_for_one_scan_after_error_is_not_latched(self):
        """SYS-GEN-06 'only when': a condition that does not persist for the delay never becomes a process trip."""
        bp = at_power_bp()
        sp, hys = k("k_PZR_P_HI_Trip_SP"), k("k_PZR_P_HI_Trip_Hys")
        bp.step({"f_Module_Error": 1})
        out = bp.step({"f_Module_Error": 0, "f_PZR_P": sp + 1})
        self.assertEqual((out["th_PZR_P_HI_Trip"], bp.states["th_PZR_P_HI_Trip"]), (0, "TRIP_PENDING"))
        out = bp.step({"f_PZR_P": sp - hys / 2})                           # back into the band
        self.assertEqual((out["th_PZR_P_HI_Trip"], bp.states["th_PZR_P_HI_Trip"]), (1, "NORMAL"))


class FixedSetpointRisingTripWithOperatingBypass(unittest.TestCase):
    """High log power (Fixed_Setpoint_Rising_Trip_with_OB): SYS-LOGP_HI-01/03, SYS-GEN-09."""

    def setUp(self):
        self.bp = ModuleSim(spec(), "BP")
        self.bp.step({"f_NPWR_LOG": F("1e-6")})      # sub-critical start-up conditions

    def test_bypass_rejected_below_permissive(self):
        out = run(self.bp, {"f_NPWR_LOG": F("1e-5"), "f_LOGP_HI_OB_Ini": 1}, 5)[-1]
        self.assertEqual(out["f_LOGP_HI_OB_Perm"], 0)
        self.assertEqual(out["h_LOGP_HI_OB_Sta"], 0)

    def test_power_ascension_with_bypass_then_automatic_removal(self):
        self.bp.step({"f_NPWR_LOG": F("1e-3")})
        out = self.bp.step({"f_LOGP_HI_OB_Ini": 1})
        self.assertEqual(out["h_LOGP_HI_OB_Sta"], 1)
        self.bp.step({"f_LOGP_HI_OB_Ini": 0})
        outs = run(self.bp, {"f_NPWR_LOG": 50}, 20)
        self.assertTrue(all(o["th_LOGP_HI_Trip"] == 1 and o["th_LOGP_HI_Pretrip"] == 1 for o in outs))
        out = self.bp.step({"f_NPWR_LOG": F("5e-5")})         # below the permissive
        self.assertEqual(out["h_LOGP_HI_OB_Sta"], 0)           # removed in the same scan

    def test_trip_without_bypass(self):
        n = scans("k_LOGP_HI_Trip_Delay")
        outs = run(self.bp, {"f_NPWR_LOG": F("0.02")}, n + 1)
        self.assertEqual([int(o["th_LOGP_HI_Trip"]) for o in outs], [1] * n + [0])

    def test_bypass_clears_an_existing_trip(self):
        run(self.bp, {"f_NPWR_LOG": F("0.02")}, 10)
        self.assertEqual(self.bp.values["th_LOGP_HI_Trip"], 0)
        self.assertEqual(self.bp.step({"f_LOGP_HI_OB_Ini": 1})["th_LOGP_HI_Trip"], 1)


class VariableSetpointRisingTrip(unittest.TestCase):
    """Variable overpower: SYS-VOPT-01/02."""

    def fresh(self, power):
        bp = ModuleSim(spec(), "BP")
        bp.step({"f_NPWR_LIN": power})
        bp.step({"f_LOGP_HI_OB_Ini": 1})
        bp.step({"f_LOGP_HI_OB_Ini": 0})
        return bp

    def test_setpoint_initialised_and_clamped(self):
        self.assertEqual(self.fresh(100).values["h_VOPT_Trip_SP"], k("k_VOPT_SP_Max"))
        self.assertEqual(self.fresh(50).values["h_VOPT_Trip_SP"], 50 + k("k_VOPT_Band"))
        self.assertEqual(self.fresh(5).values["h_VOPT_Trip_SP"], k("k_VOPT_SP_Min"))

    def test_slow_power_increase_is_followed(self):
        bp = self.fresh(50)
        rate = k("k_VOPT_SP_Step") / 2                  # half the permitted setpoint rate
        p = F(50)
        for _ in range(400):
            p += rate
            out = bp.step({"f_NPWR_LIN": p})
            self.assertEqual(out["h_VOPT_Trip_SP"], p + k("k_VOPT_Band"))
            self.assertEqual((out["th_VOPT_Trip"], out["th_VOPT_Pretrip"]), (1, 1))

    def test_fast_power_increase_trips(self):
        bp = self.fresh(50)
        n = scans("k_VOPT_Trip_Delay")
        outs = run(bp, {"f_NPWR_LIN": 65}, n + 1)
        self.assertEqual([int(o["th_VOPT_Trip"]) for o in outs], [1] * n + [0])
        steps = [o["h_VOPT_Trip_SP"] for o in outs]
        self.assertTrue(all(b - a == k("k_VOPT_SP_Step") for a, b in zip([F(60)] + steps, steps)))

    def test_setpoint_follows_power_down_immediately(self):
        bp = self.fresh(80)
        self.assertEqual(bp.step({"f_NPWR_LIN": 40})["h_VOPT_Trip_SP"], 40 + k("k_VOPT_Band"))

    def test_setpoint_holds_while_input_invalid(self):
        bp = self.fresh(50)
        out = bp.step({"f_NPWR_LIN": -1})
        self.assertEqual(out["h_VOPT_Trip_SP"], 60)
        self.assertEqual(out["th_VOPT_Trip"], 0)


class ManualResetVariableSetpointFallingTrip(unittest.TestCase):
    """Low pressurizer pressure: SYS-PZR_P_LO-01/02/03."""

    def setUp(self):
        self.bp = at_power_bp()
        self.band = k("k_PZR_P_LO_Band")

    def press(self, pressure, scans_held=1):
        out = run(self.bp, {"f_PZR_P": pressure, "f_PZR_P_LO_SP_Reset": 1}, scans_held)
        self.bp.step({"f_PZR_P_LO_SP_Reset": 0})
        return out

    def test_reset_lowers_setpoint_once_per_press(self):
        self.assertEqual(self.bp.values["h_PZR_P_LO_Trip_SP"], k("k_PZR_P_LO_SP_Max"))
        outs = self.press(1900, scans_held=20)
        self.assertEqual(sum(int(o["h_PZR_P_LO_Reset_Pulse"]) for o in outs), 1)
        self.assertEqual(self.bp.values["h_PZR_P_LO_Trip_SP"], 1900 - self.band)
        self.bp.step({"f_PZR_P": 1850})
        self.assertEqual(self.bp.values["h_PZR_P_LO_Trip_SP"], 1900 - self.band)   # no auto decrease
        self.press(1850)
        self.assertEqual(self.bp.values["h_PZR_P_LO_Trip_SP"], 1850 - self.band)

    def test_depressurisation_without_reset_trips(self):
        n = scans("k_PZR_P_LO_Trip_Delay")
        outs = run(self.bp, {"f_PZR_P": k("k_PZR_P_LO_SP_Max")}, n + 1)
        self.assertEqual([int(o["th_PZR_P_LO_Trip"]) for o in outs], [1] * n + [0])

    def test_setpoint_rises_automatically_up_to_maximum(self):
        self.press(1600)
        self.assertEqual(self.bp.values["h_PZR_P_LO_Trip_SP"], 1400)
        self.bp.step({"f_PZR_P": 1700})
        self.assertEqual(self.bp.values["h_PZR_P_LO_Trip_SP"], 1500)
        self.bp.step({"f_PZR_P": 2250})
        self.assertEqual(self.bp.values["h_PZR_P_LO_Trip_SP"], k("k_PZR_P_LO_SP_Max"))

    def test_reset_floor(self):
        self.press(250)
        self.assertEqual(self.bp.values["h_PZR_P_LO_Trip_SP"], k("k_PZR_P_LO_SP_Min"))

    def test_button_held_at_power_up_is_ignored(self):
        bp = ModuleSim(spec(), "BP")
        outs = run(bp, {"f_PZR_P_LO_SP_Reset": 1, "f_PZR_P": 1900, "f_LOGP_HI_OB_Ini": 1}, 10)
        self.assertTrue(all(o["h_PZR_P_LO_Reset_Pulse"] == 0 for o in outs))
        self.assertEqual(bp.values["h_PZR_P_LO_Trip_SP"], k("k_PZR_P_LO_SP_Max"))

    def test_bypass_removal_restarts_the_persistence_delay(self):
        """SYS-GEN-06: an active operating bypass interrupts persistence; the trip follows the delay after removal."""
        bp = ModuleSim(spec(), "BP")
        bp.step({"f_PZR_P": 300})                                           # below the 400 psia permissive
        bp.step({"f_PZR_P_LO_OB_Ini": 1})
        outs = run(bp, {"f_PZR_P_LO_OB_Ini": 0}, 5)
        self.assertTrue(all(o["h_PZR_P_LO_OB_Sta"] == 1 and o["th_PZR_P_LO_Trip"] == 1 for o in outs))
        n = scans("k_PZR_P_LO_Trip_Delay")
        outs = run(bp, {"f_PZR_P": 450}, n + 1)                             # permissive lost: bypass removed
        self.assertEqual(outs[0]["h_PZR_P_LO_OB_Sta"], 0)
        self.assertEqual([int(o["th_PZR_P_LO_Trip"]) for o in outs], [1] * n + [0])

    def test_operating_bypass_below_permissive(self):
        self.press(600)
        self.press(420)                                           # setpoint 220
        self.bp.step({"f_PZR_P": 390, "f_PZR_P_LO_OB_Ini": 1})
        self.assertEqual(self.bp.values["h_PZR_P_LO_OB_Sta"], 1)
        outs = run(self.bp, {"f_PZR_P_LO_OB_Ini": 0, "f_PZR_P": 50}, 10)
        self.assertTrue(all(o["th_PZR_P_LO_Trip"] == 1 for o in outs))
        out = self.bp.step({"f_PZR_P": 450})
        self.assertEqual(out["h_PZR_P_LO_OB_Sta"], 0)


class RateLimitedVariableSetpointFallingTrip(unittest.TestCase):
    """Low reactor coolant flow: SYS-SG1_F_LO-01/02/03."""

    def test_rapid_flow_reduction_trips(self):
        bp = at_power_bp()
        self.assertEqual(bp.values["h_SG1_F_LO_Trip_SP"], k("k_SG1_F_LO_SP_Max"))
        n = scans("k_SG1_F_LO_Trip_Delay")
        outs = run(bp, {"f_SG1_F": 70}, n + 1)
        self.assertEqual([int(o["th_SG1_F_LO_Trip"]) for o in outs], [1] * n + [0])
        self.assertEqual(outs[0]["h_SG1_F_LO_Trip_SP"], k("k_SG1_F_LO_SP_Max") - k("k_SG1_F_LO_SP_Step"))

    def test_slow_flow_reduction_tracks_until_floor(self):
        bp = at_power_bp()
        f, step = F(100), k("k_SG1_F_LO_SP_Step") / 2
        floor = k("k_SG1_F_LO_SP_Min")
        tripped_at = None
        while f > 30:
            f -= step
            out = bp.step({"f_SG1_F": f})
            if out["th_SG1_F_LO_Trip"] == 0 and tripped_at is None:
                tripped_at = f
        self.assertIsNotNone(tripped_at)
        self.assertLessEqual(tripped_at, floor)                           # no trip above the floor
        self.assertGreaterEqual(tripped_at, floor - 3 * step)             # ... and promptly at the floor

    def test_low_power_operating_bypass(self):
        bp = ModuleSim(spec(), "BP")
        bp.step({"f_NPWR_LOG": F("1e-6"), "f_NPWR_LIN": 0, "f_SG1_F": 0, "f_SG2_F": 0})
        bp.step({"f_SG1_F_LO_OB_Ini": 1, "f_SG2_F_LO_OB_Ini": 1})
        out = bp.step({"f_SG1_F_LO_OB_Ini": 0, "f_SG2_F_LO_OB_Ini": 0})
        self.assertEqual((out["h_SG1_F_LO_OB_Sta"], out["th_SG1_F_LO_Trip"]), (1, 1))
        out = run(bp, {"f_NPWR_LOG": F("0.01")}, scans("k_SG1_F_LO_Trip_Delay") + 1)[-1]
        self.assertEqual((out["h_SG1_F_LO_OB_Sta"], out["th_SG1_F_LO_Trip"]), (0, 0))


class DigitalTripWithOperatingBypass(unittest.TestCase):
    """Low DNBR from the CPC: SYS-DNBR_LO-01/03."""

    def test_trip_follows_cpc_signal_with_delay(self):
        bp = at_power_bp()
        n = scans("k_DNBR_LO_Trip_Delay")
        outs = run(bp, {"f_DNBR_LO_Trip_In": 0}, n + 1)
        self.assertEqual([int(o["th_DNBR_LO_Trip"]) for o in outs], [1] * n + [0])
        self.assertEqual(bp.step({"f_DNBR_LO_Trip_In": 1})["th_DNBR_LO_Trip"], 1)

    def test_bypass_only_at_low_power_and_removed_on_power_increase(self):
        bp = at_power_bp()
        self.assertEqual(bp.step({"f_DNBR_LO_OB_Ini": 1})["h_DNBR_LO_OB_Sta"], 0)   # rejected at power
        bp.step({"f_DNBR_LO_OB_Ini": 0, "f_NPWR_LOG": F("1e-6")})
        self.assertEqual(bp.step({"f_DNBR_LO_OB_Ini": 1})["h_DNBR_LO_OB_Sta"], 1)
        bp.step({"f_DNBR_LO_OB_Ini": 0})
        outs = run(bp, {"f_DNBR_LO_Trip_In": 0}, 10)
        self.assertTrue(all(o["th_DNBR_LO_Trip"] == 1 for o in outs))
        n = scans("k_DNBR_LO_Trip_Delay")
        outs = run(bp, {"f_NPWR_LOG": F("0.001")}, n + 1)
        self.assertEqual(outs[0]["h_DNBR_LO_OB_Sta"], 0)
        self.assertEqual([int(o["th_DNBR_LO_Trip"]) for o in outs], [1] * n + [0])


if __name__ == "__main__":
    unittest.main()
