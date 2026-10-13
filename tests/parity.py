#!/usr/bin/env python3
"""Real-model parity across bindings; optional comparison with a local reference server.

This script never starts/stops a server. Run it after building the Node addon.
"""
import argparse
import json
import os
import pathlib
import subprocess
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('model')
parser.add_argument('--server-url')
parser.add_argument('--request', type=pathlib.Path, help='Primitive, structured or batch JSON fixture')
parser.add_argument('--tolerance', type=float, default=0.000001)
args = parser.parse_args()
root = pathlib.Path(__file__).resolve().parents[1]
request = (args.request or root / 'examples/request.json').read_bytes()
if args.server_url and args.request:
    parser.error('--server-url only supports the default primitive fixture')
env = dict(os.environ)
env['LD_LIBRARY_PATH'] = str(root / 'build') + ':' + env.get('LD_LIBRARY_PATH', '')
commands = {
    'C': [str(root / 'build/laya-decide'), args.model],
    'C++': [str(root / 'build/laya-decide-cpp'), args.model],
    'Rust': ['cargo', 'run', '--offline', '--quiet', '--manifest-path',
             str(root / 'bindings/rust/Cargo.toml'), '-p', 'ducktape-laya',
             '--example', 'decide', '--', args.model],
    'Node': ['node', str(root / 'bindings/node/decide.cjs'), args.model],
}
results = {}
for name, command in commands.items():
    output = subprocess.run(command, input=request, capture_output=True, env=env, check=True, timeout=120)
    results[name] = json.loads(output.stdout)
    if results[name] != results['C']:
        raise AssertionError(f'{name} differs from C output')
    print(f'{name}: exact agreement')

if args.server_url:
    req = urllib.request.Request(args.server_url.rstrip('/') + '/v1/systemone', request,
                                 {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=120) as response:
        reference = json.load(response)
    differences = []

    def compare(a, b, path):
        if isinstance(a, dict):
            assert list(a) == list(b), f'Key/order mismatch at {path}'
            for key in a:
                compare(a[key], b[key], path + '.' + key)
        elif isinstance(a, (int, float)):
            differences.append((abs(a - b), path))
        else:
            assert a == b, f'Value mismatch at {path}: {a!r} vs {b!r}'

    compare(results['C']['answers'], reference['answers'], 'answers')
    assert results['C']['usage'] == reference['usage'], 'Token usage mismatch'
    error, path = max(differences)
    print(f'Reference maximum absolute difference: {error:.9g} at {path}')
    assert error <= args.tolerance, f'Exceeded declared tolerance {args.tolerance}'
