#!/usr/bin/env python3
"""Run identity-recorded real-model sanitizer suites; never qualifies a release alone.

Configures the Clang sanitizer builds through their presets (AddressSanitizer with
UndefinedBehaviorSanitizer: preset `sanitize` in build-sanitize; ThreadSanitizer:
preset `sanitize-thread` in build-sanitize-thread; no suppressions or recovery),
runs every registered native test with the supplied model and a short stress run
under each, and records source/build/model identities and logs. Instrumented
libraries share the candidate source but are never packaged. The ThreadSanitizer
run also builds the Rust binding's tests with the pinned nightly Rust (RUST_NIGHTLY,
-Zsanitizer=thread, -Zbuild-std; needs the rust-src component) against the
instrumented library. Node worker threads are not covered.

--only runs one sanitizer (CI runs them as two jobs); --build DIR reuses an existing
build of that sanitizer's preset instead of the preset's directory. --compiler-launcher
ccache compiles through ccache (instrumented builds are never shipped); the default
is none.

--merge ADDRESS_DIR THREAD_DIR --output DIR joins the reports of the two --only runs
into one report of today's schema (both entries plus merged_from). It refuses unless
both passed on the same clean source commit, model and runtime, the first holds a
passed address entry and the second a passed thread entry.
"""
import argparse
import hashlib
import json
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from release_support import (CCACHE_ENVIRONMENT, ROOT, ccache_config, check_record, default_jobs, git, read_json, sha256, source_identity,
                             native_build_identity, verify_model)

# The pinned nightly toolchain of the Rust ThreadSanitizer leg (CI installs it with
# `rustup toolchain install nightly-2026-10-17 --profile minimal --component rust-src`).
RUST_NIGHTLY = 'nightly-2026-10-17'
SANITIZERS = {
    'address': {'preset': 'sanitize', 'directory': 'build-sanitize',
                'flags': '-fsanitize=address,undefined -fno-omit-frame-pointer -fno-sanitize-recover=all',
                'env': {'ASAN_OPTIONS': 'detect_leaks=1:strict_string_checks=1:halt_on_error=1',
                        'UBSAN_OPTIONS': 'halt_on_error=1:print_stacktrace=1'}},
    'thread': {'preset': 'sanitize-thread', 'directory': 'build-sanitize-thread',
               'flags': '-fsanitize=thread -fno-omit-frame-pointer',
               'env': {'TSAN_OPTIONS': 'halt_on_error=1:second_deadlock_stack=1'}},
}
REQUIRED_TESTS = {'real-model', 'real-features', 'real-control', 'real-faults'}
LAUNCHERS = {'none': '', 'ccache': 'ccache'}
# Instrumented code runs several times slower; the CTest TIMEOUT property overrides --timeout.
TEST_TIMEOUT = 3600


class Refused(ValueError):
    pass


def default_test_jobs():
    """One test at a time on a host (the real-model tests time CPU), four on a CI runner."""
    return min(4, os.cpu_count() or 1) if os.environ.get('GITHUB_ACTIONS') == 'true' else 1


def cache_value(build, key):
    found = re.search(rf'^{re.escape(key)}:[^=]*=(.*)$', (build / 'CMakeCache.txt').read_text(errors='replace'), re.M)
    return found[1] if found else None


def merge(address_dir, thread_dir, output):
    """Join the address report of ADDRESS_DIR and the thread report of THREAD_DIR into OUTPUT/report.json."""
    address_dir, thread_dir, output = (pathlib.Path(path).resolve() for path in (address_dir, thread_dir, output))
    reports = {}
    for name, directory in (('address', address_dir), ('thread', thread_dir)):
        path = directory / 'report.json'
        if not path.is_file():
            raise Refused(f'{directory}: no report.json')
        report = json.loads(path.read_text())
        if report.get('status') != 'passed':
            raise Refused(f'{directory}: status {report.get("status")}, not passed')
        if report.get('source_tracked_dirty') is not False:
            raise Refused(f'{directory}: produced from a dirty tree')
        entry = report.get('sanitizers', {}).get(name)
        if not isinstance(entry, dict):
            raise Refused(f'{directory}: no {name} sanitizer entry')
        if entry.get('status') != 'passed':
            raise Refused(f'{directory}: the {name} sanitizer is {entry.get("status")}, not passed')
        reports[name] = (report, path)
    first, second = reports['address'][0], reports['thread'][0]
    for key in ('source_commit', 'model_sha256', 'runtime'):
        if first.get(key) is None or first.get(key) != second.get(key):
            raise Refused(f'the reports differ in {key}: {first.get(key)!r} and {second.get(key)!r}')
    logs = {}
    for directory in (address_dir, thread_dir):
        for log in sorted(directory.glob('*.log')):
            if log.name in logs:
                raise Refused(f'both directories hold {log.name}')
            logs[log.name] = log
    merged = {key: value for key, value in first.items() if key not in ('sanitizers', 'error')}
    merged.update(status='passed', release_ready=False,
                  sanitizers={'address': first['sanitizers']['address'], 'thread': second['sanitizers']['thread']},
                  merged_from=[{'directory': str(directory), 'report_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                               for directory, (_, path) in zip((address_dir, thread_dir), (reports['address'], reports['thread']))])
    launchers = {first.get('compiler_launcher'), second.get('compiler_launcher')}
    if len(launchers) == 1:
        merged['compiler_launcher'] = launchers.pop()
    else:
        merged.pop('compiler_launcher', None)
    output.mkdir(parents=True, exist_ok=False)
    for name, log in logs.items():
        shutil.copy2(log, output / name)
    (output / 'report.json').write_text(json.dumps(merged, indent=2) + '\n')
    return merged


def run_sanitizers(a):
    record = check_record()
    manifest = read_json(ROOT / record['models'][0])
    model, out = a.model.resolve(), a.output.resolve()
    verify_model(model, manifest)
    out.mkdir(parents=True, exist_ok=False)
    selected = [a.only] if a.only else list(SANITIZERS)
    launcher = LAUNCHERS[a.compiler_launcher]
    report = {'schema_version': 1, 'status': 'running', 'release_ready': False,
              'source_commit': git(ROOT, 'rev-parse', 'HEAD'),
              'source_tracked_dirty': bool(git(ROOT, 'diff', 'HEAD', '--name-only')),
              'runtime': record['runtime'], 'platform': platform.platform(), 'model_sha256': manifest['sha256'],
              'scope': 'native library, llama.cpp/ggml, native real-model test executables and Rust binding tests; not Node threads',
              'compiler_launcher': a.compiler_launcher, 'test_jobs': a.test_jobs,
              'sanitizers': {name: {'status': 'not_run'} for name in SANITIZERS}}

    def save():
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')

    def run(label, command, env=None, cwd=None, timeout=7200):
        with (out / (label + '.log')).open('w') as log:
            result = subprocess.run(list(map(str, command)), env=env, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        if result.returncode:
            raise RuntimeError(f'{label} failed with {result.returncode}; see {label}.log')

    save()
    try:
        for name in selected:
            settings = SANITIZERS[name]
            entry = report['sanitizers'][name] = {'status': 'running', 'preset': settings['preset'], 'env': settings['env'],
                                                  'compiler_launcher': a.compiler_launcher}
            save()
            env = dict(os.environ, **CCACHE_ENVIRONMENT, **settings['env'])
            options = [f'-DDUCKTAPE_LLAMA_SOURCE={ROOT / record["runtime"]["path"]}', '-DDUCKTAPE_LLAMA_REVISION=',
                       f'-DDUCKTAPE_TEST_MODEL={model}', f'-DDUCKTAPE_REAL_TEST_TIMEOUT={TEST_TIMEOUT}',
                       # ggml's GGML_CCACHE would set a global launch rule with any ccache on PATH; the launcher is explicit.
                       '-DGGML_CCACHE=OFF', f'-DCMAKE_C_COMPILER_LAUNCHER={launcher}', f'-DCMAKE_CXX_COMPILER_LAUNCHER={launcher}']
            if a.build:
                build = a.build.resolve()
                if not (build / 'CMakeCache.txt').is_file():
                    raise RuntimeError(f'{build}: not a configured build (configure it with cmake --preset {settings["preset"]} first)')
                configure = ['cmake', '-S', ROOT, '-B', build, *options]
            else:
                build = ROOT / settings['directory']
                configure = ['cmake', '--preset', settings['preset'], *options]
            entry['build'] = str(build)
            entry['configure_command'] = list(map(str, configure))
            run(f'{name}-configure', configure, env, cwd=ROOT)
            for flags in ('CMAKE_C_FLAGS', 'CMAKE_CXX_FLAGS'):
                if settings['flags'] not in (cache_value(build, flags) or ''):
                    raise RuntimeError(f'{name}: {build} is not a {settings["preset"]} build ({flags} lacks {settings["flags"]})')
            entry['flags'] = cache_value(build, 'CMAKE_CXX_FLAGS')
            entry['compiler'] = cache_value(build, 'CMAKE_CXX_COMPILER')
            if launcher:
                entry['ccache_config'] = ccache_config(env)
            run(f'{name}-build', ['cmake', '--build', build, '--parallel', a.jobs], env)
            run(f'{name}-build-stress', ['cmake', '--build', build, '--target', 'laya-stress', '--parallel', a.jobs], env)
            entry['build_identity'] = native_build_identity(build, 'ducktape_laya', 'libducktape_laya.so', source_identity())
            entry['cache_sha256'] = sha256(build / 'CMakeCache.txt')
            tests = json.loads(subprocess.check_output(['ctest', '--test-dir', str(build), '--show-only=json-v1'], env=env))
            entry['tests'] = sorted(test['name'] for test in tests['tests'])
            if not REQUIRED_TESTS <= set(entry['tests']):
                raise RuntimeError(f'{name}: real-model tests are not registered')
            started = time.monotonic()
            run(f'{name}-ctest', ['ctest', '--test-dir', build, '--output-on-failure', '--no-tests=error', '--timeout', TEST_TIMEOUT,
                                  '-j', a.test_jobs], env)
            stress_dir = out / f'{name}-stress'
            stress_dir.mkdir()
            run(f'{name}-stress', [build / 'laya-stress', model, a.stress_rounds], dict(env, LD_LIBRARY_PATH=str(build)), cwd=stress_dir)
            if name == 'thread':
                # rustc does not export the runtime's interceptors to shared libraries the way
                # clang does; without __cxa_guard_* exported, guarded static initialization in
                # the library is reported as a race. Doctests are not built with RUSTFLAGS.
                entry['rust_toolchain'] = subprocess.check_output(['rustc', '+' + RUST_NIGHTLY, '--version', '--verbose'], text=True).strip()
                run('thread-rust', ['cargo', '+' + RUST_NIGHTLY, 'test', '--locked', '-Zbuild-std', '--target', 'x86_64-unknown-linux-gnu',
                    '--lib', '--manifest-path', ROOT / 'bindings/rust/Cargo.toml'],
                    dict(env, RUSTFLAGS='-Zsanitizer=thread -Clink-arg=-Wl,--export-dynamic', LAYA_MODEL=str(model),
                         DUCKTAPE_LAYA_LIB_DIR=str(build), LD_LIBRARY_PATH=str(build), CARGO_TARGET_DIR=str(build / 'rust-tsan')))
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
            if entry['status'] == 'running':
                entry['status'] = 'failed'
    finally:
        save()
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('model', type=pathlib.Path, nargs='?', help='The primary model (not with --merge)')
    p.add_argument('--output', type=pathlib.Path, required=True)
    p.add_argument('--jobs', type=int, default=default_jobs())
    p.add_argument('--test-jobs', type=int, default=default_test_jobs(), help='ctest -j (default 1 on a host, 4 on a CI runner)')
    p.add_argument('--stress-rounds', type=int, default=10)
    p.add_argument('--only', choices=list(SANITIZERS))
    p.add_argument('--build', type=pathlib.Path, help='Reuse this existing build of the --only sanitizer\'s preset')
    p.add_argument('--compiler-launcher', choices=list(LAUNCHERS), default='none')
    p.add_argument('--merge', nargs=2, type=pathlib.Path, metavar=('ADDRESS_DIR', 'THREAD_DIR'))
    a = p.parse_args(argv)
    if a.merge:
        if a.model or a.only or a.build:
            p.error('--merge takes no model, --only or --build')
        try:
            merged = merge(*a.merge, a.output)
        except Refused as error:
            print(f'check-sanitizers: refused: {error}', file=sys.stderr)
            return 1
        print(json.dumps({'status': merged['status'], 'report': str(a.output.resolve() / 'report.json')}))
        return 0
    if a.model is None:
        p.error('the model is required (except with --merge)')
    if a.jobs < 1 or a.test_jobs < 1 or not 1 <= a.stress_rounds <= 1000:
        p.error('jobs and test jobs positive; stress rounds 1..1000')
    if a.build and not a.only:
        p.error('--build needs --only (one build directory per sanitizer)')
    report = run_sanitizers(a)
    print(json.dumps({'status': report['status'], 'report': str(a.output.resolve() / 'report.json')}))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
