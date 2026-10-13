#!/usr/bin/env python3
"""Verify a supplied local GGUF; performs no network operations."""
import hashlib
import json
import pathlib
import sys
manifest = json.loads((pathlib.Path(__file__).resolve().parents[1] / 'models/manifest.json').read_text())
if len(sys.argv) != 2:
    sys.exit('Usage: python3 scripts/verify-model.py /path/to/Laya-Q8_0.gguf')
model = pathlib.Path(sys.argv[1])
if model.stat().st_size != manifest['size_bytes']:
    sys.exit('Model size does not match the pinned manifest')
with model.open('rb') as stream:
    checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
if checksum != manifest['sha256']:
    sys.exit('Model SHA-256 does not match the pinned manifest')
print('Model matches the pinned Laya English Q8_0 GGUF')
