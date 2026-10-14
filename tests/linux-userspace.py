#!/usr/bin/env python3
"""Run an installed native consumer in isolated, networkless Linux userspaces."""
import argparse
import json
import os
import pathlib
import subprocess
import tarfile
import tempfile
root = pathlib.Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument('artifacts', type=pathlib.Path)
p.add_argument('model', type=pathlib.Path)
p.add_argument('rootfs', type=pathlib.Path, nargs='+', help='Locally verified/extracted Ubuntu base directories')
p.add_argument('--report', type=pathlib.Path, required=True)
a = p.parse_args()
artifacts = a.artifacts.resolve()
manifest = json.loads((artifacts / 'inventory.json').read_text())
version = manifest['package_version']
request = (root / 'tests/fixtures/primitive.json').read_bytes()
report = {'scope': 'isolated userspace; shared host kernel and physical CPU', 'artifact_inventory': manifest, 'environments': {}}
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
        assert result == expected, directory
        libc = subprocess.check_output(prefix + ['/usr/bin/ldd', '--version'], text=True).splitlines()[0]
        os_release = dict(line.split('=',1) for line in (directory/'etc/os-release').read_text().splitlines() if '=' in line)
        report['environments'][directory.name] = {'status': 'passed', 'os_release': os_release, 'libc': libc, 'result': result}
        print(directory.name + ': installed artifact, exact real-model agreement', flush=True)
a.report.write_text(json.dumps(report, indent=2) + '\n')
