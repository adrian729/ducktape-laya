#!/usr/bin/env python3
"""Model-free regression tests for artifact identity validation."""
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
from release_support import browser_build_identity, sha256


class BrowserIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.build = pathlib.Path(self.temp.name)
        self.record = {'package_version': '1', 'runtime': {'commit': 'runtime', 'patches': []}}
        self.toolchain = {'sdk_version': 'sdk', 'emscripten_commit': 'compiler'}
        for name in ['laya.mjs', 'laya.wasm']:
            (self.build / name).write_text(name)
        self.identity = {'package_version': '1', 'llama_cpp_commit': 'runtime', 'runtime_patches': [],
                         'toolchain': 'sdk compiler', 'backend': 'wasm',
                         'assets': {name: sha256(self.build / name) for name in ['laya.mjs', 'laya.wasm']}}

    def check(self, slot):
        (self.build / 'laya.build.json').write_text(json.dumps(self.identity))
        return browser_build_identity(self.build, slot, self.record, self.toolchain)

    def test_matching_backends(self):
        for backend in ['wasm', 'webgpu']:
            self.identity['backend'] = backend
            self.assertEqual(self.check(backend)['backend'], backend)

    def test_swapped_or_duplicate_builds(self):
        for actual, slot in [('wasm', 'webgpu'), ('webgpu', 'wasm')]:
            self.identity['backend'] = actual
            with self.assertRaisesRegex(ValueError, 'backend differs'):
                self.check(slot)

    def test_old_identity_requires_rebuild(self):
        del self.identity['backend']
        with self.assertRaisesRegex(ValueError, 'backend differs'):
            self.check('wasm')

    def test_modified_asset(self):
        (self.build / 'laya.wasm').write_text('different binary')
        with self.assertRaisesRegex(ValueError, 'asset hash differs'):
            self.check('wasm')


if __name__ == '__main__':
    unittest.main()
