#!/usr/bin/env python3
"""scripts/ci-changes.py: which changes let CI skip its heavy jobs (model-free, offline)."""
import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location('ci_changes', pathlib.Path(__file__).resolve().parents[1] / 'scripts/ci-changes.py')
ci_changes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci_changes)
classify = ci_changes.classify


class Classify(unittest.TestCase):
    def test_documentation_only(self):
        for paths in (['docs/BUILDING.md'], ['README.md', 'CHANGELOG.md', 'docs/RELEASE_CHECKS.md'], ['assets/ducktape-logo.svg']):
            with self.subTest(paths=paths):
                self.assertEqual(classify(paths), {'docs_only': True, 'library': False})

    def test_packaged_or_tested_markdown_is_not_documentation_only(self):
        # Package READMEs, the packaged notices, the patch notes (a library input) and test data.
        for path in ('bindings/node/README.md', 'bindings/python/README.md', 'THIRD_PARTY_NOTICES.md',
                     'patches/README.md', 'tests/original-reference/README.md'):
            with self.subTest(path=path):
                self.assertFalse(classify(['docs/API.md', path])['docs_only'])

    def test_library_inputs(self):
        for path in ('src/laya.cpp', 'include/ducktape_laya.h', 'cmake/runtime_patches.cmake', 'patches/x.patch',
                     'CMakeLists.txt', 'release/compatibility.json', '.gitmodules', 'third_party/llama.cpp',
                     'bindings/node/addon.cpp', 'scripts/check-reproducibility.py', '.github/workflows/native.yml'):
            with self.subTest(path=path):
                self.assertEqual(classify([path]), {'docs_only': False, 'library': True})
        for path in ('scripts/validate.py', 'bindings/go/laya.go', 'tests/native.cpp', 'release/qualification.json'):
            with self.subTest(path=path):
                self.assertEqual(classify([path]), {'docs_only': False, 'library': False})

    def test_unknown_or_empty_changes_skip_nothing(self):
        self.assertEqual(classify(None), {'docs_only': False, 'library': True})
        self.assertEqual(classify([]), {'docs_only': False, 'library': False})
        self.assertIsNone(ci_changes.changed_paths('0' * 40, 'HEAD'))
        self.assertIsNone(ci_changes.changed_paths('', 'HEAD'))
        self.assertIsNone(ci_changes.changed_paths('f' * 40, 'HEAD'))


if __name__ == '__main__':
    unittest.main()
