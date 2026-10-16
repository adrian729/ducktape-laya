#!/usr/bin/env python3
"""Rehearse upgrade and rollback of installed native packages outside the checkout.

Installs retained last-known-good (LKG) Linux packages and the candidate side by
side, switches an application's `current` link LKG -> candidate -> LKG, and runs
one unchanged C consumer at each step. Rollback must restore the LKG library
identity and byte-identical outputs on every fixture; the consumer built once
must work with both (ABI compatibility in both directions).
"""
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
from artifact_support import verify_inventory
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
try:
    inventories = {'previous': verify_inventory(a.previous), 'candidate': verify_inventory(a.candidate)}
    report['previous_source_commit'] = inventories['previous']['packaging_source_commit']
    report['candidate_source_commit'] = inventories['candidate']['packaging_source_commit']
    with tempfile.TemporaryDirectory(prefix='ducktape-rollback-') as temp:
        temp = pathlib.Path(temp)
        roots = {}
        for label, directory in (('previous', a.previous), ('candidate', a.candidate)):
            version = inventories[label]['package_version']
            with tarfile.open(directory / f'ducktape-laya-{version}-linux-x64.tar.gz') as archive:
                archive.extractall(temp / label, filter='data')
            roots[label] = next((temp / label).iterdir())
        current = temp / 'current'
        def switch(label):
            if current.is_symlink(): current.unlink()
            current.symlink_to(roots[label], target_is_directory=True)
        def libdir():
            return next(current.glob('lib*/libducktape_laya.so')).parent
        switch('previous')
        consumer = temp / 'consumer'
        subprocess.run(['cc', str(ROOT / 'tests/consumers/c.c'), f'-I{current / "include"}', f'-L{libdir()}',
                        '-lducktape_laya', '-o', str(consumer)], check=True)
        def step(label):
            switch(label)
            lib = libdir()
            env = dict(os.environ, LD_LIBRARY_PATH=str(lib))
            outputs = {f.name: subprocess.run([str(consumer), str(a.model.resolve())], input=f.read_bytes(), env=env,
                                              capture_output=True, check=True, timeout=600).stdout.decode() for f in fixtures}
            library = sha256(lib / 'libducktape_laya.so')
            expected = inventories[label]['build_identities']['ducktape_laya']['binary_sha256']
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
