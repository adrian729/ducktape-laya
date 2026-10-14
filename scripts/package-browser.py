#!/usr/bin/env python3
"""Assemble a local browser package; models and test references are excluded."""
import argparse
import sbom
import json
import pathlib
import shutil
import subprocess
import tempfile
from release_support import ROOT, check_record, read_json, sha256, git
p = argparse.ArgumentParser()
p.add_argument('--wasm', type=pathlib.Path, required=True)
p.add_argument('--webgpu', type=pathlib.Path, required=True)
p.add_argument('--output', type=pathlib.Path, required=True)
a = p.parse_args()
record = check_record()
package_info = read_json(ROOT / 'bindings/browser/package.json')
if package_info['version'] != record['package_version']: p.error('Browser package version mismatch')
out = a.output.resolve(); out.mkdir(parents=True, exist_ok=False)
with tempfile.TemporaryDirectory(prefix='ducktape-browser-pack-') as directory:
    package = pathlib.Path(directory)
    for name in ['package.json', 'index.mjs', 'index.d.ts', 'protocol.d.ts', 'worker.mjs']:
        shutil.copy(ROOT / 'bindings/browser' / name, package / name) # follows shared type symlink
    for name in ['LICENSE', 'THIRD_PARTY_NOTICES.md']:
        shutil.copy(ROOT / name, package / name)
    shutil.copytree(ROOT / 'licenses', package / 'licenses')
    shutil.copy(ROOT / 'release/browser-toolchain.json', package / 'toolchain.json')
    assets, identities = {}, {}
    for backend, build in [('wasm', a.wasm.resolve()), ('webgpu', a.webgpu.resolve())]:
        identity = read_json(build / 'laya.build.json')
        toolchain = read_json(ROOT / 'release/browser-toolchain.json')
        if (identity['package_version'] != record['package_version'] or identity['llama_cpp_commit'] != record['runtime']['commit'] or
            identity['runtime_patches'] != record['runtime']['patches'] or toolchain['sdk_version'] not in identity['toolchain'] or toolchain['emscripten_commit'] not in identity['toolchain']):
            p.error(backend + ': built identity differs from release/toolchain record; rebuild first')
        for name in ['laya.mjs', 'laya.wasm']:
            if sha256(build / name) != identity['assets'][name]: p.error(backend + ': asset hash differs from completed build record')
        identities[backend] = identity
        destination = package / backend; destination.mkdir()
        shutil.copy(build / 'laya.build.json', destination / 'laya.build.json')
        for name in ['laya.mjs', 'laya.wasm']:
            shutil.copy(build / name, destination / name)
            assets[backend + '/' + name] = sha256(destination / name)
    subprocess.run(['npm', 'pack', '--ignore-scripts', '--pack-destination', str(out)], cwd=package, check=True, capture_output=True)
    sbom.write(out / 'sbom.cdx.json', record, browser=True)
    inventory = {'source_commit': git(ROOT, 'rev-parse', 'HEAD'), 'source_tracked_dirty': bool(git(ROOT, 'diff', 'HEAD', '--name-only')),
        'runtime': record['runtime'], 'toolchain': read_json(ROOT / 'release/browser-toolchain.json'),
        'build_identities': identities, 'assets': assets, 'artifacts': {f.name: sha256(f) for f in out.iterdir()},
        'status': 'experimental; cross-backend numeric gate remains failed; see docs/BROWSER.md'}
    (out / 'inventory.json').write_text(json.dumps(inventory, indent=2) + '\n')
print(out)
