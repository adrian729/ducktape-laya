#!/usr/bin/env python3
"""Rehearse upgrade and rollback of installed native packages outside the checkout.

Installs retained last-known-good (LKG) Linux x86-64 packages and the candidate (a
single-platform or multi-platform package directory) side by
side, switches an application's `current` link LKG -> candidate -> LKG, and runs
one C consumer at each step. Rollback must restore the LKG library identity and
byte-identical outputs on every fixture. When both sides have the same ABI, one
consumer built once must work with both (ABI compatibility in both directions).
Across an ABI break (allowed before 1.0, see docs/COMPATIBILITY.md) an
application must rebuild, so each side runs a consumer built from its own
source commit against its own headers.
"""
import re
import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tarfile
import tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
from artifact_support import platform_section, verify_inventory
from release_support import ROOT, read_json, sha256, verify_model

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('previous', type=pathlib.Path, help='Retained LKG package directory with inventory.json')
p.add_argument('candidate', type=pathlib.Path, help='Candidate package directory with inventory.json')
p.add_argument('model', type=pathlib.Path)
p.add_argument('--report', type=pathlib.Path, required=True)
a = p.parse_args()
if a.report.exists(): p.error('Report exists; choose a new path')
manifest = read_json(ROOT / 'models/manifest.json')
verify_model(a.model, manifest)
fixtures = sorted((ROOT / 'tests/fixtures').glob('*.json'))
report = {'schema_version': 1, 'status': 'running', 'model_sha256': manifest['sha256'],
          'previous_inventory_sha256': sha256(a.previous / 'inventory.json'),
          'candidate_inventory_sha256': sha256(a.candidate / 'inventory.json'),
          'fixtures_sha256': {f.name: sha256(f) for f in fixtures}, 'steps': []}
# Each side runs the fixtures of its own source commit: an older release need not
# accept requests added later. Rollback compares the LKG side with itself.
try:
    inventories = {'previous': verify_inventory(a.previous), 'candidate': verify_inventory(a.candidate)}
    report['previous_source_commit'] = inventories['previous']['packaging_source_commit']
    report['previous_version'] = inventories['previous']['package_version']
    report['candidate_version'] = inventories['candidate']['package_version']
    report['candidate_source_commit'] = inventories['candidate']['packaging_source_commit']
    with tempfile.TemporaryDirectory(prefix='ducktape-rollback-') as temp:
        temp = pathlib.Path(temp)
        roots = {}
        for label, directory in (('previous', a.previous), ('candidate', a.candidate)):
            version = inventories[label]['package_version']
            with tarfile.open(directory / f'ducktape-laya-{version}-linux-x64.tar.gz') as archive:
                archive.extractall(temp / label, filter='data')
            roots[label] = next((temp / label).iterdir())
        abi = {label: int(re.search(r'#define DTL_ABI_VERSION (\d+)u', (root / 'include/ducktape_laya.h').read_text())[1])
               for label, root in roots.items()}
        report['abi_versions'] = abi
        report['consumer'] = 'shared' if abi['previous'] == abi['candidate'] else 'per_side'
        current = temp / 'current'
        def switch(label):
            if current.is_symlink(): current.unlink()
            current.symlink_to(roots[label], target_is_directory=True)
        def libdir():
            return next(current.glob('lib*/libducktape_laya.so')).parent
        def build(label, source):
            switch(label)
            binary = temp / f'consumer-{label}'
            subprocess.run(['cc', '-x', 'c', '-', f'-I{current / "include"}', f'-L{libdir()}', '-lducktape_laya',
                            '-o', str(binary)], input=source, check=True)
            return binary
        current_source = (ROOT / 'tests/consumers/c.c').read_bytes()
        names = subprocess.check_output(['git', '-C', str(ROOT), 'ls-tree', '--name-only',
                                         f'{report["previous_source_commit"]}:tests/fixtures'], text=True).split()
        inputs = {'candidate': {f.name: f.read_bytes() for f in fixtures},
                  'previous': {name: subprocess.check_output(['git', '-C', str(ROOT), 'show',
                                                              f'{report["previous_source_commit"]}:tests/fixtures/{name}'])
                               for name in names if name.endswith('.json')}}
        if report['consumer'] == 'shared':
            consumers = dict.fromkeys(roots, build('previous', current_source))
        else:
            previous_source = subprocess.check_output(['git', '-C', str(ROOT), 'show',
                                                       f'{report["previous_source_commit"]}:tests/consumers/c.c'])
            consumers = {'previous': build('previous', previous_source), 'candidate': build('candidate', current_source)}
        def step(label):
            switch(label)
            consumer = consumers[label]
            lib = libdir()
            env = dict(os.environ, LD_LIBRARY_PATH=str(lib))
            outputs = {name: subprocess.run([str(consumer), str(a.model.resolve())], input=body, env=env,
                                              capture_output=True, check=True, timeout=600).stdout.decode()
                       for name, body in inputs[label].items()}
            library = sha256(lib / 'libducktape_laya.so')
            section = platform_section(inventories[label], 'linux-x64')
            # Libraries are shipped stripped from 0.3.0 on (the build identity is the unstripped build); 0.2.0 and earlier shipped the build itself.
            expected = (section.get('toolchain', {}).get('strip', {}).get('shipped_sha256', {}).get('ducktape_laya')
                        or section['build_identities']['ducktape_laya']['binary_sha256'])
            if library != expected: raise AssertionError(f'{label}: installed library is not the inventoried library')
            info = subprocess.run([sys.executable, '-c', 'import json,sys; sys.path.insert(0, sys.argv[2]); '
                                   'from release_support import binary_info; print(json.dumps(binary_info(sys.argv[1])))',
                                   str(lib), str(ROOT / 'scripts')], capture_output=True, text=True, check=True)
            diagnostics = json.loads(info.stdout)
            report['steps'].append({'step': len(report['steps']) + 1, 'installed': label, 'library_sha256': library,
                                    'runtime_patches': [patch['path'] for patch in diagnostics['runtime_patches']],
                                    'outputs_sha256': {name: hashlib.sha256(text.encode()).hexdigest() for name, text in outputs.items()}})
            return outputs
        before = step('previous')
        step('candidate')
        after = step('previous')
        changed = [name for name in before if before[name] != after[name]]
        if changed: raise AssertionError(f'rollback outputs differ: {changed}')
        if report['steps'][0]['library_sha256'] != report['steps'][2]['library_sha256']:
            raise AssertionError('rollback did not restore the LKG library')
    report['status'] = 'passed'
except Exception as error:
    report['status'] = 'failed'
    report['error'] = str(error)
finally:
    a.report.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({'status': report['status'], 'report': str(a.report)}))
raise SystemExit(report['status'] != 'passed')
