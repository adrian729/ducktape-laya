#!/usr/bin/env python3
"""Real-model public-API conformance, independent of examples and demos.

Optional reference comparison never starts/stops a server. Reports capture outputs
for before/after adapter comparisons; they are not automatically accepted goldens.
"""
import argparse
from collections import OrderedDict
import math
import json
import os
import pathlib
import subprocess
import urllib.request
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from compare_results import compare_results, js_object_order
from release_support import binary_info, sha256, source_identity

parser = argparse.ArgumentParser()
parser.add_argument('model', type=pathlib.Path)
parser.add_argument('--build-dir', type=pathlib.Path)
parser.add_argument('--server-url')
parser.add_argument('--request', type=pathlib.Path, help='Use one fixture instead of the shared corpus')
parser.add_argument('--report', type=pathlib.Path)
parser.add_argument('--tolerance', type=float, default=0.000001)
args = parser.parse_args()
if not math.isfinite(args.tolerance) or args.tolerance < 0:
    parser.error('--tolerance must be finite and nonnegative')
root = pathlib.Path(__file__).resolve().parents[1]
build = (args.build_dir or root / 'build').resolve()
model = str(args.model.resolve())
fixtures = [args.request] if args.request else sorted((root / 'tests/fixtures').glob('*.json'))
if not fixtures:
    parser.error('No conformance fixtures found')
if args.server_url:
    fixtures = [args.request or root / 'tests/fixtures/primitive.json']
    body = json.loads(fixtures[0].read_text())
    if not isinstance(body, dict) or 'questions' not in body or 'schema' in body:
        parser.error('--server-url requires a primitive question fixture')
env = dict(os.environ)
env['LD_LIBRARY_PATH'] = str(build) + ':' + env.get('LD_LIBRARY_PATH', '')
env['DUCKTAPE_LAYA_LIB_DIR'] = str(build)
env['DUCKTAPE_LAYA_ADDON'] = str(build / 'ducktape_laya.node')
commands = {
    'C': [str(build / 'laya-conformance-c'), model],
    'C++': [str(build / 'laya-conformance-cpp'), model],
    'Rust': ['cargo', 'run', '--locked', '--offline', '--quiet', '--manifest-path',
             str(root / 'tests/consumers/rust/Cargo.toml'), '--target-dir',
             str(build / 'conformance-rust'), '--', model],
    'Node': ['node', str(root / 'tests/consumers/node.cjs'), model],
}
report = {'fixtures': {}, 'model_sha256': sha256(args.model),
          'fixture_hashes': {fixture.name: sha256(fixture) for fixture in fixtures},
          'runtime': binary_info(build), 'library_sha256': sha256(build / 'libducktape_laya.so'),
          'source_identity': source_identity(), 'settings': {'threads': 4}}
for fixture in fixtures:
    request = fixture.read_bytes()
    results = {}
    for name, command in commands.items():
        output = subprocess.run(command, input=request, capture_output=True, env=env, timeout=180)
        if output.returncode:
            raise RuntimeError(f'{fixture.name}/{name}: {output.stderr.decode(errors="replace")}')
        results[name] = json.loads(output.stdout, object_pairs_hook=OrderedDict)
        # Parsed JavaScript objects enumerate integer-like keys first; values stay exact.
        expected = js_object_order(results['C']) if name == 'Node' else results['C']
        compare_results(expected, results[name], path=f'{fixture.name}/{name}')
        print(f'{fixture.name}/{name}: exact agreement', flush=True)
    report['fixtures'][fixture.name] = results
    if args.server_url:
        req = urllib.request.Request(args.server_url.rstrip('/') + '/v1/systemone', request,
                                     {'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=120) as response:
            reference = json.load(response)
        error, path = compare_results(results['C']['answers'], reference['answers'], args.tolerance, 'answers')
        compare_results(results['C']['usage'], reference['usage'])
        print(f'Reference maximum absolute difference: {error:.9g} at {path}')
if args.report:
    args.report.write_text(json.dumps(report, indent=2) + '\n')
