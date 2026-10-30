import unittest
from fractions import Fraction

import helpers  # noqa: F401  (sets sys.path)
from nuscr_lang import ExprError, find_model, is_valid, parse, to_text


class ParserTests(unittest.TestCase):
    def test_precedence_and_round_trip(self):
        for text in ["a = 1 && (b = 1 || !(c = 1))", "x - (y - z) / (2 * w) >= -k",
                     "(a = 1 && b = 0) || c = 1", "f_X >= k_SP - k_Hys"]:
            self.assertEqual(parse(to_text(parse(text))), parse(text))

    def test_type_errors(self):
        with self.assertRaises(ExprError):
            parse("a && b = 1")            # bare variable used as boolean
        with self.assertRaises(ExprError):
            parse("x + (y > 1)")           # boolean inside arithmetic
        with self.assertRaises(ExprError):
            parse("x = 1", "num")
        with self.assertRaises(ExprError):
            parse("x >= ")

    def test_scientific_literals_are_exact(self):
        self.assertEqual(parse("1.0e-8"), ("num", Fraction(1, 10 ** 8)))


class SolverTests(unittest.TestCase):
    def test_linear_real_arithmetic(self):
        self.assertIsNone(find_model(parse("x >= 10 && x < 5"), {}))
        self.assertIsNone(find_model(parse("a < b && b < c && c < a"), {}))
        self.assertIsNone(find_model(parse("x != 3 && x >= 3 && x <= 3"), {}))
        m = find_model(parse("x > 2 * y && y > 3 && x < 7"), {})
        self.assertTrue(m["x"] > 2 * m["y"] and m["y"] > 3 and m["x"] < 7)

    def test_strict_boundaries(self):
        self.assertIsNone(find_model(parse("x > 5 && x <= 5"), {}))
        self.assertEqual(find_model(parse("x >= 5 && x <= 5"), {})["x"], 5)

    def test_finite_domains(self):
        fd = {"e": [0, 1], "v": [0, 1]}
        self.assertTrue(is_valid(parse("(e = 1 || v = 0) || (e = 0 && v = 1)"), fd)[0])
        ok, cex = is_valid(parse("e = 1 || v = 0"), fd)
        self.assertFalse(ok)
        self.assertEqual((cex["e"], cex["v"]), (0, 1))

    def test_nonlinear_rejected(self):
        with self.assertRaises(ExprError):
            find_model(parse("x * y > 3"), {})


if __name__ == "__main__":
    unittest.main()
