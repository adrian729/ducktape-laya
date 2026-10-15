#!/usr/bin/env python3
"""Run local native validation and retain explicit passed/failed/not_run gates.

Without --model this is a model-free smoke check, never release approval.
No model downloads, expected-output changes, publication or pin promotion.
"""
import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
from release_support import ROOT, check_record, git, read_json, sha256, verify_model, source_files, binary_info, require_runtime_identity

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--model', type=pathlib.Path)
p.add_argument('--build', type=pathlib.Path, default=ROOT / 'build-validation')
p.add_argument('--output', type=pathlib.Path, required=True)
p.add_argument('--jobs', type=int, default=2)
p.add_argument('--node-include', type=pathlib.Path)
p.add_argument('--packages', action='store_true', help='Build and exercise installed artifacts; requires --model')
p.add_argument('--upstream-reference', action='store_true', help='Build an isolated test-only upstream processor and compare every real-model fixture request')
p.add_argument('--reproducibility', action='store_true', help='Also compile two independent source snapshots')
p.add_argument('--require-release', action='store_true', help='Fail if any required release gate is failed or not run')
a = p.parse_args()
if a.jobs < 1: p.error('--jobs must be positive')
if a.packages and not a.model: p.error('--packages requires --model')
if a.upstream_reference and not a.model: p.error('--upstream-reference requires --model')
record = check_record()
model = a.model.resolve() if a.model else None
manifest = read_json(ROOT / record['models'][0])
if model: verify_model(model, manifest)
out, build = a.output.resolve(), a.build.resolve()
out.mkdir(parents=True, exist_ok=False)
headers = a.node_include or pathlib.Path(shutil.which('node')).resolve().parents[1] / 'include/node'
if not (headers / 'node_api.h').is_file(): p.error('Supply --node-include pointing to node_api.h')
env = dict(os.environ, LD_LIBRARY_PATH=str(build), DUCKTAPE_LAYA_LIB_DIR=str(build),
           DUCKTAPE_LAYA_ADDON=str(build / 'ducktape_laya.node'))
env.pop('LAYA_MODEL', None)
if model: env['LAYA_MODEL'] = str(model)
report = {'schema_version': 1, 'source_commit': git(ROOT, 'rev-parse', 'HEAD'),
          'source_tracked_dirty': bool(git(ROOT, 'diff', 'HEAD', '--name-only')),
          'runtime': record['runtime'], 'model': manifest if model else None,
          'mode': 'real_model' if model else 'model_free', 'release_ready': False,
          'source_files_sha256': source_files(),
          'gates': {key: {'status': 'not_run'} for key in record['required_release_gates']}, 'runs': {},
          'fixtures_sha256': {x.name: sha256(x) for x in sorted((ROOT / 'tests/fixtures').glob('*.json'))},
          'notes': ['Passing these selected checks does not substitute for unrun release gates.',
                    'Historical evidence is not automatically accepted for a different source or binary.']}
def save():
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
def run(label, command, gate=None):
    started = time.monotonic()
    command = list(map(str, command))
    with (out / (label + '.log')).open('w') as log:
        try:
            result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=3600)
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
    report['runs'][label] = {'command': command, 'exit_code': code, 'seconds': round(time.monotonic()-started, 3),
                             'log': label + '.log'}
    if gate: report['gates'][gate] = {'status': 'failed' if code else 'passed', 'run': label}
    elif code and label in {'rust', 'node'}:
        report['gates']['binding_conformance'] = {'status': 'failed', 'run': label}
    save()
    print(f'{label}: {"FAILED" if code else "passed"}', flush=True)
    if code: raise RuntimeError(f'{label} failed; see {out / (label + ".log")}')
save()
try:
    run('result-comparison', [sys.executable, ROOT / 'tests/result-comparison.py'])
    run('qualification-policy', [sys.executable, ROOT / 'tests/qualification.py'])
    run('abi-layouts', [sys.executable, ROOT / 'tests/abi-layout.py'])
    run('typescript-consumers', [sys.executable, ROOT / 'tests/typescript/check.py'])
    run('packaging-identities', [sys.executable, ROOT / 'tests/packaging-identity.py'])
    run('browser-backend-identities', ['node', '--experimental-vm-modules', '--test', ROOT / 'tests/browser-backend.mjs'])
    run('browser-lifecycle', ['node', '--experimental-vm-modules', '--test', ROOT / 'tests/browser-lifecycle.mjs'])
    run('configure', ['cmake', '-S', ROOT, '-B', build, '-DCMAKE_BUILD_TYPE=Release',
        # Validation is for the approved checkout, even if this cache previously
        # built a candidate runtime. Candidate evaluation has its own runner.
        f'-DDUCKTAPE_LLAMA_SOURCE={ROOT / record["runtime"]["path"]}', '-DDUCKTAPE_LLAMA_REVISION=',
        '-DDUCKTAPE_BUILD_FUZZERS=OFF', '-DDUCKTAPE_BUILD_TESTS=ON', '-DDUCKTAPE_BUILD_EXAMPLES=OFF', '-DDUCKTAPE_BUILD_NODE=ON',
        '-DDUCKTAPE_BUILD_REFERENCE_PROBE=' + ('ON' if a.upstream_reference else 'OFF'),
        f'-DNODE_INCLUDE_DIR={headers}', f'-DDUCKTAPE_TEST_MODEL={model or ""}'])
    run('build', ['cmake', '--build', build, '--parallel', a.jobs])
    report['library_sha256'] = sha256(build / 'libducktape_laya.so')
    report['cmake_cache_sha256'] = sha256(build / 'CMakeCache.txt')
    report['diagnostics'] = binary_info(build)
    require_runtime_identity(report['diagnostics'], record)
    cache = (build / 'CMakeCache.txt').read_text().splitlines()
    report['build_options'] = {line.split(':', 1)[0]: line.split('=', 1)[1]
        for line in cache if '=' in line and ':' in line and
        line.startswith(('CMAKE_C_COMPILER:', 'CMAKE_CXX_COMPILER:', 'CMAKE_C_FLAGS', 'CMAKE_CXX_FLAGS', 'CMAKE_BUILD_TYPE:', 'GGML_'))}
    # Assert model-dependent tests were registered; an empty green suite is not evidence.
    registered = json.loads(subprocess.check_output(['ctest', '--test-dir', build, '--show-only=json-v1'], env=env))
    required = {'abi-errors', 'feature-errors', 'control-errors', 'decision-rules'}
    if model: required |= {'real-model', 'real-features', 'real-control', 'real-faults'}
    if not required <= {test['name'] for test in registered['tests']}:
        raise RuntimeError('Required native tests are missing')
    run('native', ['ctest', '--test-dir', build, '--output-on-failure'], 'native_contracts' if model else None)
    run('rust', ['cargo', 'test', '--locked', '--offline', '--manifest-path', ROOT / 'bindings/rust/Cargo.toml'])
    run('node', ['node', '--test', ROOT / 'bindings/node/test.cjs'])
    run('abi', [sys.executable, ROOT / 'scripts/check-abi.py', build / 'libducktape_laya.so'], 'abi')
    if model:
        run('bindings', [sys.executable, ROOT / 'tests/parity.py', model, '--build-dir', build,
                         '--report', out / 'outputs.json'], 'binding_conformance')
    if a.upstream_reference:
        run('upstream-reference', [sys.executable, ROOT / 'tests/compare-native-reference.py', model,
            '--build', build, '--report', out / 'upstream-reference.json'], 'upstream_reference')
    if a.packages:
        run('package', [sys.executable, ROOT / 'scripts/package-linux.py', '--build', build, '--output', out / 'packages'])
        run('installed', [sys.executable, ROOT / 'tests/installed-packages.py', out / 'packages', model], 'installed_packages')
    if a.reproducibility:
        run('reproducibility', [sys.executable, ROOT / 'scripts/check-reproducibility.py', '--output', out / 'reproducibility',
                               '--jobs', a.jobs, '--node-include', headers], 'reproducibility')
    report['selected_checks'] = 'passed'
except Exception as error:
    report['selected_checks'] = 'failed'
    report['error'] = str(error)
finally:
    report['release_ready'] = all(gate['status'] == 'passed' for gate in report['gates'].values())
    save()
print(json.dumps({'selected_checks': report['selected_checks'], 'release_ready': report['release_ready'], 'report': str(out / 'report.json')}))
raise SystemExit(report['selected_checks'] != 'passed' or (a.require_release and not report['release_ready']))
