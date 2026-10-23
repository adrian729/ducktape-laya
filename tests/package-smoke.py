#!/usr/bin/env python3
"""Load each local package outside the checkout without a model.

The native archive links and runs a C program through its CMake package, the
npm package loads its addon, the wheel, the Maven jar and the NuGet package load
their bundled library; each must report the record's ABI. Takes one platform's
package directory or an assembled multi-platform one and uses this machine's
platform. tests/installed-packages.py runs the real-model checks."""
import argparse
import json
import pathlib
import shutil
import subprocess
import re
import sys
import tempfile
import zipfile

from package_support import (built_program, clean_environment, cmake_consumer_options, extract_native, host_packages, library_directory,
                             tool, venv_python, with_library_path)
from release_support import read_json
import java_package

root = pathlib.Path(__file__).resolve().parents[1]


def manifest_attributes(data):
    """The main attributes of a JAR manifest, by the rules java.util.jar applies: lines end in CRLF, LF or CR,
    the main section ends at an empty line, every line is `Name: value` (a space after the colon), and the last
    line must be terminated (java.util.jar silently drops an unterminated line; a literal backslash-r-backslash-n
    is no line break at all)."""
    text = data.decode('utf-8')
    if not text.endswith(('\r\n', '\n', '\r')): raise SystemExit('MANIFEST.MF: last line is not terminated')
    attributes = {}
    for line in re.split(r'\r\n|\n|\r', text)[:-1]:
        if not line: break
        match = re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9_-]{0,69}): (.*)', line)
        if not match: raise SystemExit(f'MANIFEST.MF: not a manifest line: {line[:80]!r}')
        attributes[match[1]] = match[2]
    return attributes
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('artifacts', type=pathlib.Path)
p.add_argument('--platform', help='Package platform to test (default: this machine\'s; it must be this machine\'s)')
a = p.parse_args()
artifacts = a.artifacts.resolve()
inventory, version, target = host_packages(artifacts, a.platform)
abi = read_json(root / 'release/compatibility.json')['abi_version']
env = clean_environment()
with tempfile.TemporaryDirectory(prefix='ducktape-smoke-') as directory:
    directory = pathlib.Path(directory)
    native = extract_native(artifacts, version, target, directory)
    probe = directory / 'abi'
    probe.mkdir()
    (probe / 'abi.c').write_text('#include "ducktape_laya.h"\n#include <stdio.h>\nint main(void) { printf("%u", dtl_abi_version()); return 0; }\n')
    (probe / 'CMakeLists.txt').write_text('cmake_minimum_required(VERSION 3.22)\nproject(abi LANGUAGES C)\n'
                                          'find_package(ducktape_laya CONFIG REQUIRED)\nadd_executable(abi abi.c)\n'
                                          'target_link_libraries(abi PRIVATE ducktape::laya)\n')
    subprocess.run(['cmake', '-S', str(probe), '-B', str(probe / 'build'), f'-DCMAKE_PREFIX_PATH={native}', *cmake_consumer_options()],
                   check=True, capture_output=True)
    subprocess.run(['cmake', '--build', str(probe / 'build'), '--config', 'Release'], check=True, capture_output=True)
    # Linux and macOS programs find the library through the build tree's run path; Windows needs bin/ on PATH.
    run_env = with_library_path(env, library_directory(native, target), target) if target.os == 'win32' else env
    reported = {'native': subprocess.check_output([str(built_program(probe / 'build', 'abi'))], env=run_env, text=True)}
    app = directory / 'application'
    app.mkdir()
    (app / 'package.json').write_text('{"private":true}')
    subprocess.run([tool('npm'), 'install', '--offline', '--ignore-scripts', '--no-audit', '--no-fund',
                    str(artifacts / f'ducktape-laya-{version}.tgz')], cwd=app, check=True, capture_output=True)
    reported['npm'] = subprocess.check_output(['node', '-e', "const r = require('ducktape-laya/resolve-addon.cjs')(); "
                                               f"if (!r.includes(require('node:path').join('native', '{target.npm}', 'ducktape_laya.node'))) throw new Error(r); "
                                               "process.stdout.write(String(require('ducktape-laya').Laya.runtimeInfo().abi_version))"],
                                              cwd=app, env=env, text=True)
    wheel = artifacts / f'ducktape_laya-{version}-py3-none-{target.wheel}.whl'
    venv = directory / 'venv'
    subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
    subprocess.run([str(venv_python(venv)), '-m', 'pip', 'install', '--quiet', '--no-index', str(wheel)], check=True)
    reported['wheel'] = subprocess.check_output([str(venv_python(venv)), '-c', 'import ducktape_laya; print(ducktape_laya.runtime_info()["abi_version"], end="")'],
                                                cwd=directory, env=env, text=True)
    # The Maven jar extracts its bundled library when no library is configured.
    java = directory / 'Probe.java'
    java.write_text('public class Probe { public static void main(String[] a) {\n'
                    '  String info = io.github.adrian729.laya.Laya.runtimeInfo();\n'
                    '  System.out.print(info.replaceAll("(?s).*.abi_version.:[ ]*([0-9]+).*", "$1")); } }\n')
    reported['jar'] = subprocess.check_output(['java', '--enable-native-access=ALL-UNNAMED', '-cp',
                                               str(artifacts / f'ducktape-laya-{version}.jar'), str(java)],
                                              cwd=directory, env=env, text=True)
    # Every Maven jar's manifest is readable, and the main jar's carries the version and module name.
    for name in (f'ducktape-laya-{version}.jar', f'ducktape-laya-{version}-sources.jar', f'ducktape-laya-{version}-javadoc.jar'):
        with zipfile.ZipFile(artifacts / name) as archive:
            attributes = manifest_attributes(archive.read('META-INF/MANIFEST.MF'))
        if (attributes.get('Manifest-Version'), attributes.get('Implementation-Version'), attributes.get('Automatic-Module-Name')) != \
                ('1.0', version, java_package.MODULE):
            raise SystemExit(f'{name}: unexpected manifest {attributes}')
    (directory / 'ManifestProbe.java').write_text(
        'public class ManifestProbe { public static void main(String[] a) throws Exception {\n'
        '  try (var jar = new java.util.jar.JarFile(a[0])) { var m = jar.getManifest().getMainAttributes();\n'
        '    System.out.print(m.getValue("Implementation-Version") + " " + m.getValue("Automatic-Module-Name")); } } }\n')
    seen = subprocess.check_output(['java', str(directory / 'ManifestProbe.java'), str(artifacts / f'ducktape-laya-{version}.jar')],
                                   cwd=directory, env=env, text=True)
    if seen != f'{version} {java_package.MODULE}': raise SystemExit(f'java.util.jar reads the jar manifest as {seen!r}')
    # The NuGet package, restored from a local feed holding only the candidate package.
    dotnet = directory / 'dotnet'
    dotnet.mkdir()
    (dotnet / 'Program.cs').write_text('using System.Text.Json;\n'
                                       'var info = JsonDocument.Parse(Ducktape.Laya.LayaEngine.RuntimeInfo());\n'
                                       'System.Console.Write(info.RootElement.GetProperty("abi_version").GetInt32());\n')
    (dotnet / 'Probe.csproj').write_text(f'''<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net10.0</TargetFramework><Nullable>enable</Nullable><ImplicitUsings>enable</ImplicitUsings></PropertyGroup>
  <ItemGroup><PackageReference Include="DucktapeLaya" Version="{version}" /></ItemGroup>
</Project>
''')
    feed = directory / 'feed'
    feed.mkdir()
    shutil.copy(artifacts / f'DucktapeLaya.{version}.nupkg', feed)
    (dotnet / 'nuget.config').write_text(f'<configuration><packageSources><clear /><add key="local" value="{feed}" /></packageSources></configuration>')
    dotnet_env = dict(env, DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_NOLOGO='1', NUGET_PACKAGES=str(directory / 'nuget-packages'))
    subprocess.run([tool('dotnet'), 'build', '-c', 'Release', '-o', str(dotnet / 'out'), '-m:1'], cwd=dotnet, env=dotnet_env,
                   check=True, capture_output=True)
    reported['nupkg'] = subprocess.check_output([tool('dotnet'), str(dotnet / 'out/Probe.dll')], cwd=directory, env=dotnet_env, text=True)
bad = {name: value for name, value in reported.items() if value != str(abi)}
if bad:
    raise SystemExit(f'Packages report the wrong ABI (expected {abi}): {bad}')
print(json.dumps({'abi_version': abi, 'platform': target.name, 'packages': sorted(reported)}))
