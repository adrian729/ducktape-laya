#!/usr/bin/env python3
"""Verify a supplied local GGUF; performs no network operations."""
import argparse
from release_support import ROOT, read_json, verify_model
parser = argparse.ArgumentParser()
parser.add_argument('model')
parser.add_argument('--manifest', default=ROOT / 'models/manifest.json')
args = parser.parse_args()
verify_model(args.model, read_json(args.manifest))
print('Model matches the pinned manifest')
