#!/usr/bin/env python3
"""Run identity-recorded real-model sanitizer suites; never qualifies a release alone.

Configures dedicated Clang builds (ASan+UBSan and TSan, no suppressions or
recovery), runs every registered native test with the supplied model and a short
stress run under each, and records source/build/model identities and logs.
Instrumented libraries share the candidate source but are never packaged.
"""
import argparse
import json
import os
import pathlib
import platform
import subprocess
import time
from release_support import ROOT, check_record, git, read_json, sha256, source_identity, native_build_identity, verify_model

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('model', type=pathlib.Path)
p.add_argument('--output', type=pathlib.Path, required=True)
p.add_argument('--jobs', type=int, default=2)
p.add_argument('--stress-rounds', type=int, default=10)
p.add_argument('--only', choices=['address', 'thread'])
a = p.parse_args()
if a.jobs < 1 or not 1 <= a.stress_rounds <= 1000: p.error('jobs positive; stress rounds 1..1000')
record = check_record()
manifest = read_json(ROOT / record['models'][0])
model, out = a.model.resolve(), a.output.resolve()
verify_model(model, manifest)
out.mkdir(parents=True, exist_ok=False)
SANITIZERS = {
    'address': {'flags': '-fsanitize=address,undefined -fno-omit-frame-pointer -fno-sanitize-recover=all',
                'env': {'ASAN_OPTIONS': 'detect_leaks=1:strict_string_checks=1:halt_on_error=1',
                        'UBSAN_OPTIONS': 'halt_on_error=1:print_stacktrace=1'}},
    'thread': {'flags': '-fsanitize=thread -fno-omit-frame-pointer',
               'env': {'TSAN_OPTIONS': 'halt_on_error=1:second_deadlock_stack=1'}},
}
selected = [a.only] if a.only else list(SANITIZERS)
report = {'schema_version': 1, 'status': 'running', 'release_ready': False,
          'source_commit': git(ROOT, 'rev-parse', 'HEAD'),
          'source_tracked_dirty': bool(git(ROOT, 'diff', 'HEAD', '--name-only')),
          'runtime': record['runtime'], 'platform': platform.platform(), 'model_sha256': manifest['sha256'],
          'scope': 'native library, llama.cpp/ggml and native real-model test executables; not Node or Rust threads',
          'sanitizers': {name: {'status': 'not_run'} for name in SANITIZERS}}
def save():
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
def run(label, command, env=None, cwd=None, timeout=7200):
    with (out / (label + '.log')).open('w') as log:
        result = subprocess.run(command, env=env, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    if result.returncode: raise RuntimeError(f'{label} failed with {result.returncode}; see {label}.log')
save()
try:
    for name in selected:
        entry = report['sanitizers'][name] = {'status': 'running', **SANITIZERS[name]}
        save()
        build = ROOT / f'build-sanitizer-{name}'
        env = dict(os.environ, CC='clang', CXX='clang++', **SANITIZERS[name]['env'])
        run(f'{name}-configure', ['cmake', '-S', ROOT, '-B', build, '-DCMAKE_BUILD_TYPE=RelWithDebInfo',
            f'-DDUCKTAPE_LLAMA_SOURCE={ROOT / record["runtime"]["path"]}', '-DDUCKTAPE_LLAMA_REVISION=',
            '-DDUCKTAPE_BUILD_TESTS=ON', '-DDUCKTAPE_BUILD_NODE=OFF', '-DDUCKTAPE_BUILD_FUZZERS=OFF',
            f'-DDUCKTAPE_TEST_MODEL={model}',
            f'-DCMAKE_C_FLAGS={SANITIZERS[name]["flags"]}', f'-DCMAKE_CXX_FLAGS={SANITIZERS[name]["flags"]}'], env)
        run(f'{name}-build', ['cmake', '--build', build, '--parallel', str(a.jobs)], env)
        run(f'{name}-build-stress', ['cmake', '--build', build, '--target', 'laya-stress', '--parallel', str(a.jobs)], env)
        entry['build_identity'] = native_build_identity(build, 'ducktape_laya', 'libducktape_laya.so', source_identity())
        entry['cache_sha256'] = sha256(build / 'CMakeCache.txt')
        tests = json.loads(subprocess.check_output(['ctest', '--test-dir', build, '--show-only=json-v1'], env=env))
        entry['tests'] = sorted(test['name'] for test in tests['tests'])
        if not {'real-model', 'real-features', 'real-control', 'real-faults'} <= set(entry['tests']):
            raise RuntimeError(f'{name}: real-model tests are not registered')
        started = time.monotonic()
        run(f'{name}-ctest', ['ctest', '--test-dir', build, '--output-on-failure', '--timeout', '3600'], env)
        stress_dir = out / f'{name}-stress'
        stress_dir.mkdir()
        run(f'{name}-stress', [build / 'laya-stress', model, str(a.stress_rounds)],
            dict(env, LD_LIBRARY_PATH=str(build)), cwd=stress_dir)
        entry['elapsed_seconds'] = time.monotonic() - started
        if sha256(build / 'libducktape_laya.so') != entry['build_identity']['binary_sha256']:
            raise RuntimeError(f'{name}: library changed during the run')
        entry['status'] = 'passed'
        save()
    report['status'] = 'passed'
except Exception as error:
    report['status'] = 'failed'
    report['error'] = str(error)
    for entry in report['sanitizers'].values():
        if entry['status'] == 'running': entry['status'] = 'failed'
finally:
    save()
print(json.dumps({'status': report['status'], 'report': str(out / 'report.json')}))
raise SystemExit(0 if report['status'] == 'passed' else 1)
