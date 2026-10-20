#!/usr/bin/env python3
"""Assemble GitHub release assets from qualified packages, or retain a release.

assemble: copies the native and browser package artifacts, renames their
inventories and SBOMs (native-inventory.json, npm-browser-inventory.json,
native-sbom.cdx.json, npm-browser-sbom.cdx.json), adds gates.json from
scripts/check-candidate.py and writes SHA256SUMS. attest-release.yml checks
exactly this layout.

retain: turns downloaded release assets back into the native package
directory tests/rollback.py takes as the last known good release.
"""
import argparse
import json
import pathlib
import shutil

from release_support import sha256
from artifact_support import verify_inventory

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
commands = parser.add_subparsers(dest='command', required=True)
assemble = commands.add_parser('assemble')
assemble.add_argument('--packages', type=pathlib.Path, required=True, help='Native package directory (validate.py --packages)')
assemble.add_argument('--browser-package', type=pathlib.Path, required=True, help='Browser package directory (package-browser.py)')
assemble.add_argument('--gates', type=pathlib.Path, required=True, help='gates.json from scripts/check-candidate.py')
assemble.add_argument('--output', type=pathlib.Path, required=True)
retain = commands.add_parser('retain')
retain.add_argument('assets', type=pathlib.Path, help='Downloaded release assets')
retain.add_argument('--output', type=pathlib.Path, required=True)
a = parser.parse_args()
if a.output.exists():
    parser.error(f'{a.output} exists; choose a new path')

if a.command == 'assemble':
    gates = json.loads(a.gates.read_text())
    if not gates.get('release_ready'):
        raise SystemExit('gates.json is not release ready')
    a.output.mkdir(parents=True)
    for prefix, directory in (('native', a.packages), ('npm-browser', a.browser_package)):
        inventory = json.loads((directory / 'inventory.json').read_text())
        if gates.get('inventories_sha256', {}).get(prefix.split('-')[-1]) != sha256(directory / 'inventory.json'):
            raise SystemExit(f'{directory} is not the package directory gates.json qualified')
        for name in inventory['artifacts']:
            target = f'{prefix}-sbom.cdx.json' if name == 'sbom.cdx.json' else name
            shutil.copy(directory / name, a.output / target)
        shutil.copy(directory / 'inventory.json', a.output / f'{prefix}-inventory.json')
    shutil.copy(a.gates, a.output / 'gates.json')
    sums = ''.join(f'{sha256(path)}  {path.name}\n' for path in sorted(a.output.iterdir()))
    (a.output / 'SHA256SUMS').write_text(sums)
else:
    a.output.mkdir(parents=True)
    inventory = json.loads((a.assets / 'native-inventory.json').read_text())
    for name in inventory['artifacts']:
        source = 'native-sbom.cdx.json' if name == 'sbom.cdx.json' else name
        shutil.copy(a.assets / source, a.output / name)
    shutil.copy(a.assets / 'native-inventory.json', a.output / 'inventory.json')
    verify_inventory(a.output)
print(a.output)
