#!/usr/bin/env python3
"""Run an installed native consumer in isolated, networkless Linux userspaces."""
import argparse
import json
import os
import pathlib
import subprocess
import tarfile
import tempfile
import sys
root = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'scripts'))
from artifact_support import verify_inventory
from compare_results import compare_results
from release_support import sha256, verify_model
p = argparse.ArgumentParser()
p.add_argument('artifacts', type=pathlib.Path)
p.add_argument('model', type=pathlib.Path)
p.add_argument('rootfs', type=pathlib.Path, nargs='+', help='Locally verified/extracted Ubuntu base directories')
p.add_argument('--report', type=pathlib.Path, required=True)
a = p.parse_args()
artifacts = a.artifacts.resolve()
if a.report.exists(): p.error('Report exists; choose a new path')
manifest = verify_inventory(artifacts)
model_manifest = json.loads((root / 'models/manifest.json').read_text())
verify_model(a.model, model_manifest)
version = manifest['package_version']
request = (root / 'tests/fixtures/primitive.json').read_bytes()
report = {'status': 'running', 'model_sha256': model_manifest['sha256'],
          'artifact_inventory_sha256': sha256(artifacts / 'inventory.json'),
          'fixture_sha256': sha256(root / 'tests/fixtures/primitive.json'), 'scope': 'isolated userspace; shared host kernel and physical CPU', 'artifact_inventory': manifest, 'environments': {}}
try:
    with tempfile.TemporaryDirectory(prefix='ducktape-linux-consumer-') as temp:
        temp = pathlib.Path(temp)
        with tarfile.open(artifacts / f'ducktape-laya-{version}-linux-x64.tar.gz') as archive:
            archive.extractall(temp, filter='data')
        installed = temp / f'ducktape-laya-{version}-linux-x64'
        lib = next(installed.glob('lib*/libducktape_laya.so')).parent
        consumer = temp / 'consumer'
        subprocess.run(['cc', str(root / 'tests/consumers/c.c'), '-I' + str(installed / 'include'), '-L' + str(lib), '-lducktape_laya', '-o', str(consumer)], check=True)
        expected = json.loads(subprocess.check_output([str(consumer), str(a.model.resolve())], input=request, env=dict(os.environ, LD_LIBRARY_PATH=str(lib))))
        for directory in a.rootfs:
            directory = directory.resolve()
            prefix = ['bwrap', '--unshare-all', '--die-with-parent', '--ro-bind', str(directory), '/', '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
                '--ro-bind', str(installed), '/tmp/ducktape', '--ro-bind', str(consumer), '/tmp/consumer', '--ro-bind', str(a.model.resolve()), '/tmp/model.gguf',
                '--clearenv', '--setenv', 'PATH', '/usr/bin:/bin', '--setenv', 'LD_LIBRARY_PATH', '/tmp/ducktape/' + lib.name]
            result = json.loads(subprocess.check_output(prefix + ['/tmp/consumer', '/tmp/model.gguf'], input=request, timeout=180))
            compare_results(expected, result)
            libc = subprocess.check_output(prefix + ['/usr/bin/ldd', '--version'], text=True).splitlines()[0]
            os_release = dict(line.split('=',1) for line in (directory/'etc/os-release').read_text().splitlines() if '=' in line)
            # Roots provisioned by download-userspaces.py all end in /rootfs. The
            # complete path is the key so one image cannot overwrite another's evidence.
            if str(directory) in report['environments']:
                raise ValueError('Duplicate root filesystem: ' + str(directory))
            provenance = directory.parent / 'provenance.json'
            report['environments'][str(directory)] = {'status': 'passed', 'os_release': os_release,
                'image_provenance': json.loads(provenance.read_text()) if provenance.is_file() else None,
                'libc': libc, 'result': result}
            print(str(directory) + ': installed artifact, exact real-model agreement', flush=True)
    report['status'] = 'passed'
except Exception as error:
    report['status'] = 'failed'
    report['error'] = str(error)
    raise
finally:
    a.report.write_text(json.dumps(report, indent=2) + '\n')
