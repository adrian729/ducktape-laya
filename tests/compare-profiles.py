#!/usr/bin/env python3
"""Measure fixture drift between two native numerical profiles (release targets).

Takes two tests/parity.py reports (outputs.json) made with the same model and
fixtures: the reference profile's (the tier-1 linux-x64 build) and a target's.
Compares the C consumer's results of every fixture: structure, ordering,
metadata and token usage must be identical; probabilities and other floats may
drift and are reported; a changed decision (a choice, a structured value) is
reported and fails, since no target documents one. Results are not expected to
be equal across profiles (different ISA, compiler or math library); this report
is what check-candidate.py's tier-2 cross_profile_drift gate reads.
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
from compare_results import decision_paths, measure_profile_drift  # noqa: E402
from release_support import sha256  # noqa: E402

p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
p.add_argument('reference', type=pathlib.Path, help='outputs.json of the reference (tier-1) profile')
p.add_argument('target', type=pathlib.Path, help='outputs.json of the target profile')
p.add_argument('--reference-platform', default='linux-x64')
p.add_argument('--target-platform', required=True)
p.add_argument('--report', type=pathlib.Path, required=True)
a = p.parse_args()
reference, target = (json.loads(path.read_text()) for path in (a.reference, a.target))
report = {'schema_version': 1, 'comparison': 'native_cross_profile_drift', 'probability_equivalence': False,
          'reference_platform': a.reference_platform, 'target_platform': a.target_platform,
          'reference_report_sha256': sha256(a.reference), 'target_report_sha256': sha256(a.target),
          'reference_library_sha256': reference.get('library_sha256'), 'target_library_sha256': target.get('library_sha256'),
          'reference_profile': reference.get('runtime', {}).get('numerical_profile'),
          'target_profile': target.get('runtime', {}).get('numerical_profile'),
          'model_sha256': reference.get('model_sha256'), 'fixture_hashes': reference.get('fixture_hashes'), 'status': 'failed'}
try:
    def require(condition, message):
        if not condition:
            raise AssertionError(message)
    require(reference['model_sha256'] == target['model_sha256'], 'Model identity mismatch')
    require(reference['fixture_hashes'] == target['fixture_hashes'], 'Fixture identity mismatch')
    for identity in ('llama_cpp_commit', 'runtime_patches', 'package_version', 'abi_version'):
        require(reference['runtime'][identity] == target['runtime'][identity], f'Runtime {identity} differs')
    for runtime in (reference['runtime'], target['runtime']):
        require(runtime['numerical_profile']['cross_profile_equivalence'] is False, 'Unexpected profile policy')
    require(set(reference['fixtures']) == set(target['fixtures']), 'Fixture set changed')
    expected = {name: outputs['C'] for name, outputs in reference['fixtures'].items()}
    actual = {name: target['fixtures'][name]['C'] for name in expected}
    report.update(measure_profile_drift(expected, actual, decision_paths(expected)))
    report['status'] = 'failed' if report['decision_differences'] else 'passed'
except (AssertionError, KeyError, TypeError) as error:
    report['error'] = f'{type(error).__name__}: {error}'
a.report.parent.mkdir(parents=True, exist_ok=True)
a.report.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
print(json.dumps({key: report.get(key) for key in ('status', 'maximum_absolute_difference', 'path', 'numeric_differences',
                                                     'decision_differences', 'error') if key in report}, indent=2))
raise SystemExit(report['status'] != 'passed')
