#!/usr/bin/env python3
"""Reproduce a converted model from its manifest and verify it.

Downloads the official checkpoint files at the pinned revision (or uses
--source), checks their hashes, converts them with the pinned llama.cpp
converter, quantizes when the manifest asks for it, and checks the result
against the manifest's size and SHA-256.

Needs a Python environment with the converter's requirements
(third_party/llama.cpp/requirements/requirements-convert_hf_to_gguf.txt) and,
for quantized outputs, llama-quantize built from the pinned llama.cpp.
"""
import argparse
import json
import pathlib
import subprocess
import tempfile
from urllib.request import urlopen

from release_support import ROOT, git, read_json, sha256, verify_model


def fetch(manifest, directory):
    source = manifest['source']
    for name, expected in source['files'].items():
        target = directory / name
        if target.is_file() and sha256(target) == expected:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        url = f'{source["repository_url"]}/resolve/{source["revision"]}/{source["subfolder"]}{name}'
        with urlopen(url, timeout=60) as response, target.open('wb') as output:
            while chunk := response.read(1 << 20):
                output.write(chunk)
    for name, expected in source['files'].items():
        if sha256(directory / name) != expected:
            raise ValueError(f'{name} does not match the manifest')


parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument('manifest', type=pathlib.Path)
parser.add_argument('output', type=pathlib.Path)
parser.add_argument('--source', type=pathlib.Path, help='Checkpoint directory; downloaded when missing or incomplete')
parser.add_argument('--python', default='python3', help='Interpreter with the converter requirements')
parser.add_argument('--quantize', type=pathlib.Path, help='llama-quantize from the pinned llama.cpp')
args = parser.parse_args()
manifest = read_json(args.manifest)
conversion = manifest['conversion']
runtime = ROOT / read_json(ROOT / 'release/compatibility.json')['runtime']['path']
if git(runtime, 'rev-parse', 'HEAD') != conversion['llama_cpp_commit']:
    raise SystemExit('third_party/llama.cpp is not at the manifest\'s converter commit')
if conversion.get('quantize') and not args.quantize:
    raise SystemExit('This model is quantized; pass --quantize')
if args.output.exists():
    raise SystemExit(f'{args.output} exists; choose a new path')

with tempfile.TemporaryDirectory(dir=args.output.parent) as scratch:
    scratch = pathlib.Path(scratch)
    source = args.source or scratch / 'checkpoint'
    fetch(manifest, source)
    metadata = scratch / 'metadata.json'
    metadata.write_text(json.dumps(conversion['metadata']))
    converted = scratch / 'converted.gguf' if conversion.get('quantize') else args.output
    subprocess.run([args.python, runtime / 'convert_hf_to_gguf.py', source, '--outtype', conversion['outtype'],
                    '--metadata', metadata, '--outfile', converted], check=True)
    if conversion.get('quantize'):
        subprocess.run([args.quantize, converted, args.output, conversion['quantize']], check=True)
try:
    verify_model(args.output, manifest)
except ValueError:
    args.output.unlink()  # never leave an unverified model behind
    raise
print(f'{args.output} matches {manifest["id"]}')
