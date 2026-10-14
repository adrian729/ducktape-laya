#!/usr/bin/env python3
"""Compare the adapter to unchanged upstream processing on the SAME backend."""
import argparse
import json
import pathlib
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
maximum = (0, '')
def compare(x, y, path):
    global maximum
    if isinstance(x, dict):
        assert list(x) == list(y), ('Key/order difference', path)
        for key in x: compare(x[key], y[key], path + '.' + key)
    elif isinstance(x, (int, float)) and not isinstance(x, bool):
        delta = abs(x-y)
        if delta > maximum[0]: maximum = (delta, path)
    else: assert x == y, (path, x, y)
assert set(results) == set(reference['results'])
for key, value in results.items():
    compare(value['answers'], reference['results'][key]['answers'], key + '.answers')
    assert value['usage']['input_tokens'] == reference['results'][key]['input_tokens'], key
print(json.dumps({'backend': adapter['backend'], 'maximum_absolute_difference': maximum[0], 'path': maximum[1], 'tolerance': a.tolerance, 'passed': maximum[0] <= a.tolerance}))
assert maximum[0] <= a.tolerance, 'Same-backend upstream reference gate failed'
