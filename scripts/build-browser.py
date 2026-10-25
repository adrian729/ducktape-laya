#!/usr/bin/env python3
"""Build the shared core for browser WASM/WebGPU using the declared local SDK.

Release builds compile without a cache (--compiler-launcher none, the default): no gate compares the shipped
.wasm/.mjs with an uncached build, and ccache's compiler check hashes the emcc wrapper, not the emsdk clang it
runs. --compiler-launcher ccache is for rehearsal and test builds; laya.build.json records the launcher
(compiler_launcher) and scripts/package-browser.py refuses such a build.
"""
import argparse
import os
import pathlib
import shutil
import subprocess
from release_support import ROOT, CCACHE_ENVIRONMENT, check_record, default_jobs, generator_arguments, read_json, git
p = argparse.ArgumentParser()
p.add_argument('--emsdk', type=pathlib.Path, required=True)
p.add_argument('--build', type=pathlib.Path, required=True)
p.add_argument('--backend', choices=['wasm', 'webgpu'], required=True)
p.add_argument('--jobs', type=int, help='build jobs (default: DUCKTAPE_JOBS, or min(CPUs, available memory / 0.6 GB))')
p.add_argument('--compiler-launcher', choices=['none', 'ccache'], default='none',
               help='compile through ccache (rehearsal builds only; packaging refuses them)')
p.add_argument('--reference', action='store_true', help='Build the test-only upstream reference separately')
p.add_argument('--trace-gpu', action='store_true', help='Enable upstream operation diagnostics for validation')
p.add_argument('--maximum-memory', type=int, default=4294967296,
               help='Test-only WebAssembly memory ceiling in bytes; packaging rejects anything but the default')
a = p.parse_args()
try:
    a.jobs = a.jobs if a.jobs is not None else default_jobs()
except ValueError as error:
    p.error(str(error))
if a.jobs < 1: p.error('--jobs must be positive')
launcher, env = '', dict(os.environ)
if a.compiler_launcher == 'ccache':
    launcher = shutil.which('ccache')
    if not launcher: p.error('--compiler-launcher ccache: ccache is not on PATH')
    env.update(CCACHE_ENVIRONMENT)
record = check_record()
pin = read_json(ROOT / 'release/browser-toolchain.json')
sdk = a.emsdk.resolve()
if git(sdk, 'rev-parse', 'HEAD') != pin['emsdk_commit']: p.error('emsdk checkout does not match toolchain pin')
em = sdk / 'upstream/emscripten'
version = subprocess.check_output([str(em / 'emcc'), '--version'], text=True)
if pin['sdk_version'] not in version or pin['emscripten_commit'] not in version: p.error('Installed Emscripten does not match toolchain pin')
build = a.build.resolve()
build.mkdir(parents=True, exist_ok=True)
try:
    generator = generator_arguments([], build)
except ValueError as error:
    p.error(str(error))
subprocess.run([str(em / 'emcmake'), 'cmake', '-S', str(ROOT), '-B', str(a.build.resolve()), *generator,
    f'-DDUCKTAPE_LLAMA_SOURCE={ROOT / record["runtime"]["path"]}', '-DDUCKTAPE_LLAMA_REVISION=',
    '-DDUCKTAPE_BUILD_FUZZERS=OFF', '-DDUCKTAPE_DEV_BUILD=OFF', '-DDUCKTAPE_DEV_PCH=OFF', '-DCMAKE_BUILD_TYPE=Release', '-DDUCKTAPE_BUILD_TESTS=OFF', '-DDUCKTAPE_BUILD_NODE=OFF', '-DDUCKTAPE_BUILD_EXAMPLES=OFF',
    '-DDUCKTAPE_WASM_OPTIMIZATION=1', '-DEMSCRIPTEN_SYSTEM_PROCESSOR=wasm32', '-DGGML_WEBGPU=' + ('ON' if a.backend == 'webgpu' else 'OFF'),
    '-DGGML_WEBGPU_DEBUG=' + ('ON' if a.trace_gpu else 'OFF'),
    '-DDUCKTAPE_BUILD_REFERENCE_PROBE=' + ('ON' if a.reference else 'OFF'),
    f'-DDUCKTAPE_WASM_MAXIMUM_MEMORY={a.maximum_memory}',
    # ggml's GGML_CCACHE (ON by default) would use any ccache on PATH through a global RULE_LAUNCH_COMPILE; the
    # launcher is only ever the explicit, recorded CMAKE_<LANG>_COMPILER_LAUNCHER (empty by default).
    '-DGGML_CCACHE=OFF', f'-DCMAKE_C_COMPILER_LAUNCHER={launcher}', f'-DCMAKE_CXX_COMPILER_LAUNCHER={launcher}'], check=True, cwd=build, env=env)
subprocess.run(['cmake', '--build', str(build), '--target', 'ducktape_laya_wasm', '--parallel', str(a.jobs)], check=True, cwd=build, env=env)
if a.reference:
    subprocess.run(['cmake', '--build', str(build), '--target', 'laya-reference', '--parallel', str(a.jobs)], check=True, cwd=build, env=env)
