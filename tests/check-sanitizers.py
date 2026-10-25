#!/usr/bin/env python3
"""Offline checks of scripts/check-sanitizers.py --merge and its pinned toolchain.

The merge joins the address report of one CI job and the thread report of the other
into the single-report schema the candidate checker reads; it refuses reports of
another commit, model or runtime, a missing or failed sanitizer, a failed run and a
dirty tree, leaving no output behind."""
import contextlib
import copy
import importlib.util
import io
import json
import pathlib
import shutil
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('check_sanitizers', ROOT / 'scripts/check-sanitizers.py')
sanitizers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sanitizers)

COMMIT = 'a' * 40
RUNTIME = {'path': 'third_party/llama.cpp', 'commit': 'b' * 40}


def report(name):
    entries = {other: {'status': 'not_run'} for other in sanitizers.SANITIZERS}
    entries[name] = {'status': 'passed', 'preset': sanitizers.SANITIZERS[name]['preset'], 'tests': sorted(sanitizers.REQUIRED_TESTS),
                     'compiler_launcher': 'ccache'}
    if name == 'thread':
        entries[name]['rust_toolchain'] = 'rustc 1.99.0-nightly'
    return {'schema_version': 1, 'status': 'passed', 'release_ready': False, 'source_commit': COMMIT, 'source_tracked_dirty': False,
            'runtime': copy.deepcopy(RUNTIME), 'platform': 'Linux', 'model_sha256': 'c' * 64, 'scope': 'native',
            'compiler_launcher': 'ccache', 'test_jobs': 4, 'sanitizers': entries}


class Merge(unittest.TestCase):
    def setUp(self):
        self.directory = pathlib.Path(tempfile.mkdtemp(prefix='ducktape-sanitizers-'))
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.output = self.directory / 'merged'
        self.reports = {name: report(name) for name in sanitizers.SANITIZERS}

    def write(self):
        directories = []
        for name, value in self.reports.items():
            directory = self.directory / name
            directory.mkdir(exist_ok=True)
            (directory / 'report.json').write_text(json.dumps(value))
            (directory / f'{name}-ctest.log').write_text(f'{name} log\n')
            directories.append(directory)
        return directories

    def main(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as errors:
            code = sanitizers.main(list(map(str, argv)))
        return code, errors.getvalue()

    def refused(self, message):
        address, thread = self.write()
        code, errors = self.main('--merge', address, thread, '--output', self.output)
        self.assertEqual(code, 1)
        self.assertIn(message, errors)
        self.assertFalse(self.output.exists())

    def test_merge(self):
        address, thread = self.write()
        self.assertEqual(self.main('--merge', address, thread, '--output', self.output)[0], 0)
        merged = json.loads((self.output / 'report.json').read_text())
        self.assertEqual(merged['status'], 'passed')
        self.assertEqual(merged['sanitizers'], {'address': self.reports['address']['sanitizers']['address'],
                                                'thread': self.reports['thread']['sanitizers']['thread']})
        self.assertEqual((merged['source_commit'], merged['source_tracked_dirty'], merged['model_sha256'], merged['runtime']),
                         (COMMIT, False, 'c' * 64, RUNTIME))
        self.assertEqual([item['directory'] for item in merged['merged_from']], [str(address), str(thread)])
        for item, directory in zip(merged['merged_from'], (address, thread)):
            self.assertEqual(item['report_sha256'], sanitizers.sha256(directory / 'report.json'))
        self.assertEqual(sorted(path.name for path in self.output.iterdir()), ['address-ctest.log', 'report.json', 'thread-ctest.log'])
        self.assertEqual(merged['compiler_launcher'], 'ccache')

    def test_another_commit(self):
        self.reports['thread']['source_commit'] = 'd' * 40
        self.refused('source_commit')

    def test_another_model(self):
        self.reports['thread']['model_sha256'] = 'e' * 64
        self.refused('model_sha256')

    def test_another_runtime(self):
        self.reports['address']['runtime']['commit'] = 'f' * 40
        self.refused('runtime')

    def test_dirty_tree(self):
        self.reports['address']['source_tracked_dirty'] = True
        self.refused('dirty tree')

    def test_missing_sanitizer(self):
        del self.reports['thread']['sanitizers']['thread']
        self.refused('no thread sanitizer entry')
        self.setUp()
        # The thread report's own address entry is not the address evidence.
        self.reports['address']['sanitizers']['address'] = {'status': 'not_run'}
        self.reports['thread']['sanitizers']['address'] = {'status': 'passed'}
        self.refused('the address sanitizer is not_run')

    def test_failed_sanitizer(self):
        self.reports['thread']['sanitizers']['thread']['status'] = 'failed'
        self.refused('the thread sanitizer is failed')

    def test_failed_report(self):
        self.reports['address']['status'] = 'failed'
        self.refused('status failed')

    def test_swapped_directories(self):
        address, thread = self.write()
        code, errors = self.main('--merge', thread, address, '--output', self.output)
        self.assertEqual(code, 1)
        self.assertIn('address sanitizer is not_run', errors)

    def test_missing_report(self):
        address, thread = self.write()
        (thread / 'report.json').unlink()
        code, errors = self.main('--merge', address, thread, '--output', self.output)
        self.assertEqual((code, 'no report.json' in errors), (1, True))


class Toolchain(unittest.TestCase):
    def test_rust_nightly_is_pinned(self):
        self.assertRegex(sanitizers.RUST_NIGHTLY, r'^nightly-\d{4}-\d{2}-\d{2}$')
        source = (ROOT / 'scripts/check-sanitizers.py').read_text()
        self.assertNotIn("'+nightly'", source)

    def test_presets(self):
        self.assertEqual({name: (item['preset'], item['directory']) for name, item in sanitizers.SANITIZERS.items()},
                         {'address': ('sanitize', 'build-sanitize'), 'thread': ('sanitize-thread', 'build-sanitize-thread')})


if __name__ == '__main__':
    unittest.main()
