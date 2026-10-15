#!/usr/bin/env python3
"""Compare every fixture request with upstream primitive processing in-process.

Structured requests are compared through their schema-derived primitive questions."""
import argparse
import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
from compare_results import compare_results
from release_support import ROOT, check_record, read_json, sha256, verify_model, git
from reference_requests import reference_key, reference_requests

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('model', type=pathlib.Path)
p.add_argument('--build', type=pathlib.Path, default=ROOT / 'build')
p.add_argument('--report', type=pathlib.Path, required=True)
a = p.parse_args()
if a.report.exists(): p.error('Report exists; choose a new path')
record = check_record()
manifest = read_json(ROOT / record['models'][0])
verify_model(a.model, manifest)
build, model = a.build.resolve(), a.model.resolve()
policy = read_json(ROOT / 'release/qualification.json')
tolerance = policy['budgets']['native_upstream_absolute_tolerance']
report = {'schema_version': 1, 'status': 'not_run', 'source_commit': git(ROOT, 'rev-parse', 'HEAD'),
          'source_tracked_dirty': bool(git(ROOT, 'diff', 'HEAD', '--name-only')), 'runtime': record['runtime'],
          'settings': {'threads': 4}, 'model_sha256': manifest['sha256'], 'absolute_tolerance': tolerance,
          'binaries': {name: sha256(build / name) for name in ['laya-reference', 'laya-conformance-c', 'libducktape_laya.so']},
          'reference_source_sha256': sha256(ROOT / 'tests/upstream-reference.cpp'), 'fixtures': {}}
env = dict(os.environ, LD_LIBRARY_PATH=str(build))
try:
    fixtures = sorted((ROOT / 'tests/fixtures').glob('*.json'))
    report['fixtures'] = {fixture.name: {'sha256': sha256(fixture), 'cases': []} for fixture in fixtures}
    originals = {}
    for fixture in fixtures:
        body = read_json(fixture)
        for index, request in (enumerate(body) if isinstance(body, list) else [(None, body)]):
            originals[reference_key(fixture.stem, index)] = request
    for key, (name, primitive) in reference_requests().items():
        outputs = {}
        for label, binary, request in [('adapter', 'laya-conformance-c', originals[key]),
                                       ('upstream', 'laya-reference', primitive)]:
            process = subprocess.run([build / binary, model], input=json.dumps(request), capture_output=True,
                                     text=True, timeout=180, env=env)
            if process.returncode:
                raise RuntimeError(f'{key}/{label}: {process.stderr[-4000:]}')
            outputs[label] = json.loads(process.stdout)
        adapter, upstream = outputs['adapter'], outputs['upstream']
        maximum = compare_results(adapter['answers'], upstream['answers'], tolerance)
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
