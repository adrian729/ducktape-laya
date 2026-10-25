#!/usr/bin/env python3
"""Compare every fixture request with upstream primitive processing in-process.

Structured requests are compared through their schema-derived primitive questions."""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
from compare_results import compare_results
from release_support import ROOT, EXE, LIBRARY, check_record, find_manifest, read_json, sha256, git
from reference_requests import comparable_answers, reference_key, reference_requests

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('model', type=pathlib.Path)
p.add_argument('--build', type=pathlib.Path, default=ROOT / 'build')
p.add_argument('--report', type=pathlib.Path, required=True)
a = p.parse_args()
if a.report.exists(): p.error('Report exists; choose a new path')
record = check_record()
manifest = find_manifest(a.model, record)
build, model = a.build.resolve(), a.model.resolve()
policy = read_json(ROOT / 'release/qualification.json')
tolerance = policy['budgets']['native_upstream_absolute_tolerance']
report = {'schema_version': 1, 'status': 'not_run', 'source_commit': git(ROOT, 'rev-parse', 'HEAD'),
          'source_tracked_dirty': bool(git(ROOT, 'diff', 'HEAD', '--name-only')), 'runtime': record['runtime'],
          'settings': {'threads': 4}, 'model_sha256': manifest['sha256'], 'absolute_tolerance': tolerance,
          'binaries': {name: sha256(build / name) for name in ['laya-reference' + EXE, 'laya-conformance-c' + EXE, LIBRARY]},
          'reference_source_sha256': sha256(ROOT / 'tests/upstream-reference.cpp'), 'fixtures': {}}
env = dict(os.environ, LD_LIBRARY_PATH=str(build), DYLD_LIBRARY_PATH=str(build), PATH=str(build) + os.pathsep + os.environ.get('PATH', ''))
# The model's applied and stored temperatures, from the core's own diagnostics.
info = subprocess.run([sys.executable, '-c', 'import json, sys, ducktape_laya\n'
                       'with ducktape_laya.Engine.open(sys.argv[1], threads=1) as e: print(json.dumps(e.info()["model"]))', model],
                      capture_output=True, text=True, timeout=180, check=True,
                      env=dict(env, DUCKTAPE_LAYA_LIB_DIR=str(build), PYTHONPATH=str(ROOT / 'bindings/python/src')))
model_info = json.loads(info.stdout)


def adapter_one(key, request):
    process = subprocess.run([build / ('laya-conformance-c' + EXE), model], input=json.dumps(request), capture_output=True,
                             text=True, timeout=180, env=env)
    if process.returncode:
        raise RuntimeError(f'{key}/adapter: {process.stderr[-4000:]}')
    return json.loads(process.stdout)


def adapter_results(requests):
    """The C consumer's results for {key: request} (one open config): one process for all of them (--requests, one
    engine), each file holding the bytes adapter_one() sends on stdin. When that launch fails, every request runs
    alone, so a failure names its own request and error."""
    with tempfile.TemporaryDirectory(prefix='ducktape-native-reference-') as scratch:
        files = []
        for index, request in enumerate(requests.values()):
            files.append(pathlib.Path(scratch) / f'request-{index}.json')
            files[-1].write_text(json.dumps(request), encoding='utf-8')
        if files:
            try:
                process = subprocess.run([build / ('laya-conformance-c' + EXE), model, '--requests', *map(str, files)],
                                         stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180 * len(files), env=env)
                lines = [line for line in process.stdout.splitlines() if line.strip()]
                if process.returncode == 0 and len(lines) == len(files):
                    return {key: json.loads(line) for key, line in zip(requests, lines)}
            except subprocess.TimeoutExpired:
                pass
    return {key: adapter_one(key, request) for key, request in requests.items()}


try:
    fixtures = sorted((ROOT / 'tests/fixtures').glob('*.json'))
    report['fixtures'] = {fixture.name: {'sha256': sha256(fixture), 'cases': []} for fixture in fixtures}
    originals = {}
    for fixture in fixtures:
        body = read_json(fixture)
        for index, request in (enumerate(body) if isinstance(body, list) else [(None, body)]):
            originals[reference_key(fixture.stem, index)] = request
    cases = reference_requests()
    adapters = adapter_results({key: originals[key] for key in cases})
    for key, (name, primitive) in cases.items():
        outputs = {'adapter': adapters[key]}
        process = subprocess.run([build / ('laya-reference' + EXE), model], input=json.dumps(primitive), capture_output=True,
                                 text=True, timeout=180, env=env)
        if process.returncode:
            raise RuntimeError(f'{key}/upstream: {process.stderr[-4000:]}')
        outputs['upstream'] = json.loads(process.stdout)
        adapter, upstream = outputs['adapter'], outputs['upstream']
        answers, reference = comparable_answers(adapter['answers'], upstream['answers'], key, model_info)
        maximum = compare_results(answers, reference, tolerance)
        compare_results(adapter['usage']['input_tokens'], upstream['input_tokens'])
        report['fixtures'][name]['cases'].append({'key': key, 'structured': 'schema' in originals[key],
                                                  'maximum_absolute_error': maximum[0], 'path': maximum[1]})
        print(f'{key}: upstream agreement, maximum error {maximum[0]}', flush=True)
    if not any(value['cases'] for value in report['fixtures'].values()):
        raise RuntimeError('No reference cases ran')
    report['status'] = 'passed'
except Exception as error:
    report['status'] = 'failed'; report['error'] = str(error)
finally:
    a.report.write_text(json.dumps(report, indent=2) + '\n')
if report['status'] != 'passed':
    print(report['error'], file=sys.stderr)
    raise SystemExit(1)
