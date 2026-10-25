#!/usr/bin/env python3
"""Compile actual C/C++ installed consumers outside the checkout and infer."""
import argparse
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import tempfile

from package_support import built_program, clean_environment, cmake_consumer_options, library_directory, tool, with_library_path
from release_support import host_target

root = pathlib.Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument('prefix', type=pathlib.Path)
p.add_argument('model', type=pathlib.Path)
p.add_argument('--outputs', type=pathlib.Path, help='Write each consumer\'s result here as JSON')
p.add_argument('--skipped', type=pathlib.Path, help='Write the consumers not run here, with the reason, as JSON')
a = p.parse_args()
prefix, model = a.prefix.resolve(), a.model.resolve()
target = host_target()
fixture = (root / 'tests/fixtures/primitive.json').read_bytes()
# The installed package must satisfy a request for its own minor version.
minor = '.'.join(json.loads((root / 'release/compatibility.json').read_text())['package_version'].split('.')[:2])
# Windows has no run path: programs find the DLL in the prefix's bin/ through PATH.
env = with_library_path(clean_environment(), library_directory(prefix, target), target) if target.os == 'win32' else clean_environment()


def quiet(command):
    """Run a build step without its output, but show the output when it fails."""
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode:
        sys.stdout.flush()
        sys.stdout.buffer.write(result.stdout)
        sys.stdout.flush()
        raise SystemExit(f'{shlex.join(command)} failed with exit status {result.returncode}')


with tempfile.TemporaryDirectory(prefix='ducktape-consumer-') as tmp:
    tmp = pathlib.Path(tmp)
    for source in ['c.c', 'cpp.cpp']:
        shutil.copy(root / 'tests/consumers' / source, tmp / source)
    (tmp / 'CMakeLists.txt').write_text(f'''cmake_minimum_required(VERSION 3.22)
project(consumer LANGUAGES C CXX)
find_package(ducktape_laya {minor} CONFIG REQUIRED)
add_executable(c c.c)
add_executable(cpp cpp.cpp)
target_link_libraries(c PRIVATE ducktape::laya)
target_link_libraries(cpp PRIVATE ducktape::laya)
''')
    quiet(['cmake', '-S', str(tmp), '-B', str(tmp / 'build'), f'-DCMAKE_PREFIX_PATH={prefix}', *cmake_consumer_options()])
    quiet(['cmake', '--build', str(tmp / 'build'), '--config', 'Release'])
    results = [json.loads(subprocess.check_output([str(built_program(tmp / 'build', exe)), str(model)], input=fixture, env=env))
               for exe in ['c', 'cpp']]
    assert results[0] == results[1]
    outputs = {'cmake_c': results[0], 'cmake_cpp': results[1]}
    pc = list(prefix.glob('lib*/pkgconfig/ducktape-laya.pc'))
    assert len(pc) == 1
    # pkg-config with a GNU-style compiler: cc on Linux and macOS, MinGW-w64 gcc (which links the
    # import library ducktape_laya.lib for -lducktape_laya) on Windows when it and pkg-config exist.
    compiler = 'gcc' if target.os == 'win32' else 'cc'
    skipped = {}
    if target.os == 'win32' and not (shutil.which('pkg-config') and shutil.which(compiler)):
        skipped['pkgconfig_c'] = 'pkg-config or MinGW-w64 gcc is not installed on this Windows machine'
        print('pkg-config consumer: skipped,', skipped['pkgconfig_c'])
    else:
        # Keep every flag: some pkg-config implementations drop directories they take for system ones.
        pc_env = dict(env, PKG_CONFIG_PATH=str(pc[0].parent), PKG_CONFIG_ALLOW_SYSTEM_CFLAGS='1', PKG_CONFIG_ALLOW_SYSTEM_LIBS='1')
        flags = ' '.join(subprocess.check_output([tool('pkg-config'), option, 'ducktape-laya'], env=pc_env, text=True).strip()
                         for option in ('--cflags', '--libs'))
        print(f'pkg-config consumer: {tool("pkg-config")} gives {flags}')
        if '-I' not in flags:
            raise SystemExit(f'{tool("pkg-config")} gave no include directory: {flags}')
        program = tmp / ('pc-consumer' + ('.exe' if target.os == 'win32' else ''))
        subprocess.run([tool(compiler), str(tmp / 'c.c'), *shlex.split(flags, posix=target.os != 'win32'), '-o', str(program)], check=True)
        run_env = with_library_path(env, library_directory(prefix, target), target)
        outputs['pkgconfig_c'] = json.loads(subprocess.check_output([str(program), str(model)], env=run_env, input=fixture))
        assert outputs['pkgconfig_c'] == results[0]
if a.outputs:
    a.outputs.write_text(json.dumps(outputs) + '\n')
if a.skipped:
    a.skipped.write_text(json.dumps(skipped) + '\n')
print('Installed CMake C/C++ and pkg-config C consumers: real-model agreement')
