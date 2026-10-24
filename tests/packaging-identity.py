#!/usr/bin/env python3
"""Model-free regression tests for artifact identity validation."""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
from release_support import (LIBRARY, require_uninstrumented_binary, ROOT, browser_build_identity, sha256, source_identity, native_build_identity,
                             require_mapped_source_paths, require_runtime_identity, require_runtime_sources, require_elf_hardening,
                             require_guard_cf, require_compiled_hardening)
WINDOWS = sys.platform == 'win32'

from artifact_support import verify_inventory


class ArtifactInventoryTests(unittest.TestCase):
    def test_guard_cf_check_rejects_a_dll_without_it(self):
        """package-native.py's hardening checks on tiny fixtures: a PE header with and without GUARD_CF
        here, shared objects linked with and without full RELRO below."""
        import struct
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            def pe(characteristics):
                data = bytearray(0x40 + 24 + 240)
                data[:2] = b'MZ'
                struct.pack_into('<I', data, 0x3c, 0x40)
                data[0x40:0x44] = b'PE\0\0'
                struct.pack_into('<HHIIIHH', data, 0x44, 0x8664, 0, 0, 0, 0, 240, 0x2022)
                optional = 0x40 + 24
                struct.pack_into('<H', data, optional, 0x20b)
                struct.pack_into('<H', data, optional + 70, characteristics)
                struct.pack_into('<I', data, optional + 108, 16)
                path = base / f'fixture-{characteristics:x}.dll'
                path.write_bytes(bytes(data))
                return path
            require_guard_cf(pe(0x4000 | 0x0160))
            with self.assertRaisesRegex(SystemExit, 'Control Flow Guard'):
                require_guard_cf(pe(0x0160))

    @unittest.skipIf(WINDOWS or not (shutil.which('cc') or shutil.which('gcc')) or not shutil.which('readelf'), 'needs cc and readelf')
    def test_elf_hardening_check_rejects_partial_relro(self):
        compiler = shutil.which('cc') or shutil.which('gcc')
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            (base / 'tiny.c').write_text('int tiny(void) { return 1; }\n')
            for name, flags, hardened in [('full', ['-Wl,-z,relro,-z,now'], True), ('lazy', ['-Wl,-z,relro,-z,lazy'], False),
                                          ('norelro', ['-Wl,-z,norelro,-z,now'], False)]:
                library = base / f'lib{name}.so'
                subprocess.run([compiler, '-shared', '-fPIC', *flags, str(base / 'tiny.c'), '-o', str(library)], check=True)
                with self.subTest(name=name):
                    if hardened:
                        require_elf_hardening(library)
                    else:
                        with self.assertRaisesRegex(SystemExit, 'full RELRO'):
                            require_elf_hardening(library)

    @unittest.skipIf(WINDOWS or not (shutil.which('cc') or shutil.which('gcc')) or not shutil.which('readelf'), 'needs cc and readelf')
    def test_compiled_hardening_check(self):
        """The stack protector's handler and, in a library, fortified calls must be imported."""
        compiler = shutil.which('cc') or shutil.which('gcc')
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            (base / 'tiny.c').write_text('#include <string.h>\nint tiny(const char *s, unsigned long n) { char b[16]; memcpy(b, s, n); '
                                         'return b[n / 2]; }\n')
            for name, flags, hardened in [('both', ['-fstack-protector-all', '-D_FORTIFY_SOURCE=2'], True),
                                          ('no-protector', ['-fno-stack-protector', '-D_FORTIFY_SOURCE=2'], False),
                                          ('no-fortify', ['-fstack-protector-all', '-U_FORTIFY_SOURCE', '-D_FORTIFY_SOURCE=0'], False)]:
                library = base / f'lib{name}.so'
                subprocess.run([compiler, '-shared', '-fPIC', '-O2', '-U_FORTIFY_SOURCE', *flags, str(base / 'tiny.c'), '-o', str(library)], check=True)
                with self.subTest(name=name):
                    if hardened:
                        require_compiled_hardening(library, library=True)
                    else:
                        with self.assertRaisesRegex(SystemExit, 'stack protector|fortified'):
                            require_compiled_hardening(library, library=True)
            # An addon needs only the stack protector.
            require_compiled_hardening(base / 'libno-fortify.so', library=False)

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


class DevelopmentMetadataTests(unittest.TestCase):
    """bindings/python/pyproject.toml and bindings/java/pom.xml (development builds) declare what the released
    wheel and POM declare: the licence expression, the operating systems and the bundled library files."""
    def test_pyproject_matches_the_wheel(self):
        import fnmatch
        import tomllib
        import python_wheel
        from release_support import TARGETS
        project = tomllib.loads((ROOT / 'bindings/python/pyproject.toml').read_text())
        self.assertEqual(project['project']['license'], python_wheel.LICENSE_EXPRESSION)
        systems = {c for c in project['project']['classifiers'] if c.startswith('Operating System ::')}
        self.assertEqual(systems, set(python_wheel.CLASSIFIERS.values()))
        patterns = project['tool']['setuptools']['package-data']['ducktape_laya']
        for target in TARGETS:
            self.assertTrue(any(fnmatch.fnmatch(f'_native/{target.library}', pattern) for pattern in patterns), target.library)

    def test_pom_matches_the_released_pom(self):
        import xml.etree.ElementTree as ElementTree
        import java_package
        ns = {'m': 'http://maven.apache.org/POM/4.0.0'}
        licences = lambda root: [(l.findtext('m:name', namespaces=ns), l.findtext('m:url', namespaces=ns))
                                 for l in root.findall('m:licenses/m:license', ns)]
        released = licences(ElementTree.fromstring(java_package.pom('0.0.0').encode()))
        self.assertEqual([name for name, _ in released], python_wheel_licences())
        self.assertEqual(licences(ElementTree.parse(ROOT / 'bindings/java/pom.xml').getroot()), released)


def python_wheel_licences():
    import python_wheel
    return python_wheel.LICENSE_EXPRESSION.split(' AND ')


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


class SourcePathTests(unittest.TestCase):
    """Release binaries hold only source paths the reproducible-path map made (./...). A compiler cache
    with base_dir hands the compiler relative paths (../../src/laya.cpp) that the map does not match."""
    def binary(self, directory, strings):
        path = pathlib.Path(directory) / 'binary'
        path.write_bytes(b'\x7fELF\x01' + b''.join(text + b'\x00' for text in strings))
        return path

    def test_mapped_paths_and_messages_are_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            # GCC keeps the map's leading ./, Clang drops it, clang-cl writes Windows separators.
            require_mapped_source_paths(self.binary(directory, [b'./build/runtime-patched/ggml/src/ggml.c', b'./src/laya.cpp',
                b'./third_party/llama.cpp/ggml/src/ggml-cpu/../../quants.h', b'.\\src\\platform.cpp',
                b'build/runtime-patched/ggml/src/ggml.c', b'third_party/llama.cpp/src/llama.cpp',
                b'build\\runtime-patched\\ggml\\src\\ggml-alloc.c', b'src\\laya.cpp',
                b'see tools/mtmd/clip.cpp for details', b'GGML_ASSERT(%s) failed', b'version 1.2.h']))

    def test_unmapped_paths_are_refused(self):
        for path in (b'../../../../src/laya.cpp', b'./../src/laya.cpp', b'..\\src\\laya.cpp', b'runtime-patched/ggml/src/ggml.c',
                     b'/home/user/ducktape-laya/src/laya.cpp', b'D:\\a\\ducktape-laya\\src\\laya.cpp', b'\\\\server\\src\\laya.cpp'):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(ValueError, 'outside the reproducible-path map'):
                    require_mapped_source_paths(self.binary(directory, [b'./src/laya.cpp', path]))

    @unittest.skipUnless(shutil.which('cc') and not WINDOWS, 'compiles with a GNU-style cc')
    def test_relative_compiler_paths_defeat_the_map(self):
        # What ccache's base_dir does: the same source, given to the compiler by a relative path.
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory).resolve()
            (root / 'src').mkdir()
            (root / 'build').mkdir()
            (root / 'src/where.c').write_text('const char *where(void) { return __FILE__; }\n')
            for source, accepted in ((root / 'src/where.c', True), (pathlib.Path('../src/where.c'), False)):
                with self.subTest(source=str(source)):
                    subprocess.run(['cc', '-O2', f'-ffile-prefix-map={root}=.', '-c', str(source), '-o', 'where.o'],
                                   cwd=root / 'build', check=True, capture_output=True)
                    if accepted:
                        require_mapped_source_paths(root / 'build/where.o')
                    else:
                        with self.assertRaisesRegex(ValueError, r'\.\./src/where\.c'):
                            require_mapped_source_paths(root / 'build/where.o')


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



class RuntimeSourcesTests(unittest.TestCase):
    """A fake runtime tree built into a small library through cmake/runtime_sources.cmake: packaging
    (require_runtime_sources) refuses a library linked while the tree or a patched copy was modified,
    also after the tree is restored."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name) / 'source'
        self.runtime = self.root / 'third_party/llama.cpp'
        for path, text in {'CMakeLists.txt': '# upstream build script\n', 'src/runtime.c': 'int runtime(void) { return 1; }\n',
                           'ggml/src/ggml.c': 'int ggml(void) { return 1; }\n',
                           'ggml/src/ggml-cuda/unused.cu': '// another backend, not built\n'}.items():
            (self.runtime / path).parent.mkdir(parents=True, exist_ok=True)
            (self.runtime / path).write_text(text)
        (self.root / 'cmake').mkdir()
        for name in ['runtime_sources.cmake', 'generated_files.cmake']:
            shutil.copy(ROOT / 'cmake' / name, self.root / 'cmake' / name)
        (self.root / 'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.22)
project(runtime_fixture C)
set(UPSTREAM "${CMAKE_CURRENT_SOURCE_DIR}/third_party/llama.cpp")
set(DTL_PATCH_ROOT "${CMAKE_CURRENT_BINARY_DIR}/runtime-patched")
# Stands in for cmake/runtime_patches.cmake: a patched copy of one runtime file, made at configure time
# in a staging directory and copied over the build's copy only when its content changed.
include(cmake/generated_files.cmake)
set(stage "${CMAKE_CURRENT_BINARY_DIR}/runtime-patched.stage")
file(REMOVE_RECURSE "${stage}")
file(READ "${UPSTREAM}/ggml/src/ggml.c" original)
file(WRITE "${stage}/ggml/src/ggml.c" "${original}int patched(void) { return 2; }\\n")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${UPSTREAM}/ggml/src/ggml.c")
if(UNCOVERED_PATCH)
  file(WRITE "${stage}/tools/tool.c" "int tool(void) { return 3; }\\n")
endif()
dtl_sync_patched_copies("${stage}" "${DTL_PATCH_ROOT}")
include(cmake/runtime_sources.cmake)
set(CMAKE_WINDOWS_EXPORT_ALL_SYMBOLS ON)
add_library(ducktape_laya SHARED "${UPSTREAM}/src/runtime.c" "${DTL_PATCH_ROOT}/ggml/src/ggml.c")
dtl_record_runtime_sources(ducktape_laya)
''')
        self.build = self.root / 'build'
        self.configure()
        self.compile()

    def configure(self, *args):
        self.cmake('-S', self.root, '-B', self.build, *(['-G', 'Ninja'] if WINDOWS else []), *args)

    def compile(self):
        self.cmake('--build', self.build)

    def cmake(self, *args):
        subprocess.run(['cmake', *map(str, args)], check=True, capture_output=True)

    def check(self):
        return require_runtime_sources(self.build, self.runtime, root=self.root)

    def write(self, path, text, mtime=None):
        """Write text, with the given modification time, or one clearly after every earlier build output."""
        path.write_text(text)
        stamp = mtime if mtime is not None else max(item.stat().st_mtime for item in self.build.rglob('*')) + 5
        os.utime(path, (stamp, stamp))

    def test_clean_build_is_accepted(self):
        self.assertEqual(self.check(), {'upstream_files': 3, 'patched_files': 1})

    def test_modified_built_then_restored_runtime_is_refused(self):
        source = self.runtime / 'src/runtime.c'
        original = source.read_text()
        self.write(source, 'int runtime(void) { return 7; }\n')
        self.compile()
        self.write(source, original)
        with self.assertRaisesRegex(ValueError, r'linked from runtime sources that differ from the current runtime tree \(src/runtime.c\)'):
            self.check()
        # Rebuilding from the restored tree links a new library and a new record.
        self.compile()
        self.check()

    def test_restored_with_its_old_timestamp_is_still_refused(self):
        # The build does not recompile a file restored with its old timestamp, so the library keeps the
        # modified code, and the record taken when it was linked still says so.
        source = self.runtime / 'src/runtime.c'
        original, stat = source.read_text(), source.stat()
        self.write(source, 'int runtime(void) { return 7; }\n')
        self.compile()
        self.write(source, original, stat.st_mtime)
        self.compile()
        with self.assertRaisesRegex(ValueError, 'differ from the current runtime tree'):
            self.check()

    def test_restored_with_its_old_timestamp_then_relinked_is_refused(self):
        # Restored with an older timestamp than the last link, the file is not recompiled when an unrelated
        # change relinks the library: that link lists it as stale, and so does every later one until it
        # is touched (and so recompiled).
        source, patched = self.runtime / 'src/runtime.c', self.build / 'runtime-patched/ggml/src/ggml.c'
        original, stat = source.read_text(), source.stat()
        self.write(source, 'int runtime(void) { return 7; }\n')
        self.compile()
        self.write(source, original, stat.st_mtime - 100)
        self.write(patched, patched.read_text())
        self.compile()
        with self.assertRaisesRegex(ValueError, r'not recompiled \(upstream/src/runtime.c\)'):
            self.check()
        self.write(patched, patched.read_text())
        self.compile()
        with self.assertRaisesRegex(ValueError, r'not recompiled \(upstream/src/runtime.c\)'):
            self.check()
        self.write(source, original)
        self.compile()
        self.check()

    def test_reconfigure_keeps_unchanged_copies(self):
        # An unchanged reconfigure leaves the patched copy (and its old timestamp) alone, so nothing is
        # recompiled or relinked, and the build is still accepted.
        copy, library = self.build / 'runtime-patched/ggml/src/ggml.c', self.build / LIBRARY
        old = os.stat(copy).st_mtime - 100
        os.utime(copy, (old, old))
        before = (copy.stat().st_mtime_ns, library.stat().st_mtime_ns, sha256(library))
        self.configure()
        self.compile()
        self.assertEqual((copy.stat().st_mtime_ns, library.stat().st_mtime_ns, sha256(library)), before)
        self.check()

    def test_removed_patch_output_is_removed(self):
        # A copy the configure no longer makes does not stay behind in the patch root (or its record).
        self.configure('-DUNCOVERED_PATCH=OFF')
        stray = self.build / 'runtime-patched/ggml/src/stray.c'
        stray.write_text('int stray(void) { return 5; }\n')
        self.configure()
        self.assertFalse(stray.exists())
        self.compile()
        self.check()

    def test_edited_patched_copy_is_refused(self):
        copy = self.build / 'runtime-patched/ggml/src/ggml.c'
        self.write(copy, copy.read_text() + 'int edited(void) { return 4; }\n')
        self.compile()
        with self.assertRaisesRegex(ValueError, r'patched runtime copies changed after configure \(ggml/src/ggml.c\)'):
            self.check()
        self.configure()
        self.compile()
        self.check()

    def test_patched_from_a_modified_original_is_refused(self):
        # Configured (patched) while ggml.c was modified, restored with its old timestamp so nothing
        # reconfigures, then relinked for an unrelated change: the patched copy still holds the change.
        original_path = self.runtime / 'ggml/src/ggml.c'
        original, stat = original_path.read_text(), original_path.stat()
        self.write(original_path, 'int ggml(void) { return 9; }\n')
        self.compile()
        self.write(original_path, original, stat.st_mtime)
        self.write(self.runtime / 'src/runtime.c', (self.runtime / 'src/runtime.c').read_text())
        self.compile()
        with self.assertRaisesRegex(ValueError, r'runtime patched from sources that differ from the current runtime tree \(ggml/src/ggml.c\)'):
            self.check()

    def test_unbuilt_backends_are_not_recorded(self):
        self.write(self.runtime / 'ggml/src/ggml-cuda/unused.cu', '// changed\n')
        self.check()

    def test_missing_record_or_replaced_library(self):
        with (self.build / LIBRARY).open('ab') as stream:
            stream.write(b'modified')
        with self.assertRaisesRegex(ValueError, 'library differs from the one its runtime source record describes'):
            self.check()
        (self.build / 'ducktape_laya.runtime-sources.json').unlink()
        with self.assertRaisesRegex(ValueError, 'missing runtime source record'):
            self.check()

    def test_patched_file_outside_the_recorded_directories_fails_configure(self):
        with self.assertRaises(subprocess.CalledProcessError) as failure:
            self.configure('-DUNCOVERED_PATCH=ON')
        # CMake wraps the message.
        self.assertIn('does not cover the patched runtime file tools/tool.c', ' '.join(failure.exception.stderr.decode().split()))

if __name__ == '__main__':
    unittest.main()
