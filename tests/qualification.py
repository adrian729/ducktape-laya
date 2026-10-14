#!/usr/bin/env python3
"""Qualification inventory must fail closed when requirements drift."""
import copy
import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
from release_support import ROOT, read_json, check_qualification


class Qualification(unittest.TestCase):
    def setUp(self):
        self.record = read_json(ROOT / 'release/compatibility.json')
        self.policy = read_json(ROOT / 'release/qualification.json')

    def test_current_policy(self):
        check_qualification(self.record)

    def test_missing_unknown_duplicate_gate(self):
        original = self.record['required_release_gates']
        for gates in [original[1:], original + ['unknown'], original + [original[0]]]:
            record = copy.deepcopy(self.record)
            record['required_release_gates'] = gates
            with self.subTest(gates=gates), self.assertRaises(ValueError):
                check_qualification(record)

    def test_invalid_reference_and_empty_acceptance(self):
        for change in [{'checks': ['missing-qualification-check']},
                       {'checks': ['/etc/passwd']}, {'acceptance': ''}]:
            policy = copy.deepcopy(self.policy)
            policy['qualification_gates']['abi'].update(change)
            with patch('release_support.read_json', return_value=policy), self.assertRaises(ValueError):
                check_qualification(self.record)

    def test_numerical_policy(self):
        for key, value in [('native_binding_absolute_tolerance', 0.001),
                           ('native_upstream_absolute_tolerance', float('nan')),
                           ('native_upstream_absolute_tolerance', True),
                           ('browser_absolute_tolerance_source', 'native_wasm_absolute_tolerance')]:
            policy = copy.deepcopy(self.policy)
            policy['budgets'][key] = value
            with patch('release_support.read_json', return_value=policy), self.assertRaises(ValueError):
                check_qualification(self.record)
        policy = copy.deepcopy(self.policy)
        policy['numerical_contract']['policy'] = 'interchangeable'
        with patch('release_support.read_json', return_value=policy), self.assertRaises(ValueError):
            check_qualification(self.record)

    def test_invalid_budgets(self):
        for value in [0, -1, True, 1.5]:
            policy = copy.deepcopy(self.policy)
            policy['budgets']['candidate_soak_seconds'] = value
            with patch('release_support.read_json', return_value=policy), self.assertRaises(ValueError):
                check_qualification(self.record)


if __name__ == '__main__':
    unittest.main()
