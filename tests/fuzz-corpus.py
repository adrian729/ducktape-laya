#!/usr/bin/env python3
"""Create writable libFuzzer seeds outside the source tree; never modifies fixtures."""
import argparse
import json
import pathlib

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('output', type=pathlib.Path)
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=False)
root = pathlib.Path(__file__).resolve().parents[1]
for fixture in (root / 'tests/fixtures').glob('*.json'):
    value = json.loads(fixture.read_text())
    for request in value if isinstance(value, list) else [value]:
        request['model'] = 'fuzz'
    (a.output / fixture.name).write_text(json.dumps(value))
seeds = {'config': '{"models":{"fuzz":"/proc/self/exe"},"max_loaded":1}',
         'null': 'null', 'deep': '[' * 66 + '0' + ']' * 66,
         'oversized': ' ' * 1048577,
         'nul': '{"state":"x\\u0000y","questions":{"q":{"type":"noul"}}}'}
for name, value in seeds.items():
    (a.output / name).write_text(value)
print('Seeded ' + str(a.output))
