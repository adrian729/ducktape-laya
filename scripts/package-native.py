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
import concurrent.futures
import importlib.util
import sbom
import json
import os
import pathlib
import shutil
import subprocess
import re
import sys
import tempfile
from release_support import (ROOT, build_environment, host_target, check_record, sha256, git, binary_info, source_identity, native_build_identity,
                             require_runtime_identity, require_runtime_sources, require_guard_cf)
from artifact_support import write_zip, archive_tree, archive_zip, set_npm_platforms, native_payload
import java_package
import python_wheel

# The binary constraints (instrumentation, source paths, hardening, symbol-version floors, exports, deployment target)
# live in scripts/check-binary.py, which validate.py also runs.
_spec = importlib.util.spec_from_file_location('check_binary', pathlib.Path(__file__).resolve().parent / 'check-binary.py')
check_binary = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_binary)

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
if not (build / 'ducktape_laya.node').is_file():
    p.error('Build the Node addon before packaging')
binary_summary = check_binary.check_binaries(build / target.library, build / 'ducktape_laya.node', target.name)
if binary_summary['status'] != 'passed':
    p.error('The built binaries fail the release constraints (scripts/check-binary.py): ' + '; '.join(binary_summary['problems']))
diagnostics = binary_info(build)
try:
    require_runtime_identity(diagnostics, record)
except ValueError as error:
    p.error(str(error))
cache = {}
for line in (build / 'CMakeCache.txt').read_text().splitlines():
    match = re.match(r'([^:#]+):[^=]+=(.*)', line)
    if match: cache[match[1]] = match[2]
# Only the product configuration is packaged: the approved runtime checkout, a Release build and
# CMake's own default flags (scripts/validate.py and CI pass nothing else). Anything else would ship
# unrecorded sources or host-specific instructions under the approved runtime's name.
WINDOWS_FLAGS = {'CMAKE_C_FLAGS': '/DWIN32 /D_WINDOWS', 'CMAKE_CXX_FLAGS': '/DWIN32 /D_WINDOWS /EHsc',
                 'CMAKE_C_FLAGS_RELEASE': '/O2 /Ob2 /DNDEBUG', 'CMAKE_CXX_FLAGS_RELEASE': '/O2 /Ob2 /DNDEBUG'}
DEFAULT_FLAGS = {'CMAKE_C_FLAGS': '', 'CMAKE_CXX_FLAGS': '', 'CMAKE_C_FLAGS_RELEASE': '-O3 -DNDEBUG', 'CMAKE_CXX_FLAGS_RELEASE': '-O3 -DNDEBUG'}
def refuse_non_product_build():
    problems = []
    if cache.get('CMAKE_BUILD_TYPE') != 'Release': problems.append(f'CMAKE_BUILD_TYPE is {cache.get("CMAKE_BUILD_TYPE")!r}, not Release')
    if cache.get('DUCKTAPE_LLAMA_REVISION'): problems.append('DUCKTAPE_LLAMA_REVISION is set')
    # Development settings (precompiled headers, dev-only build changes) never reach a package.
    for name in ('DUCKTAPE_DEV_BUILD', 'DUCKTAPE_DEV_PCH'):
        if cache.get(name, 'OFF').upper() not in ('OFF', '0', 'FALSE', 'NO', 'N', ''):
            problems.append(f'{name} is {cache[name]!r}, not OFF')
    approved = (ROOT / record['runtime']['path']).resolve()
    if not cache.get('DUCKTAPE_LLAMA_SOURCE') or pathlib.Path(cache['DUCKTAPE_LLAMA_SOURCE']).resolve() != approved:
        problems.append(f'DUCKTAPE_LLAMA_SOURCE is {cache.get("DUCKTAPE_LLAMA_SOURCE")!r}, not the checkout\'s {approved.as_posix()}')
    if not cache.get('CMAKE_HOME_DIRECTORY') or pathlib.Path(cache['CMAKE_HOME_DIRECTORY']).resolve() != ROOT.resolve():
        problems.append(f'the build directory was configured from {cache.get("CMAKE_HOME_DIRECTORY")!r}, not this checkout')
    for name, default in (WINDOWS_FLAGS if target.os == 'win32' else DEFAULT_FLAGS).items():
        if cache.get(name, '').strip() != default: problems.append(f'{name} is {cache.get(name)!r}, not the default {default!r}')
    if problems:
        p.error('Not a product build (' + '; '.join(problems) + '); configure a fresh build directory as scripts/validate.py does')
refuse_non_product_build()
# The library must have been linked from the runtime tree as it is now (pinned commit, approved patches,
# checked by check_record above): a tree modified for the build and restored afterwards is refused.
try:
    require_runtime_sources(build, ROOT / record['runtime']['path'], target.library)
except ValueError as error:
    p.error(str(error))
out.mkdir(parents=True, exist_ok=False)
# npm and Cargo run through their Windows .cmd/.exe launchers too.
npm, cargo, dotnet = (shutil.which(name) or name for name in ('npm', 'cargo', 'dotnet'))
with tempfile.TemporaryDirectory(prefix='ducktape-pack-') as directory:
    directory = pathlib.Path(directory)
    native = directory / f'ducktape-laya-{version}-{platform_dir}'
    subprocess.run(['cmake', '--install', str(build), '--prefix', str(native)], check=True, capture_output=True)
    shipped_sha256 = {}
    if target.os == 'linux':
        # Drop the symbol table and debug sections the dynamic loader does not need (--strip-unneeded keeps
        # .dynsym, which check-abi.py and the version requirements below read). Deterministic for one binutils.
        library_files = [path for path in native.glob('lib*/libducktape_laya.so*') if path.is_file() and not path.is_symlink()]
        addon_file = next(native.glob('lib*/ducktape-laya/node/ducktape_laya.node'))
        for path in [*library_files, addon_file]:
            subprocess.run(['strip', '--strip-unneeded', str(path)], check=True)
        if len(library_files) != 1: raise SystemExit(f'Expected one installed library file, found {[x.name for x in library_files]}')
        subprocess.run([sys.executable, str(ROOT / 'scripts/check-abi.py'), str(library_files[0])], check=True, capture_output=True)
        shipped_sha256 = {'ducktape_laya': sha256(library_files[0]), 'ducktape_laya_node': sha256(addon_file)}
        # Stripping keeps the dynamic section and segments; check the hardening on the shipped bytes too.
        for path in (library_files[0], addon_file):
            problems = check_binary.hardening_problems(path, library=path == library_files[0])
            if problems: raise SystemExit('; '.join(problems))
    elif target.os == 'win32':
        for path in (native.glob('bin/ducktape_laya.dll'), native.glob('lib*/ducktape-laya/node/ducktape_laya.node')):
            for item in path: require_guard_cf(item)
    elif target.os == 'darwin':
        # Same checks as scripts/check-binary.py, on the installed files (no strip step on macOS).
        for path in (*native.glob('lib*/libducktape_laya.dylib'), *native.glob('lib*/ducktape-laya/node/ducktape_laya.node')):
            problems = check_binary.macho_problems(path, target, library=path.suffix == '.dylib')
            if problems: raise SystemExit('; '.join(problems))
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
    # Copy real bytes under the name the addon links (libducktape_laya.so.1 on Linux),
    # avoiding symlink support differences in npm tarballs.
    for name in target.node_libraries:
        shutil.copy(installed_library, addon / name)
    # The ecosystems' packages are independent (each reads installed_library or its own sources and writes its own files
    # in out), and each is dominated by an external tool (npm, cargo, javac and javadoc, dotnet), so they are made
    # concurrently; the first failure in this order is the one reported.
    def pack_npm():
        subprocess.run([npm, 'pack', '--ignore-scripts', '--pack-destination', str(out)], cwd=package, check=True, capture_output=True)

    def pack_crates():
        env = dict(os.environ, DUCKTAPE_LAYA_LIB_DIR=str(build))
        workspace = ROOT / 'bindings/rust/Cargo.toml'
        for name in ['ducktape-laya-sys', 'ducktape-laya']:
            command = [cargo, 'package', '--manifest-path', str(workspace), '-p', name, '--locked', '--offline', '--allow-dirty', '--no-verify']
            # Permit packaging the unpublished sibling locally. Cargo normalizes the
            # actual .crate dependency to its version; no checkout path is shipped.
            command += ['--config', f'patch.crates-io.ducktape-laya-sys.path="{(ROOT / "bindings/rust/ducktape-laya-sys").as_posix()}"']
            subprocess.run(command, cwd=ROOT, env=env, check=True, capture_output=True)
            shutil.copy(ROOT / f'bindings/rust/target/package/{name}-{version}.crate', out)

    def pack_wheel():
        python_wheel.build(out, version, installed_library, epoch, target)

    def pack_jar():
        java_package.build(out, version, installed_library, epoch, target)

    def pack_nupkg():
        # NuGet: the library at runtimes/<rid>/native/, packed from source like the crates. No PDB
        # (none is shipped): its ID in the assembly hashes generated sources, whose line endings
        # follow the host, and every platform must produce the same managed assembly. A private
        # artifacts path: otherwise pack reuses any Release build already in the checkout's obj/
        # (tests build one), with its own options and absolute paths.
        nuget = directory / 'nuget'
        subprocess.run([dotnet, 'pack', str(ROOT / 'bindings/dotnet/DucktapeLaya/DucktapeLaya.csproj'), '-c', 'Release', '-m:1',
                        '--artifacts-path', str(directory / 'dotnet-build'),
                        '-o', str(nuget), f'-p:DucktapeLayaNativeLibrary={installed_library}', '-p:DebugType=none',
                        f'-p:DucktapeLayaRuntime={target.nuget}', '-p:ContinuousIntegrationBuild=true', '--nologo'],
                       cwd=ROOT, env=dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_NOLOGO='1'), check=True, capture_output=True)
        normalize_nupkg(nuget / f'DucktapeLaya.{version}.nupkg', out / f'DucktapeLaya.{version}.nupkg', epoch)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(task) for task in (pack_npm, pack_crates, pack_wheel, pack_jar, pack_nupkg)]
        for future in futures:
            future.result()
    # The options that select instructions on this architecture (the other's are CMake cache defaults, not build inputs).
    prefixes = ('GGML_NATIVE',) + (('GGML_CPU_ARM_ARCH',) if target.name.endswith('arm64') else
                                   ('GGML_AVX', 'GGML_FMA', 'GGML_F16C', 'GGML_BMI', 'GGML_SSE'))
    isa = {key: value for key, value in cache.items() if key.startswith(prefixes)}
    toolchain = {}
    if target.os == 'linux':
        # check-binary.py checked these against the wheel tag and the declared floors.
        toolchain['required_runtime_symbol_versions'] = binary_summary['required_runtime_symbol_versions']
        toolchain['strip'] = {'command': 'strip --strip-unneeded', 'tool': subprocess.check_output(['strip', '--version'], text=True).splitlines()[0],
                              'shipped_sha256': shipped_sha256}
        toolchain['hardening'] = ('full RELRO (BIND_NOW) and the stack protector verified on the shipped library and addon, _FORTIFY_SOURCE '
                                  'on the library' + ('; return-address signing (PAC)' if target.name.endswith('arm64') else ''))
    elif target.os == 'darwin':
        # check-binary.py checked the deployment target against the wheel tag (macosx_14_0).
        toolchain['minimum_macos'] = binary_summary['minimum_macos']
        toolchain['linked_libraries'] = binary_summary['linked_libraries']
    else:
        # check-binary.py checked the machine, Control Flow Guard and the exports.
        # The C++ runtime is the Visual C++ Redistributable (/MD); applications provide it.
        toolchain['hardening'] = 'Control Flow Guard (GUARD_CF) verified on the DLL and addon'
        toolchain['linked_libraries'] = binary_summary['linked_libraries']
        toolchain['delay_loaded_libraries'] = binary_summary['delay_loaded_libraries']
    compiler = subprocess.check_output([cache['CMAKE_CXX_COMPILER'], '--version'], text=True).splitlines()[0]
    # The runner image and, on Windows, the MSVC toolset and SDK whose libraries the DLL links.
    if build_environment(): toolchain['build_environment'] = build_environment()
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
