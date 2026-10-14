#!/usr/bin/env python3
"""Create local native/npm/Rust artifacts. Does not publish, sign or download."""
import argparse
import sbom
import json
import os
import pathlib
import shutil
import subprocess
import re
import tempfile
from release_support import ROOT, check_record, sha256, git, binary_info, source_identity, native_build_identity
from artifact_support import archive_tree

p = argparse.ArgumentParser()
p.add_argument('--build', type=pathlib.Path, default=ROOT / 'build')
p.add_argument('--output', type=pathlib.Path, required=True)
a = p.parse_args()
record = check_record()
version = record['package_version']
epoch = int(os.environ.get('SOURCE_DATE_EPOCH', git(ROOT, 'show', '-s', '--format=%ct', 'HEAD')))
if epoch < 0 or epoch > 4294967295: p.error('SOURCE_DATE_EPOCH outside gzip timestamp range')
build, out = a.build.resolve(), a.output.resolve()
source = source_identity()
try:
    identities = {target: native_build_identity(build, target, binary, source) for target, binary in [
        ('ducktape_laya', 'libducktape_laya.so'), ('ducktape_laya_node', 'ducktape_laya.node')]}
except ValueError as error:
    p.error(str(error))
diagnostics = binary_info(build)
if (diagnostics['package_version'] != version or diagnostics['abi_version'] != record['abi_version'] or
    diagnostics['llama_cpp_commit'] != record['runtime']['commit'] or diagnostics['runtime_patches'] != record['runtime']['patches']):
    p.error('Built library identity differs from release record; rebuild before packaging')
out.mkdir(parents=True, exist_ok=False)
if not (build / 'ducktape_laya.node').is_file():
    p.error('Build the Node addon before packaging')
with tempfile.TemporaryDirectory(prefix='ducktape-pack-') as directory:
    directory = pathlib.Path(directory)
    native = directory / f'ducktape-laya-{version}-linux-x64'
    subprocess.run(['cmake', '--install', str(build), '--prefix', str(native)], check=True, capture_output=True)
    archive_tree(native, out / (native.name + '.tar.gz'), native.name, epoch)
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
    installed_addon = next(native.glob('lib*/ducktape-laya/node/ducktape_laya.node'))
    installed_libdir = installed_addon.parents[2]
    shutil.copy(installed_addon, addon / 'ducktape_laya.node')
    # Copy real bytes under both loader names, avoiding symlink support differences in npm tarballs.
    for name in ['libducktape_laya.so', 'libducktape_laya.so.0']:
        shutil.copy(installed_libdir / 'libducktape_laya.so', addon / name)
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
    cache = {}
    for line in (build / 'CMakeCache.txt').read_text().splitlines():
        match = re.match(r'([^:#]+):[^=]+=(.*)', line)
        if match: cache[match[1]] = match[2]
    isa = {key: value for key, value in cache.items() if key.startswith(('GGML_AVX', 'GGML_FMA', 'GGML_F16C', 'GGML_BMI', 'GGML_SSE', 'GGML_NATIVE'))}
    symbols = subprocess.check_output(['readelf', '--version-info', str(build / 'libducktape_laya.so')], text=True)
    requirements = sorted(set(re.findall(r'Name: ((?:GLIBC|GLIBCXX|CXXABI)_[0-9.]+)', symbols)))
    compiler = subprocess.check_output([cache['CMAKE_CXX_COMPILER'], '--version'], text=True).splitlines()[0]
    sbom.write(out / 'sbom.cdx.json', record)
    inventory = {'schema_version': 2, 'package_version': version, 'packaging_source_commit': git(ROOT, 'rev-parse', 'HEAD'),
        'packaging_source_tracked_dirty': bool(git(ROOT, 'diff', 'HEAD', '--name-only')), 'runtime': record['runtime'],
        'build_source': source, 'build_identities': identities,
        'diagnostics': diagnostics, 'platform': 'linux-x64', 'source_date_epoch': epoch, 'cpu_build_options': isa,
        'toolchain': {'compiler': compiler, 'build_type': cache.get('CMAKE_BUILD_TYPE'), 'cxx_flags': cache.get('CMAKE_CXX_FLAGS'), 'required_runtime_symbol_versions': requirements},
        'reproducibility_scope': 'deterministic packaging of identical build inputs; independent compilation is a separate gate',
        'artifacts': {f.name: {'sha256': sha256(f), 'bytes': f.stat().st_size} for f in sorted(out.iterdir())},
        'components': [{'name': 'ducktape-laya', 'license': 'MIT'}, {'name': 'llama.cpp/ggml', 'license': 'MIT', 'revision': record['runtime']['commit']}, {'name': 'nlohmann-json', 'license': 'MIT'}],
        'status': 'local unsigned development artifacts; not release approval'}
    (out / 'inventory.json').write_text(json.dumps(inventory, indent=2)+'\n')
print(out)
