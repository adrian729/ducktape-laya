#!/usr/bin/env python3
"""Exercise local npm and normalized Rust crate artifacts outside the checkout."""
import argparse
import json
import os
import pathlib
import re
import subprocess
import tarfile
import tempfile
import sys

root = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'scripts'))
from artifact_support import verify_inventory
p = argparse.ArgumentParser()
p.add_argument('artifacts', type=pathlib.Path)
p.add_argument('model', type=pathlib.Path)
a = p.parse_args()
artifacts, model = a.artifacts.resolve(), a.model.resolve()
version = verify_inventory(artifacts)['package_version']
request = (root / 'tests/fixtures/primitive.json').read_bytes()
with tempfile.TemporaryDirectory(prefix='ducktape-artifacts-') as directory:
    directory = pathlib.Path(directory)
    for artifact in list(artifacts.glob('*.crate')) + list(artifacts.glob('*linux-x64.tar.gz')):
        with tarfile.open(artifact) as tar:
            tar.extractall(directory, filter='data')
    native = directory / f'ducktape-laya-{version}-linux-x64'
    subprocess.run(['python3', str(root / 'tests/installed-native.py'), str(native), str(model)], check=True)
    app = directory / 'application'
    app.mkdir()
    (app / 'package.json').write_text('{"private":true}')
    subprocess.run(['npm', 'install', '--offline', '--ignore-scripts', '--no-audit', '--no-fund', str(artifacts / f'ducktape-laya-{version}.tgz')], cwd=app, check=True, capture_output=True)
    addon = app / 'node_modules/ducktape-laya/native/linux-x64/ducktape_laya.node'
    dynamic = subprocess.check_output(['readelf', '-d', str(addon)], text=True)
    for line in dynamic.splitlines():
        if 'RPATH' in line or 'RUNPATH' in line:
            paths = re.search(r'\[(.*?)\]', line).group(1).split(':')
            assert all(path.startswith('$ORIGIN') for path in paths), paths
    script = (root / 'tests/consumers/node.cjs').read_text().replace("require('../../bindings/node/index.cjs')", "require('ducktape-laya')")
    (app / 'test.cjs').write_text(script)
    env = dict(os.environ)
    env.pop('DUCKTAPE_LAYA_ADDON', None)
    env.pop('DUCKTAPE_LAYA_LIB_DIR', None)
    env.pop('LD_LIBRARY_PATH', None)
    node = json.loads(subprocess.check_output(['node', 'test.cjs', str(model)], cwd=app, env=env, input=request))
    (app / 'src').mkdir()
    (app / 'src/main.rs').write_text((root / 'tests/consumers/rust/src/main.rs').read_text())
    safe = directory / f'ducktape-laya-{version}'
    sys = directory / f'ducktape-laya-sys-{version}'
    assert 'path = ' not in (safe / 'Cargo.toml').read_text().split('[dependencies.ducktape-laya-sys]')[1].split('[')[0]
    assert not (safe / 'examples').exists()
    (app / 'Cargo.toml').write_text(f'''[package]
name = "installed-consumer"
version = "0.0.0"
edition = "2021"
[dependencies]
ducktape-laya = {{ path = "{safe}" }}
[patch.crates-io]
ducktape-laya-sys = {{ path = "{sys}" }}
''')
    libdir = next(native.glob('lib*/pkgconfig/ducktape-laya.pc')).parent.parent
    env['PKG_CONFIG_PATH'] = str(libdir / 'pkgconfig')
    env['LD_LIBRARY_PATH'] = str(libdir)
    rust = json.loads(subprocess.check_output(['cargo', 'run', '--offline', '--quiet', '--', str(model)], cwd=app, env=env, input=request))
    assert node == rust
print('Installed npm and Rust crate consumers: exact real-model agreement, no repository paths or inference server')
