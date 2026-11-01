"""The OpenModelica model (3_case_tool/openmodelica/RPS.mo) is generated from the current design and is well
formed; when OpenModelica is installed, simulating it gives the requirements model's values in every scan and a
wrong model is detected (fault injection)."""
import os
import re
import shutil
import tempfile
import unittest
from functools import lru_cache
from pathlib import Path

import helpers
from design_model import load_design
from export_modelica import model_text, verification_text
from paths import MODELICA_DIR
from run_openmodelica import find_omc, read_result, result_columns, short_path, simulate
from vectors import boundary_run, bp_scenario_run, check_trace, reference_traces

OMC = find_omc()


@lru_cache(maxsize=1)
def design():
    return load_design(spec=helpers.spec())


@lru_cache(maxsize=1)
def text():
    return model_text(design())


class ModelIsCurrentAndWellFormed(unittest.TestCase):
    def test_file_in_package_is_current(self):
        self.assertEqual((MODELICA_DIR / "RPS.mo").read_text(encoding="utf-8"), text())

    def test_every_class_is_closed_and_every_group_is_modelled(self):
        opened = re.findall(r"^\s*(?:partial\s+)?(?:package|model|block)\s+(\w+)", text(), re.M)
        closed = re.findall(r"^\s*end\s+(?!when\b)(\w+);", text(), re.M)
        self.assertEqual(sorted(opened), sorted(closed))
        spec = helpers.spec()
        for m in spec.raw["modules"]:
            for g in m["fod"]["nodes"]:
                self.assertIn(f"model {g['name']} ", text())
        for b in design().raw["blocks"]:
            self.assertIn(f"block {b} ", text())

    def test_no_library_and_balanced_brackets(self):
        self.assertNotIn("uses(", text())
        self.assertIsNone(re.search(r"(?<![A-Za-z])Modelica\.\w", text()))
        for o, c in ("()", "{}", "[]"):
            self.assertEqual(text().count(o), text().count(c), o + c)


@unittest.skipUnless(OMC, "OpenModelica (omc) is not installed")
class OpenModelicaSimulation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="rps_om_test_"))
        cls.work = short_path(cls.tmp)
        cls.env = dict(os.environ)
        cls.env.setdefault("OPENMODELICAHOME", str(OMC.parent.parent))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_model(self, model, run):
        (self.work / "RPS.mo").write_text(model, encoding="utf-8")
        (self.work / "RPS_Verification.mo").write_text(verification_text(design(), [run]), encoding="utf-8")
        result, _ = simulate(OMC, self.work, design(), run, self.env)
        cols, counter = result_columns(design(), run)
        trace = read_result(result, cols, counter, len(run.scans))
        return check_trace(design(), run, trace, reference_traces(design(), run), bit_exact=False)

    def test_scenario_matches_the_requirements(self):
        rep = self.run_model(text(), bp_scenario_run(helpers.spec(), "pzr_pressure_excursion"))
        self.assertTrue(rep["passed"], rep)

    def test_failed_simulation_is_reported_with_the_tool_message(self):
        # OpenModelica leaves a partial result file when a simulation fails; that must not count as a result
        run = bp_scenario_run(helpers.spec(), "pzr_pressure_excursion")
        ver = verification_text(design(), [run])
        marker = "    equation\n"
        self.assertIn(marker, ver)
        ver = ver.replace(marker, marker + '      assert(time < 2.0, "INJECTED RUNTIME FAILURE");\n', 1)
        (self.work / "RPS.mo").write_text(text(), encoding="utf-8")
        (self.work / "RPS_Verification.mo").write_text(ver, encoding="utf-8")
        with self.assertRaises(RuntimeError) as cm:
            simulate(OMC, self.work, design(), run, self.env)
        self.assertIn("INJECTED RUNTIME FAILURE", str(cm.exception))

    def test_wrong_comparator_is_detected(self):
        old = "PZR_P_HI_TRIP_COND(y = (f_PZR_P >= k_PZR_P_HI_Trip_SP))"
        self.assertIn(old, text())
        mutant = text().replace(old, old.replace(">=", ">"))
        self.assertFalse(self.run_model(mutant, boundary_run(helpers.spec()))["passed"])


if __name__ == "__main__":
    unittest.main()
