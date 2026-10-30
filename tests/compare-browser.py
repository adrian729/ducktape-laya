#!/usr/bin/env python3
"""Measure cross-profile drift against an explicitly supplied native parity run."""
import argparse
import json
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from compare_results import compare_results, decision_paths, js_object_order, measure_profile_drift
from release_support import sha256
p=argparse.ArgumentParser()
p.add_argument('report',type=pathlib.Path)
p.add_argument('--native', type=pathlib.Path, required=True,
               help='Native report produced by tests/parity.py --report with the same model and fixtures')
a=p.parse_args()
report=json.loads(a.report.read_text())
native=json.loads(a.native.read_text())
# Browser results are parsed JavaScript objects; compare in their property order.
reference={name: js_object_order(outputs['C']) for name, outputs in native['fixtures'].items()}
def require(condition, message):
    if not condition:
        raise AssertionError(message)

require(report['status'] == 'passed', 'Browser execution failed')
require(report['model_sha256'] == native['model_sha256'], 'Model identity mismatch')
require(report['fixture_hashes'] == native['fixture_hashes'], 'Fixture identity mismatch')
for identity in ['llama_cpp_commit', 'runtime_patches']:
    compare_results(native['runtime'][identity], report['info'][identity])
for runtime in [native['runtime'], report['info']]:
    require(runtime['numerical_profile']['cross_profile_equivalence'] is False, 'Unexpected profile policy')
require(report['ticks'] > 0, 'No main-thread heartbeat during worker execution')
require([event['kind'] for event in report['events']] == ['start', 'end'] * len(reference),
        'Unexpected lifecycle events')
require(set(reference) == set(report['results']), 'Fixture set changed')
# Fixture enumeration is not result-object ordering. Permit changed predictions
# only at schema-defined decision fields; metadata and token usage remain exact.
ordered = {name: report['results'][name] for name in reference}
drift = measure_profile_drift(reference, ordered, decision_paths(reference))
print(json.dumps({'comparison': 'cross_profile_drift', 'probability_equivalence': False,
                  'browser_backend': report['backend'],
                  'browser_runtime': report.get('info'),
                  'native_runtime': native['runtime'],
                  'native_library_sha256': native['library_sha256'],
                  'model_sha256': native['model_sha256'], 'fixture_hashes': native['fixture_hashes'],
                  'native_report_sha256': sha256(a.native), 'browser_report_sha256': sha256(a.report),
                  **drift, 'status': 'passed'}, allow_nan=False))
