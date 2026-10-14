#!/usr/bin/env python3
import pathlib
import copy
import json
import subprocess
import tempfile
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
from compare_results import compare_results, measure_profile_drift


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

    def test_profile_drift_records_only_permitted_decisions(self):
        left = {'probability': 0.49, 'values': {'flag': False, 'level': 1, 'name': 'a'},
                'usage': {'input_tokens': 42}}
        right = {'probability': 0.51, 'values': {'flag': True, 'level': 2, 'name': 'b'},
                 'usage': {'input_tokens': 42}}
        paths = [('values', key) for key in left['values']]
        result = measure_profile_drift(left, right, paths)
        self.assertAlmostEqual(result['maximum_absolute_difference'], 0.02)
        self.assertEqual(result['numeric_differences'], 1)
        self.assertEqual([x['path'] for x in result['decision_differences']],
                         ['$.values.flag', '$.values.level', '$.values.name'])
        with self.assertRaises(AssertionError):
            compare_results(left, right, 1)
        right['usage']['input_tokens'] = 43
        with self.assertRaises(AssertionError):
            measure_profile_drift(left, right, paths)

    def test_drift_still_rejects_invalid_structure_and_values(self):
        for left, right in [(0.5, float('nan')), (float('inf'), 0.5),
                            ({'a': 0.1, 'b': 0.2}, {'b': 0.2, 'a': 0.1}),
                            ([0.5], []), (True, 1), ('noul', 'choice')]:
            with self.subTest(left=left, right=right), self.assertRaises(AssertionError):
                measure_profile_drift(left, right)
        with self.assertRaises(AssertionError):
            measure_profile_drift({'choice': 'a'}, {'choice': False}, [('choice',)])

    def test_browser_cli_checks_identity_and_preserves_decision_drift(self):
        runtime = {'llama_cpp_commit': 'test-runtime', 'runtime_patches': [],
                   'numerical_profile': {'id': 'test-profile', 'cross_profile_equivalence': False}}
        result = {'answers': {'q': {'type': 'choice', 'choice': 'a',
                  'probabilities': {'a': 0.51, 'b': 0.49}}}, 'usage': {'input_tokens': 10}}
        native = {'fixtures': {'case.json': {'C': result}}, 'runtime': runtime,
                  'library_sha256': 'test-library', 'model_sha256': 'test-model',
                  'fixture_hashes': {'case.json': 'test-fixture'}}
        browser = {'status': 'passed', 'backend': 'wasm', 'ticks': 1,
                   'events': [{'event': 'start'}, {'event': 'end'}],
                   'results': {'case.json': copy.deepcopy(result)}, 'info': runtime,
                   'model_sha256': 'test-model', 'fixture_hashes': native['fixture_hashes']}
        browser['results']['case.json']['answers']['q'].update(
            choice='b', probabilities={'a': 0.49, 'b': 0.51})
        script = pathlib.Path(__file__).with_name('compare-browser.py')
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            (base / 'native.json').write_text(json.dumps(native))
            for optimization in [[], ['-O']]:
                for invalid in [None, 'model_sha256', 'fixture_hashes']:
                    candidate = copy.deepcopy(browser)
                    if invalid: candidate[invalid] = 'mismatched'
                    (base / 'browser.json').write_text(json.dumps(candidate))
                    process = subprocess.run([sys.executable, *optimization, script, base / 'browser.json',
                                              '--native', base / 'native.json'], capture_output=True, text=True)
                    if invalid:
                        self.assertNotEqual(process.returncode, 0)
                    else:
                        self.assertEqual(process.returncode, 0, process.stderr)
                        drift = json.loads(process.stdout)
                        self.assertFalse(drift['probability_equivalence'])
                        self.assertEqual(len(drift['decision_differences']), 1)
                        self.assertAlmostEqual(drift['maximum_absolute_difference'], 0.02)

    def test_bad_tolerance(self):
        for tolerance in [-1, float('nan'), float('inf'), True]:
            with self.assertRaises(ValueError):
                compare_results(1, 1, tolerance)


if __name__ == '__main__':
    unittest.main()
