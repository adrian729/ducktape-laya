#!/usr/bin/env python3
"""Build, install, and provision a local Ducktape Laya development setup."""
import argparse
import json
import os
import platform
import re
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / 'models/manifest.json').read_text())
MINOR = '.'.join(json.loads((ROOT / 'release/compatibility.json').read_text())['package_version'].split('.')[:2])


def prompt_path(label, default):
    answer = input(f'{label} [{default}]: ').strip()
    return Path(answer).expanduser().resolve() if answer else default


def prompt_yes(label, default):
    suffix = 'Y/n' if default else 'y/N'
    answer = input(f'{label} [{suffix}]: ').strip().lower()
    if answer not in ('', 'y', 'yes', 'n', 'no'):
        raise ValueError('Please answer yes or no')
    return default if not answer else answer in ('y', 'yes')


def run(command, dry_run):
    print('+ ' + shlex.join(map(str, command)), flush=True)
    if not dry_run:
        subprocess.run(command, check=True)


def default_jobs():
    """DUCKTAPE_JOBS if set, else min(CPUs, available memory / 0.6 GB): one compile job peaks below 0.6 GB."""
    if os.environ.get('DUCKTAPE_JOBS', '').isdigit() and int(os.environ['DUCKTAPE_JOBS']) > 0:
        return int(os.environ['DUCKTAPE_JOBS'])
    cpus = os.cpu_count() or 1
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'):
                return max(1, min(cpus, int(line.split()[1]) * 1024 // int(0.6 * 1024 ** 3)))
    except (OSError, ValueError, IndexError):
        pass
    return cpus


def generator(build):
    """['-G', 'Ninja'] when Ninja is installed, unless the build directory was already configured with another
    generator (CMake cannot switch it; that directory keeps its own)."""
    cache = build / 'CMakeCache.txt'
    if cache.is_file() and re.search(r'^CMAKE_GENERATOR:INTERNAL=(?!Ninja$)', cache.read_text(errors='replace'), re.M):
        return []
    return ['-G', 'Ninja'] if shutil.which('ninja') else []


def native_libdir(prefix):
    matches = sorted(prefix.glob('lib*/libducktape_laya.so'))
    if len(matches) != 1:
        raise FileNotFoundError(f'Expected one installed libducktape_laya.so under {prefix}')
    return matches[0].parent


def stage_node_package(prefix, libdir):
    """Create a local npm-installable package; no npm publish or install hook."""
    package = prefix / 'share/ducktape-laya/npm'
    package.mkdir(parents=True, exist_ok=True)
    for name in ('package.json', 'README.md', 'index.cjs', 'index.d.ts', 'worker.cjs', 'resolve-addon.cjs'):
        shutil.copy2(ROOT / 'bindings/node' / name, package / name)
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(ROOT / name, package / name)
    shutil.copytree(ROOT / 'licenses', package / 'licenses', dirs_exist_ok=True)
    shutil.copy2(ROOT / 'release/compatibility.json', package / 'compatibility.json')
    shutil.copy2(ROOT / 'models/manifest.json', package / 'model-manifest.json')
    shutil.copy2(ROOT / 'scripts/download-model.py', package / 'download-model.py')
    addon = package / 'native' / ('linux-arm64' if platform.machine() in ('aarch64', 'arm64') else 'linux-x64')
    addon.mkdir(parents=True, exist_ok=True)
    shutil.copy2(libdir / 'ducktape-laya/node/ducktape_laya.node', addon / 'ducktape_laya.node')
    for name in ('libducktape_laya.so', 'libducktape_laya.so.1'):
        shutil.copy2(libdir / 'libducktape_laya.so', addon / name)
    return package


def notes(prefix, model_dir, libdir, node_package, download_requested, dry_run):
    model = model_dir / MANIFEST['filename']
    q = shlex.quote
    print('\n' + ('Planned setup' if dry_run else 'Setup complete') + '. Use these values in your projects:\n')
    print(f'  Library: {libdir / "libducktape_laya.so"}')
    model_status = ' (planned download)' if dry_run and download_requested else (
        '' if download_requested else ' (not downloaded)')
    print(f'  Model:   {model}{model_status}')
    print('\nC/C++ with CMake:')
    print(f'  cmake -S . -B build -DCMAKE_PREFIX_PATH={q(str(prefix))}')
    print('  In CMakeLists.txt:')
    print(f'  find_package(ducktape_laya {MINOR} CONFIG REQUIRED)')
    print('  target_link_libraries(your_target PRIVATE ducktape::laya)')
    print('  In your C++ source:')
    print('  #include <ducktape_laya.hpp>')
    print(f'  auto engine = ducktape::engine::open({json.dumps(str(model))});')
    print('  For C, include <ducktape_laya.h> and pass the model path to dtl_open().')
    print('\nOther native builds:')
    print(f'  export PKG_CONFIG_PATH={q(str(libdir / "pkgconfig"))}"${{PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}}"')
    print('  pkg-config --cflags --libs ducktape-laya')
    print(f'  export LD_LIBRARY_PATH={q(str(libdir))}"${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}"')
    print('\nRust (using the source checkout crate):')
    print(f'  export DUCKTAPE_LAYA_LIB_DIR={q(str(libdir))}')
    print(f'  cargo add ducktape-laya --path {q(str(ROOT / "bindings/rust/ducktape-laya"))}')
    print('  In your Rust source:')
    print('  use ducktape_laya::Engine;')
    print('  The same LD_LIBRARY_PATH above is needed when running the program.')
    if node_package is not None:
        print('\nNode.js (run in your application directory):')
        print(f'  npm install --save {q(str(node_package))}')
        print('  In your JavaScript source:')
        print("  const { Engine } = require('ducktape-laya');")
        print(f'  const engine = await Engine.open({json.dumps(str(model))});')
    else:
        print('\nNode.js: rerun setup with --node to build a local npm-installable package.')
    if not download_requested:
        print('\nDownload the model when ready:')
        print(f'  python3 {q(str(prefix / "share/ducktape-laya/download-model.py"))} {q(str(model_dir))}')
    print('\nPATH does not need a change: this installs a library, not an executable.')
    print('For distribution, bundle the GGUF in your app resources and pass its installed absolute path.')
    print('These exports affect only the current shell; add them to your build/run configuration as needed.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path, help='Native installation prefix (default: ~/.local)')
    parser.add_argument('--model-dir', type=Path, help='Directory for verified GGUF weights')
    parser.add_argument('--build-dir', type=Path, default=ROOT / 'build-setup')
    parser.add_argument('--jobs', type=int, help='Build jobs (default: DUCKTAPE_JOBS, or min(CPUs, available memory / 0.6 GB))')
    parser.add_argument('--node', action='store_true', help='Build and stage a local npm package')
    parser.add_argument('--node-include-dir', type=Path, help='Directory containing node_api.h')
    parser.add_argument('--skip-model', action='store_true', help='Install without downloading the model')
    parser.add_argument('--yes', action='store_true', help='Accept defaults without prompts')
    parser.add_argument('--dry-run', action='store_true', help='Print commands and setup notes only')
    args = parser.parse_args()
    if args.jobs is not None and args.jobs < 1:
        parser.error('--jobs must be positive')
    jobs = args.jobs or default_jobs()

    if not args.yes and not sys.stdin.isatty():
        parser.error('Interactive setup needs a terminal; pass --yes for unattended setup')
    prefix = (args.prefix or Path('~/.local')).expanduser().resolve()
    if not args.yes and args.prefix is None:
        prefix = prompt_path('Installation prefix', prefix)
    model_dir = (args.model_dir or prefix / 'share/ducktape-laya/models').expanduser().resolve()
    if not args.yes and args.model_dir is None:
        model_dir = prompt_path('Model directory', model_dir)
    with_node = args.node or (not args.yes and prompt_yes('Build Node.js package', False))
    download_model = not args.skip_model
    if not args.yes and not args.skip_model:
        download_model = prompt_yes('Download the 449 MB Q8 model', True)

    if shutil.which('cmake') is None:
        parser.error('CMake 3.22+ is required')
    if not (ROOT / 'third_party/llama.cpp/CMakeLists.txt').is_file():
        parser.error('Pinned llama.cpp source is missing; run git submodule update --init --recursive')
    if args.node_include_dir and not with_node:
        parser.error('--node-include-dir requires --node')
    node_include = args.node_include_dir
    if with_node:
        node_executable = shutil.which('node')
        if node_executable is None:
            parser.error('Node.js 20+ is required for --node')
        node_major = int(subprocess.check_output([node_executable, '-p', 'process.versions.node.split(".")[0]'], text=True))
        if node_major < 20:
            parser.error('Node.js 20+ is required for --node')
        if node_include is None:
            candidate = Path(node_executable).resolve().parent.parent / 'include/node'
            if (candidate / 'node_api.h').is_file():
                node_include = candidate
        if node_include is not None and not (node_include / 'node_api.h').is_file():
            parser.error('--node-include-dir must contain node_api.h')

    build = args.build_dir.expanduser().resolve()
    configure = ['cmake', '-S', str(ROOT), '-B', str(build), *generator(build), '-DCMAKE_BUILD_TYPE=Release',
                 '-DDUCKTAPE_BUILD_TESTS=OFF', '-DDUCKTAPE_BUILD_EXAMPLES=OFF',
                 f'-DDUCKTAPE_BUILD_NODE={"ON" if with_node else "OFF"}']
    if node_include:
        configure.append(f'-DNODE_INCLUDE_DIR={node_include.expanduser().resolve()}')
    try:
        run(configure, args.dry_run)
        run(['cmake', '--build', str(build), '--parallel', str(jobs)], args.dry_run)
        run(['cmake', '--install', str(build), '--prefix', str(prefix)], args.dry_run)
        libdir = prefix / 'lib' if args.dry_run else native_libdir(prefix)
        node_package = None
        if with_node:
            node_package = prefix / 'share/ducktape-laya/npm'
            if not args.dry_run:
                stage_node_package(prefix, libdir)
        if download_model:
            helper = prefix / 'share/ducktape-laya/download-model.py'
            run([sys.executable, str(helper), str(model_dir)], args.dry_run)
        notes(prefix, model_dir, libdir, node_package, download_model, args.dry_run)
    except (OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Setup failed: {error}\n')


if __name__ == '__main__':
    main()
