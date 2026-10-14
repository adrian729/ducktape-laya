#!/usr/bin/env python3
"""Loopback-only conformance server; exposes only fixtures, browser assets and selected model."""
import argparse
import functools
import http.server
import pathlib
import urllib.parse
root = pathlib.Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument('model', type=pathlib.Path)
p.add_argument('--package-dir', type=pathlib.Path, help='Serve an extracted browser package instead of development files')
p.add_argument('--port', type=int, default=0)
a = p.parse_args()
class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        path = urllib.parse.unquote(urllib.parse.urlsplit(path).path)
        if path == '/lost-worker.mjs': return str(root / 'tests/lost-worker.mjs')
        if path == '/reference-worker.mjs': return str(root / 'tests/reference-worker.mjs')
        if path == '/': return str(root / 'tests/browser.html')
        if path == '/model.gguf': return str(a.model.resolve())
        for prefix, base in [('/browser/', a.package_dir or root / 'bindings/browser'), ('/wasm/', a.package_dir / 'wasm' if a.package_dir else root / 'build-browser-wasm'), ('/webgpu/', a.package_dir / 'webgpu' if a.package_dir else root / 'build-browser-webgpu'), ('/fixtures/', root / 'tests/fixtures')]:
            if path.startswith(prefix):
                resolved = (base / path[len(prefix):]).resolve()
                if resolved.is_relative_to(base.resolve()) and resolved.is_file(): return str(resolved)
        return '/nonexistent-ducktape-test-path'
    def log_message(self, *args): pass
with http.server.ThreadingHTTPServer(('127.0.0.1', a.port), Handler) as server:
    print(f'http://127.0.0.1:{server.server_port}', flush=True)
    server.serve_forever()
