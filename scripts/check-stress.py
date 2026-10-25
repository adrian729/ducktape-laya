#!/usr/bin/env python3
"""Run an identity-recorded native stress campaign; never qualifies a release alone.

Uses an existing product build configured with DUCKTAPE_BUILD_TESTS=ON (the
library must match its completed build record and the current source). Records
library/test/model identities before the run and verifies them unchanged after.
The stress executable must be run from a directory on the model's filesystem; it
creates its router hard links there.

--budget routine (default) qualifies against the one-hour routine stress or, from
candidate_soak_seconds (three hours), the candidate soak, which also serves the
routine stress gate; --budget short against the tier-2 targets' short_stress_seconds
(release/qualification.json). The soak and the short run also have a rounds floor
(candidate_soak_rounds, short_stress_rounds), passed to laya-stress as --min-rounds:
the run continues past its seconds until the floor is reached, and the budget is met
only when both are. The run is killed after max(seconds, rounds floor x ROUND_SECONDS) + 1800 s, or as soon as
laya-stress has written no progress for STALL_SECONDS (a hang). --prebuilt uses the --build directory's laya-stress as it is, without
building (a restored CI build; its identities are still checked).

A failed run that completed at least one round still prints its result line
(status failed, the failure and the closed-RSS series); it is recorded as `result`
and the report keeps status failed.
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
from release_support import (EXE, LIBRARY, ROOT, binary_info, build_environment, check_record, default_jobs, git, host_target, read_json,
                             require_runtime_identity, require_runtime_sources, sha256,
                             source_identity, native_build_identity, verify_model)

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('model', type=pathlib.Path)
p.add_argument('--build', type=pathlib.Path, help='Build to compile laya-stress in (default build)')
p.add_argument('--prebuilt', action='store_true', help='Use the --build directory\'s laya-stress as it is, without building')
p.add_argument('--output', type=pathlib.Path, required=True)
p.add_argument('--seconds', type=int, help='Run length (default routine_stress_seconds, or short_stress_seconds with --budget short)')
p.add_argument('--min-rounds', type=int,
               help='Rounds floor (default candidate_soak_rounds for the soak, short_stress_rounds with --budget short, none for routine stress)')
p.add_argument('--jobs', type=int, default=default_jobs())
p.add_argument('--budget', choices=('routine', 'short'), default='routine')
a = p.parse_args()
policy = read_json(ROOT / 'release/qualification.json')
budgets = policy['budgets']
if a.seconds is None:
    a.seconds = budgets['short_stress_seconds'] if a.budget == 'short' else budgets['routine_stress_seconds']
if not 1 <= a.seconds <= 86400 or a.jobs < 1:
    p.error('seconds must be 1..86400 and jobs positive')
if a.budget == 'short':
    kind, required, required_rounds = 'short_stress', budgets['short_stress_seconds'], budgets['short_stress_rounds']
elif a.seconds >= budgets['candidate_soak_seconds']:
    kind, required, required_rounds = 'candidate_soak', budgets['candidate_soak_seconds'], budgets['candidate_soak_rounds']
else:
    kind, required, required_rounds = 'routine_stress', budgets['routine_stress_seconds'], None
if a.min_rounds is None:
    a.min_rounds = required_rounds
if a.min_rounds is not None and not 1 <= a.min_rounds <= 1000000:
    p.error('--min-rounds must be 1..1000000')
# A rounds floor that outlasts the seconds budget is not cut off. A round takes 2.2-3.0 s on the CI runners but
# 11-14 s on the release host's pinned soak cores under the overlapping gates (V1 H10: 12.3 s on average, 13.9 s at
# worst); 20 s is the margin. A hang is caught sooner by the stall limit: laya-stress reports progress on stderr
# after the first round that ends 60 s after the last report, so STALL_SECONDS without any output is a hang.
ROUND_SECONDS, STALL_SECONDS = 20, 900
timeout = max(a.seconds, (a.min_rounds or 0) * ROUND_SECONDS) + 1800
record = check_record()
manifest = read_json(ROOT / record['models'][0])
if a.prebuilt and not a.build:
    p.error('--prebuilt uses the --build directory: name it')
build = (a.build or ROOT / 'build').resolve()
model, out = a.model.resolve(), a.output.resolve()
verify_model(model, manifest)
out.mkdir(parents=True, exist_ok=False)
try:
    target_platform = host_target().name
except ValueError:
    target_platform = None
report = {'schema_version': 1, 'status': 'running', 'release_ready': False,
          'source_commit': git(ROOT, 'rev-parse', 'HEAD'),
          'source_tracked_dirty': bool(git(ROOT, 'diff', 'HEAD', '--name-only')),
          'runtime': record['runtime'], 'platform': platform.platform(), 'model_sha256': manifest['sha256'],
          'requested_seconds': a.seconds, 'required_seconds': required,
          'min_rounds': a.min_rounds, 'required_rounds': required_rounds, 'timeout_seconds': timeout, 'prebuilt': a.prebuilt,
          'kind': kind, 'target_platform': target_platform, 'build_environment': build_environment(), 'qualification_budget_met': False}
def save():
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
def last_result_line(path):
    """The last JSON object line of laya-stress's output, or None."""
    for line in reversed(path.read_text(errors='replace').splitlines()):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None
def run_watched(command, cwd, env, stdout, stderr, log_path):
    """(exit code, None) when laya-stress exits by itself; (124, reason) when it is killed at the overall timeout or
    after STALL_SECONDS without output on stderr."""
    process = subprocess.Popen(command, cwd=cwd, env=env, stdout=stdout, stderr=stderr)
    started = last_output = time.monotonic()
    size = 0
    while True:
        try:
            return process.wait(timeout=10), None
        except subprocess.TimeoutExpired:
            pass
        now = time.monotonic()
        grown = log_path.stat().st_size
        if grown != size:
            size, last_output = grown, now
        reason = (f'timeout after {timeout} s' if now - started >= timeout else
                  f'stalled: no progress for {STALL_SECONDS} s' if now - last_output >= STALL_SECONDS else None)
        if reason:
            process.kill()
            process.wait()
            return 124, reason
save()
try:
    commands = [] if a.prebuilt else [['cmake', '--build', str(build), '--target', 'ducktape_laya', 'laya-stress', '--parallel', str(a.jobs)]]
    report['build_commands'] = commands
    with (out / 'build.log').open('w') as log:
        for command in commands:
            subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT, timeout=3600)
    if not (build / ('laya-stress' + EXE)).is_file():
        raise RuntimeError(f'{build}: no laya-stress (build the laya-stress target first)')
    report['build_identity'] = native_build_identity(build, 'ducktape_laya', LIBRARY, source_identity())
    report['diagnostics'] = binary_info(build)
    require_runtime_identity(report['diagnostics'], record)
    require_runtime_sources(build, ROOT / record['runtime']['path'])
    library, binary = build / LIBRARY, build / ('laya-stress' + EXE)
    report['library_sha256'] = sha256(library)
    report['test_sha256'] = sha256(binary)
    report['test_source_sha256'] = sha256(ROOT / 'tests/stress.cpp')
    command = [str(binary), str(model), '--seconds', str(a.seconds)] + (['--min-rounds', str(a.min_rounds)] if a.min_rounds else [])
    report['command'] = command
    save()
    started = time.monotonic()
    # Windows finds the DLL on PATH.
    env = dict(os.environ, PATH=str(build) + os.pathsep + os.environ.get('PATH', '')) if sys.platform == 'win32' \
        else dict(os.environ, LD_LIBRARY_PATH=str(build))
    with (out / 'stress.log').open('w') as log, (out / 'result.json').open('w') as result:
        report['exit_code'], report['stopped'] = run_watched(command, out, env, result, log, out / 'stress.log')
    report['elapsed_seconds'] = time.monotonic() - started
    if report['stopped']:
        # A killed laya-stress leaves its router hard links behind (its cleanup runs only on a normal exit).
        for links in out.glob('stress-router-*'):
            shutil.rmtree(links, ignore_errors=True)
    if report['exit_code']:
        # A failure after the first completed round still prints the result line (status failed, the failure and
        # the series so far); keep it for analysis. The report stays failed.
        failed = last_result_line(out / 'result.json')
        if failed is not None:
            report['result'] = failed
        detail = f": {failed['failure']}" if isinstance(failed, dict) and failed.get('failure') else \
            f" ({report['stopped']})" if report['stopped'] else ''
        raise RuntimeError(f'Stress run failed with exit code {report["exit_code"]}{detail}; inspect stress.log and result.json')
    report['result'] = json.loads((out / 'result.json').read_text())
    if sha256(binary) != report['test_sha256'] or sha256(library) != report['library_sha256']:
        raise RuntimeError('Test binary or shared library changed during the run')
    if report['result']['elapsed_seconds'] < a.seconds:
        raise RuntimeError('Stress exited before its requested duration')
    if report['result'].get('status', 'passed') != 'passed':
        raise RuntimeError(f'Stress result is {report["result"].get("status")}: {report["result"].get("failure")}')
    rounds = report['result'].get('rounds')
    if a.min_rounds and not (isinstance(rounds, int) and rounds >= a.min_rounds):
        raise RuntimeError(f'Stress completed {rounds} rounds, fewer than --min-rounds {a.min_rounds}')
    report['post_run_identity_verified'] = True
    report['qualification_budget_met'] = a.seconds >= required and (
        required_rounds is None or (isinstance(rounds, int) and rounds >= required_rounds))
    report['status'] = 'passed'
except Exception as error:
    report['status'] = 'failed'
    report['error'] = str(error)
finally:
    save()
print(json.dumps({'status': report['status'], 'qualification_budget_met': report['qualification_budget_met'],
                  'report': str(out / 'report.json')}))
raise SystemExit(0 if report['status'] == 'passed' else 1)
