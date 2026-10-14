#!/usr/bin/env python3
"""Run a bounded, identity-recorded libFuzzer campaign; never qualifies a release alone."""
import argparse
import json
import os
import pathlib
import platform
import re
import subprocess
import time
from release_support import ROOT, check_record, read_json, sha256, source_identity, native_build_identity, git

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--build', type=pathlib.Path, default=ROOT / 'build-fuzzer')
p.add_argument('--output', type=pathlib.Path, required=True)
p.add_argument('--seconds', type=int, default=3600)
p.add_argument('--seed', type=int, default=444211238)
p.add_argument('--jobs', type=int, default=2)
a = p.parse_args()
if not 1 <= a.seconds <= 86400 or not 0 <= a.seed <= 4294967295 or a.jobs < 1:
    p.error('seconds must be 1..86400, seed uint32, and jobs positive')
record = check_record()
policy = read_json(ROOT / 'release/qualification.json')
build, out = a.build.resolve(), a.output.resolve()
out.mkdir(parents=True, exist_ok=False)
report = {'schema_version': 1, 'status': 'running', 'release_ready': False,
          'source_commit': git(ROOT, 'rev-parse', 'HEAD'),
          'source_tracked_dirty': bool(git(ROOT, 'diff', 'HEAD', '--name-only')),
          'runtime': record['runtime'], 'platform': platform.platform(),
          'scope': 'shared-core parsing/planning/router boundaries; no model or tensor-kernel fuzzing',
          'requested_seconds': a.seconds, 'seed': a.seed,
          'required_seconds': policy['budgets']['fuzz_seconds_per_target'],
          'qualification_budget_met': False}
def save():
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
save()
try:
    # Require an explicitly prepared fuzz build; never convert a product build
    # into an instrumented one implicitly. Reset candidate runtime cache overrides.
    cache = (build / 'CMakeCache.txt').read_text()
    enabled = re.search(r'^DUCKTAPE_BUILD_FUZZERS:BOOL=(.*)$', cache, re.M)
    if not enabled or enabled[1].upper() not in ['ON', 'YES', 'TRUE', '1']:
        raise RuntimeError('Configure a dedicated build with DUCKTAPE_BUILD_FUZZERS=ON first')
    commands = [
        ['cmake', '-S', str(ROOT), '-B', str(build),
         f'-DDUCKTAPE_LLAMA_SOURCE={ROOT / record["runtime"]["path"]}', '-DDUCKTAPE_LLAMA_REVISION='],
        ['cmake', '--build', str(build), '--target', 'laya-input-fuzzer', '--parallel', str(a.jobs)]]
    report['build_commands'] = commands
    with (out / 'build.log').open('w') as log:
        for command in commands:
            subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT, timeout=3600)
    report['build_identity'] = native_build_identity(build, 'ducktape_laya', 'libducktape_laya.so', source_identity())
    library, binary = build / 'libducktape_laya.so', build / 'laya-input-fuzzer'
    symbols = subprocess.check_output(['readelf', '--dyn-syms', '--wide', str(library)], text=True)
    if '__sanitizer_cov_8bit_counters_init' not in symbols:
        raise RuntimeError('Shared library lacks coverage instrumentation')
    report['binary_sha256'] = sha256(binary)
    report['test_source_sha256'] = sha256(ROOT / 'tests/input-libfuzzer.cpp')
    report['cache_sha256'] = sha256(build / 'CMakeCache.txt')
    subprocess.run(['python3', ROOT / 'tests/fuzz-corpus.py', out / 'corpus'], check=True, capture_output=True)
    report['seed_corpus_sha256'] = {path.name: sha256(path) for path in sorted((out / 'corpus').iterdir())}
    env = dict(os.environ, ASAN_OPTIONS='detect_leaks=1:strict_string_checks=1',
               UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1', LD_LIBRARY_PATH=str(build))
    report['sanitizer_options'] = {key: env[key] for key in ['ASAN_OPTIONS', 'UBSAN_OPTIONS']}
    command = [str(binary), str(out / 'corpus'), f'-seed={a.seed}', f'-max_total_time={a.seconds}',
               '-max_len=1048577', '-timeout=30', '-rss_limit_mb=2048',
               f'-artifact_prefix={out}/', '-print_final_stats=1']
    report['command'] = command
    save()
    started = time.monotonic()
    with (out / 'fuzz.log').open('w') as log:
        try:
            result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=a.seconds + 90)
            report['exit_code'] = result.returncode
        except subprocess.TimeoutExpired:
            report['exit_code'] = 124
    report['elapsed_seconds'] = time.monotonic() - started
    log = (out / 'fuzz.log').read_text(errors='replace')
    report['stats'] = {key: int(value) for key, value in re.findall(r'^stat::([a-z_]+):\s+(\d+)$', log, re.M)}
    if report['exit_code'] or not report['stats'].get('number_of_executed_units'):
        raise RuntimeError('Fuzz campaign failed or produced no execution statistics; inspect fuzz.log and reproducers')
    if sha256(binary) != report['binary_sha256'] or sha256(library) != report['build_identity']['binary_sha256']:
        raise RuntimeError('Test binary or shared library changed during campaign')
    if report['elapsed_seconds'] < a.seconds:
        raise RuntimeError('Campaign exited before its requested duration')
    report['qualification_budget_met'] = a.seconds >= report['required_seconds']
    report['status'] = 'passed'
except Exception as error:
    report['status'] = 'failed'
    report['error'] = str(error)
finally:
    save()
print(json.dumps({'status': report['status'], 'qualification_budget_met': report['qualification_budget_met'],
                  'report': str(out / 'report.json')}))
raise SystemExit(0 if report['status'] == 'passed' else 1)
