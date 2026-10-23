#!/usr/bin/env python3
"""Model-free smoke test of the shipped examples.

Every examples/*.json and examples/presets/*.json request must pass the library's request
validation (a router engine validates before it loads a model, so none is needed), a
deliberately broken request must be rejected, and examples/decide.c and decide.cpp must
compile against the built library and report a missing model or usage error cleanly.
Usage: examples-smoke.py --build-dir BUILD [--output DIR]. Unix-like hosts only."""
import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--build-dir', type=pathlib.Path, required=True, help='Directory with the built shared library')
a = p.parse_args()
build = a.build_dir.resolve()
cc, cxx = os.environ.get('CC') or 'cc', os.environ.get('CXX') or 'c++'
env = dict(os.environ, LD_LIBRARY_PATH=str(build) + ':' + os.environ.get('LD_LIBRARY_PATH', ''),
           DYLD_LIBRARY_PATH=str(build) + ':' + os.environ.get('DYLD_LIBRARY_PATH', ''))
flags = ['-I', str(ROOT / 'include'), '-L', str(build), '-lducktape_laya', f'-Wl,-rpath,{build}']

def run(command, **kwargs):
    return subprocess.run(list(map(str, command)), env=env, capture_output=True, text=True, timeout=300, **kwargs)

failures = []
with tempfile.TemporaryDirectory(prefix='ducktape-examples-') as temp:
    temp = pathlib.Path(temp)
    def compile(label, compiler, source, output):
        result = run([compiler, *(['-std=c++17'] if compiler == cxx else []), '-O0', '-Wall', '-Wextra', source, '-o', output, *flags])
        if result.returncode:
            failures.append(f'{label}: compilation failed\n{result.stderr}')
        return not result.returncode
    if compile('examples-validate.c', cc, ROOT / 'tests/examples-validate.c', temp / 'validate'):
        router = json.dumps({'models': {'unused': str(temp / 'validate')}})
        def validate(request):
            result = run([temp / 'validate', router], input=json.dumps(request))
            return result.stdout.strip() or f'harness failure: {result.stderr.strip()}'
        # Examples are single-engine requests; a router needs the model ID.
        tag = lambda r: {**r, 'model': 'unused'}
        files = sorted([*(ROOT / 'examples').glob('*.json'), *(ROOT / 'examples/presets').glob('*.json')])
        if not files:
            failures.append('no example requests found')
        for path in files:
            body = json.loads(path.read_text())
            for index, request in enumerate(body if isinstance(body, list) else [body]):
                outcome = validate(tag(request))
                label = f'{path.relative_to(ROOT)}' + (f'[{index}]' if isinstance(body, list) else '')
                print(f'{label}: {outcome}')
                if not outcome.startswith('valid'):
                    failures.append(f'{label}: {outcome}')
        broken = tag(json.loads((ROOT / 'examples/request.json').read_text()))
        broken['surprise'] = True
        if not validate(broken).startswith('invalid'):
            failures.append('a request with an unknown top-level key was not rejected (the smoke test would not catch bad examples)')
    for name, compiler, source in (('decide-c', cc, 'decide.c'), ('decide-cpp', cxx, 'decide.cpp')):
        if not compile(source, compiler, ROOT / 'examples' / source, temp / name):
            continue
        usage = run([temp / name])
        missing = run([temp / name, temp / 'no-such-model.gguf'], input=(ROOT / 'examples/request.json').read_text())
        print(f'{source}: usage exit {usage.returncode}, missing model exit {missing.returncode}')
        if usage.returncode != 2 or 'Usage' not in usage.stderr:
            failures.append(f'{source}: without a model argument it must print usage and exit 2 (got {usage.returncode}: {usage.stderr!r})')
        if missing.returncode != 1 or not missing.stderr.strip() or missing.stdout:
            failures.append(f'{source}: a missing model must exit 1 with a message on stderr (got {missing.returncode}: {missing.stderr!r})')
if failures:
    sys.exit('examples smoke test failed:\n' + '\n'.join(failures))
print('examples smoke test passed')
