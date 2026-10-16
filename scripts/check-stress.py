#!/usr/bin/env python3
"""Run an identity-recorded native stress campaign; never qualifies a release alone.

Uses an existing product build configured with DUCKTAPE_BUILD_TESTS=ON (the
library must match its completed build record and the current source). Records
library/test/model identities before the run and verifies them unchanged after.
The stress executable must be run from a directory on the model's filesystem; it
creates its router hard links there.
"""
import argparse
import json
import os
import pathlib
import platform
import subprocess
import time
from release_support import ROOT, binary_info, check_record, git, read_json, require_runtime_identity, sha256, source_identity, native_build_identity, verify_model

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('model', type=pathlib.Path)
p.add_argument('--build', type=pathlib.Path, default=ROOT / 'build')
p.add_argument('--output', type=pathlib.Path, required=True)
p.add_argument('--seconds', type=int, default=3600)
p.add_argument('--jobs', type=int, default=2)
a = p.parse_args()
if not 1 <= a.seconds <= 86400 or a.jobs < 1:
    p.error('seconds must be 1..86400 and jobs positive')
record = check_record()
policy = read_json(ROOT / 'release/qualification.json')
manifest = read_json(ROOT / record['models'][0])
model, build, out = a.model.resolve(), a.build.resolve(), a.output.resolve()
verify_model(model, manifest)
out.mkdir(parents=True, exist_ok=False)
required = policy['budgets']['candidate_soak_seconds'] if a.seconds >= policy['budgets']['candidate_soak_seconds'] \
    else policy['budgets']['routine_stress_seconds']
report = {'schema_version': 1, 'status': 'running', 'release_ready': False,
          'source_commit': git(ROOT, 'rev-parse', 'HEAD'),
          'source_tracked_dirty': bool(git(ROOT, 'diff', 'HEAD', '--name-only')),
          'runtime': record['runtime'], 'platform': platform.platform(), 'model_sha256': manifest['sha256'],
          'requested_seconds': a.seconds, 'required_seconds': required,
          'kind': 'candidate_soak' if required == policy['budgets']['candidate_soak_seconds'] else 'routine_stress',
          'qualification_budget_met': False}
def save():
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
save()
try:
    commands = [['cmake', '--build', str(build), '--target', 'ducktape_laya', 'laya-stress', '--parallel', str(a.jobs)]]
    report['build_commands'] = commands
    with (out / 'build.log').open('w') as log:
        for command in commands:
            subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT, timeout=3600)
    report['build_identity'] = native_build_identity(build, 'ducktape_laya', 'libducktape_laya.so', source_identity())
    report['diagnostics'] = binary_info(build)
    require_runtime_identity(report['diagnostics'], record)
    library, binary = build / 'libducktape_laya.so', build / 'laya-stress'
    report['library_sha256'] = sha256(library)
    report['test_sha256'] = sha256(binary)
    report['test_source_sha256'] = sha256(ROOT / 'tests/stress.cpp')
    command = [str(binary), str(model), '--seconds', str(a.seconds)]
    report['command'] = command
    save()
    started = time.monotonic()
    env = dict(os.environ, LD_LIBRARY_PATH=str(build))
    with (out / 'stress.log').open('w') as log, (out / 'result.json').open('w') as result:
        try:
            run = subprocess.run(command, cwd=out, env=env, stdout=result, stderr=log, timeout=a.seconds + 1800)
            report['exit_code'] = run.returncode
        except subprocess.TimeoutExpired:
            report['exit_code'] = 124
    report['elapsed_seconds'] = time.monotonic() - started
    if report['exit_code']:
        raise RuntimeError('Stress run failed; inspect stress.log')
    report['result'] = json.loads((out / 'result.json').read_text())
    if sha256(binary) != report['test_sha256'] or sha256(library) != report['library_sha256']:
        raise RuntimeError('Test binary or shared library changed during the run')
    if report['result']['elapsed_seconds'] < a.seconds:
        raise RuntimeError('Stress exited before its requested duration')
    report['post_run_identity_verified'] = True
    report['qualification_budget_met'] = a.seconds >= required
    report['status'] = 'passed'
except Exception as error:
    report['status'] = 'failed'
    report['error'] = str(error)
finally:
    save()
print(json.dumps({'status': report['status'], 'qualification_budget_met': report['qualification_budget_met'],
                  'report': str(out / 'report.json')}))
raise SystemExit(0 if report['status'] == 'passed' else 1)
