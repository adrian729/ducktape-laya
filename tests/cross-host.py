#!/usr/bin/env python3
"""Record or compare exact native outputs of one built library across physical CPUs.

`bundle` copies the built library and C conformance consumer into a directory.
`record` runs every fixture through that unchanged bundle on the current host and
stores the raw output bytes with the host CPU identity. `compare` requires two
records of the same bundle, model and fixtures from different CPUs to be
byte-identical. It is evidence for one ISA profile, not for other profiles.
"""
import argparse
import hashlib
import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
FILES = ('libducktape_laya.so', 'laya-conformance-c')
SONAME = 'libducktape_laya.so.1'
MODES = 'file-modes.json'  # the executables of a bundle, for a download that lost the modes

def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()

def cpu_identity():
    fields, flags = {}, []
    for line in pathlib.Path('/proc/cpuinfo').read_text().splitlines():
        key, _, value = line.partition(':')
        key, value = key.strip(), value.strip()
        if key == 'processor' and fields: break
        if key in {'vendor_id', 'cpu family', 'model', 'model name', 'stepping', 'microcode'}:
            fields[key] = value
        elif key == 'flags':
            flags = sorted(value.split())
    fields['flags_sha256'] = hashlib.sha256(' '.join(flags).encode()).hexdigest()
    fields['isa'] = [flag for flag in flags if flag in {'avx', 'avx2', 'fma', 'f16c', 'bmi2', 'sse4_2',
                                                        'avx512f', 'avx512_vnni', 'avx_vnni'}]
    fields['logical_cpus'] = os.cpu_count()
    fields['machine'] = platform.machine()
    return fields

def bundle(args):
    args.output.mkdir(parents=True, exist_ok=False)
    for name in FILES:
        shutil.copy2(args.build / name, args.output / name)
    # The consumer loads the library by its SONAME; keep both names identical.
    shutil.copy2(args.build / 'libducktape_laya.so', args.output / SONAME)
    # CI artifact zips drop file modes; scripts/fetch-ci-evidence.py restores the executables listed here.
    modes = {name: '755' for name in FILES if os.access(args.output / name, os.X_OK)}
    (args.output / MODES).write_text(json.dumps(modes, indent=2) + '\n')

def record(args):
    files = {name: sha256(args.bundle / name) for name in FILES}
    if not (args.bundle / SONAME).is_file():
        shutil.copy2(args.bundle / 'libducktape_laya.so', args.bundle / SONAME)
    if sha256(args.bundle / SONAME) != files['libducktape_laya.so']:
        raise SystemExit(f'{SONAME} differs from libducktape_laya.so')
    fixtures = sorted((ROOT / 'tests/fixtures').glob('*.json'))
    consumer = args.bundle / 'laya-conformance-c'
    if not os.access(consumer, os.X_OK):
        raise SystemExit(f'{consumer} is not executable: an Actions artifact download drops file modes; '
                         f'scripts/fetch-ci-evidence.py restores them from {MODES}, so fetch the bundle with it')
    env = dict(os.environ, LD_LIBRARY_PATH=str(args.bundle.resolve()))
    outputs = {}
    for fixture in fixtures:
        result = subprocess.run([str(args.bundle.resolve() / 'laya-conformance-c'), str(args.model.resolve())],
                                input=fixture.read_bytes(), capture_output=True, env=env, timeout=600)
        if result.returncode:
            raise SystemExit(f'{fixture.name}: {result.stderr.decode(errors="replace")}')
        outputs[fixture.name] = result.stdout.decode()
        print(f'{fixture.name}: recorded', flush=True)
    report = {'schema_version': 1, 'bundle_sha256': files, 'model_sha256': sha256(args.model),
              'fixtures_sha256': {fixture.name: sha256(fixture) for fixture in fixtures},
              'cpu': cpu_identity(), 'kernel': platform.release(), 'outputs': outputs}
    args.report.write_text(json.dumps(report, indent=2) + '\n')

def compare(args):
    first, second = (json.loads(path.read_text()) for path in (args.first, args.second))
    for key in ('bundle_sha256', 'model_sha256', 'fixtures_sha256'):
        if first[key] != second[key]:
            raise SystemExit(f'{key} differs; records are not of the same bundle, model and fixtures')
    if first['cpu'] == second['cpu']:
        raise SystemExit('Both records come from the same CPU identity')
    if not first['outputs'] or first['outputs'].keys() != second['outputs'].keys():
        raise SystemExit('Fixture sets differ or are empty')
    changed = [name for name in first['outputs'] if first['outputs'][name] != second['outputs'][name]]
    summary = {'exact': not changed, 'changed_fixtures': changed, 'fixtures': sorted(first['outputs']),
               'cpus': [first['cpu'], second['cpu']], 'bundle_sha256': first['bundle_sha256']}
    print(json.dumps(summary, indent=2))
    if changed: raise SystemExit(1)

parser = argparse.ArgumentParser(description=__doc__)
commands = parser.add_subparsers(dest='command', required=True)
command = commands.add_parser('bundle')
command.add_argument('--build', type=pathlib.Path, required=True)
command.add_argument('--output', type=pathlib.Path, required=True)
command.set_defaults(run=bundle)
command = commands.add_parser('record')
command.add_argument('--bundle', type=pathlib.Path, required=True)
command.add_argument('--model', type=pathlib.Path, required=True)
command.add_argument('--report', type=pathlib.Path, required=True)
command.set_defaults(run=record)
command = commands.add_parser('compare')
command.add_argument('first', type=pathlib.Path)
command.add_argument('second', type=pathlib.Path)
command.set_defaults(run=compare)
args = parser.parse_args()
args.run(args)
