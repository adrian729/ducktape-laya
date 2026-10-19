#!/usr/bin/env python3
"""Compare the adapter to unchanged upstream processing on the SAME backend."""
import argparse
import json
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from compare_results import compare_results
from reference_requests import comparable_answers, reference_key
p = argparse.ArgumentParser()
p.add_argument('adapter', type=pathlib.Path)
p.add_argument('reference', type=pathlib.Path)
p.add_argument('--tolerance', type=float, default=json.loads((pathlib.Path(__file__).resolve().parents[1] / 'release/browser-toolchain.json').read_text())['upstream_absolute_tolerance'])
a = p.parse_args()
adapter, reference = [json.loads(path.read_text()) for path in (a.adapter, a.reference)]
if not adapter['status'] == reference['status'] == 'passed':
    raise AssertionError('Execution or reference failed')
if adapter['backend'] != reference['backend']:
    raise AssertionError('Backend mismatch')
results = {}
for name, value in adapter['results'].items():
    stem = name.removesuffix('.json')
    items = enumerate(value) if isinstance(value, list) else [(None, value)]
    results.update({reference_key(stem, index): item for index, item in items})
if set(results) != set(reference['results']):
    raise AssertionError('Reference fixture set changed')
maximum = (0, '')
for key, value in results.items():
    ours, theirs = comparable_answers(value['answers'], reference['results'][key]['answers'], key + '.answers')
    difference = compare_results(ours, theirs, a.tolerance, key + '.answers')
    if difference[0] > maximum[0]: maximum = difference
    compare_results(value['usage']['input_tokens'], reference['results'][key]['input_tokens'])
print(json.dumps({'backend': adapter['backend'], 'maximum_absolute_difference': maximum[0], 'path': maximum[1], 'tolerance': a.tolerance, 'passed': True}))
