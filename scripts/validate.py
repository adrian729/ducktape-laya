#!/usr/bin/env python3
"""Run local native validation and retain explicit passed/failed/not_run gates.

Without --model this is a model-free smoke check, never release approval.
No model downloads, expected-output changes, publication or pin promotion.

Runs on every release target (Linux x86-64 and arm64, Windows x64). A check that
does not apply on a platform is never left out silently: the report lists it
under `skipped` with the reason. On Windows run it from a Visual Studio x64
developer environment and pass the clang-cl build through --cmake-arg.
"""
import argparse
import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import time
from release_support import (EXE, LIBRARY, ROOT, check_record, find_manifest, git, host_target, read_json, sha256, verify_model, source_files,
                             binary_info, require_runtime_identity)

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--model', type=pathlib.Path)
p.add_argument('--build', type=pathlib.Path, default=ROOT / 'build-validation')
p.add_argument('--output', type=pathlib.Path, required=True)
p.add_argument('--jobs', type=int, default=2)
p.add_argument('--node-include', type=pathlib.Path)
p.add_argument('--node-lib', type=pathlib.Path, help='node.lib of the Node distribution (Windows; scripts/fetch-node-sdk.py --lib)')
p.add_argument('--cmake-arg', action='append', default=[], help='Extra configure argument, repeatable (Windows: -GNinja, '
               '-DCMAKE_C_COMPILER=.../clang-cl.exe, -DCMAKE_CXX_COMPILER=..., -DDTL_PATCH_EXECUTABLE=...)')
p.add_argument('--packages', action='store_true', help='Build the packages and load them outside the checkout; with --model also run them')
p.add_argument('--upstream-reference', action='store_true', help='Build an isolated test-only upstream processor and compare every real-model fixture request')
p.add_argument('--reproducibility', action='store_true', help='Also compile two independent source snapshots')
p.add_argument('--require-release', action='store_true', help='Fail if any required release gate is failed or not run')
p.add_argument('--extra-model', type=pathlib.Path, action='append', default=[], help='GGUF of an additional pinned model (release/compatibility.json additional_models) to qualify against the original Laya and upstream; repeatable')
a = p.parse_args()
if a.jobs < 1: p.error('--jobs must be positive')
if a.upstream_reference and not a.model: p.error('--upstream-reference requires --model')
record = check_record()
model = a.model.resolve() if a.model else None
manifest = read_json(ROOT / record['models'][0])
if model: verify_model(model, manifest)
extra_models = [(path.resolve(), find_manifest(path, record)) for path in a.extra_model]
if any(m['id'] == manifest['id'] for _, m in extra_models): p.error('--extra-model takes additional models; pass the primary with --model')
out, build = a.output.resolve(), a.build.resolve()
out.mkdir(parents=True, exist_ok=False)
headers = a.node_include or pathlib.Path(shutil.which('node')).resolve().parents[1] / 'include/node'
if not (headers / 'node_api.h').is_file(): p.error('Supply --node-include pointing to node_api.h')
WINDOWS = sys.platform == 'win32'
if WINDOWS and not (a.node_lib and a.node_lib.is_file()): p.error('Supply --node-lib with node.lib on Windows')
try:
    PLATFORM = host_target().name
except ValueError:
    PLATFORM = None
env = dict(os.environ, LD_LIBRARY_PATH=str(build), DUCKTAPE_LAYA_LIB_DIR=str(build),
           DUCKTAPE_LAYA_ADDON=str(build / 'ducktape_laya.node'), PYTHONPATH=str(ROOT / 'bindings/python/src'),
           DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_NOLOGO='1', GOTOOLCHAIN='local',
           CGO_CFLAGS='-I' + str(ROOT / 'include'), CGO_LDFLAGS='-L' + str(build))
env.pop('LAYA_MODEL', None)
if model: env['LAYA_MODEL'] = str(model)
# Windows finds the DLL (for ctypes, Rust and Go test programs) on PATH.
if WINDOWS: env['PATH'] = str(build) + os.pathsep + env.get('PATH', '')
report = {'schema_version': 1, 'source_commit': git(ROOT, 'rev-parse', 'HEAD'),
          'source_tracked_dirty': bool(git(ROOT, 'diff', 'HEAD', '--name-only')),
          'runtime': record['runtime'], 'model': manifest if model else None,
          'additional_models': [m for _, m in extra_models], 'additional_model_gates': {},
          'mode': 'real_model' if model else 'model_free', 'release_ready': False, 'platform': PLATFORM, 'skipped': {},
          'node_version': subprocess.check_output(['node', '--version'], text=True).strip(),
          'toolchains': {name: (subprocess.run(command, capture_output=True, text=True).stdout or '').strip().splitlines()[:1]
                         if shutil.which(command[0]) else []
                         for name, command in {'rustc': ['rustc', '--version'], 'go': ['go', 'version'],
                                               'java': ['java', '--version'], 'dotnet': ['dotnet', '--version'],
                                               'python': [sys.executable, '--version']}.items()},
          'source_files_sha256': source_files(),
          'gates': {key: {'status': 'not_run'} for key in record['required_release_gates']}, 'runs': {},
          'fixtures_sha256': {x.name: sha256(x) for x in sorted((ROOT / 'tests/fixtures').glob('*.json'))},
          'notes': ['Passing these selected checks does not substitute for unrun release gates.',
                    'Historical evidence is not automatically accepted for a different source or binary.']}
def save():
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
BINDING_SUITES = {'rust', 'node', 'python', 'java-compile', 'java-test-compile', 'java', 'dotnet', 'go'}
def skip(label, reason):
    report['skipped'][label] = reason
    save()
    print(f'{label}: skipped ({reason})', flush=True)
def run(label, command, gate=None, extra_env=None):
    started = time.monotonic()
    command = list(map(str, command))
    with (out / (label + '.log')).open('w') as log:
        try:
            result = subprocess.run(command, cwd=ROOT, env=dict(env, **(extra_env or {})), stdout=log, stderr=subprocess.STDOUT,
                                    timeout=3600)
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
    report['runs'][label] = {'command': command, 'exit_code': code, 'seconds': round(time.monotonic()-started, 3),
                             'log': label + '.log'}
    if gate: report['gates'][gate] = {'status': 'failed' if code else 'passed', 'run': label}
    elif code and label in BINDING_SUITES:
        report['gates']['binding_conformance'] = {'status': 'failed', 'run': label}
    save()
    print(f'{label}: {"FAILED" if code else "passed"}', flush=True)
    if code: raise RuntimeError(f'{label} failed; see {out / (label + ".log")}')
save()
try:
    run('result-comparison', [sys.executable, ROOT / 'tests/result-comparison.py'])
    run('qualification-policy', [sys.executable, ROOT / 'tests/qualification.py'])
    # The C probes need a GNU-style driver; on Windows that is clang next to clang-cl (the
    # configure step below names clang-cl, so the layouts are checked before it with the
    # compiler --cmake-arg selects).
    layout_env = None
    if WINDOWS:
        compiler = next((arg.split('=', 1)[1] for arg in a.cmake_arg if arg.startswith('-DCMAKE_C_COMPILER=')), None)
        if not compiler: p.error('On Windows pass the clang-cl compiler with --cmake-arg=-DCMAKE_C_COMPILER=...')
        clang = pathlib.Path(compiler).with_name('clang' + EXE)
        layout_env = {'CC': str(clang), 'CXX': str(clang.with_name('clang++' + EXE))}
    run('abi-layouts', [sys.executable, ROOT / 'tests/abi-layout.py'], extra_env=layout_env)
    run('typescript-consumers', [sys.executable, ROOT / 'tests/typescript/check.py'])
    run('packaging-identities', [sys.executable, ROOT / 'tests/packaging-identity.py'])
    run('package-assembly', [sys.executable, ROOT / 'tests/package-assembly.py'])
    run('browser-backend-identities', ['node', '--experimental-vm-modules', '--test', ROOT / 'tests/browser-backend.mjs'])
    run('browser-lifecycle', ['node', '--experimental-vm-modules', '--test', ROOT / 'tests/browser-lifecycle.mjs'])
    run('browser-binding', ['node', '--experimental-vm-modules', '--test', ROOT / 'bindings/browser/test.mjs'])
    run('model-download', [sys.executable, ROOT / 'tests/download-model.py'])
    # CMake paths with forward slashes (identical on Linux; Windows paths otherwise carry escapes).
    run('configure', ['cmake', '-S', ROOT, '-B', build, '-DCMAKE_BUILD_TYPE=Release',
        # Validation is for the approved checkout, even if this cache previously
        # built a candidate runtime. Candidate evaluation has its own runner.
        f'-DDUCKTAPE_LLAMA_SOURCE={(ROOT / record["runtime"]["path"]).as_posix()}', '-DDUCKTAPE_LLAMA_REVISION=',
        '-DDUCKTAPE_BUILD_FUZZERS=OFF', '-DDUCKTAPE_BUILD_TESTS=ON', '-DDUCKTAPE_BUILD_EXAMPLES=OFF', '-DDUCKTAPE_BUILD_NODE=ON',
        '-DDUCKTAPE_BUILD_REFERENCE_PROBE=' + ('ON' if a.upstream_reference else 'OFF'),
        f'-DNODE_INCLUDE_DIR={headers.as_posix()}', f'-DDUCKTAPE_TEST_MODEL={model.as_posix() if model else ""}',
        *([f'-DNODE_LIB={a.node_lib.resolve().as_posix()}'] if a.node_lib else []), *a.cmake_arg])
    run('build', ['cmake', '--build', build, '--parallel', a.jobs])
    report['library_sha256'] = sha256(build / LIBRARY)
    report['addon_sha256'] = sha256(build / 'ducktape_laya.node')
    report['cmake_cache_sha256'] = sha256(build / 'CMakeCache.txt')
    report['diagnostics'] = binary_info(build)
    require_runtime_identity(report['diagnostics'], record)
    cache = (build / 'CMakeCache.txt').read_text().splitlines()
    report['build_options'] = {line.split(':', 1)[0]: line.split('=', 1)[1]
        for line in cache if '=' in line and ':' in line and
        line.startswith(('CMAKE_C_COMPILER:', 'CMAKE_CXX_COMPILER:', 'CMAKE_C_FLAGS', 'CMAKE_CXX_FLAGS', 'CMAKE_BUILD_TYPE:', 'GGML_'))}
    # Assert model-dependent tests were registered; an empty green suite is not evidence.
    registered = json.loads(subprocess.check_output(['ctest', '--test-dir', build, '--show-only=json-v1'], env=env))
    report['ctest_registered'] = sorted(test['name'] for test in registered['tests'])
    required = {'abi-errors', 'feature-errors', 'control-errors', 'decision-rules', 'language-routing'}
    if model: required |= {'real-model', 'real-features', 'real-control'}
    linux = sys.platform.startswith('linux')
    # Fault injection uses Linux process controls (prctl, rlimits); CMake registers it on Linux only.
    if model and linux: required |= {'real-faults', 'real-c-faults'}
    elif model:
        for name in ('real-faults', 'real-c-faults'):
            report['skipped'][name] = 'fault injection uses Linux process controls (prctl, rlimits); registered on Linux only'
    # The emulated-CPU guard test exercises the x86-64 AVX2 guard under QEMU user mode (Linux only).
    if model and linux and platform.machine() in ('x86_64', 'AMD64'): required |= {'cpu-guard'}
    elif model and platform.machine() in ('x86_64', 'AMD64'):
        report['skipped']['cpu-guard'] = 'QEMU user-mode CPU emulation runs on Linux only'
    if not required <= set(report['ctest_registered']):
        raise RuntimeError('Required native tests are missing: ' + ', '.join(sorted(required - set(report['ctest_registered']))))
    run('native', ['ctest', '--test-dir', build, '--output-on-failure'], 'native_contracts' if model else None)
    run('rust', ['cargo', 'test', '--locked', '--offline', '--manifest-path', ROOT / 'bindings/rust/Cargo.toml'])
    run('node', ['node', '--test', ROOT / 'bindings/node/test.cjs'])
    run('python', [sys.executable, '-m', 'unittest', 'discover', '-s', ROOT / 'bindings/python/tests'])
    if WINDOWS:
        # bindings/java/build.sh's commands with Windows classpath separators (bash may be WSL's).
        java = ROOT / 'bindings/java'
        lint = '-Xlint:all,-restricted,-serial,-missing-explicit-ctor,-module'
        sources = lambda part: sorted(str(path) for path in (java / 'src' / part).rglob('*.java'))
        classes, tests = java / 'target/classes', java / 'target/test-classes'
        shutil.rmtree(java / 'target', ignore_errors=True)
        run('java-compile', ['javac', '--release', '22', lint, '-d', classes, *sources('main')])
        run('java-test-compile', ['javac', '--release', '22', lint, '-cp', classes, '-d', tests, *sources('test')])
        run('java', ['java', '--enable-native-access=ALL-UNNAMED', '-cp', os.pathsep.join(map(str, (classes, tests))),
                     'io.github.adrian729.laya.LayaTest'])
    else:
        run('java', ['bash', ROOT / 'bindings/java/build.sh'])
    run('dotnet', ['dotnet', 'test', ROOT / 'bindings/dotnet/DucktapeLaya.slnx', '-m:1'])
    run('go', ['go', '-C', ROOT / 'bindings/go', 'test', '-count=1', './...'])
    run('abi', [sys.executable, ROOT / 'scripts/check-abi.py', build / LIBRARY], 'abi')
    if model:
        run('bindings', [sys.executable, ROOT / 'tests/parity.py', model, '--build-dir', build,
                         '--report', out / 'outputs.json'], 'binding_conformance')
    if model:
        run('original-reference', [sys.executable, ROOT / 'tests/original-reference.py', model, '--build-dir', build,
                                   '--report', out / 'original-reference.json'], 'original_reference')
    if a.upstream_reference:
        run('upstream-reference', [sys.executable, ROOT / 'tests/compare-native-reference.py', model,
            '--build', build, '--report', out / 'upstream-reference.json'], 'upstream_reference')
    # The additional models must pass the same reference gates; check-candidate.py
    # requires every model in release/compatibility.json additional_models.
    def extra_gate(name, gate, label, command):
        report['additional_model_gates'].setdefault(name, {})[gate] = 'failed'
        run(label, command)
        report['additional_model_gates'][name][gate] = 'passed'
        save()
    for extra, extra_manifest in extra_models:
        name = extra_manifest['id']
        extra_gate(name, 'original_reference', f'original-reference-{name}',
                   [sys.executable, ROOT / 'tests/original-reference.py', extra, '--build-dir', build,
                    '--cases', ROOT / extra_manifest['original_reference_cases'], '--report', out / f'original-reference-{name}.json'])
        if a.upstream_reference:
            extra_gate(name, 'upstream_reference', f'upstream-reference-{name}',
                       [sys.executable, ROOT / 'tests/compare-native-reference.py', extra,
                        '--build', build, '--report', out / f'upstream-reference-{name}.json'])
    if a.packages:
        run('package', [sys.executable, ROOT / 'scripts/package-native.py', '--build', build, '--output', out / 'packages'])
        run('package-smoke', [sys.executable, ROOT / 'tests/package-smoke.py', out / 'packages'])
        # The multi-platform assembly of this one platform must load the same way.
        run('package-assembly-real', [sys.executable, ROOT / 'scripts/assemble-packages.py', '--output', out / 'assembled', out / 'packages'])
        run('assembled-smoke', [sys.executable, ROOT / 'tests/package-smoke.py', out / 'assembled'])
        if model:
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
