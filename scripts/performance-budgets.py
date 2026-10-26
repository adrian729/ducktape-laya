#!/usr/bin/env python3
"""Derive frozen per-profile performance budgets from baseline runs, or check a
candidate benchmark report against them.

`derive` needs at least the qualification policy's baseline run count of passed
tests/benchmark.py reports from one profile, model, thread count and host CPU.
Each limit is the worst baseline value times a declared headroom factor: the
reference host is a shared laptop, so limits catch regressions well beyond
measured run-to-run spread rather than promise portable latency. Descriptor and
thread leaks have no headroom: they must be zero. `check` compares a candidate
from the same profile, model, threads and CPU model with the frozen limits.
"""
import argparse
import json
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from release_support import ROOT, host_summary, read_json

HEADROOM = {'latency': 1.5, 'memory': 1.25}
def metrics(report):
    values = {'open_seconds': ('latency', report['open_seconds']), 'close_seconds': ('latency', report['close_seconds']),
              'peak_rss_kib': ('memory', report['peak_rss_kib']),
              'steady_rss_kib': ('memory', report['resources_steady']['rss_kib']),
              'closed_rss_kib': ('memory', report['resources_after_close']['rss_kib']),
              'cancellation_response_seconds': ('latency', report['cancellation']['response_max_seconds']),
              'deadline_overshoot_seconds': ('latency', report['cancellation']['deadline_overshoot_max_seconds']),
              'router_cold_load_call_seconds': ('latency', report['router']['cold_load_call_max_seconds']),
              'two_engines_p95_seconds': ('latency', report['two_engines']['p95_seconds'])}
    for name, measured in report['latency'].items():
        for key in ('first_seconds', 'p50_seconds', 'p95_seconds'):
            values[f'{name}_{key}'] = ('latency', measured[key])
    return values

def identity(report):
    return {'profile': report['diagnostics']['numerical_profile']['id'], 'model_sha256': report['model_sha256'],
            'threads': report['threads'], 'cpu_model': report['cpu_model'],
            'workloads': {name: w['request_sha256'] for name, w in report['workloads'].items()}}

def derive(args):
    reports = [json.loads(path.read_text()) for path in args.runs]
    required = read_json(ROOT / 'release/qualification.json')['budgets']['baseline_runs']
    if len(reports) < required: raise SystemExit(f'{required} baseline runs required')
    if any(r['status'] != 'passed' or any(r['leaks'].values()) for r in reports): raise SystemExit('A baseline run failed or leaked')
    if len({json.dumps(identity(r), sort_keys=True) for r in reports}) != 1: raise SystemExit('Baseline runs differ in profile identity')
    if len({r['library_sha256'] for r in reports}) != 1: raise SystemExit('Baseline runs used different libraries')
    measured = [metrics(r) for r in reports]
    limits = {}
    for name, (kind, _) in measured[0].items():
        values = [m[name][1] for m in measured]
        limits[name] = {'kind': kind, 'baseline': values, 'limit': max(values) * HEADROOM[kind]}
    budget = {'schema_version': 1, 'identity': identity(reports[0]), 'headroom': HEADROOM,
              'baseline_library_sha256': reports[0]['library_sha256'], 'baseline_source_commit': reports[0]['source_commit'],
              'leaks': {'descriptors': 0, 'threads': 0}, 'limits': limits,
              'rationale': 'Limits are the worst of the baseline runs times the headroom factor for that metric kind. '
                           'They detect regressions on this reference host and profile; they are not portable latency promises.'}
    args.output.write_text(json.dumps(budget, indent=2) + '\n')
    print(json.dumps({name: round(limit['limit'], 4) for name, limit in limits.items()}, indent=2))

def check(args):
    budget, report = read_json(args.budgets), json.loads(args.report.read_text())
    failures = []
    if identity(report) != budget['identity']: failures.append('profile identity differs from the frozen budget')
    if report['status'] != 'passed': failures.append('benchmark failed')
    for key, allowed in budget['leaks'].items():
        if report['leaks'][key] > allowed: failures.append(f'{key} leak {report["leaks"][key]}')
    measured = metrics(report)
    for name, limit in budget['limits'].items():
        if name not in measured: failures.append(f'{name} missing')
        elif measured[name][1] > limit['limit']: failures.append(f'{name} {measured[name][1]:.4g} exceeds {limit["limit"]:.4g}')
    # The host's thermal and frequency context, so a miss can be attributed to the environment or the code; never part of the verdict.
    print(json.dumps({'status': 'failed' if failures else 'passed', 'failures': failures, 'host': host_summary(report)}, indent=2))
    raise SystemExit(bool(failures))

parser = argparse.ArgumentParser(description=__doc__)
commands = parser.add_subparsers(dest='command', required=True)
command = commands.add_parser('derive')
command.add_argument('runs', type=pathlib.Path, nargs='+')
command.add_argument('--output', type=pathlib.Path, required=True)
command.set_defaults(run=derive)
command = commands.add_parser('check')
command.add_argument('report', type=pathlib.Path)
command.add_argument('--budgets', type=pathlib.Path, default=ROOT / 'release/budgets/native-performance.json')
command.set_defaults(run=check)
args = parser.parse_args()
args.run(args)
