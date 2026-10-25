#!/usr/bin/env python3
"""Assemble a local browser package; models and test references are excluded."""
import argparse
import sbom
import json
import pathlib
import shutil
import subprocess
import tempfile
from release_support import ROOT, check_record, read_json, sha256, git, browser_build_identity, source_identity
p = argparse.ArgumentParser()
p.add_argument('--wasm', type=pathlib.Path, required=True)
p.add_argument('--webgpu', type=pathlib.Path, required=True)
p.add_argument('--output', type=pathlib.Path, required=True)
a = p.parse_args()
record = check_record()
source = source_identity()
package_info = read_json(ROOT / 'bindings/browser/package.json')
if package_info['version'] != record['package_version']: p.error('Browser package version mismatch')
def refuse_development_build(build, backend):
    """Development settings never reach a package: DUCKTAPE_DEV_* must be OFF in the build's CMake cache (they are
    inert for Emscripten, but a build configured with them is not the one scripts/build-browser.py makes)."""
    cache = build / 'CMakeCache.txt'
    if not cache.is_file():
        return
    for line in cache.read_text(errors='replace').splitlines():
        name, _, value = line.partition('=')
        name = name.split(':')[0]
        if name in ('DUCKTAPE_DEV_BUILD', 'DUCKTAPE_DEV_PCH') and value.strip().upper() not in ('OFF', '0', 'FALSE', 'NO', 'N', ''):
            p.error(f'{backend}: {name} is {value.strip()!r} in {cache}; rebuild with scripts/build-browser.py')

# Check both builds before creating the output, so a rejected build leaves nothing behind. browser_build_identity
# also refuses a build compiled through a compiler launcher (a rehearsal build: laya.build.json compiler_launcher).
for backend, build in [('wasm', a.wasm.resolve()), ('webgpu', a.webgpu.resolve())]:
    refuse_development_build(build, backend)
    try:
        browser_build_identity(build, backend, record, read_json(ROOT / 'release/browser-toolchain.json'), source)
    except ValueError as error:
        p.error(str(error))
out = a.output.resolve(); out.mkdir(parents=True, exist_ok=False)
with tempfile.TemporaryDirectory(prefix='ducktape-browser-pack-') as directory:
    package = pathlib.Path(directory)
    for name in ['package.json', 'README.md', 'index.mjs', 'index.d.ts', 'protocol.d.ts', 'worker.mjs']:
        shutil.copy(ROOT / 'bindings/browser' / name, package / name)
    for name in ['LICENSE', 'THIRD_PARTY_NOTICES.md']:
        shutil.copy(ROOT / name, package / name)
    shutil.copytree(ROOT / 'licenses', package / 'licenses')
    shutil.copy(ROOT / 'release/browser-toolchain.json', package / 'toolchain.json')
    assets, identities = {}, {}
    for backend, build in [('wasm', a.wasm.resolve()), ('webgpu', a.webgpu.resolve())]:
        toolchain = read_json(ROOT / 'release/browser-toolchain.json')
        try:
            identity = browser_build_identity(build, backend, record, toolchain, source)
        except ValueError as error:
            p.error(str(error))
        identities[backend] = identity
        destination = package / backend; destination.mkdir()
        shutil.copy(build / 'laya.build.json', destination / 'laya.build.json')
        for name in ['laya.mjs', 'laya.wasm']:
            shutil.copy(build / name, destination / name)
            assets[backend + '/' + name] = sha256(destination / name)
    subprocess.run(['npm', 'pack', '--ignore-scripts', '--pack-destination', str(out)], cwd=package, check=True, capture_output=True)
    sbom.write(out / 'sbom.cdx.json', record, browser=True)
    inventory = {'schema_version': 2, 'packaging_source_commit': git(ROOT, 'rev-parse', 'HEAD'),
        'packaging_source_tracked_dirty': bool(git(ROOT, 'diff', 'HEAD', '--name-only')), 'build_source': source,
        'runtime': record['runtime'], 'toolchain': read_json(ROOT / 'release/browser-toolchain.json'),
        'build_identities': identities, 'assets': assets, 'artifacts': {f.name: sha256(f) for f in out.iterdir()},
        'status': 'experimental; distinct numerical profiles; full qualification pending; see docs/BROWSER.md'}
    (out / 'inventory.json').write_text(json.dumps(inventory, indent=2) + '\n')
print(out)
