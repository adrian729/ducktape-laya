#!/usr/bin/env python3
"""Build immutable baseline/candidate sources and report compatibility. Never promote."""
import argparse
from collections import OrderedDict
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import time
from release_support import ROOT, check_record, git, read_json, sha256, verify_model
from compare_results import compare_results

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--runtime', required=True, help='Candidate commit already available in the runtime Git object database')
p.add_argument('--model', required=True, type=pathlib.Path)
p.add_argument('--candidate-model', type=pathlib.Path)
p.add_argument('--candidate-manifest', type=pathlib.Path)
p.add_argument('--output', required=True, type=pathlib.Path, help='New directory; existing reports are never overwritten')
p.add_argument('--jobs', type=int, default=2)
p.add_argument('--node-include', type=pathlib.Path)
a = p.parse_args()
if not 1 <= a.jobs <= 16:
    p.error('--jobs must be 1..16')
if bool(a.candidate_model) != bool(a.candidate_manifest):
    p.error('--candidate-model and --candidate-manifest must be supplied together')
record = check_record()
if git(ROOT, 'diff', 'HEAD', '--name-only'):
    p.error('Commit tracked source changes before testing an immutable candidate')
runtime_repo = ROOT / record['runtime']['path']
candidate = git(runtime_repo, 'rev-parse', '--verify', a.runtime + '^{commit}')
model = a.model.resolve()
manifest = read_json(ROOT / record['models'][0])
verify_model(model, manifest)
new_model = a.candidate_model.resolve() if a.candidate_model else model
new_manifest = read_json(a.candidate_manifest) if a.candidate_manifest else manifest
verify_model(new_model, new_manifest)
output = a.output.resolve()
output.mkdir(parents=True, exist_ok=False)
node_headers = a.node_include or pathlib.Path(shutil.which('node')).resolve().parents[1] / 'include/node'
if not (node_headers / 'node_api.h').is_file():
    p.error('Supply --node-include pointing to Node-API headers')
source_revision = git(ROOT, 'rev-parse', 'HEAD')
report = {
    'schema_version': 1, 'source_commit': source_revision, 'source_tracked_dirty': False,
    'package_version': record['package_version'], 'model': manifest, 'candidate_model': new_manifest,
    'baseline_runtime': record['runtime']['commit'], 'candidate_runtime': candidate,
    'absolute_tolerance': record['candidate_absolute_tolerance'], 'release_ready': False,
    'gates': {gate: {'status': 'not_run'} for gate in record['required_release_gates']},
    'runs': {}, 'toolchain': {}, 'notes': [
        'No pins, expected results or package versions are promoted by this tool.',
        'Passed adapter checks are not release approval; all required gates must be satisfied separately.',
        'Command elapsed times are diagnostics, not controlled performance benchmarks.'
    ]
}
for name, command in {'cmake': ['cmake', '--version'], 'cxx': ['c++', '--version'], 'node': ['node', '--version'], 'cargo': ['cargo', '--version']}.items():
    report['toolchain'][name] = subprocess.check_output(command, text=True).splitlines()[0]


def save():
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')


def archive(repo, revision, destination):
    destination.mkdir()
    tarpath = output / (destination.name + '.tar')
    with tarpath.open('wb') as stream:
        subprocess.run(['git', '-C', str(repo), 'archive', revision], stdout=stream, check=True)
    with tarfile.open(tarpath) as stream:
        stream.extractall(destination, filter='data')
    tarpath.unlink()


def run(label, command, cwd, env=None):
    log = output / (label + '.log')
    started = time.monotonic()
    with log.open('w') as stream:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=1800)
    report['runs'][label] = {'command': list(map(str, command)), 'exit_code': result.returncode,
                             'seconds': round(time.monotonic() - started, 3), 'log': log.name}
    save()
    print(f'{label}: {"passed" if result.returncode == 0 else "FAILED"}', flush=True)
    if result.returncode:
        gate = 'native_contracts' if label.endswith('-native') else 'binding_conformance' if label.endswith(('-rust', '-node', '-bindings')) else 'build'
        report['gates'][gate] = {'status': 'failed', 'run': label}
        save()
        raise RuntimeError(f'{label} failed; inspect {log}')



try:
    core = output / 'source'
    archive(ROOT, source_revision, core)
    report['fixtures_sha256'] = {x.name: sha256(x) for x in sorted((core / 'tests/fixtures').glob('*.json'))}
    diff_paths = ['include/llama.h', 'common/jinja', 'common/json.cpp', 'tools/server/server-decision.cpp', 'src/models/modern-bert.cpp', 'convert_hf_to_gguf.py']
    (output / 'upstream-relevant.diff').write_text(git(runtime_repo, 'diff', record['runtime']['commit'], candidate, '--', *diff_paths) + '\n')
    report['upstream_diff'] = 'upstream-relevant.diff'
    outcomes = {}
    symbols = {}
    pairs = [('baseline', record['runtime']['commit'], model), ('candidate', candidate, new_model)]
    # When both change, retain each independent axis rather than hiding interaction effects.
    if candidate != record['runtime']['commit'] and new_manifest['sha256'] != manifest['sha256']:
        pairs += [('runtime_only', candidate, model), ('model_only', record['runtime']['commit'], new_model)]
    for label, revision, weights in pairs:
        upstream = output / (label + '-runtime')
        archive(runtime_repo, revision, upstream)
        build = output / (label + '-build')
        run(label + '-configure', ['cmake', '-S', str(core), '-B', str(build), '-DCMAKE_BUILD_TYPE=Release',
            f'-DDUCKTAPE_LLAMA_SOURCE={upstream}', f'-DDUCKTAPE_LLAMA_REVISION={revision}', '-DDUCKTAPE_BUILD_EXAMPLES=OFF', '-DDUCKTAPE_BUILD_TESTS=ON',
            '-DDUCKTAPE_BUILD_NODE=ON', f'-DNODE_INCLUDE_DIR={node_headers}', f'-DDUCKTAPE_TEST_MODEL={weights}'], core)
        run(label + '-build', ['cmake', '--build', str(build), '--parallel', str(a.jobs)], core)
        env = dict(os.environ, LAYA_MODEL=str(weights), LD_LIBRARY_PATH=str(build),
                   DUCKTAPE_LAYA_LIB_DIR=str(build), DUCKTAPE_LAYA_ADDON=str(build / 'ducktape_laya.node'),
                   CARGO_TARGET_DIR=str(build / 'rust-tests'))
        run(label + '-native', ['ctest', '--test-dir', str(build), '--output-on-failure'], core, env)
        run(label + '-rust', ['cargo', 'test', '--locked', '--offline', '--manifest-path', str(core / 'bindings/rust/Cargo.toml')], core, env)
        run(label + '-node', ['node', '--test', str(core / 'bindings/node/test.cjs')], core, env)
        parity = output / (label + '-outputs.json')
        run(label + '-bindings', [sys.executable, str(core / 'tests/parity.py'), str(weights), '--build-dir', str(build), '--report', str(parity)], core, env)
        outcomes[label] = json.loads(parity.read_text(), object_pairs_hook=OrderedDict)
        exported = subprocess.check_output(['nm', '-D', '--defined-only', str(build / 'libducktape_laya.so')], text=True)
        symbols[label] = sorted(re.findall(r'\b(dtl_\w+)(?:@@\S+)?$', exported, re.M))
        report['runs'][label + '-build']['cmake_cache_sha256'] = sha256(build / 'CMakeCache.txt')
        report['runs'][label + '-build']['library_sha256'] = sha256(build / 'libducktape_laya.so')
        report['runs'][label + '-bindings']['outputs_sha256'] = sha256(parity)
        selected = ['CMAKE_BUILD_TYPE', 'CMAKE_CXX_COMPILER', 'GGML_NATIVE', 'GGML_AVX2', 'GGML_FMA', 'GGML_F16C', 'GGML_BMI2', 'GGML_SSE42', 'GGML_CPU', 'GGML_WEBGPU', 'GGML_CUDA', 'GGML_METAL', 'GGML_OPENMP']
        settings = {}
        for line in (build / 'CMakeCache.txt').read_text().splitlines():
            match = re.match(r'([^:#]+):[^=]+=(.*)', line)
            if match and (match[1] in selected or match[1].startswith('CMAKE_CXX_FLAGS')):
                settings[match[1]] = match[2]
        report['runs'][label + '-build']['settings'] = settings
    expected = sorted(set(re.findall(r'\b(dtl_\w+)\s*;', (core / 'src/exports.map').read_text())))
    if any(value != expected for value in symbols.values()):
        raise ValueError('Public export mismatch')
    for gate in ['native_contracts', 'binding_conformance', 'abi']:
        report['gates'][gate] = {'status': 'passed'}
    report['symbols'] = symbols
    try:
        # Runtime, library and model identities are expected to differ and are recorded per run;
        # the inputs and settings must match and the results are compared.
        for key in ('fixture_hashes', 'settings'):
            if outcomes['baseline'][key] != outcomes['candidate'][key]:
                raise ValueError(f'Value/type changed: $.{key}')
        delta, path = compare_results(outcomes['baseline']['fixtures'], outcomes['candidate']['fixtures'],
                                      record['candidate_absolute_tolerance'], '$.fixtures')
        report['comparison'] = {'maximum_absolute_delta': delta, 'path': path, 'status': 'passed'}
    except (AssertionError, ValueError) as error:
        report['comparison'] = {'status': 'failed', 'detail': str(error)}
    if new_manifest['sha256'] != manifest['sha256']:
        report['comparison']['interpretation'] = 'Model changed; output drift requires reference evaluation, not blind baseline equality.'
    report['adapter_checks_passed'] = report['comparison']['status'] == 'passed'
except Exception as error:
    report['failure'] = str(error)
    report['adapter_checks_passed'] = False
    print(error, file=sys.stderr)
finally:
    save()
print(f'Report: {output / "report.json"}; release_ready=false', flush=True)
sys.exit(0 if report['adapter_checks_passed'] else 1)
