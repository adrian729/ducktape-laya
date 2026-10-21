#!/usr/bin/env python3
"""Create local native/npm/Rust/Python artifacts. Does not publish, sign or download."""
import argparse
import sbom
import json
import os
import pathlib
import shutil
import subprocess
import re
import tempfile
from release_support import ROOT, host_platform, check_record, sha256, git, binary_info, source_identity, native_build_identity, require_runtime_identity, require_uninstrumented_binary
from artifact_support import write_zip, archive_tree
import java_package
import python_wheel

p = argparse.ArgumentParser()
p.add_argument('--build', type=pathlib.Path, default=ROOT / 'build')
p.add_argument('--output', type=pathlib.Path, required=True)
a = p.parse_args()
def normalize_nupkg(source, destination, epoch):
    """Rewrite a nupkg deterministically: NuGet stamps entries with the pack time and
    gives relationships random IDs. Entry order and contents are otherwise kept."""
    import hashlib
    import zipfile
    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        files = {name: (archive.read(name), False) for name in names}
    rels = files['_rels/.rels'][0].decode()
    rels = re.sub(r'Target="([^"]+)" Id="[^"]+"',
                  lambda m: f'Target="{m[1]}" Id="R{hashlib.sha256(m[1].encode()).hexdigest()[:16].upper()}"', rels)
    files['_rels/.rels'] = (rels.encode(), False)
    write_zip(destination, files, epoch, names)

record = check_record()
version = record['package_version']
platform_dir = host_platform()[0]
epoch = int(os.environ.get('SOURCE_DATE_EPOCH', git(ROOT, 'show', '-s', '--format=%ct', 'HEAD')))
if epoch < 0 or epoch > 4294967295: p.error('SOURCE_DATE_EPOCH outside gzip timestamp range')
build, out = a.build.resolve(), a.output.resolve()
source = source_identity()
try:
    identities = {target: native_build_identity(build, target, binary, source) for target, binary in [
        ('ducktape_laya', 'libducktape_laya.so'), ('ducktape_laya_node', 'ducktape_laya.node')]}
except ValueError as error:
    p.error(str(error))
try:
    for name in ['libducktape_laya.so', 'ducktape_laya.node']:
        require_uninstrumented_binary(build / name)
except ValueError as error:
    p.error(str(error))
diagnostics = binary_info(build)
try:
    require_runtime_identity(diagnostics, record)
except ValueError as error:
    p.error(str(error))
out.mkdir(parents=True, exist_ok=False)
if not (build / 'ducktape_laya.node').is_file():
    p.error('Build the Node addon before packaging')
with tempfile.TemporaryDirectory(prefix='ducktape-pack-') as directory:
    directory = pathlib.Path(directory)
    native = directory / f'ducktape-laya-{version}-{platform_dir}'
    subprocess.run(['cmake', '--install', str(build), '--prefix', str(native)], check=True, capture_output=True)
    archive_tree(native, out / (native.name + '.tar.gz'), native.name, epoch)
    package = directory / 'npm'
    package.mkdir()
    for name in ['package.json', 'README.md', 'index.cjs', 'index.d.ts', 'worker.cjs', 'resolve-addon.cjs']:
        shutil.copy(ROOT / 'bindings/node' / name, package / name)
    # The package holds one platform's addon; declare that CPU so npm refuses others.
    cpu = platform_dir.split('-', 1)[1]
    manifest = (package / 'package.json').read_text()
    if cpu != 'x64':
        manifest = re.sub(r'"cpu": \[\s*"x64"\s*\]', f'"cpu": [\n    "{cpu}"\n  ]', manifest)
        if f'"{cpu}"' not in manifest: p.error('Could not set the npm package cpu field')
        (package / 'package.json').write_text(manifest)
    for name in ['LICENSE', 'THIRD_PARTY_NOTICES.md']:
        shutil.copy(ROOT / name, package / name)
    shutil.copytree(ROOT / 'licenses', package / 'licenses')
    shutil.copy(ROOT / 'release/compatibility.json', package / 'compatibility.json')
    shutil.copy(ROOT / 'models/manifest.json', package / 'model-manifest.json')
    shutil.copy(ROOT / 'scripts/download-model.py', package / 'download-model.py')
    addon = package / 'native' / platform_dir
    addon.mkdir(parents=True)
    installed_addon = next(native.glob('lib*/ducktape-laya/node/ducktape_laya.node'))
    installed_libdir = installed_addon.parents[2]
    shutil.copy(installed_addon, addon / 'ducktape_laya.node')
    # Copy real bytes under both loader names, avoiding symlink support differences in npm tarballs.
    for name in ['libducktape_laya.so', 'libducktape_laya.so.1']:
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
    python_wheel.build(out, version, installed_libdir / 'libducktape_laya.so', epoch)
    java_package.build(out, version, installed_libdir / 'libducktape_laya.so', epoch)
    # NuGet: the library at runtimes/<platform>/native/, packed from source like the crates.
    nuget = directory / 'nuget'
    subprocess.run(['dotnet', 'pack', str(ROOT / 'bindings/dotnet/DucktapeLaya/DucktapeLaya.csproj'), '-c', 'Release', '-m:1',
                    '-o', str(nuget), f'-p:DucktapeLayaNativeLibrary={installed_libdir / "libducktape_laya.so"}',
                    f'-p:DucktapeLayaRuntime={platform_dir}', '-p:ContinuousIntegrationBuild=true', '--nologo'],
                   cwd=ROOT, env=dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_NOLOGO='1'), check=True, capture_output=True)
    normalize_nupkg(nuget / f'DucktapeLaya.{version}.nupkg', out / f'DucktapeLaya.{version}.nupkg', epoch)
    cache = {}
    for line in (build / 'CMakeCache.txt').read_text().splitlines():
        match = re.match(r'([^:#]+):[^=]+=(.*)', line)
        if match: cache[match[1]] = match[2]
    isa = {key: value for key, value in cache.items() if key.startswith(('GGML_AVX', 'GGML_FMA', 'GGML_F16C', 'GGML_BMI', 'GGML_SSE', 'GGML_NATIVE', 'GGML_CPU_ARM_ARCH'))}
    symbols = subprocess.check_output(['readelf', '--version-info', str(build / 'libducktape_laya.so')], text=True)
    requirements = sorted(set(re.findall(r'Name: ((?:GLIBC|GLIBCXX|CXXABI)_[0-9.]+)', symbols)))
    # The wheel tag and the declared floors must cover what the library actually needs.
    def newest(prefix):
        return max((tuple(map(int, r[len(prefix):].split('.'))) for r in requirements if r.startswith(prefix)), default=())
    platform = json.loads((ROOT / 'release/qualification.json').read_text())['platforms']['native']
    wheel_glibc = tuple(map(int, re.search(r'manylinux_(\d+)_(\d+)', python_wheel.TAG).groups()))
    for prefix, limit in (('GLIBC_', wheel_glibc), ('GLIBC_', tuple(map(int, platform['maximum_glibc'].split('.')))),
                          ('GLIBCXX_', tuple(map(int, platform['maximum_glibcxx'].split('.'))))):
        if newest(prefix) > limit:
            raise SystemExit(f'Library needs {prefix}{".".join(map(str, newest(prefix)))}, above the declared {".".join(map(str, limit))}')
    compiler = subprocess.check_output([cache['CMAKE_CXX_COMPILER'], '--version'], text=True).splitlines()[0]
    sbom.write(out / 'sbom.cdx.json', record)
    inventory = {'schema_version': 2, 'package_version': version, 'packaging_source_commit': git(ROOT, 'rev-parse', 'HEAD'),
        'packaging_source_tracked_dirty': bool(git(ROOT, 'diff', 'HEAD', '--name-only')), 'runtime': record['runtime'],
        'build_source': source, 'build_identities': identities,
        'diagnostics': diagnostics, 'platform': platform_dir, 'source_date_epoch': epoch, 'cpu_build_options': isa,
        'toolchain': {'compiler': compiler, 'build_type': cache.get('CMAKE_BUILD_TYPE'), 'cxx_flags': cache.get('CMAKE_CXX_FLAGS'), 'required_runtime_symbol_versions': requirements},
        'reproducibility_scope': 'deterministic packaging of identical build inputs; independent compilation is a separate gate',
        'artifacts': {f.name: {'sha256': sha256(f), 'bytes': f.stat().st_size} for f in sorted(out.iterdir())},
        'components': [{'name': 'ducktape-laya', 'license': 'MIT'}, {'name': 'llama.cpp/ggml', 'license': 'MIT', 'revision': record['runtime']['commit']}, {'name': 'nlohmann-json', 'license': 'MIT'}, {'name': 'llamafile-cpu-kernels', 'license': 'MIT'}, {'name': 'laya-language-data', 'license': 'Apache-2.0'}],
        'status': 'local unsigned development artifacts; not release approval'}
    (out / 'inventory.json').write_text(json.dumps(inventory, indent=2)+'\n')
print(out)
