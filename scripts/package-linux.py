#!/usr/bin/env python3
"""Create local native/npm/Rust artifacts. Does not publish, sign or download."""
import argparse
import json
import os
import pathlib
import shutil
import subprocess
import tarfile
import tempfile
from release_support import ROOT, check_record, sha256, git

p = argparse.ArgumentParser()
p.add_argument('--build', type=pathlib.Path, default=ROOT / 'build')
p.add_argument('--output', type=pathlib.Path, required=True)
a = p.parse_args()
record = check_record()
version = record['package_version']
build, out = a.build.resolve(), a.output.resolve()
out.mkdir(parents=True, exist_ok=False)
if not (build / 'ducktape_laya.node').is_file():
    p.error('Build the Node addon before packaging')
with tempfile.TemporaryDirectory(prefix='ducktape-pack-') as directory:
    directory = pathlib.Path(directory)
    native = directory / f'ducktape-laya-{version}-linux-x64'
    subprocess.run(['cmake', '--install', str(build), '--prefix', str(native)], check=True, capture_output=True)
    with tarfile.open(out / (native.name + '.tar.gz'), 'w:gz') as tar:
        tar.add(native, arcname=native.name)
    package = directory / 'npm'
    package.mkdir()
    for name in ['package.json', 'index.cjs', 'index.d.ts', 'worker.cjs', 'resolve-addon.cjs']:
        shutil.copy(ROOT / 'bindings/node' / name, package / name)
    for name in ['LICENSE', 'THIRD_PARTY_NOTICES.md']:
        shutil.copy(ROOT / name, package / name)
    shutil.copytree(ROOT / 'licenses', package / 'licenses')
    shutil.copy(ROOT / 'release/compatibility.json', package / 'compatibility.json')
    shutil.copy(ROOT / 'models/manifest.json', package / 'model-manifest.json')
    addon = package / 'native/linux-x64'
    addon.mkdir(parents=True)
    shutil.copy(build / 'ducktape_laya.node', addon / 'ducktape_laya.node')
    # Copy real bytes under both loader names, avoiding symlink support differences in npm tarballs.
    for name in ['libducktape_laya.so', 'libducktape_laya.so.0']:
        shutil.copy(build / 'libducktape_laya.so', addon / name)
    subprocess.run(['npm', 'pack', '--ignore-scripts', '--pack-destination', str(out)], cwd=package, check=True, capture_output=True)
    env = dict(os.environ, DUCKTAPE_LAYA_LIB_DIR=str(build))
    workspace = ROOT / 'bindings/rust/Cargo.toml'
    for name in ['ducktape-laya-sys', 'ducktape-laya']:
        command = ['cargo', 'package', '--manifest-path', str(workspace), '-p', name, '--locked', '--offline', '--allow-dirty', '--no-verify']
        # Permit packaging the unpublished sibling locally. Cargo normalizes the
        # actual .crate dependency to its version; no checkout path is shipped.
        command += ['--config', f'patch.crates-io.ducktape-laya-sys.path="{ROOT / "bindings/rust/ducktape-laya-sys"}"']
        subprocess.run(command, cwd=ROOT, env=env, check=True, capture_output=True)
        shutil.copy(ROOT / f'bindings/rust/target/package/{name}-{version}.crate', out)
    inventory = {'schema_version': 1, 'package_version': version, 'source_commit': git(ROOT, 'rev-parse', 'HEAD'),
        'source_tracked_dirty': bool(git(ROOT, 'diff', 'HEAD', '--name-only')), 'runtime': record['runtime'],
        'platform': 'linux-x64', 'cpu_requirement': 'AVX2 FMA F16C BMI2 SSE4.2',
        'artifacts': {f.name: {'sha256': sha256(f), 'bytes': f.stat().st_size} for f in sorted(out.iterdir())},
        'components': [{'name': 'ducktape-laya', 'license': 'MIT'}, {'name': 'llama.cpp/ggml', 'license': 'MIT', 'revision': record['runtime']['commit']}, {'name': 'nlohmann-json', 'license': 'MIT'}],
        'status': 'local unsigned development artifacts; not release approval'}
    (out / 'inventory.json').write_text(json.dumps(inventory, indent=2)+'\n')
print(out)
