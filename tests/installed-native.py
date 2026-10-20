#!/usr/bin/env python3
"""Compile actual C/C++ installed consumers outside the checkout and infer."""
import argparse
import json
import os
import pathlib
import shutil
import subprocess
import tempfile

root = pathlib.Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument('prefix', type=pathlib.Path)
p.add_argument('model', type=pathlib.Path)
a = p.parse_args()
prefix, model = a.prefix.resolve(), a.model.resolve()
fixture = (root / 'tests/fixtures/primitive.json').read_bytes()
# The installed package must satisfy a request for its own minor version.
minor = '.'.join(json.loads((root / 'release/compatibility.json').read_text())['package_version'].split('.')[:2])
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
    subprocess.run(['cmake', '-S', str(tmp), '-B', str(tmp / 'build'), f'-DCMAKE_PREFIX_PATH={prefix}'], check=True, capture_output=True)
    subprocess.run(['cmake', '--build', str(tmp / 'build')], check=True, capture_output=True)
    results = [json.loads(subprocess.check_output([str(tmp / 'build' / exe), str(model)], input=fixture)) for exe in ['c', 'cpp']]
    assert results[0] == results[1]
    pc = list(prefix.glob('lib*/pkgconfig/ducktape-laya.pc'))
    assert len(pc) == 1
    env = dict(os.environ, PKG_CONFIG_PATH=str(pc[0].parent))
    flags = subprocess.check_output(['pkg-config', '--cflags', '--libs', 'ducktape-laya'], env=env, text=True)
    import shlex
    subprocess.run(['cc', str(tmp / 'c.c'), *shlex.split(flags), '-o', str(tmp / 'pc-consumer')], check=True)
    env['LD_LIBRARY_PATH'] = str(pc[0].parent.parent)
    assert json.loads(subprocess.check_output([str(tmp / 'pc-consumer'), str(model)], env=env, input=fixture)) == results[0]
print('Installed CMake C/C++ and pkg-config C consumers: real-model agreement')
