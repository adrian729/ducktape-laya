#!/usr/bin/env python3
"""Compare the adapter to unchanged upstream processing on the SAME backend."""
import argparse
import json
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from compare_results import compare_results
p = argparse.ArgumentParser()
p.add_argument('adapter', type=pathlib.Path)
p.add_argument('reference', type=pathlib.Path)
p.add_argument('--tolerance', type=float, default=0.00001)
a = p.parse_args()
adapter, reference = [json.loads(path.read_text()) for path in (a.adapter, a.reference)]
assert adapter['status'] == reference['status'] == 'passed'
assert adapter['backend'] == reference['backend'], 'Backend mismatch'
results = adapter['results']
results = {'primitive': results['primitive.json'], 'structured': results['structured.json'], **{f'batch{i}': v for i, v in enumerate(results['batch.json'])}}
assert set(results) == set(reference['results'])
maximum = (0, '')
for key, value in results.items():
    difference = compare_results(value['answers'], reference['results'][key]['answers'], a.tolerance, key + '.answers')
    if difference[0] > maximum[0]: maximum = difference
    compare_results(value['usage']['input_tokens'], reference['results'][key]['input_tokens'])
print(json.dumps({'backend': adapter['backend'], 'maximum_absolute_difference': maximum[0], 'path': maximum[1], 'tolerance': a.tolerance, 'passed': True}))
