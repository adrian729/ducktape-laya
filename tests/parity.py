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
import shutil
import subprocess
import urllib.request
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from compare_results import compare_results, js_object_order
from release_support import LIBRARY, binary_info, sha256, source_identity

parser = argparse.ArgumentParser()
parser.add_argument('model', type=pathlib.Path)
parser.add_argument('--build-dir', type=pathlib.Path)
parser.add_argument('--server-url')
parser.add_argument('--request', type=pathlib.Path, help='Use one fixture instead of the shared corpus')
parser.add_argument('--report', type=pathlib.Path)
parser.add_argument('--tolerance', type=float, default=0.000001)
parser.add_argument('--call-timeout', type=float, default=900,
                    help='Seconds per consumer call; a hang guard, sized for loaded or slower hosts')
args = parser.parse_args()
if not math.isfinite(args.tolerance) or args.tolerance < 0:
    parser.error('--tolerance must be finite and nonnegative')
if not math.isfinite(args.call_timeout) or args.call_timeout <= 0:
    parser.error('--call-timeout must be finite and positive')
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
windows = sys.platform == 'win32'
exe = '.exe' if windows else ''
if windows:
    # Windows finds DLLs on PATH; the Python consumer reads and writes UTF-8.
    env['PATH'] = str(build) + os.pathsep + env.get('PATH', '')
    env['PYTHONUTF8'] = '1'
else:
    env['LD_LIBRARY_PATH'] = str(build) + ':' + env.get('LD_LIBRARY_PATH', '')
env['DUCKTAPE_LAYA_LIB_DIR'] = str(build)
env['DUCKTAPE_LAYA_ADDON'] = str(build / 'ducktape_laya.node')
env['PYTHONPATH'] = str(root / 'bindings/python/src')
env.update(DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_NOLOGO='1')
# Compiled consumers are built once, outside the per-fixture loop and its timeout.
if windows:
    # build.sh --no-test without bash (the bash found first on Windows can be WSL's).
    java = root / 'bindings/java/target'
    shutil.rmtree(java, ignore_errors=True)
    (java / 'classes').mkdir(parents=True)
    subprocess.run(['javac', '--release', '22', '-d', str(java / 'classes')] +
                   [str(path) for path in sorted((root / 'bindings/java/src/main').rglob('*.java'))],
                   check=True, env=env, capture_output=True)
    subprocess.run(['jar', '--create', '--file', str(java / 'ducktape-laya.jar'), '-C', str(java / 'classes'), '.'],
                   check=True, env=env, capture_output=True)
else:
    subprocess.run(['bash', str(root / 'bindings/java/build.sh'), '--no-test'], check=True, env=env, capture_output=True)
env.update(CGO_CFLAGS='-I' + str(root / 'include'), CGO_LDFLAGS='-L' + str(build), GOTOOLCHAIN='local')
subprocess.run(['go', 'build', '-o', str(build / ('conformance-go' + exe)), '.'], cwd=root / 'tests/consumers/go',
               check=True, env=env, capture_output=True)
subprocess.run(['dotnet', 'build', str(root / 'tests/consumers/dotnet/Consumer.csproj'), '-c', 'Release', '-m:1',
                '-o', str(build / 'conformance-dotnet')], check=True, env=env, capture_output=True)
commands = {
    'C': [str(build / ('laya-conformance-c' + exe)), model],
    'C++': [str(build / ('laya-conformance-cpp' + exe)), model],
    'Rust': ['cargo', 'run', '--locked', '--offline', '--quiet', '--manifest-path',
             str(root / 'tests/consumers/rust/Cargo.toml'), '--target-dir',
             str(build / 'conformance-rust'), '--', model],
    'Node': ['node', str(root / 'tests/consumers/node.cjs'), model],
    'Python': [sys.executable, str(root / 'tests/consumers/python/consumer.py'), model],
    'Java': ['java', '--enable-native-access=ALL-UNNAMED', '-cp', str(root / 'bindings/java/target/ducktape-laya.jar'),
             str(root / 'tests/consumers/java/Consumer.java'), model],
    '.NET': ['dotnet', str(build / 'conformance-dotnet/Consumer.dll'), model],
    'Go': [str(build / ('conformance-go' + exe)), model],
}
# Second pass: open config (context, overflow, calibration temperatures) through every binding.
OPEN_CONFIG = {'threads': 4, 'context_tokens': 256, 'overflow': 'truncate_end',
               'temperatures': {'choice': 1.3, 'noul.2': 1.2}}
OPEN_CONFIG_FIXTURE = root / 'tests/fixtures/options.json'
report = {'fixtures': {}, 'model_sha256': sha256(args.model),
          'fixture_hashes': {fixture.name: sha256(fixture) for fixture in fixtures},
          'runtime': binary_info(build), 'library_sha256': sha256(build / LIBRARY),
          'source_identity': source_identity(), 'settings': {'threads': 4}}
def run_bindings(fixture, label, config=None):
    request = fixture.read_bytes()
    results = {}
    for name, command in commands.items():
        if config is not None:
            command = command + [json.dumps(config, separators=(',', ':'))]
        output = subprocess.run(command, input=request, capture_output=True, env=env, timeout=args.call_timeout)
        if output.returncode:
            raise RuntimeError(f'{label}/{name}: {output.stderr.decode(errors="replace")}')
        results[name] = json.loads(output.stdout, object_pairs_hook=OrderedDict)
        # Parsed JavaScript objects enumerate integer-like keys first; values stay exact.
        expected = js_object_order(results['C']) if name == 'Node' else results['C']
        compare_results(expected, results[name], path=f'{label}/{name}')
        print(f'{label}/{name}: exact agreement', flush=True)
    return results
for fixture in fixtures:
    results = run_bindings(fixture, fixture.name)
    request = fixture.read_bytes()
    report['fixtures'][fixture.name] = results
    if args.server_url:
        req = urllib.request.Request(args.server_url.rstrip('/') + '/v1/systemone', request,
                                     {'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=120) as response:
            reference = json.load(response)
        error, path = compare_results(results['C']['answers'], reference['answers'], args.tolerance, 'answers')
        compare_results(results['C']['usage'], reference['usage'])
        print(f'Reference maximum absolute difference: {error:.9g} at {path}')
if not args.request and not args.server_url and OPEN_CONFIG_FIXTURE.is_file():
    report['open_config'] = {'config': OPEN_CONFIG, 'fixture': OPEN_CONFIG_FIXTURE.name,
                             'fixture_hash': sha256(OPEN_CONFIG_FIXTURE),
                             'results': run_bindings(OPEN_CONFIG_FIXTURE, OPEN_CONFIG_FIXTURE.name + '+open_config', OPEN_CONFIG)}
if args.report:
    args.report.write_text(json.dumps(report, indent=2) + '\n')
