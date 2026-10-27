#!/usr/bin/env python3
"""Model-free tests of the runtime source records (cmake/runtime_sources.cmake, release_support.require_runtime_sources).

A fake runtime tree is configured and built into a small library once per class; each test restores that
build at the same path, mutates it (modified, restored, relinked or edited sources and patched copies) and
checks that packaging refuses a library linked from anything but the current runtime tree. Moved out of
tests/packaging-identity.py so the identity checks stay quick."""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
from release_support import LIBRARY, ROOT, require_runtime_sources, sha256
WINDOWS = sys.platform == 'win32'


def cmake(*args):
    subprocess.run(['cmake', *map(str, args)], check=True, capture_output=True)


def configure(root, build, *args):
    cmake('-S', root, '-B', build, *(['-G', 'Ninja'] if WINDOWS else []), *args)


class RuntimeSourcesTests(unittest.TestCase):
    """A fake runtime tree built into a small library through cmake/runtime_sources.cmake: packaging
    (require_runtime_sources) refuses a library linked while the tree or a patched copy was modified,
    also after the tree is restored."""
    @classmethod
    def setUpClass(cls):
        """Configure and build the fixture once; every test starts from a copy of that state."""
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = pathlib.Path(cls.temp.name) / 'source'
        cls.runtime = cls.root / 'third_party/llama.cpp'
        for path, text in {'CMakeLists.txt': '# upstream build script\n', 'src/runtime.c': 'int runtime(void) { return 1; }\n',
                           'ggml/src/ggml.c': 'int ggml(void) { return 1; }\n',
                           'ggml/src/ggml-cuda/unused.cu': '// another backend, not built\n'}.items():
            (cls.runtime / path).parent.mkdir(parents=True, exist_ok=True)
            (cls.runtime / path).write_text(text)
        (cls.root / 'cmake').mkdir()
        for name in ['runtime_sources.cmake', 'generated_files.cmake']:
            shutil.copy(ROOT / 'cmake' / name, cls.root / 'cmake' / name)
        (cls.root / 'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.22)
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
if(PATCH_BACKUP)
  # What GNU patch leaves beside a file whose hunk applied at a line offset.
  file(WRITE "${stage}/ggml/src/ggml.c.orig" "${original}")
endif()
dtl_sync_patched_copies("${stage}" "${DTL_PATCH_ROOT}")
include(cmake/runtime_sources.cmake)
set(CMAKE_WINDOWS_EXPORT_ALL_SYMBOLS ON)
add_library(ducktape_laya SHARED "${UPSTREAM}/src/runtime.c" "${DTL_PATCH_ROOT}/ggml/src/ggml.c")
dtl_record_runtime_sources(ducktape_laya)
''')
        cls.build = cls.root / 'build'
        configure(cls.root, cls.build)
        cmake('--build', cls.build)
        # The pristine state. Each test restores it at the same absolute path (CMake caches absolute paths), with
        # the modification times kept, so the build tool sees an up-to-date tree as after a fresh build.
        cls.pristine = pathlib.Path(cls.temp.name) / 'pristine'
        shutil.copytree(cls.root, cls.pristine, symlinks=True)

    def setUp(self):
        if self.root.exists():
            shutil.rmtree(self.root)
        shutil.copytree(self.pristine, self.root, symlinks=True)

    def configure(self, *args):
        configure(self.root, self.build, *args)

    def compile(self):
        cmake('--build', self.build)

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

    def test_patch_backup_is_not_a_patched_copy(self):
        # A NAME.orig backup in the stage (a hunk applied at an offset) neither reaches the patch root
        # nor fails the configure as an uncovered patched file; one already in the root is removed.
        stale = self.build / 'runtime-patched/ggml/src/ggml.c.orig'
        stale.write_text('int stale(void) { return 6; }\n')
        self.configure('-DPATCH_BACKUP=ON')
        self.assertFalse(stale.exists())
        self.compile()
        self.assertEqual(self.check(), {'upstream_files': 3, 'patched_files': 1})

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
