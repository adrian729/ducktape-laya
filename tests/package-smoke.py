#!/usr/bin/env python3
"""Load each local package outside the checkout without a model.

The native tarball links and runs a C program, the npm package loads its addon,
and the wheel and the Maven jar load their bundled library; each must report the
record's ABI.
tests/installed-packages.py runs the real-model checks."""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import tarfile
import tempfile

root = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'scripts'))
from artifact_support import verify_inventory
from release_support import read_json

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('artifacts', type=pathlib.Path)
a = p.parse_args()
artifacts = a.artifacts.resolve()
inventory = verify_inventory(artifacts)
version, platform_dir = inventory['package_version'], inventory['platform']
abi = read_json(root / 'release/compatibility.json')['abi_version']
env = {k: v for k, v in os.environ.items() if k not in ('DUCKTAPE_LAYA_ADDON', 'DUCKTAPE_LAYA_LIB_DIR', 'LD_LIBRARY_PATH', 'PYTHONPATH')}
with tempfile.TemporaryDirectory(prefix='ducktape-smoke-') as directory:
    directory = pathlib.Path(directory)
    with tarfile.open(artifacts / f'ducktape-laya-{version}-{platform_dir}.tar.gz') as tar:
        tar.extractall(directory, filter='data')
    native = directory / f'ducktape-laya-{version}-{platform_dir}'
    libdir = next(native.glob('lib*/libducktape_laya.so')).parent
    program = directory / 'abi'
    subprocess.run(['cc', '-x', 'c', '-', f'-I{native / "include"}', f'-L{libdir}', '-lducktape_laya', '-o', str(program)],
                   input=b'#include "ducktape_laya.h"\n#include <stdio.h>\nint main(void) { printf("%u", dtl_abi_version()); }\n',
                   check=True)
    reported = {'native': subprocess.check_output([str(program)], env=dict(env, LD_LIBRARY_PATH=str(libdir)), text=True)}
    app = directory / 'application'
    app.mkdir()
    (app / 'package.json').write_text('{"private":true}')
    subprocess.run(['npm', 'install', '--offline', '--ignore-scripts', '--no-audit', '--no-fund',
                    str(artifacts / f'ducktape-laya-{version}.tgz')], cwd=app, check=True, capture_output=True)
    reported['npm'] = subprocess.check_output(['node', '-e', "process.stdout.write(String(require('ducktape-laya').Laya.runtimeInfo().abi_version))"],
                                              cwd=app, env=env, text=True)
    wheel, = artifacts.glob(f'ducktape_laya-{version}-*.whl')
    venv = directory / 'venv'
    subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
    subprocess.run([str(venv / 'bin/pip'), 'install', '--quiet', '--no-index', str(wheel)], check=True)
    reported['wheel'] = subprocess.check_output([str(venv / 'bin/python'), '-c', 'import ducktape_laya; print(ducktape_laya.runtime_info()["abi_version"], end="")'],
                                                cwd=directory, env=env, text=True)
    # The Maven jar extracts its bundled library when no library is configured.
    probe = directory / 'Probe.java'
    probe.write_text('public class Probe { public static void main(String[] a) {\n'
                     '  String info = io.github.adrian729.laya.Laya.runtimeInfo();\n'
                     '  System.out.print(info.replaceAll("(?s).*.abi_version.:[ ]*([0-9]+).*", "$1")); } }\n')
    reported['jar'] = subprocess.check_output(['java', '--enable-native-access=ALL-UNNAMED', '-cp',
                                               str(artifacts / f'ducktape-laya-{version}.jar'), str(probe)],
                                              cwd=directory, env=env, text=True)
bad = {name: value for name, value in reported.items() if value != str(abi)}
if bad:
    raise SystemExit(f'Packages report the wrong ABI (expected {abi}): {bad}')
print(json.dumps({'abi_version': abi, 'packages': sorted(reported)}))
