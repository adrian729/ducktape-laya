#!/usr/bin/env python3
"""Load each local package outside the checkout without a model.

The native archive links and runs a C program through its CMake package, the
npm package loads its addon, the wheel, the Maven jar and the NuGet package load
their bundled library; each must report the record's ABI. Takes one platform's
package directory or an assembled multi-platform one and uses this machine's
platform. tests/installed-packages.py runs the real-model checks."""
import argparse
import concurrent.futures
import os
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
SKIP_MACOS = 'skipped: the DYLD_LIBRARY_PATH decoy checks run on macOS only'


def macos_decoys(native, target, directory):
    """Real copies of the library under every name a macOS loader might ask for, in a directory for
    DYLD_LIBRARY_PATH and one for the current directory. dyld searches DYLD_LIBRARY_PATH by leaf name even
    for an absolute path, so a same-leaf decoy there replaces the library asked for unless the binding
    notices: it must refuse the decoy (status 7, naming DYLD_LIBRARY_PATH) or never load it."""
    source = library_directory(native, target) / target.library
    decoys = {}
    for place in ('dyld', 'cwd'):
        decoys[place] = directory / f'decoy-{place}'
        decoys[place].mkdir()
        for name in {target.library, *target.node_libraries}:
            shutil.copy(source, decoys[place] / name)
    return decoys


def check_decoy_outcome(what, refused, images, expected):
    """Either the binding refused (a message naming DYLD_LIBRARY_PATH), or it loaded only the expected files."""
    if refused:
        if 'DYLD_LIBRARY_PATH' not in refused:
            raise SystemExit(f'{what}: refused with a message that does not name DYLD_LIBRARY_PATH: {refused}')
        return 'refused the decoy'
    if sorted(set(images)) != sorted(set(str(pathlib.Path(path).resolve()) for path in expected)):
        raise SystemExit(f'{what}: loaded {sorted(set(images))}, expected only {sorted(str(pathlib.Path(p).resolve()) for p in expected)}')
    return 'ignored the decoy'


# In a child process: the library images mapped (dyld's list) whose file is a ducktape_laya library.
MACOS_IMAGES_PY = (
    "import ctypes, json, os\n"
    "def images():\n"
    "    libsystem = ctypes.CDLL('/usr/lib/libSystem.B.dylib')\n"
    "    libsystem._dyld_get_image_name.restype = ctypes.c_char_p\n"
    "    libsystem._dyld_get_image_name.argtypes = [ctypes.c_uint32]\n"
    "    names = (libsystem._dyld_get_image_name(i) for i in range(libsystem._dyld_image_count()))\n"
    "    return sorted({os.path.realpath(os.fsdecode(n)) for n in names if n and os.path.basename(n).startswith(b'libducktape_laya')})\n")


def check_wheel_decoys(venv, native, target, directory, env):
    """macOS: the wheel's bundled library, with same-leaf decoys on DYLD_LIBRARY_PATH and in the current directory."""
    decoys = macos_decoys(native, target, directory)
    script = (MACOS_IMAGES_PY + "import ducktape_laya\n"
              "bundled = os.path.realpath(os.path.join(os.path.dirname(ducktape_laya.__file__), '_native', 'libducktape_laya.dylib'))\n"
              "refused = None\n"
              "try:\n    ducktape_laya.runtime_info()\nexcept ducktape_laya.LayaLoadError as error:\n    refused = str(error)\n"
              "print(json.dumps({'refused': refused, 'images': images(), 'bundled': bundled}))\n")
    outcomes = []
    for env_vars, label in (({'DYLD_LIBRARY_PATH': str(decoys['dyld'])}, 'DYLD_LIBRARY_PATH'), ({}, 'current directory')):
        result = subprocess.run([str(venv_python(venv)), '-c', script], cwd=decoys['cwd'], env=dict(env, **env_vars),
                                capture_output=True, text=True)
        if result.returncode: raise SystemExit(f'wheel with a decoy in the {label} failed: {result.stdout}{result.stderr}')
        data = json.loads(result.stdout)
        outcomes.append(check_decoy_outcome(f'wheel with a decoy in the {label}', data['refused'], data['images'], [data['bundled']]))
    return f'DYLD_LIBRARY_PATH decoy {outcomes[0]}; current-directory decoy {outcomes[1]}'


def check_jar_decoys(jar, probe, native, target, directory, env):
    """macOS: the jar's extracted library has a content-hash leaf, so a same-leaf decoy on DYLD_LIBRARY_PATH cannot
    shadow it; an explicitly configured library is checked with dladdr and refused if a decoy replaced it."""
    decoys = macos_decoys(native, target, directory)
    base = ['java', '--enable-native-access=ALL-UNNAMED', '-cp', str(jar)]
    run_env = dict(env, DYLD_LIBRARY_PATH=str(decoys['dyld']))
    result = subprocess.run([*base, str(probe)], cwd=decoys['cwd'], env=run_env, capture_output=True, text=True)
    if result.returncode or result.stdout != str(abi):
        raise SystemExit(f'jar with decoys on DYLD_LIBRARY_PATH and in the current directory failed: {result.stdout}{result.stderr}')
    real = directory / 'real'
    real.mkdir()
    shutil.copy(library_directory(native, target) / target.library, real / target.library)
    result = subprocess.run([*base, f'-Dducktape.laya.library={real / target.library}', str(probe)], cwd=decoys['cwd'], env=run_env,
                            capture_output=True, text=True)
    if result.returncode:
        refused = 'DYLD_LIBRARY_PATH' in result.stderr and 'Refusing' in result.stderr
        if not refused: raise SystemExit(f'jar with an explicit library and a DYLD_LIBRARY_PATH decoy failed unexpectedly: {result.stdout}{result.stderr}')
        return 'extracted library not shadowed; explicit library with a same-leaf decoy refused'
    if result.stdout != str(abi): raise SystemExit(f'jar with an explicit library reported {result.stdout!r}')
    return 'extracted library not shadowed; explicit library with a same-leaf decoy ignored'


def check_dotnet_resolution(out, native, target, directory, env):
    """The NuGet package loads the library it bundles (runtimes/<rid>/native/), never a copy of the
    same name in the current directory or on the search path (PATH on Windows, LD_LIBRARY_PATH on
    Linux). Without the bundled copy, Windows searches only the assembly and system directories, so
    those decoys are still not loaded; on Linux the system fallback is the dynamic loader's search
    (LD_LIBRARY_PATH, never the current directory). The decoys are real copies of the library, so
    loading one would succeed and show in the process's mapped files."""
    names = [target.library] if target.os == 'win32' else [target.library, target.node_libraries[0]]
    source = library_directory(native, target) / target.library
    decoys = {}
    for place in ('cwd', 'search-path'):
        decoys[place] = directory / f'decoy-{place}'
        decoys[place].mkdir()
        for name in names:
            shutil.copy(source, decoys[place] / name)
    search = with_library_path(env, decoys['search-path'], target)

    def loaded(app, run_env):
        result = subprocess.run([tool('dotnet'), str(app / 'Probe.dll'), '--loaded-from'], cwd=decoys['cwd'], env=run_env,
                                capture_output=True, text=True)
        return result, [pathlib.Path(line).resolve() for line in result.stdout.splitlines() if line]

    bundled = (out / 'runtimes' / target.nuget / 'native' / target.library).resolve()
    if not bundled.is_file(): raise SystemExit(f'NuGet consumer output lacks {bundled}')
    result, files = loaded(out, search)
    # macOS: a same-leaf decoy on DYLD_LIBRARY_PATH is either never loaded or refused with a message naming it.
    refused = target.os == 'darwin' and result.returncode and not files and 'DYLD_LIBRARY_PATH' in result.stdout + result.stderr
    if not refused and (result.returncode or files != [bundled]):
        raise SystemExit(f'NuGet package with decoys in the current directory and on the search path loaded {files}, '
                         f'expected only {bundled}: {result.stdout}{result.stderr}')
    # The same application without the bundled library (and without its deps.json, which lists it).
    bare = directory / 'dotnet-unbundled'
    shutil.copytree(out, bare, ignore=shutil.ignore_patterns('runtimes', 'Probe.deps.json', target.library))
    result, files = loaded(bare, search if target.os in ('win32', 'darwin') else env)
    # A copy installed in the system's own library directories (ldconfig, /usr/local/lib) is a
    # legitimate answer of the system fallback; only a decoy must never load.
    system_copy = (result.returncode == 0 and len(files) == 1 and files[0].name in names and
                   not files[0].is_relative_to(scratch_root.resolve()))
    # macOS loads installed libraries by absolute path only, so a decoy on DYLD_LIBRARY_PATH is never a candidate.
    if system_copy:
        print(f'NuGet package without its bundled library loaded the system copy {files[0]} (not a decoy)')
    elif result.returncode == 0 or files or f'{target.library} was not found' not in result.stderr:
        raise SystemExit(f'NuGet package without its bundled library loaded {files} (exit {result.returncode}); expected '
                         f'"{target.library} was not found" with decoys only in the current directory'
                         f'{" and on PATH" if target.os == "win32" else ""}: {result.stdout}{result.stderr}')
    # Control: the probe sees a decoy when one is loaded (selected explicitly with DUCKTAPE_LAYA_LIB_DIR).
    result, files = loaded(bare, dict(env, DUCKTAPE_LAYA_LIB_DIR=str(decoys['cwd'])))
    if result.returncode or files != [(decoys['cwd'] / target.library).resolve()]:
        raise SystemExit(f'DUCKTAPE_LAYA_LIB_DIR={decoys["cwd"]} loaded {files}: {result.stdout}{result.stderr}')
    if target.os == 'darwin':
        # An explicit library with a same-leaf decoy on DYLD_LIBRARY_PATH: refused, or the explicit file itself is loaded.
        explicit = directory / 'explicit'
        explicit.mkdir()
        shutil.copy(source, explicit / target.library)
        result, files = loaded(bare, dict(search, DUCKTAPE_LAYA_LIB_DIR=str(explicit)))
        refused = result.returncode and not files and 'DYLD_LIBRARY_PATH' in result.stdout + result.stderr
        if not refused and (result.returncode or files != [(explicit / target.library).resolve()]):
            raise SystemExit(f'DUCKTAPE_LAYA_LIB_DIR={explicit} with a DYLD_LIBRARY_PATH decoy loaded {files}: {result.stdout}{result.stderr}')
    if target.os == 'linux':
        result, files = loaded(bare, search)
        expected = decoys['search-path'].resolve()
        if result.returncode or len(files) != 1 or files[0].parent != expected:
            raise SystemExit(f'NuGet system fallback loaded {files}, expected one library from LD_LIBRARY_PATH ({expected}): '
                             f'{result.stdout}{result.stderr}')
    return 'bundled library loaded; current-directory and search-path decoys ignored'


def check_node_resolution(app, native, target, directory, env):
    """On Linux the npm package's addon finds its own libducktape_laya.so.1 through DT_RPATH $ORIGIN, which the
    loader searches before LD_LIBRARY_PATH (a DT_RUNPATH would be searched after it): a copy of the library on
    LD_LIBRARY_PATH is not loaded. The decoy is a real copy, so loading it would succeed and show in
    /proc/self/maps."""
    addon = app / 'node_modules/ducktape-laya/native' / target.npm / 'ducktape_laya.node'
    dynamic = subprocess.check_output(['readelf', '-d', str(addon)], text=True)
    if 'RUNPATH' in dynamic or not re.search(r'\(RPATH\)\s+Library rpath: \[\$ORIGIN', dynamic):
        raise SystemExit(f'{addon}: expected a DT_RPATH starting with $ORIGIN and no DT_RUNPATH:\n{dynamic}')
    decoy = directory / 'decoy-node'
    decoy.mkdir()
    for name in target.node_libraries:
        shutil.copy(library_directory(native, target) / target.library, decoy / name)
    script = ("require('ducktape-laya').Laya.runtimeInfo(); const fs = require('node:fs');"
              "process.stdout.write([...new Set(fs.readFileSync('/proc/self/maps', 'utf8').split('\\n')"
              ".map(line => line.slice(line.indexOf('/'))).filter(file => file.includes('/libducktape_laya.so')))].join('\\n'))")
    files = subprocess.check_output(['node', '-e', script], cwd=app, env=with_library_path(env, decoy, target), text=True).split()
    expected = [str((addon.parent / target.node_libraries[0]).resolve())]
    if [str(pathlib.Path(file).resolve()) for file in files] != expected:
        raise SystemExit(f'With a decoy on LD_LIBRARY_PATH the addon loaded {files}, expected only {expected}')
    return 'bundled library loaded; LD_LIBRARY_PATH decoy ignored (DT_RPATH $ORIGIN)'


def check_node_resolution_macos(app, native, target, directory, env):
    """On macOS the addon's rpaths are @loader_path and @loader_path/../.. only, and at its first use the addon
    requires the image that provides dtl_abi_version to be in its own directory or two levels up. A same-leaf
    decoy on DYLD_LIBRARY_PATH is therefore refused (status 7, naming DYLD_LIBRARY_PATH) or never loaded, and one in
    the current directory is never loaded. The loaded images come from process.report's shared-object list."""
    addon = app / 'node_modules/ducktape-laya/native' / target.npm / 'ducktape_laya.node'
    commands = subprocess.check_output(['otool', '-l', str(addon)], text=True)
    rpaths = re.findall(r'cmd LC_RPATH\n(?:.*\n)*?\s+path (\S+) \(offset', commands)
    if not rpaths or any(not rpath.startswith('@loader_path') for rpath in rpaths):
        raise SystemExit(f'{addon}: expected only @loader_path rpaths, found {rpaths}')
    decoys = macos_decoys(native, target, directory)
    script = ("const path = require('node:path'); let refused = '';"
              f"try {{ require({json.dumps(str(app / 'node_modules/ducktape-laya'))}).Laya.runtimeInfo(); }} catch (error) {{ refused = error.message; }}"
              "const files = [...new Set(process.report.getReport().sharedObjects.filter(file => path.basename(file).startsWith('libducktape_laya')))];"
              "process.stdout.write(JSON.stringify({ refused, files }))")
    expected = [(addon.parent / target.node_libraries[0]).resolve()]
    outcomes = []
    for env_vars, label in (({'DYLD_LIBRARY_PATH': str(decoys['dyld'])}, 'DYLD_LIBRARY_PATH'), ({}, 'current directory')):
        result = subprocess.run(['node', '-e', script], cwd=decoys['cwd'], env=dict(env, **env_vars), capture_output=True, text=True)
        if result.returncode: raise SystemExit(f'node with a decoy in the {label} failed: {result.stdout}{result.stderr}')
        data = json.loads(result.stdout)
        images = [str(pathlib.Path(file).resolve()) for file in data['files']]
        outcomes.append(check_decoy_outcome(f'npm package with a decoy in the {label}', data['refused'], images, expected))
        if label == 'current directory' and data['refused']:
            raise SystemExit(f'npm package refused a library with only a current-directory decoy: {data["refused"]}')
    return f'DYLD_LIBRARY_PATH decoy {outcomes[0]}; current-directory decoy {outcomes[1]}; only @loader_path rpaths'


p = argparse.ArgumentParser(description=__doc__)
p.add_argument('artifacts', type=pathlib.Path)
p.add_argument('--platform', help='Package platform to test (default: this machine\'s; it must be this machine\'s)')
a = p.parse_args()
artifacts = a.artifacts.resolve()
inventory, version, target = host_packages(artifacts, a.platform)
abi = read_json(root / 'release/compatibility.json')['abi_version']
env = clean_environment()
def probe_native(directory, native):
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
    return {'native': subprocess.check_output([str(built_program(probe / 'build', 'abi'))], env=run_env, text=True)}, {}


def probe_npm(directory, native):
    app = directory / 'application'
    app.mkdir()
    (app / 'package.json').write_text('{"private":true}')
    subprocess.run([tool('npm'), 'install', '--offline', '--ignore-scripts', '--no-audit', '--no-fund',
                    str(artifacts / f'ducktape-laya-{version}.tgz')], cwd=app, check=True, capture_output=True)
    reported = subprocess.check_output(['node', '-e', "const r = require('ducktape-laya/resolve-addon.cjs')(); "
                                        f"if (!r.includes(require('node:path').join('native', '{target.npm}', 'ducktape_laya.node'))) throw new Error(r); "
                                        "process.stdout.write(String(require('ducktape-laya').Laya.runtimeInfo().abi_version))"],
                                       cwd=app, env=env, text=True)
    if target.os == 'linux':
        node_resolution = check_node_resolution(app, native, target, directory, env)
    elif target.os == 'darwin':
        node_resolution = check_node_resolution_macos(app, native, target, directory, env)
    else:
        node_resolution = 'skipped: the loaded-library check reads /proc/self/maps and the DT_RPATH (Linux) or the loaded images (macOS)'
    return {'npm': reported}, {'npm_resolution': node_resolution}


def probe_wheel(directory, native):
    wheel = artifacts / f'ducktape_laya-{version}-py3-none-{target.wheel}.whl'
    venv = directory / 'venv'
    subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
    subprocess.run([str(venv_python(venv)), '-m', 'pip', 'install', '--quiet', '--no-index', str(wheel)], check=True)
    reported = subprocess.check_output([str(venv_python(venv)), '-c', 'import ducktape_laya; print(ducktape_laya.runtime_info()["abi_version"], end="")'],
                                       cwd=directory, env=env, text=True)
    resolution = check_wheel_decoys(venv, native, target, directory, env) if target.os == 'darwin' else SKIP_MACOS
    return {'wheel': reported}, {'wheel_resolution': resolution}


def probe_jar(directory, native):
    # The Maven jar extracts its bundled library when no library is configured.
    java = directory / 'Probe.java'
    java.write_text('public class Probe { public static void main(String[] a) {\n'
                    '  String info = io.github.adrian729.laya.Laya.runtimeInfo();\n'
                    '  System.out.print(info.replaceAll("(?s).*.abi_version.:[ ]*([0-9]+).*", "$1")); } }\n')
    reported = subprocess.check_output(['java', '--enable-native-access=ALL-UNNAMED', '-cp',
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
    resolution = check_jar_decoys(artifacts / f'ducktape-laya-{version}.jar', java, native, target, directory, env) if target.os == 'darwin' else SKIP_MACOS
    return {'jar': reported}, {'jar_resolution': resolution}


def probe_nupkg(directory, native):
    # The NuGet package, restored from a local feed holding only the candidate package.
    dotnet = directory / 'dotnet'
    dotnet.mkdir()
    # With --loaded-from the probe prints the file of every ducktape_laya library mapped into the process
    # (/proc/self/maps on Linux, the process modules on Windows, dyld's image list on macOS).
    (dotnet / 'Program.cs').write_text('''using System.Text.Json;
using System.Runtime.InteropServices;
[DllImport("/usr/lib/libSystem.B.dylib")] static extern uint _dyld_image_count();
[DllImport("/usr/lib/libSystem.B.dylib")] static extern IntPtr _dyld_get_image_name(uint index);
var info = JsonDocument.Parse(Ducktape.Laya.LayaEngine.RuntimeInfo());
if (args.Length > 0 && args[0] == "--loaded-from")
{
    var loaded = new List<string>();
    if (OperatingSystem.IsLinux())
    {
        foreach (var line in File.ReadLines("/proc/self/maps"))
        {
            var start = line.IndexOf('/');
            if (start >= 0 && Path.GetFileName(line[start..]).StartsWith("libducktape_laya.so") && !loaded.Contains(line[start..])) loaded.Add(line[start..]);
        }
    }
    else if (OperatingSystem.IsMacOS())
    {
        for (uint i = 0, count = _dyld_image_count(); i < count; i++)
        {
            var name = Marshal.PtrToStringUTF8(_dyld_get_image_name(i));
            if (name is not null && Path.GetFileName(name).StartsWith("libducktape_laya") && !loaded.Contains(name)) loaded.Add(name);
        }
    }
    else
    {
        foreach (System.Diagnostics.ProcessModule module in System.Diagnostics.Process.GetCurrentProcess().Modules)
            if (string.Equals(module.ModuleName, "ducktape_laya.dll", StringComparison.OrdinalIgnoreCase)) loaded.Add(module.FileName);
    }
    Console.Write(string.Join("\\n", loaded));
    return;
}
System.Console.Write(info.RootElement.GetProperty("abi_version").GetInt32());
''')
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
    reported = subprocess.check_output([tool('dotnet'), str(dotnet / 'out/Probe.dll')], cwd=directory, env=dotnet_env, text=True)
    resolution = check_dotnet_resolution(dotnet / 'out', native, target, directory, dotnet_env)
    return {'nupkg': reported}, {'nupkg_resolution': resolution}


# The package probes are independent: each works in its own directory, and they run concurrently. Their results
# are collected in this fixed order, and the first failing probe in this order is the one reported.
PROBES = (probe_native, probe_npm, probe_wheel, probe_jar, probe_nupkg)
reported, resolutions = {}, {}
with tempfile.TemporaryDirectory(prefix='ducktape-smoke-') as directory:
    directory = scratch_root = pathlib.Path(directory)
    native = extract_native(artifacts, version, target, directory)
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, os.cpu_count() or 1)) as pool:
        futures = []
        for probe in PROBES:
            (directory / probe.__name__).mkdir()
            futures.append(pool.submit(probe, directory / probe.__name__, native))
        for future in futures:
            values, notes = future.result()
            reported.update(values)
            resolutions.update(notes)
bad = {name: value for name, value in reported.items() if value != str(abi)}
if bad:
    raise SystemExit(f'Packages report the wrong ABI (expected {abi}): {bad}')
print(json.dumps({'abi_version': abi, 'platform': target.name, 'packages': sorted(reported), **resolutions}))
