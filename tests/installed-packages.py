#!/usr/bin/env python3
"""Exercise the local package artifacts outside the checkout with the real model.

Takes one platform's scripts/package-native.py output or a scripts/assemble-packages.py
output and uses the files for this machine's platform: the native archive (CMake,
pkg-config), the npm package, both crates against the archive, the wheel, the
Maven jar, the NuGet package and the Go module against the archive. Every
consumer must give exactly the same results."""
import argparse
import atexit
import concurrent.futures
import hashlib
import json
import os
import pathlib
import platform as platform_module
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading

from package_support import (EXE, clean_environment, extract_native, host_packages, library_directory, tool, venv_python,
                             with_library_path)
from release_support import git, macho_info, sha256

root = pathlib.Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('artifacts', type=pathlib.Path)
p.add_argument('model', type=pathlib.Path)
p.add_argument('--platform', help='Package platform to test (default: this machine\'s; it must be this machine\'s)')
p.add_argument('--report', type=pathlib.Path, help='Write a JSON report: status, platform, inventory and model hashes, '
               'checkout commit and the sha256 of each consumer\'s output')
a = p.parse_args()
artifacts, model = a.artifacts.resolve(), a.model.resolve()
inventory, version, target = host_packages(artifacts, a.platform)
request = (root / 'tests/fixtures/primitive.json').read_bytes()
env = clean_environment()
report = {'schema_version': 1, 'status': 'failed', 'platform': target.name, 'artifact_inventory_sha256': sha256(artifacts / 'inventory.json'),
          'package_version': version, 'model_sha256': sha256(model), 'source_commit': git(root, 'rev-parse', 'HEAD'),
          'source_tracked_dirty': bool(git(root, 'diff', 'HEAD', '--name-only')), 'consumers': {},
          # Where this ran: scripts/check-candidate.py requires the release host's own run (not a GitHub Actions one) for installed_release.
          'host': {'system': platform_module.system(), 'machine': platform_module.machine(), 'hostname': platform_module.node(),
                   'github_actions': os.environ.get('GITHUB_ACTIONS') == 'true'}}


def record(name, output):
    """Keep a consumer's result; every consumer must agree exactly."""
    report['consumers'][name] = hashlib.sha256(json.dumps(output, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return output


def save():
    if a.report:
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text(json.dumps(report, indent=2) + '\n')


atexit.register(save)
# One real-model consumer at a time: each opens the model with 4 threads, and the builds and installs of the
# other probes already share the machine.
inference = threading.Lock()


def consume(command, **kwargs):
    with inference:
        return json.loads(subprocess.check_output(command, input=request, **kwargs))


def probe_native(directory, native):
    # installed-native.py runs its CMake and pkg-config consumers itself.
    subprocess.run([sys.executable, str(root / 'tests/installed-native.py'), str(native), str(model), '--outputs', str(directory / 'native.json'),
                    '--skipped', str(directory / 'native-skipped.json')], check=True)
    return json.loads((directory / 'native-skipped.json').read_text()), json.loads((directory / 'native.json').read_text())


def probe_node(directory, native):
    app = directory / 'application'
    app.mkdir()
    (app / 'package.json').write_text('{"private":true}')
    subprocess.run([tool('npm'), 'install', '--offline', '--ignore-scripts', '--no-audit', '--no-fund', str(artifacts / f'ducktape-laya-{version}.tgz')],
                   cwd=app, check=True, capture_output=True)
    addon = app / f'node_modules/ducktape-laya/native/{target.npm}/ducktape_laya.node'
    # The addon finds the library next to itself: a relative run path (Windows looks in the addon's directory).
    # On Linux it is a DT_RPATH, which the loader searches before LD_LIBRARY_PATH, never a DT_RUNPATH.
    if target.os == 'linux':
        dynamic = subprocess.check_output(['readelf', '-d', str(addon)], text=True)
        assert 'RUNPATH' not in dynamic, dynamic
        lines = [line for line in dynamic.splitlines() if '(RPATH)' in line]
        assert len(lines) == 1, dynamic
        paths = re.search(r'\[(.*?)\]', lines[0]).group(1).split(':')
        assert all(path.startswith('$ORIGIN') for path in paths), paths
    elif target.os == 'darwin':
        paths = macho_info(addon)['rpaths']
        assert paths and all(path.startswith('@loader_path') for path in paths), paths
    script = (root / 'tests/consumers/node.cjs').read_text().replace("require('../../bindings/node/index.cjs')", "require('ducktape-laya')")
    (app / 'test.cjs').write_text(script)
    return consume(['node', 'test.cjs', str(model)], cwd=app, env=env)


def probe_rust(directory, native):
    app = directory / 'rust'
    (app / 'src').mkdir(parents=True)
    (app / 'src/main.rs').write_text((root / 'tests/consumers/rust/src/main.rs').read_text())
    safe = crates / f'ducktape-laya-{version}'
    sys_crate = crates / f'ducktape-laya-sys-{version}'
    assert 'path = ' not in (safe / 'Cargo.toml').read_text().split('[dependencies.ducktape-laya-sys]')[1].split('[')[0]
    assert not (safe / 'examples').exists()
    (app / 'Cargo.toml').write_text(f'''[package]
name = "installed-consumer"
version = "0.0.0"
edition = "2021"
[dependencies]
ducktape-laya = {{ path = "{safe.as_posix()}" }}
serde_json = "1"
[patch.crates-io]
ducktape-laya-sys = {{ path = "{sys_crate.as_posix()}" }}
''')
    # The sys crate finds the library through pkg-config where it is installed, otherwise
    # through DUCKTAPE_LAYA_LIB_DIR (the directory with the import library on Windows).
    if target.os != 'win32' and shutil.which('pkg-config'):
        build_env = dict(env, PKG_CONFIG_PATH=str(libdir / 'pkgconfig'))
    else:
        build_env = dict(env, DUCKTAPE_LAYA_LIB_DIR=str(libdir))
    subprocess.run([tool('cargo'), 'build', '--offline', '--quiet'], cwd=app, env=build_env, check=True)
    return consume([str(app / 'target/debug' / ('installed-consumer' + EXE)), str(model)], cwd=app, env=with_library_path(env, runtime, target))


def probe_python(directory, native):
    # The wheel bundles the library; loader overrides must not be needed.
    wheel = artifacts / f'ducktape_laya-{version}-py3-none-{target.wheel}.whl'
    venv = directory / 'venv'
    subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
    subprocess.run([str(venv_python(venv)), '-m', 'pip', 'install', '--quiet', '--no-index', str(wheel)], check=True)
    work = directory / 'python'
    work.mkdir()
    return consume([str(venv_python(venv)), str(root / 'tests/consumers/python/consumer.py'), str(model)], cwd=work, env=env)


def probe_java(directory, native):
    # The Maven jar bundles the library as well.
    jar = artifacts / f'ducktape-laya-{version}.jar'
    work = directory / 'java'
    work.mkdir()
    return consume(['java', '--enable-native-access=ALL-UNNAMED', '-cp', str(jar), str(root / 'tests/consumers/java/Consumer.java'), str(model)],
                   cwd=work, env=env)


def probe_dotnet(directory, native):
    # The NuGet package, restored from a local feed holding only the candidate package.
    dotnet = directory / 'dotnet'
    dotnet.mkdir()
    shutil.copy(root / 'tests/consumers/dotnet/Program.cs', dotnet / 'Program.cs')
    (dotnet / 'Consumer.csproj').write_text(f'''<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net10.0</TargetFramework><Nullable>enable</Nullable><ImplicitUsings>enable</ImplicitUsings></PropertyGroup>
  <ItemGroup><PackageReference Include="DucktapeLaya" Version="{version}" /></ItemGroup>
</Project>
''')
    feed = directory / 'feed'
    feed.mkdir()
    shutil.copy(artifacts / f'DucktapeLaya.{version}.nupkg', feed)
    (dotnet / 'nuget.config').write_text(f'''<configuration><packageSources><clear /><add key="local" value="{feed}" /></packageSources></configuration>''')
    dotnet_env = dict(env, DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_NOLOGO='1', NUGET_PACKAGES=str(directory / 'nuget-packages'))
    subprocess.run([tool('dotnet'), 'build', '-c', 'Release', '-o', str(dotnet / 'out'), '-m:1'], cwd=dotnet, env=dotnet_env,
                   check=True, capture_output=True)
    work = directory / 'dotnet-run'
    work.mkdir()
    return consume([tool('dotnet'), str(dotnet / 'out/Consumer.dll'), str(model)], cwd=work, env=dotnet_env)


def probe_go(directory, native):
    # Go is distributed as source: the module outside the checkout, linked against the archive
    # (on Windows by MinGW-w64 gcc against the import library).
    go = directory / 'go'
    shutil.copytree(root / 'bindings/go', go / 'binding')
    shutil.copytree(root / 'tests/consumers/go', go / 'consumer')
    module = (go / 'consumer/go.mod').read_text()
    (go / 'consumer/go.mod').write_text(module.replace('../../../bindings/go', '../binding'))
    go_env = dict(env, GOTOOLCHAIN='local', GOFLAGS='-mod=mod', CGO_ENABLED='1',
                  CGO_CFLAGS=f'-I{(native / "include").as_posix()}', CGO_LDFLAGS=f'-L{libdir.as_posix()}')
    program = go / ('consumer-bin' + EXE)
    subprocess.run([tool('go'), 'build', '-o', str(program), '.'], cwd=go / 'consumer', env=go_env, check=True, capture_output=True)
    work = go / 'run'
    work.mkdir()
    return consume([str(program), str(model)], cwd=work, env=with_library_path(env, runtime, target))


# The ecosystem probes are independent: each works in its own directory, and they run concurrently. Results are
# recorded and compared in this fixed order, and the first failing probe in this order is the one reported.
PROBES = {'native': probe_native, 'node': probe_node, 'rust': probe_rust, 'python': probe_python, 'java': probe_java,
          'dotnet': probe_dotnet, 'go': probe_go}
with tempfile.TemporaryDirectory(prefix='ducktape-artifacts-') as directory:
    directory = pathlib.Path(directory)
    crates = directory / 'crates'
    crates.mkdir()
    for artifact in artifacts.glob('*.crate'):
        with tarfile.open(artifact) as tar:
            tar.extractall(crates, filter='data')
    native = extract_native(artifacts, version, target, directory)
    libdir = next(native.glob('lib*/pkgconfig/ducktape-laya.pc')).parent.parent
    runtime = library_directory(native, target)
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, os.cpu_count() or 1)) as pool:
        futures = {}
        for name, probe in PROBES.items():
            (directory / f'probe-{name}').mkdir()
            futures[name] = pool.submit(probe, directory / f'probe-{name}', native)
        outputs = {name: future.result() for name, future in futures.items()}
    report['skipped'], native_outputs = outputs.pop('native')
    for name, output in native_outputs.items():
        record(name, output)
    rust = outputs['rust']
    for name, output in outputs.items():
        record(name, output)
    for name in ('node', 'python', 'java', 'dotnet', 'go'):
        assert outputs[name] == rust, name
    assert len(set(report['consumers'].values())) == 1, report['consumers']
report['status'] = 'passed'
print(f'Installed npm, Rust crate, Python wheel, Maven jar, NuGet and Go consumers on {target.name}: exact real-model agreement, '
      'no repository paths or inference server')
