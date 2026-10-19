#!/usr/bin/env python3
"""Compile external CJS/ESM/bundler consumers against real package declarations."""
import argparse
import pathlib
import shutil
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
HERE = pathlib.Path(__file__).resolve().parent
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--node-package', type=pathlib.Path)
p.add_argument('--browser-package', type=pathlib.Path)
a = p.parse_args()
# The browser protocol declarations are a copy of the shared part of Node's.
# Every declaration there must appear verbatim in Node's file.
node_types = (ROOT / 'bindings/node/index.d.ts').read_text()
browser_types = (ROOT / 'bindings/browser/protocol.d.ts').read_text()
declarations, current = [], None
for line in browser_types.splitlines():
    if line.startswith('export '):
        current = [line]
        declarations.append(current)
    elif line.startswith(('/', '*')) or not line.strip():
        current = None  # comments between declarations are not compared
    elif current is not None:
        current.append(line)
for declaration in map('\n'.join, declarations):
    if declaration not in node_types:
        raise SystemExit('bindings/browser/protocol.d.ts drifted from bindings/node/index.d.ts: '
                         + declaration.splitlines()[0])
compiler = HERE / 'node_modules/typescript/bin/tsc'
if not compiler.is_file():
    p.error('Run npm ci --ignore-scripts --prefix tests/typescript first')
with tempfile.TemporaryDirectory(prefix='ducktape-typescript-') as directory:
    tmp = pathlib.Path(directory)
    packages = [('ducktape-laya', 'node', a.node_package),
                ('ducktape-laya-browser', 'browser', a.browser_package)]
    # Install all real artifacts before adding fallback source declarations:
    # npm may prune copied modules that are absent from its dependency manifest.
    for name, binding, artifact in packages:
        if artifact:
            subprocess.run(['npm', 'install', '--ignore-scripts', '--offline', '--no-audit', '--no-fund',
                            '--prefix', str(tmp), str(artifact.resolve())], check=True)
    for name, binding, artifact in packages:
        if not artifact:
            target = tmp / 'node_modules' / name
            target.mkdir(parents=True, exist_ok=True)
            for file in (ROOT / 'bindings' / binding).iterdir():
                if file.name == 'package.json' or file.name.endswith('.d.ts'):
                    shutil.copy(file, target / file.name)
    shutil.copy(HERE / 'native.cts', tmp / 'native.cts')
    shutil.copy(HERE / 'native.cts', tmp / 'native.mts')
    shutil.copy(HERE / 'browser.mts', tmp / 'browser.mts')
    for mode, resolution in [('NodeNext', 'NodeNext'), ('ESNext', 'Bundler')]:
        sources = ['native.mts', 'browser.mts'] + (['native.cts'] if mode == 'NodeNext' else [])
        subprocess.run(['node', str(compiler), '--strict', '--noEmit', '--target', 'ES2022',
                        '--module', mode, '--moduleResolution', resolution, '--lib', 'ES2022,DOM',
                        '--types', 'node', '--typeRoots', str(HERE / 'node_modules/@types'), *sources],
                       cwd=tmp, check=True)
        print(f'TypeScript external consumers: {mode}/{resolution} passed')
