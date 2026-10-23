#!/usr/bin/env python3
"""Fetch the Node-API build inputs for the running Node from nodejs.org.

Downloads the headers archive and, for Windows, the import library
win-<arch>/node.lib of the exact Node version, checks both against the
release's SHASUMS256.txt and against the SHA-256s pinned in the repository
(scripts/node-sdk-sha256.json, so a compromised mirror of both files cannot
slip in), and writes OUTPUT/include/node/ and OUTPUT/node.lib.
Pass them to CMake as NODE_INCLUDE_DIR and NODE_LIB. Linux and macOS builds can
use the headers installed with Node instead.
"""
import argparse
import hashlib
import json
import pathlib
import shutil
import subprocess
import tarfile
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('output', type=pathlib.Path)
parser.add_argument('--node', default='node', help='Node executable whose version and architecture to match')
parser.add_argument('--lib', action='store_true', help='Also fetch node.lib (Windows)')
args = parser.parse_args()
version, arch = subprocess.run([args.node, '-p', 'process.version + " " + process.arch'], check=True,
                               capture_output=True, text=True).stdout.split()
base = f'https://nodejs.org/dist/{version}/'


def fetch(name):
    with urllib.request.urlopen(base + name, timeout=120) as response:
        return response.read()


sums = {}
for line in fetch('SHASUMS256.txt').decode().splitlines():
    digest, name = line.split()
    sums[name] = digest


pinned = json.loads((pathlib.Path(__file__).resolve().parent / 'node-sdk-sha256.json').read_text()).get(version)


def verified(name):
    if pinned is None or name not in pinned:
        raise SystemExit(f'{name}: no SHA-256 pinned for Node {version} in scripts/node-sdk-sha256.json '
                         f'(add the value from {base}SHASUMS256.txt after checking it)')
    data = fetch(name)
    if hashlib.sha256(data).hexdigest() != pinned[name]:
        raise SystemExit(f'{name}: sha256 differs from the value pinned for Node {version}')
    if hashlib.sha256(data).hexdigest() != sums.get(name):
        raise SystemExit(f'{name}: sha256 differs from {version} SHASUMS256.txt')
    return data


args.output.mkdir(parents=True, exist_ok=True)
headers = args.output / f'node-{version}-headers.tar.gz'
headers.write_bytes(verified(headers.name))
include = args.output / 'include'
shutil.rmtree(include, ignore_errors=True)
prefix = f'node-{version}/include/'
with tarfile.open(headers) as archive:
    for member in archive.getmembers():
        if not member.name.startswith(prefix) or not (member.isfile() or member.isdir()) or '..' in member.name.split('/'):
            continue
        target = include / member.name[len(prefix):]
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
headers.unlink()
if not (include / 'node/node_api.h').is_file():
    raise SystemExit(f'{headers.name} has no include/node/node_api.h')
print(f'NODE_INCLUDE_DIR={(include / "node").resolve()}')
if args.lib:
    lib = args.output / 'node.lib'
    lib.write_bytes(verified(f'win-{arch}/node.lib'))
    print(f'NODE_LIB={lib.resolve()}')
