"""The C code (4_code/src) is generated from the current design, compiles without warnings, and computes exactly
what the double-arithmetic reference computes; wrong code is detected (fault injection)."""
import shutil
import subprocess
import tempfile
import unittest
from functools import lru_cache
from pathlib import Path

import helpers
from build_c import BuildError, build, find_compiler
from design_model import load_design
from export_c import generate
from paths import C_SOURCE_DIR
from vectors import (boundary_run, bp_scenario_run, check_trace, random_cp_run, random_system_run, read_trace_csv,
                     reference_traces, write_stimulus_csv)


@lru_cache(maxsize=1)
def design():
    return load_design(spec=helpers.spec())


def have_compiler():
    try:
        find_compiler()
        return True
    except BuildError:
        return False


class GeneratedSourcesAreCurrent(unittest.TestCase):
    def test_every_file_is_current(self):
        files = generate(design())
        for name, text in files.items():
            self.assertEqual((C_SOURCE_DIR / name).read_text(encoding="utf-8"), text, name)
        self.assertEqual(sorted(p.name for p in C_SOURCE_DIR.glob("rps_*.[ch]")), sorted(files))

    def test_generated_code_has_no_loops_or_dynamic_memory(self):
        for name, text in generate(design()).items():
            code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(("/*", "*")))
            for word in ("malloc", "calloc", "free(", "for (", "while (", "goto "):
                self.assertNotIn(word, code, name)


@unittest.skipUnless(have_compiler(), "no C compiler (Visual Studio C++ tools, gcc or clang)")
class CompiledCodeMatchesTheReference(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="rps_c_test_"))
        cls.exe, _ = build(cls.tmp / "build")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def replay(self, exe, run):
        stim, trace = self.tmp / f"{run.name}.csv", self.tmp / f"{run.name}_trace.csv"
        write_stimulus_csv(helpers.spec(), run, stim)
        r = subprocess.run([str(exe), "run", run.target, str(stim), str(trace)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return check_trace(design(), run, read_trace_csv(trace), reference_traces(design(), run))

    def test_scenario_and_boundary_runs(self):
        for run in (bp_scenario_run(helpers.spec(), "pzr_pressure_excursion"), boundary_run(helpers.spec())):
            rep = self.replay(self.exe, run)
            self.assertTrue(rep["passed"], rep)

    def test_coincidence_and_system_runs(self):
        for run in (random_cp_run(helpers.spec(), 3, 400), random_system_run(helpers.spec(), 3, 300)):
            rep = self.replay(self.exe, run)
            self.assertTrue(rep["passed"], rep)

    def test_invalid_stimulus_is_rejected(self):
        bad = self.tmp / "bad.csv"
        bad.write_text("cycle,f_Module_Error\n0,2\n", encoding="utf-8")
        r = subprocess.run([str(self.exe), "run", "BP", str(bad)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        bad.write_text("cycle,f_No_Such_Input\n0,1\n", encoding="utf-8")
        r = subprocess.run([str(self.exe), "run", "BP", str(bad)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    def test_wrong_code_is_detected(self):
        spec = helpers.spec()
        mutations = [
            ("rps_bp.c", "(in->f_PZR_P >= k_PZR_P_HI_Trip_SP),  /* COND */",
             "(in->f_PZR_P > k_PZR_P_HI_Trip_SP),  /* COND */", boundary_run(spec)),
            ("rps_blocks.c", "s->HOLD = true;", "s->HOLD = false;", bp_scenario_run(spec, "pzr_pressure_excursion")),
            ("rps_system.c", "CP_A_in.f_A_PZR_P_HI_Trip = out->BP_A.th_PZR_P_HI_Trip;",
             "CP_A_in.f_A_PZR_P_HI_Trip = out->BP_B.th_PZR_P_HI_Trip;", random_system_run(spec, 1, 500)),
        ]
        for k, (name, old, new, run) in enumerate(mutations):
            src = self.tmp / f"mutant{k}"
            shutil.copytree(C_SOURCE_DIR, src)
            text = (src / name).read_text(encoding="utf-8")
            self.assertIn(old, text)
            (src / name).write_text(text.replace(old, new, 1), encoding="utf-8")
            exe, _ = build(self.tmp / f"mutant{k}_build", source_dir=src)
            self.assertFalse(self.replay(exe, run)["passed"], f"mutation in {name} not detected: {new}")


if __name__ == "__main__":
    unittest.main()
