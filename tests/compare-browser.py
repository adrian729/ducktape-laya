#!/usr/bin/env python3
"""Compare browser output against an explicitly supplied native parity run."""
import argparse
import json
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from compare_results import compare_results
p=argparse.ArgumentParser()
p.add_argument('report',type=pathlib.Path)
p.add_argument('--native', type=pathlib.Path, required=True,
               help='Native report produced by tests/parity.py --report with the same model and fixtures')
a=p.parse_args()
root=pathlib.Path(__file__).resolve().parents[1]
report=json.loads(a.report.read_text())
native=json.loads(a.native.read_text())
reference={name: outputs['C'] for name, outputs in native['fixtures'].items()}
tolerance=json.loads((root/'release/browser-toolchain.json').read_text())['native_wasm_absolute_tolerance']
assert report['status']=='passed',report
assert report['ticks']>0, 'No main-thread heartbeat during worker execution'
assert [event['event'] for event in report['events']]==['start','end']*3
assert set(reference) == set(report['results']), 'Fixture set changed'
# Fixture file enumeration is not result-object ordering.
ordered = {name: report['results'][name] for name in reference}
maximum = compare_results(reference, ordered, tolerance)
print(f'Maximum absolute difference: {maximum[0]:.12g} at {maximum[1]}; tolerance {tolerance}')
