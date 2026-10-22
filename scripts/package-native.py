#!/usr/bin/env python3
"""Create this platform's local native/npm/Rust/Python/Java/.NET artifacts.

One run per runner: linux-x64, linux-arm64, macos-arm64 or windows-x64, chosen
from the machine it runs on. The output directory is a complete package set for
that platform (native archive, npm tarball, crates, wheel, Maven files, NuGet
package, SBOM and inventory) and also the per-platform fragment that
scripts/assemble-packages.py merges into the multi-platform npm, jar and NuGet
packages. Does not publish, sign or download.
"""
import argparse
import sbom
import json
import os
import pathlib
import shutil
import subprocess
import re
import tempfile
from release_support import (ROOT, host_target, check_record, sha256, git, binary_info, source_identity, native_build_identity,
                             require_runtime_identity, require_uninstrumented_binary, pe_info, macho_info)
from artifact_support import write_zip, archive_tree, archive_zip, set_npm_platforms, native_payload
import java_package
import python_wheel

p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
p.add_argument('--build', type=pathlib.Path, default=ROOT / 'build')
p.add_argument('--output', type=pathlib.Path, required=True)
a = p.parse_args()
def normalize_nupkg(source, destination, epoch):
    """Rewrite a nupkg deterministically: NuGet stamps entries with the pack time and
    gives relationships random IDs, and writes its XML parts with the platform's line
    separator (CRLF on Windows). Entry order and contents are otherwise kept."""
    import hashlib
    import zipfile
    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        files = {name: (archive.read(name), False) for name in names}
    for name in names:
        if name in ('_rels/.rels', '[Content_Types].xml') or name.endswith(('.nuspec', '.psmdcp')):
            files[name] = (files[name][0].replace(b'\r\n', b'\n'), False)
    rels = files['_rels/.rels'][0].decode()
    rels = re.sub(r'Target="([^"]+)" Id="[^"]+"',
                  lambda m: f'Target="{m[1]}" Id="R{hashlib.sha256(m[1].encode()).hexdigest()[:16].upper()}"', rels)
    files['_rels/.rels'] = (rels.encode(), False)
    write_zip(destination, files, epoch, names)

try:
    target = host_target()
except ValueError as error:
    p.error(str(error))
record = check_record()
version = record['package_version']
platform_dir = target.name
epoch = int(os.environ.get('SOURCE_DATE_EPOCH', git(ROOT, 'show', '-s', '--format=%ct', 'HEAD')))
if epoch < 0 or epoch > 4294967295: p.error('SOURCE_DATE_EPOCH outside gzip timestamp range')
build, out = a.build.resolve(), a.output.resolve()
source = source_identity()
try:
    identities = {name: native_build_identity(build, name, binary, source) for name, binary in [
        ('ducktape_laya', target.library), ('ducktape_laya_node', 'ducktape_laya.node')]}
except ValueError as error:
    p.error(str(error))
try:
    for name in [target.library, 'ducktape_laya.node']:
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
# npm and Cargo run through their Windows .cmd/.exe launchers too.
npm, cargo, dotnet = (shutil.which(name) or name for name in ('npm', 'cargo', 'dotnet'))
with tempfile.TemporaryDirectory(prefix='ducktape-pack-') as directory:
    directory = pathlib.Path(directory)
    native = directory / f'ducktape-laya-{version}-{platform_dir}'
    subprocess.run(['cmake', '--install', str(build), '--prefix', str(native)], check=True, capture_output=True)
    # tar.gz with symlinks on Linux and macOS, zip on Windows (no symlinks there).
    archive = archive_zip if target.archive == '.zip' else archive_tree
    archive(native, out / (native.name + target.archive), native.name, epoch)
    installed_addon = next(native.glob('lib*/ducktape-laya/node/ducktape_laya.node'))
    # The DLL is a runtime file (bin/) on Windows; elsewhere the library sits in lib/.
    installed_library = next(native.glob(f'{"bin" if target.os == "win32" else "lib*"}/{target.library}'))
    package = directory / 'npm'
    package.mkdir()
    for name in ['README.md', 'index.cjs', 'index.d.ts', 'worker.cjs', 'resolve-addon.cjs']:
        shutil.copy(ROOT / 'bindings/node' / name, package / name)
    # The package holds one platform's addon; declare that OS and CPU so npm refuses others.
    (package / 'package.json').write_text(set_npm_platforms((ROOT / 'bindings/node/package.json').read_text(), [target]), newline='\n')
    for name in ['LICENSE', 'THIRD_PARTY_NOTICES.md']:
        shutil.copy(ROOT / name, package / name)
    shutil.copytree(ROOT / 'licenses', package / 'licenses')
    shutil.copy(ROOT / 'release/compatibility.json', package / 'compatibility.json')
    shutil.copy(ROOT / 'models/manifest.json', package / 'model-manifest.json')
    shutil.copy(ROOT / 'scripts/download-model.py', package / 'download-model.py')
    addon = package / 'native' / target.npm
    addon.mkdir(parents=True)
    shutil.copy(installed_addon, addon / 'ducktape_laya.node')
    # Copy real bytes under every loader name (libducktape_laya.so and .so.1 on Linux),
    # avoiding symlink support differences in npm tarballs.
    for name in target.node_libraries:
        shutil.copy(installed_library, addon / name)
    subprocess.run([npm, 'pack', '--ignore-scripts', '--pack-destination', str(out)], cwd=package, check=True, capture_output=True)
    env = dict(os.environ, DUCKTAPE_LAYA_LIB_DIR=str(build))
    workspace = ROOT / 'bindings/rust/Cargo.toml'
    for name in ['ducktape-laya-sys', 'ducktape-laya']:
        command = [cargo, 'package', '--manifest-path', str(workspace), '-p', name, '--locked', '--offline', '--allow-dirty', '--no-verify']
        # Permit packaging the unpublished sibling locally. Cargo normalizes the
        # actual .crate dependency to its version; no checkout path is shipped.
        command += ['--config', f'patch.crates-io.ducktape-laya-sys.path="{(ROOT / "bindings/rust/ducktape-laya-sys").as_posix()}"']
        subprocess.run(command, cwd=ROOT, env=env, check=True, capture_output=True)
        shutil.copy(ROOT / f'bindings/rust/target/package/{name}-{version}.crate', out)
    python_wheel.build(out, version, installed_library, epoch, target)
    java_package.build(out, version, installed_library, epoch, target)
    # NuGet: the library at runtimes/<rid>/native/, packed from source like the crates.
    nuget = directory / 'nuget'
    subprocess.run([dotnet, 'pack', str(ROOT / 'bindings/dotnet/DucktapeLaya/DucktapeLaya.csproj'), '-c', 'Release', '-m:1',
                    '-o', str(nuget), f'-p:DucktapeLayaNativeLibrary={installed_library}',
                    f'-p:DucktapeLayaRuntime={target.nuget}', '-p:ContinuousIntegrationBuild=true', '--nologo'],
                   cwd=ROOT, env=dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_NOLOGO='1'), check=True, capture_output=True)
    normalize_nupkg(nuget / f'DucktapeLaya.{version}.nupkg', out / f'DucktapeLaya.{version}.nupkg', epoch)
    cache = {}
    for line in (build / 'CMakeCache.txt').read_text().splitlines():
        match = re.match(r'([^:#]+):[^=]+=(.*)', line)
        if match: cache[match[1]] = match[2]
    isa = {key: value for key, value in cache.items() if key.startswith(('GGML_AVX', 'GGML_FMA', 'GGML_F16C', 'GGML_BMI', 'GGML_SSE', 'GGML_NATIVE', 'GGML_CPU_ARM_ARCH'))}
    toolchain = {}
    if target.os == 'linux':
        symbols = subprocess.check_output(['readelf', '--version-info', str(build / target.library)], text=True)
        requirements = sorted(set(re.findall(r'Name: ((?:GLIBC|GLIBCXX|CXXABI)_[0-9.]+)', symbols)))
        # The wheel tag and the declared floors must cover what the library actually needs.
        def newest(prefix):
            return max((tuple(map(int, r[len(prefix):].split('.'))) for r in requirements if r.startswith(prefix)), default=())
        platform = json.loads((ROOT / 'release/qualification.json').read_text())['platforms']['native']
        wheel_glibc = tuple(map(int, re.search(r'manylinux_(\d+)_(\d+)', target.wheel).groups()))
        for prefix, limit in (('GLIBC_', wheel_glibc), ('GLIBC_', tuple(map(int, platform['maximum_glibc'].split('.')))),
                              ('GLIBCXX_', tuple(map(int, platform['maximum_glibcxx'].split('.'))))):
            if newest(prefix) > limit:
                raise SystemExit(f'Library needs {prefix}{".".join(map(str, newest(prefix)))}, above the declared {".".join(map(str, limit))}')
        toolchain['required_runtime_symbol_versions'] = requirements
    elif target.os == 'darwin':
        info = {name: macho_info(build / name) for name in [target.library, 'ducktape_laya.node']}
        # The wheel tag (macosx_14_0) promises the deployment target the library was linked for.
        wheel_macos = tuple(map(int, re.search(r'macosx_(\d+)_(\d+)', target.wheel).groups()))
        for name, item in info.items():
            if not item['minimum_os'] or tuple(map(int, item['minimum_os'].split('.'))) > wheel_macos:
                raise SystemExit(f'{name} targets macOS {item["minimum_os"]}, above the wheel tag {target.wheel}')
        toolchain['minimum_macos'] = info[target.library]['minimum_os']
        toolchain['linked_libraries'] = {name: item['dylibs'] for name, item in info.items()}
    else:
        info = {name: pe_info(build / name) for name in [target.library, 'ducktape_laya.node']}
        for name, item in info.items():
            if item['machine'] != 0x8664: raise SystemExit(f'{name} is not an x64 image')
        exports = info[target.library]['exports']
        expected = sorted(line.strip().lstrip('_') for line in (ROOT / 'src/exports.macos').read_text().splitlines() if line.strip())
        if exports != expected:
            raise SystemExit(f'{target.library} exports {exports}, expected exactly {expected}')
        # The C++ runtime is the Visual C++ Redistributable (/MD); applications provide it.
        toolchain['linked_libraries'] = {name: sorted(item['imports']) for name, item in info.items()}
        toolchain['delay_loaded_libraries'] = {name: sorted(item['delay_imports']) for name, item in info.items()}
    compiler = subprocess.check_output([cache['CMAKE_CXX_COMPILER'], '--version'], text=True).splitlines()[0]
    sbom.write(out / 'sbom.cdx.json', record)
    inventory = {'schema_version': 2, 'package_version': version, 'packaging_source_commit': git(ROOT, 'rev-parse', 'HEAD'),
        'packaging_source_tracked_dirty': bool(git(ROOT, 'diff', 'HEAD', '--name-only')), 'runtime': record['runtime'],
        'build_source': source, 'build_identities': identities,
        'diagnostics': diagnostics, 'platform': platform_dir, 'source_date_epoch': epoch, 'cpu_build_options': isa,
        'toolchain': {'compiler': compiler, 'build_type': cache.get('CMAKE_BUILD_TYPE'), 'cxx_flags': cache.get('CMAKE_CXX_FLAGS'), **toolchain},
        'reproducibility_scope': 'deterministic packaging of identical build inputs; independent compilation is a separate gate',
        'artifacts': {f.name: {'sha256': sha256(f), 'bytes': f.stat().st_size} for f in sorted(out.iterdir())},
        # This platform's native files inside each package, by entry name (merged as they are by assemble-packages.py).
        'payload': native_payload(out, version, target),
        'components': [{'name': 'ducktape-laya', 'license': 'MIT'}, {'name': 'llama.cpp/ggml', 'license': 'MIT', 'revision': record['runtime']['commit']}, {'name': 'nlohmann-json', 'license': 'MIT'}, {'name': 'llamafile-cpu-kernels', 'license': 'MIT'}, {'name': 'laya-language-data', 'license': 'Apache-2.0'}],
        'status': 'local unsigned development artifacts; not release approval'}
    (out / 'inventory.json').write_text(json.dumps(inventory, indent=2)+'\n', newline='\n')
print(out)
