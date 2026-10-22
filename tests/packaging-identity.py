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
from release_support import (LIBRARY, require_uninstrumented_binary, ROOT, browser_build_identity, sha256, source_identity, native_build_identity,
                             require_runtime_identity)
WINDOWS = sys.platform == 'win32'

from artifact_support import verify_inventory


class ArtifactInventoryTests(unittest.TestCase):
    def test_installed_consumers_reject_tampered_or_missing_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            artifact = base / 'package.tgz'
            artifact.write_bytes(b'approved')
            inventory = {'artifacts': {'package.tgz': {'bytes': artifact.stat().st_size, 'sha256': sha256(artifact)}}}
            (base / 'inventory.json').write_text(json.dumps(inventory))
            self.assertEqual(verify_inventory(base), inventory)
            artifact.write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError, 'mismatch'):
                verify_inventory(base)
            artifact.unlink()
            with self.assertRaisesRegex(ValueError, 'Missing'):
                verify_inventory(base)
            inventory['artifacts'] = {'../outside': {'bytes': 0, 'sha256': '0' * 64}}
            (base / 'inventory.json').write_text(json.dumps(inventory))
            with self.assertRaisesRegex(ValueError, 'local filename'):
                verify_inventory(base)


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
                         'toolchain': 'sdk compiler', 'backend': 'wasm', 'source': self.source.copy(), 'maximum_memory': 4294967296, 'optimization': '1', 'webgpu_debug': False,
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

    def test_constrained_memory_build_rejected(self):
        for ceiling in [536870912, None]:
            self.identity['maximum_memory'] = ceiling
            with self.assertRaisesRegex(ValueError, 'memory ceiling'):
                self.check('wasm')

    def test_test_builds_rejected(self):
        product = dict(self.identity)
        for key, value in [('optimization', '2'), ('optimization', None), ('webgpu_debug', True), ('webgpu_debug', None)]:
            self.identity = {**product, key: value}
            with self.assertRaisesRegex(ValueError, 'not a product build'):
                self.check('wasm')

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


class RuntimeIdentityTests(unittest.TestCase):
    def test_candidate_or_missing_runtime_identity_is_rejected(self):
        record = {'package_version': '1', 'abi_version': 1, 'runtime': {'commit': 'approved', 'patches': []}}
        identity = {'package_version': '1', 'abi_version': 1, 'llama_cpp_commit': 'approved', 'runtime_patches': []}
        require_runtime_identity(identity, record)
        for key in identity:
            for replacement in [None, 'candidate']:
                with self.subTest(key=key, value=replacement):
                    with self.assertRaisesRegex(ValueError, key + ' differs'):
                        require_runtime_identity({**identity, key: replacement}, record)


class InstrumentationTests(unittest.TestCase):
    @unittest.skipIf(WINDOWS, 'builds ELF shared libraries with clang -fPIC -fsanitize; Windows sanitizer runtimes are '
                              'rejected by their imported DLL names (release_support.DIAGNOSTIC_RUNTIME), not tested here')
    def test_instrumented_library_is_rejected_before_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            source = base / 'fixture.c'
            source.write_text('int load(int *p, int n) { return *p / n; }\n')
            for instrumentation in [None, 'address', 'undefined', 'fuzzer-no-link']:
                library = base / 'library.so'
                command = ['clang', '-shared', '-fPIC', str(source), '-o', str(library)]
                if instrumentation: command += ['-fsanitize=' + instrumentation]
                subprocess.run(command, check=True, capture_output=True)
                if instrumentation:
                    with self.assertRaisesRegex(ValueError, 'Instrumented binary'):
                        require_uninstrumented_binary(library)
                else:
                    require_uninstrumented_binary(library)


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
# Windows: an import library for the addon to link (the fixture has no dllexport).
set(CMAKE_WINDOWS_EXPORT_ALL_SYMBOLS ON)
add_library(ducktape_laya SHARED src/core.c)
dtl_record_native_build(ducktape_laya)
add_library(ducktape_laya_node MODULE bindings/node/addon.c)
set_target_properties(ducktape_laya_node PROPERTIES PREFIX "" OUTPUT_NAME ducktape_laya SUFFIX .node)
target_link_libraries(ducktape_laya_node PRIVATE ducktape_laya)
dtl_record_native_build(ducktape_laya_node)
''')
        self.build = self.root / 'build'
        # Ninja on Windows: a single-configuration build tree like the real one.
        self.run_cmake('-S', self.root, '-B', self.build, *(['-G', 'Ninja'] if WINDOWS else []))
        self.run_cmake('--build', self.build)

    def run_cmake(self, *args):
        subprocess.run(['cmake', *map(str, args)], check=True, capture_output=True)

    def check(self, target='ducktape_laya', binary=LIBRARY):
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
