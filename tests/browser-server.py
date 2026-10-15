#!/usr/bin/env python3
"""Loopback-only conformance server; exposes only fixtures, browser assets and selected model."""
import argparse
import functools
import http.server
import json
import pathlib
import tempfile
import urllib.parse
from reference_requests import reference_requests
root = pathlib.Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument('model', type=pathlib.Path)
p.add_argument('--package-dir', type=pathlib.Path, help='Serve an extracted browser package instead of development files')
p.add_argument('--port', type=int, default=0)
p.add_argument('--isolated', action='store_true', help='Exercise SharedArrayBuffer cancellation with COOP/COEP')
a = p.parse_args()
# Literal JSON request texts: parsing them in JavaScript would reorder integer-like keys.
references = tempfile.NamedTemporaryFile('w', suffix='.json')
json.dump({key: json.dumps(request, ensure_ascii=False) for key, (_, request) in reference_requests().items()}, references)
references.flush()
class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        if a.isolated:
            self.send_header('Cross-Origin-Opener-Policy', 'same-origin')
            self.send_header('Cross-Origin-Embedder-Policy', 'require-corp')
        super().end_headers()
    def translate_path(self, path):
        path = urllib.parse.unquote(urllib.parse.urlsplit(path).path)
        if path == '/lost-worker.mjs': return str(root / 'tests/lost-worker.mjs')
        if path == '/reference-worker.mjs': return str(root / 'tests/reference-worker.mjs')
        if path == '/reference-requests.json': return references.name
        if path == '/': return str(root / 'tests/browser.html')
        if path == '/model.gguf': return str(a.model.resolve())
        # Upstream probes remain separate development assets, even when testing
        # an extracted product package that correctly excludes those probes.
        for backend in ['wasm', 'webgpu']:
            for name in ['laya-reference.mjs', 'laya-reference.wasm']:
                if path == f'/{backend}/{name}':
                    return str(root / f'build-browser-{backend}' / name)
        routes = []
        for backend in ['wasm', 'webgpu']:
            base = a.package_dir / backend if a.package_dir else root / f'build-browser-{backend}'
            routes.extend([(f'/browser/{backend}/', base), (f'/{backend}/', base)])
        routes.extend([('/browser/', a.package_dir or root / 'bindings/browser'), ('/fixtures/', root / 'tests/fixtures')])
        for prefix, base in routes:
            if path.startswith(prefix):
                resolved = (base / path[len(prefix):]).resolve()
                if resolved.is_relative_to(base.resolve()) and resolved.is_file(): return str(resolved)
        return '/nonexistent-ducktape-test-path'
    def log_message(self, *args): pass
with http.server.ThreadingHTTPServer(('127.0.0.1', a.port), Handler) as server:
    print(f'http://127.0.0.1:{server.server_port}', flush=True)
    server.serve_forever()
