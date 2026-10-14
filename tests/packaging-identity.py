#!/usr/bin/env python3
"""Model-free regression tests for artifact identity validation."""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
from release_support import ROOT, browser_build_identity, sha256, source_identity, native_build_identity


class BrowserIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.build = pathlib.Path(self.temp.name)
        self.record = {'package_version': '1', 'runtime': {'commit': 'runtime', 'patches': []}}
        self.toolchain = {'sdk_version': 'sdk', 'emscripten_commit': 'compiler'}
        self.source = {'sha256': 'source', 'files': {'src/core.cpp': 'hash'}}
        for name in ['laya.mjs', 'laya.wasm']:
            (self.build / name).write_text(name)
        self.identity = {'package_version': '1', 'llama_cpp_commit': 'runtime', 'runtime_patches': [],
                         'toolchain': 'sdk compiler', 'backend': 'wasm', 'source': self.source.copy(),
                         'assets': {name: sha256(self.build / name) for name in ['laya.mjs', 'laya.wasm']}}

    def check(self, slot):
        (self.build / 'laya.build.json').write_text(json.dumps(self.identity))
        return browser_build_identity(self.build, slot, self.record, self.toolchain, self.source)

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

    def test_stale_source(self):
        self.identity['source'] = {'sha256': 'previous-build'}
        with self.assertRaisesRegex(ValueError, 'build sources differ'):
            self.check('wasm')


class NativeIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name) / 'source'
        (self.root / 'src').mkdir(parents=True)
        (self.root / 'bindings/node').mkdir(parents=True)
        (self.root / 'cmake').mkdir()
        for name in ['source_identity.cmake', 'native_identity.cmake']:
            shutil.copy(ROOT / 'cmake' / name, self.root / 'cmake' / name)
        (self.root / 'src/core.c').write_text('int value(void) { return 1; }\n')
        (self.root / 'bindings/node/addon.c').write_text('int value(void); int addon(void) { return value(); }\n')
        (self.root / 'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.22)
project(identity_fixture C)
include(cmake/source_identity.cmake)
add_library(ducktape_laya SHARED src/core.c)
dtl_record_native_build(ducktape_laya)
add_library(ducktape_laya_node MODULE bindings/node/addon.c)
set_target_properties(ducktape_laya_node PROPERTIES PREFIX "" OUTPUT_NAME ducktape_laya SUFFIX .node)
target_link_libraries(ducktape_laya_node PRIVATE ducktape_laya)
dtl_record_native_build(ducktape_laya_node)
''')
        self.build = self.root / 'build'
        self.run_cmake('-S', self.root, '-B', self.build)
        self.run_cmake('--build', self.build)

    def run_cmake(self, *args):
        subprocess.run(['cmake', *map(str, args)], check=True, capture_output=True)

    def check(self, target='ducktape_laya', binary='libducktape_laya.so'):
        return native_build_identity(self.build, target, binary, source_identity(self.root))

    def test_partial_rebuild_does_not_accept_stale_addon(self):
        self.check()
        self.check('ducktape_laya_node', 'ducktape_laya.node')
        (self.root / 'src/core.c').write_text('int value(void) { return 2; }\n')
        with self.assertRaisesRegex(ValueError, 'build sources differ'):
            self.check()
        self.run_cmake('--build', self.build, '--target', 'ducktape_laya')
        self.check()
        with self.assertRaisesRegex(ValueError, 'build sources differ'):
            self.check('ducktape_laya_node', 'ducktape_laya.node')
        self.run_cmake('--build', self.build)
        self.check('ducktape_laya_node', 'ducktape_laya.node')

    def test_wrapper_change_and_new_file_trigger_identity_refresh(self):
        for path in ['bindings/node/index.cjs', 'src/new.h']:
            (self.root / path).write_text('// newly added input\n')
            with self.assertRaisesRegex(ValueError, 'build sources differ'):
                self.check()
            self.run_cmake('--build', self.build)
            self.check()
            self.check('ducktape_laya_node', 'ducktape_laya.node')

    def test_binary_replacement_and_missing_identity(self):
        with (self.build / 'ducktape_laya.node').open('ab') as stream:
            stream.write(b'modified')
        with self.assertRaisesRegex(ValueError, 'binary differs'):
            self.check('ducktape_laya_node', 'ducktape_laya.node')
        (self.build / 'ducktape_laya.build.json').unlink()
        with self.assertRaisesRegex(ValueError, 'missing completed build identity'):
            self.check()

    def test_fingerprint_independent_of_source_path_and_generated_files(self):
        other = pathlib.Path(self.temp.name) / 'elsewhere'
        shutil.copytree(self.root, other, ignore=shutil.ignore_patterns('build'))
        self.assertEqual(source_identity(self.root), source_identity(other))
        (self.root / 'bindings/node/node_modules').mkdir()
        (self.root / 'bindings/node/node_modules/generated.js').write_text('generated')
        self.assertEqual(source_identity(self.root), source_identity(other))


if __name__ == '__main__':
    unittest.main()
