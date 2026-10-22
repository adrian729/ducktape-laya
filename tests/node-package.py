#!/usr/bin/env python3
"""Pack a development npm package for this platform from a build directory.

Test-only stand-in for the release packaging where it does not exist yet: the
package carries native/<platform>-<arch>/ with ducktape_laya.node and the shared
library next to it, and its os/cpu fields name this platform. Prints the tarball.
"""
import argparse
import json
import pathlib
import shutil
import subprocess
import tempfile

LIBRARIES = {'linux': ['libducktape_laya.so', 'libducktape_laya.so.1'], 'darwin': ['libducktape_laya.1.dylib'],
             'win32': ['ducktape_laya.dll']}
root = pathlib.Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('build', type=pathlib.Path)
parser.add_argument('output', type=pathlib.Path)
args = parser.parse_args()
build = args.build.resolve()
platform, arch = subprocess.run(['node', '-p', 'process.platform + " " + process.arch'], check=True,
                                capture_output=True, text=True).stdout.split()
args.output.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='ducktape-npm-') as directory:
    package = pathlib.Path(directory) / 'package'
    package.mkdir()
    for name in ['README.md', 'index.cjs', 'index.d.ts', 'worker.cjs', 'resolve-addon.cjs']:
        shutil.copy(root / 'bindings/node' / name, package / name)
    manifest = json.loads((root / 'bindings/node/package.json').read_text())
    manifest.update(os=[platform], cpu=[arch])
    (package / 'package.json').write_text(json.dumps(manifest, indent=2) + '\n')
    for name in ['LICENSE', 'THIRD_PARTY_NOTICES.md']:
        shutil.copy(root / name, package / name)
    shutil.copytree(root / 'licenses', package / 'licenses')
    shutil.copy(root / 'release/compatibility.json', package / 'compatibility.json')
    shutil.copy(root / 'models/manifest.json', package / 'model-manifest.json')
    shutil.copy(root / 'scripts/download-model.py', package / 'download-model.py')
    native = package / 'native' / f'{platform}-{arch}'
    native.mkdir(parents=True)
    # Real bytes under each loader name (no symlinks in npm tarballs).
    for name in ['ducktape_laya.node'] + LIBRARIES[platform]:
        shutil.copy(build / name, native / name)
    # npm on Windows is a .cmd script, which needs a shell.
    packed = subprocess.run(f'npm pack --ignore-scripts --json --pack-destination "{args.output.resolve()}"', shell=True,
                            cwd=package, check=True, capture_output=True, text=True)
print(args.output.resolve() / json.loads(packed.stdout)[0]['filename'])
