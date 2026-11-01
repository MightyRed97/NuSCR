"""The comparison of an implementation with the requirements (tools/vectors.py) does not trust its own reference: a
fault in the translation of the design that every export shares makes the run fail instead of passing as rounding
effects."""
import unittest
from unittest import mock

import helpers
import export_common
import nuscr_lang
import vectors
from design_model import load_design
from vectors import boundary_run, check_trace, reference_traces


class ReferenceIsChecked(unittest.TestCase):
    def test_correct_reference_passes(self):
        design = load_design(spec=helpers.spec())
        run = boundary_run(helpers.spec())
        ref = reference_traces(design, run)
        rep = check_trace(design, run, ref[1], ref)
        self.assertTrue(rep["passed"], rep)
        self.assertEqual(rep["reference_errors"], 0)

    def test_shared_translation_fault_fails_instead_of_passing_as_rounding(self):
        design = load_design(spec=helpers.spec())
        run = boundary_run(helpers.spec())
        wrong = lambda e, env: nuscr_lang.evaluate(e, env) + 1       # every folded threshold off by one   # noqa: E731
        try:
            with mock.patch.object(export_common, "evaluate", wrong):
                vectors._programs.pop(id(design), None)
                ref = reference_traces(design, run)
        finally:
            vectors._programs.pop(id(design), None)
        rep = check_trace(design, run, ref[1], ref)                  # an implementation that shares the fault
        self.assertFalse(rep["passed"])
        self.assertGreater(rep["reference_errors"], 0)
        self.assertGreater(rep["failures"], 0)


if __name__ == "__main__":
    unittest.main()
