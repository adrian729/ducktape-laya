#!/usr/bin/env python3
"""Exercise local npm and normalized Rust crate artifacts outside the checkout."""
import argparse
import json
import os
import pathlib
import shutil
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
inventory = verify_inventory(artifacts)
version, platform_dir = inventory['package_version'], inventory['platform']
request = (root / 'tests/fixtures/primitive.json').read_bytes()
with tempfile.TemporaryDirectory(prefix='ducktape-artifacts-') as directory:
    directory = pathlib.Path(directory)
    for artifact in list(artifacts.glob('*.crate')) + list(artifacts.glob(f'*{platform_dir}.tar.gz')):
        with tarfile.open(artifact) as tar:
            tar.extractall(directory, filter='data')
    native = directory / f'ducktape-laya-{version}-{platform_dir}'
    subprocess.run(['python3', str(root / 'tests/installed-native.py'), str(native), str(model)], check=True)
    app = directory / 'application'
    app.mkdir()
    (app / 'package.json').write_text('{"private":true}')
    subprocess.run(['npm', 'install', '--offline', '--ignore-scripts', '--no-audit', '--no-fund', str(artifacts / f'ducktape-laya-{version}.tgz')], cwd=app, check=True, capture_output=True)
    addon = app / f'node_modules/ducktape-laya/native/{platform_dir}/ducktape_laya.node'
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
    sys_crate = directory / f'ducktape-laya-sys-{version}'
    assert 'path = ' not in (safe / 'Cargo.toml').read_text().split('[dependencies.ducktape-laya-sys]')[1].split('[')[0]
    assert not (safe / 'examples').exists()
    (app / 'Cargo.toml').write_text(f'''[package]
name = "installed-consumer"
version = "0.0.0"
edition = "2021"
[dependencies]
ducktape-laya = {{ path = "{safe}" }}
serde_json = "1"
[patch.crates-io]
ducktape-laya-sys = {{ path = "{sys_crate}" }}
''')
    libdir = next(native.glob('lib*/pkgconfig/ducktape-laya.pc')).parent.parent
    env['PKG_CONFIG_PATH'] = str(libdir / 'pkgconfig')
    env['LD_LIBRARY_PATH'] = str(libdir)
    rust = json.loads(subprocess.check_output(['cargo', 'run', '--offline', '--quiet', '--', str(model)], cwd=app, env=env, input=request))
    assert node == rust
    # The wheel bundles the library; loader overrides must not be needed.
    wheel, = artifacts.glob(f'ducktape_laya-{version}-*.whl')
    venv = directory / 'venv'
    subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
    subprocess.run([str(venv / 'bin/python'), '-m', 'pip', 'install', '--quiet', '--no-index', str(wheel)], check=True)
    for key in ['LD_LIBRARY_PATH', 'PKG_CONFIG_PATH', 'PYTHONPATH']:
        env.pop(key, None)
    python = json.loads(subprocess.check_output([str(venv / 'bin/python'), str(root / 'tests/consumers/python/consumer.py'), str(model)],
                                                cwd=app, env=env, input=request))
    assert python == rust
    # The Maven jar bundles the library as well.
    jar = artifacts / f'ducktape-laya-{version}.jar'
    java = json.loads(subprocess.check_output(['java', '--enable-native-access=ALL-UNNAMED', '-cp', str(jar),
                                               str(root / 'tests/consumers/java/Consumer.java'), str(model)],
                                              cwd=app, env=env, input=request))
    assert java == rust
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
    subprocess.run(['dotnet', 'build', '-c', 'Release', '-o', str(dotnet / 'out'), '-m:1'], cwd=dotnet, env=dotnet_env,
                   check=True, capture_output=True)
    net = json.loads(subprocess.check_output(['dotnet', str(dotnet / 'out/Consumer.dll'), str(model)], cwd=app, env=dotnet_env, input=request))
    assert net == rust
    # Go is distributed as source: the module outside the checkout, linked against the tarball.
    go = directory / 'go'
    shutil.copytree(root / 'bindings/go', go / 'binding')
    shutil.copytree(root / 'tests/consumers/go', go / 'consumer')
    module = (go / 'consumer/go.mod').read_text()
    (go / 'consumer/go.mod').write_text(module.replace('../../../bindings/go', '../binding'))
    go_env = dict(env, GOTOOLCHAIN='local', GOFLAGS='-mod=mod', CGO_CFLAGS=f'-I{native / "include"}', CGO_LDFLAGS=f'-L{libdir}')
    subprocess.run(['go', 'build', '-o', str(go / 'consumer-bin'), '.'], cwd=go / 'consumer', env=go_env, check=True, capture_output=True)
    golang = json.loads(subprocess.check_output([str(go / 'consumer-bin'), str(model)], cwd=app,
                                                env=dict(env, LD_LIBRARY_PATH=str(libdir)), input=request))
    assert golang == rust
print('Installed npm, Rust crate, Python wheel, Maven jar, NuGet and Go consumers: exact real-model agreement, '
      'no repository paths or inference server')
