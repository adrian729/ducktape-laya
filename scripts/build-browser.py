#!/usr/bin/env python3
"""Build the shared core for browser WASM/WebGPU using the declared local SDK."""
import argparse
import pathlib
import subprocess
from release_support import ROOT, check_record, read_json, git
p = argparse.ArgumentParser()
p.add_argument('--emsdk', type=pathlib.Path, required=True)
p.add_argument('--build', type=pathlib.Path, required=True)
p.add_argument('--backend', choices=['wasm', 'webgpu'], required=True)
p.add_argument('--jobs', type=int, default=3)
p.add_argument('--reference', action='store_true', help='Build the test-only upstream reference separately')
p.add_argument('--trace-gpu', action='store_true', help='Enable upstream operation diagnostics for validation')
a = p.parse_args()
record = check_record()
pin = read_json(ROOT / 'release/browser-toolchain.json')
sdk = a.emsdk.resolve()
if git(sdk, 'rev-parse', 'HEAD') != pin['emsdk_commit']: p.error('emsdk checkout does not match toolchain pin')
em = sdk / 'upstream/emscripten'
version = subprocess.check_output([str(em / 'emcc'), '--version'], text=True)
if pin['sdk_version'] not in version or pin['emscripten_commit'] not in version: p.error('Installed Emscripten does not match toolchain pin')
build = a.build.resolve()
build.mkdir(parents=True, exist_ok=True)
subprocess.run([str(em / 'emcmake'), 'cmake', '-S', str(ROOT), '-B', str(a.build.resolve()),
    f'-DDUCKTAPE_LLAMA_SOURCE={ROOT / record["runtime"]["path"]}', '-DDUCKTAPE_LLAMA_REVISION=',
    '-DDUCKTAPE_BUILD_FUZZERS=OFF', '-DCMAKE_BUILD_TYPE=Release', '-DDUCKTAPE_BUILD_TESTS=OFF', '-DDUCKTAPE_BUILD_NODE=OFF', '-DDUCKTAPE_BUILD_EXAMPLES=OFF',
    '-DDUCKTAPE_WASM_OPTIMIZATION=1', '-DEMSCRIPTEN_SYSTEM_PROCESSOR=wasm32', '-DGGML_WEBGPU=' + ('ON' if a.backend == 'webgpu' else 'OFF'),
    '-DGGML_WEBGPU_DEBUG=' + ('ON' if a.trace_gpu else 'OFF'),
    '-DDUCKTAPE_BUILD_REFERENCE_PROBE=' + ('ON' if a.reference else 'OFF')], check=True, cwd=build)
subprocess.run(['cmake', '--build', str(build), '--target', 'ducktape_laya_wasm', '--parallel', str(a.jobs)], check=True, cwd=build)
if a.reference:
    subprocess.run(['cmake', '--build', str(build), '--target', 'laya-reference', '--parallel', str(a.jobs)], check=True, cwd=build)
