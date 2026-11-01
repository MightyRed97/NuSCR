"""The browser engine (4_code/js/rps_engine.js) is generated from the current design and, run under Node.js,
computes exactly what the double-arithmetic reference computes; a wrong engine is detected (fault injection)."""
import shutil
import subprocess
import tempfile
import unittest
from functools import lru_cache
from pathlib import Path

import helpers
from design_model import load_design
from export_js import engine_text
from paths import JS_ENGINE, ROOT
from vectors import (boundary_run, bp_scenario_run, check_trace, random_system_run, read_trace_csv,
                     reference_traces, write_stimulus_csv)

NODE = shutil.which("node")
RUNNER = ROOT / "tools" / "js_runner.js"


@lru_cache(maxsize=1)
def design():
    return load_design(spec=helpers.spec())


class EngineIsCurrent(unittest.TestCase):
    def test_file_in_package_is_current(self):
        self.assertEqual(JS_ENGINE.read_text(encoding="utf-8"), engine_text(design()))


@unittest.skipUnless(NODE, "Node.js is not on PATH")
class EngineMatchesTheReference(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="rps_js_test_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def replay(self, engine, run):
        stim, trace = self.tmp / f"{run.name}.csv", self.tmp / f"{run.name}_trace.csv"
        write_stimulus_csv(helpers.spec(), run, stim)
        r = subprocess.run([NODE, str(RUNNER), str(engine), run.target, str(stim), str(trace)], capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return check_trace(design(), run, read_trace_csv(trace), reference_traces(design(), run))

    def test_boundary_and_system_runs(self):
        for run in (boundary_run(helpers.spec()), random_system_run(helpers.spec(), 3, 300)):
            rep = self.replay(JS_ENGINE, run)
            self.assertTrue(rep["passed"], rep)

    def test_wrong_engine_is_detected(self):
        old = "(i.f_PZR_P >= k_PZR_P_HI_Trip_SP)"
        text = JS_ENGINE.read_text(encoding="utf-8")
        self.assertIn(old, text)
        mutant = self.tmp / "mutant_engine.js"
        mutant.write_text(text.replace(old, "(i.f_PZR_P > k_PZR_P_HI_Trip_SP)"), encoding="utf-8")
        self.assertFalse(self.replay(mutant, boundary_run(helpers.spec()))["passed"])

    def test_simulator_api(self):
        script = ("const R = require(process.argv[1]); const s = new R.Simulator('SYSTEM'); s.step();"
                  "s.set('BP.f_LOGP_HI_OB_Ini', 1); s.step(); s.set('BP.f_LOGP_HI_OB_Ini', 0); s.step();"
                  "const before = s.get('CP_A.f_RT_Init'); s.set('BP.f_PZR_P', 2400);"
                  "for (let k = 0; k < 40; k++) s.step();"
                  "console.log(JSON.stringify([before, s.get('CP_A.f_PZR_P_HI_Coinc'), s.get('CP_A.f_RT_Init'),"
                  "s.resolveInput('f_PZR_P').length, s.isDriven('CP_A.f_A_VOPT_Trip')]));")
        r = subprocess.run([NODE, "-e", script, str(JS_ENGINE)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "[true,false,false,4,true]")


if __name__ == "__main__":
    unittest.main()
