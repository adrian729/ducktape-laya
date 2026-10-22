"""Helpers for the tests that install package artifacts outside the checkout.

A package directory is either one platform's scripts/package-native.py output or
a scripts/assemble-packages.py output; either way the tests use the files for
the platform they run on."""
import os
import pathlib
import shutil
import sys
import tarfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from artifact_support import verify_inventory  # noqa: E402
from release_support import EXE, host_target  # noqa: E402

# Loader overrides the installed packages must not need.
OVERRIDES = ('DUCKTAPE_LAYA_ADDON', 'DUCKTAPE_LAYA_LIB_DIR', 'LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH', 'PYTHONPATH',
             'PKG_CONFIG_PATH', 'LAYA_MODEL')


def host_packages(directory, platform=None):
    """(inventory, version, target): the verified directory and this machine's package platform.

    platform, when given, must be this machine's: packages are installed where they run."""
    inventory = verify_inventory(directory)
    target = host_target()
    if platform and platform != target.name:
        raise SystemExit(f'{platform} packages cannot be tested on this {target.name} machine')
    platforms = inventory.get('platforms') or [inventory['platform']]
    if target.name not in platforms:
        raise SystemExit(f'{directory} holds packages for {", ".join(platforms)}, not for this {target.name} machine')
    return inventory, inventory['package_version'], target


def clean_environment():
    """The environment without the loader overrides; Python consumers read and write UTF-8."""
    env = {key: value for key, value in os.environ.items() if key not in OVERRIDES}
    env['PYTHONUTF8'] = '1'
    return env


def extract_native(artifacts, version, target, directory):
    """Unpack the native archive (tar.gz, or zip on Windows) and return its prefix."""
    archive = artifacts / f'ducktape-laya-{version}-{target.name}{target.archive}'
    if target.archive == '.zip':
        with zipfile.ZipFile(archive) as stream:
            stream.extractall(directory)
    else:
        with tarfile.open(archive) as stream:
            stream.extractall(directory, filter='data')
    return directory / f'ducktape-laya-{version}-{target.name}'


def library_directory(prefix, target):
    """Where the shared library loads from: bin/ on Windows (the DLL), lib/ elsewhere."""
    return prefix / 'bin' if target.os == 'win32' else next(prefix.glob(f'lib*/{target.library}')).parent


def with_library_path(env, directory, target):
    """env with directory on the platform's run-time library search path."""
    key = {'linux': 'LD_LIBRARY_PATH', 'darwin': 'DYLD_LIBRARY_PATH', 'win32': 'PATH'}[target.os]
    return dict(env, **{key: os.pathsep.join(filter(None, [str(directory), env.get(key)]))})


def tool(name):
    """A tool's full path; on Windows npm is npm.cmd, which subprocess finds only by its full name."""
    path = shutil.which(name)
    if not path:
        raise SystemExit(f'{name} is not installed')
    return path


def venv_python(venv):
    return venv / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')


def cmake_consumer_options():
    """Configure options for consumer projects: on Windows, Visual Studio's own C/C++ compiler
    (MSVC), as most applications use, against the clang-cl-built DLL."""
    return ['-DCMAKE_BUILD_TYPE=Release']


def built_program(build, name):
    """A program from a single- (Ninja, Make) or multi-configuration (Visual Studio) build tree."""
    for candidate in (build / (name + EXE), build / 'Release' / (name + EXE)):
        if candidate.is_file():
            return candidate
    raise SystemExit(f'{name}{EXE} was not built in {build}')
