#!/usr/bin/env python3
"""Qualification inventory must fail closed when requirements drift."""
import copy
import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
from release_support import ROOT, read_json, check_qualification, check_targets


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
                           ('native_binding_absolute_tolerance', False),
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

    def test_invalid_short_stress_budget(self):
        for value in [0, True, 3600, 7200, None]:
            policy = copy.deepcopy(self.policy)
            policy['budgets']['short_stress_seconds'] = value
            with self.subTest(value=value), patch('release_support.read_json', return_value=policy), self.assertRaises(ValueError):
                check_qualification(self.record)

    def test_incomplete_target_gates(self):
        for change in [lambda p: p.pop('target_gates'), lambda p: p['target_gates'].clear(),
                       lambda p: p['target_gates']['short_stress'].update(acceptance=''),
                       lambda p: p['target_gates']['short_stress'].update(checks=['missing-check'])]:
            policy = copy.deepcopy(self.policy)
            change(policy)
            with patch('release_support.read_json', return_value=policy), self.assertRaises(ValueError):
                check_qualification(self.record)


class Targets(unittest.TestCase):
    def setUp(self):
        self.record = read_json(ROOT / 'release/compatibility.json')

    def target(self, record, platform):
        return next(item for item in record['targets'] if item['platform'] == platform)

    def test_current_targets(self):
        self.assertEqual([t['platform'] for t in check_targets(self.record)], ['linux-x64', 'linux-arm64', 'windows-x64'])

    def test_invalid_targets(self):
        cases = {
            'no targets': lambda r: r.update(targets=[]),
            'no tier 1': lambda r: self.target(r, 'linux-x64').update(tier=2),
            'two tier 1': lambda r: self.target(r, 'linux-arm64').update(tier=1, required_gates=r['required_release_gates']),
            'tier 1 not linux-x64': lambda r: (self.target(r, 'linux-x64').update(tier=2),
                                               self.target(r, 'windows-x64').update(tier=1)),
            'bad tier': lambda r: self.target(r, 'linux-arm64').update(tier=3),
            'tier as bool': lambda r: self.target(r, 'linux-arm64').update(tier=True),
            'tier-1 gates differ': lambda r: self.target(r, 'linux-x64')['required_gates'].pop(),
            'tier-1 duplicate gate': lambda r: self.target(r, 'linux-x64')['required_gates'].append('abi'),
            'unknown tier-2 gate': lambda r: self.target(r, 'windows-x64')['required_gates'].append('unknown'),
            'empty tier-2 gates': lambda r: self.target(r, 'windows-x64').update(required_gates=[]),
            'duplicate platform': lambda r: r['targets'].append(copy.deepcopy(self.target(r, 'linux-arm64'))),
            'unknown platform': lambda r: self.target(r, 'linux-arm64').update(platform='freebsd-x64'),
            'not a release platform': lambda r: self.target(r, 'linux-arm64').update(platform='macos-arm64'),
            'tier-2 models without primary': lambda r: self.target(r, 'windows-x64').update(models=['models/laya-multilingual.json']),
            'unpinned model': lambda r: self.target(r, 'windows-x64')['models'].append('models/other.json'),
            'tier 1 without every model': lambda r: self.target(r, 'linux-x64').update(models=['models/manifest.json']),
            'profile and baseline differ': lambda r: self.target(r, 'linux-arm64')['cpu_baseline'].update(required_cpu_features=['asimddp']),
            'no math library': lambda r: self.target(r, 'windows-x64')['numerical_profile'].pop('math_library'),
            'missing workflow': lambda r: self.target(r, 'windows-x64')['build'].update(built_by='.github/workflows/missing.yml'),
            'electron not boolean': lambda r: self.target(r, 'windows-x64').update(electron='yes'),
            'deviation cap too large': lambda r: self.target(r, 'windows-x64')['known_deviations'][0].update(max_probability_drift=0.5),
            'deviation cap below the limit': lambda r: self.target(r, 'windows-x64')['known_deviations'][0].update(max_probability_drift=0.05),
            'deviation cap not finite': lambda r: self.target(r, 'windows-x64')['known_deviations'][0].update(max_probability_drift=float('nan')),
            'undocumented deviation': lambda r: self.target(r, 'windows-x64')['known_deviations'][0].update(documented_in='docs/MISSING.md'),
            'deviation without question': lambda r: self.target(r, 'windows-x64')['known_deviations'][0].pop('question'),
        }
        for label, change in cases.items():
            record = copy.deepcopy(self.record)
            change(record)
            with self.subTest(label), self.assertRaises(ValueError):
                check_targets(record)

    def test_tier2_target_needs_runner_policy(self):
        policy = read_json(ROOT / 'release/qualification.json')
        del policy['platforms']['targets']['windows-x64']
        with patch('release_support.read_json', return_value=policy), self.assertRaises(ValueError):
            check_targets(self.record)


if __name__ == '__main__':
    unittest.main()
