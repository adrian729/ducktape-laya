#!/usr/bin/env python3
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
from compare_results import compare_results


class Comparison(unittest.TestCase):
    def test_exact_and_tolerance(self):
        sample = {'values': [False, 3, 'a', None], 'probability': 0.5}
        self.assertEqual(compare_results(sample, sample), (0, ''))
        maximum, path = compare_results([0.5], [0.500001], 0.00001)
        self.assertGreater(maximum, 0)
        self.assertEqual(path, '$[0]')

    def test_reject_nonfinite_even_when_both_sides_match(self):
        for number in [float('nan'), float('inf'), float('-inf')]:
            for left, right in [(number, number), (0.5, number), (number, 0.5)]:
                with self.subTest(left=left, right=right), self.assertRaises(AssertionError):
                    compare_results(left, right, 1)

    def test_order_shape_types_and_discrete_values(self):
        for left, right in [({'a': 1, 'b': 2}, {'b': 2, 'a': 1}),
                            ({'a': 1}, {'a': 1, 'b': 2}), ([1], [1, 2]),
                            ([1], {'0': 1}), (True, 1), (0.5, True), (None, False),
                            (3, 4), (3, 3.0), ('a', 'b'), (0.5, 0.6)]:
            with self.subTest(left=left, right=right), self.assertRaises(AssertionError):
                compare_results(left, right, 0.00001)

    def test_bad_tolerance(self):
        for tolerance in [-1, float('nan'), float('inf'), True]:
            with self.assertRaises(ValueError):
                compare_results(1, 1, tolerance)


if __name__ == '__main__':
    unittest.main()
