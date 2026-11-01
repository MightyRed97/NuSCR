"""The exported Scade 6 model parses, type-checks and behaves exactly like the verified design, and the exported
scenario scripts pass on it; broken exports are detected (fault injection)."""
import random
import re
import unittest
from fractions import Fraction
from functools import lru_cache

import helpers
from design_model import DesignModuleSim, load_design
from design_verify import BoundaryInputs
from export_scade import OUT_DIR, model_text
from scade_subset import ScadeSim, check_program, parse_program, run_sss


@lru_cache(maxsize=1)
def design():
    return load_design(spec=helpers.spec())


@lru_cache(maxsize=1)
def text():
    return model_text(design())


def nominal(module):
    m = helpers.spec().modules[module]
    return {n: Fraction(str(m.variables[n]["nominal"])) for n in m.inputs}


def computed(module, name):
    v = helpers.spec().modules[module].variables.get(name)
    return v is not None and v["class"] != "input"


def bp_mismatches(prog, seeds=(1, 2), scans=1500):
    spec = helpers.spec()
    for seed in seeds:
        ss, ds = ScadeSim(prog, "BP", nominal("BP")), DesignModuleSim(design(), "BP")
        gen, req = BoundaryInputs(spec, seed), {}
        for i in range(scans):
            ch = gen.next(req)
            a, req = ss.step(ch), ds.step(ch)
            for k, v in a.items():
                if computed("BP", k) and req[k] != v:
                    return f"seed {seed} scan {i}: {k} design {req[k]}, scade {v}"
    return None


class ExportIsCorrect(unittest.TestCase):
    def test_file_in_package_is_current(self):
        self.assertEqual((OUT_DIR / "RPS_Design.scade").read_text(encoding="utf-8"), text())

    def test_parses_and_type_checks(self):
        prog = parse_program(text())
        self.assertEqual(check_program(prog), [])
        self.assertEqual(sorted(prog.operators), sorted(list(design().raw["blocks"]) + ["BP", "CP"]))

    def test_bp_behaves_like_the_design(self):
        self.assertIsNone(bp_mismatches(parse_program(text())))

    def test_cp_behaves_like_the_design(self):
        prog, rng = parse_program(text()), random.Random(7)
        ss, ds = ScadeSim(prog, "CP", nominal("CP")), DesignModuleSim(design(), "CP")
        for i in range(1500):
            ch = {n: (0 if rng.random() < 0.2 else 1) if n.endswith("_Trip") else (1 if rng.random() < 0.15 else 0)
                  for n in helpers.spec().modules["CP"].inputs}
            a, b = ss.step(ch), ds.step(ch)
            for k, v in a.items():
                if computed("CP", k):
                    self.assertEqual(v, b[k], f"scan {i}: {k}")

    def test_scenario_scripts_pass_on_the_model(self):
        prog = parse_program(text())
        scripts = sorted((OUT_DIR / "scenarios").glob("*.sss"))
        self.assertEqual(len(scripts), 7)
        for path in scripts:
            cycles, checks, failures = run_sss(prog, path, nominal("CP" if path.name.startswith("cp") else "BP"))
            self.assertGreater(checks, 0, path.name)
            self.assertEqual(failures, [], path.name)


class BrokenExportsAreDetected(unittest.TestCase):
    def mutated(self, old, new):
        t = text()
        self.assertIn(old, t)
        return parse_program(t.replace(old, new, 1))

    def test_every_comparator_strictness_error_is_caught_by_the_boundary_scenario(self):
        lines = text().splitlines()
        missed, total = [], 0
        for i, line in enumerate(lines):
            m = re.search(r"= FB_TRIP_BISTABLE\(.*-- (\w+)$", line)
            if not m or "(not f_" in line.split(",")[2]:
                continue                                           # digital trips have no comparator
            for kind, pats in (("COND", [(" >= ", " > "), (" <= ", " < ")]), ("RESET", [(" < ", " <= "), (" > ", " >= ")])):
                old, new = next(p for p in pats if p[0] in line)
                prog = parse_program("\n".join(lines[:i] + [line.replace(old, new, 1)] + lines[i + 1:]) + "\n")
                total += 1
                _, _, failures = run_sss(prog, OUT_DIR / "scenarios" / "bp_boundaries.sss", nominal("BP"),
                                         stop_at_first=True)
                if not failures:
                    missed.append(f"{m.group(1)} {kind}")
        self.assertEqual(total, 52)
        self.assertEqual(missed, [])

    def test_wrong_initial_value(self):
        prog = self.mutated("PREV : bool last = true;", "PREV : bool last = false;")
        self.assertIsNotNone(bp_mismatches(prog))

    def test_int_float_mixing_is_a_type_error(self):
        prog = self.mutated("(last 'CNT + 1)", "(last 'CNT + 1.0)")
        self.assertTrue(any("int32" in e and "float64" in e for e in check_program(prog)))

    def test_missing_memory_declaration_is_an_error(self):
        prog = self.mutated("MEM : bool last = false;", "MEM : bool;")
        self.assertTrue(any("last 'MEM" in e for e in check_program(prog)))


if __name__ == "__main__":
    unittest.main()
