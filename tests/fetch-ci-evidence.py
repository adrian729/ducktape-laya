#!/usr/bin/env python3
"""Model-free checks of scripts/fetch-ci-evidence.py against a fake GitHub API.

Serves canned runs and artifact zips in place of `gh api`: a complete candidate
run is downloaded, verified and recorded (ci-run.json, evidence.json, --merge);
runs of another commit, event, conclusion, workflow or repository, missing or
expired artifacts, a zip that does not match its digest, unsafe zip entries
(.., absolute, backslash, links, duplicates), an existing output directory and
evidence key collisions are refused, leaving no output behind."""
import contextlib
import hashlib
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('fetch_ci_evidence', ROOT / 'scripts/fetch-ci-evidence.py')
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)
COMMIT = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
RECORD = json.loads(subprocess.check_output(['git', '-C', str(ROOT), 'show', f'{COMMIT}:release/compatibility.json'], text=True))
REPO = 'owner/repo'


def zip_bytes(entries, link=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
        if link:
            info = zipfile.ZipInfo(link)
            info.external_attr = 0o120777 << 16
            archive.writestr(info, 'target')
    return stream.getvalue()


def contents(name):
    """Files of one artifact: the evidence file its key names, and one more."""
    files = {'notes.txt': name.encode()}
    for relative in fetch.evidence_keys(RECORD).values():
        artifact, _, path = relative.partition('/')
        if artifact == name:
            files[path] = json.dumps({'artifact': name}).encode()
    return files


class FakeGitHub:
    def __init__(self):
        self.run = {'id': 7, 'run_attempt': 1, 'head_sha': COMMIT, 'head_branch': 'main', 'event': 'workflow_dispatch',
                    'status': 'completed', 'conclusion': 'success', 'path': '.github/workflows/native.yml',
                    'created_at': '2026-10-22T00:00:00Z', 'html_url': 'https://example.invalid/runs/7',
                    'repository': {'full_name': REPO}, 'head_repository': {'full_name': REPO}}
        names = sorted(fetch.expected_artifacts(RECORD)) + ['native-x64-clang-node-24.21.0']
        self.zips = {name: zip_bytes(contents(name)) for name in names}
        self.artifacts = {name: {'id': 100 + index, 'name': name, 'expired': False, 'expires_at': '2099-01-01T00:00:00Z',
                                 'size_in_bytes': len(self.zips[name]), 'digest': 'sha256:' + hashlib.sha256(self.zips[name]).hexdigest()}
                          for index, name in enumerate(names)}

    def set_zip(self, name, data, digest=True):
        self.zips[name] = data
        if digest:
            self.artifacts[name]['digest'] = 'sha256:' + hashlib.sha256(data).hexdigest()

    def __call__(self, endpoint, output=None):
        if endpoint.startswith(f'repos/{REPO}/actions/workflows/native.yml/runs?'):
            return {'workflow_runs': [self.run] if f'head_sha={self.run["head_sha"]}' in endpoint else []}
        if endpoint == f'repos/{REPO}/actions/runs/{self.run["id"]}':
            return self.run
        if endpoint.startswith(f'repos/{REPO}/actions/runs/{self.run["id"]}/artifacts?'):
            return {'artifacts': list(self.artifacts.values()) if 'page=1' in endpoint else []}
        for name, item in self.artifacts.items():
            if endpoint == f'repos/{REPO}/actions/artifacts/{item["id"]}/zip':
                pathlib.Path(output).write_bytes(self.zips[name])
                return None
        raise fetch.Refused(f'unexpected endpoint {endpoint}')


class Fetch(unittest.TestCase):
    def setUp(self):
        self.directory = pathlib.Path(tempfile.mkdtemp(prefix='ducktape-fetch-'))
        self.addCleanup(lambda: __import__('shutil').rmtree(self.directory, ignore_errors=True))
        self.github = FakeGitHub()
        self.output = self.directory / 'ci'

    def fetch(self, *extra):
        argv = ['fetch-ci-evidence.py', '--candidate', COMMIT, '--output', str(self.output), '--repository', REPO, *extra]
        with patch.object(fetch, 'gh', self.github), patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()):
            fetch.main()

    def refused(self, message, *extra):
        with self.assertRaises(fetch.Refused) as caught:
            self.fetch(*extra)
        self.assertIn(message, str(caught.exception))
        self.assertFalse(self.output.exists())
        self.assertEqual([path.name for path in self.directory.iterdir() if '.partial-' in path.name], [])

    def test_complete_run(self):
        manifest = self.directory / 'evidence.json'
        manifest.write_text(json.dumps({'native': 'native/report.json'}))
        self.fetch('--merge', str(manifest))
        run = json.loads((self.output / 'ci-run.json').read_text())
        self.assertEqual((run['head_sha'], run['run_id'], run['event'], run['conclusion']), (COMMIT, 7, 'workflow_dispatch', 'success'))
        self.assertIn('native-x64-clang-node-24.21.0', run['artifacts'])
        for relative, digest in run['files'].items():
            self.assertEqual(hashlib.sha256((self.output / relative).read_bytes()).hexdigest(), digest)
        keys = json.loads((self.output / 'evidence.json').read_text())
        self.assertEqual(keys, fetch.evidence_keys(RECORD))
        for platform in ('linux-x64', 'linux-arm64', 'windows-x64'):
            self.assertIn(f'{platform}:native', keys)
        self.assertIn('windows-x64:stress', keys)
        self.assertNotIn('linux-x64:stress', keys)
        merged = json.loads(manifest.read_text())
        self.assertEqual(merged['native'], 'native/report.json')
        self.assertEqual(merged['release_packages'], 'ci/packages-assembled/inventory.json')

    def test_merge_collision(self):
        manifest = self.directory / 'evidence.json'
        manifest.write_text(json.dumps({'release_packages': 'elsewhere/inventory.json'}))
        self.refused('already has release_packages', '--merge', str(manifest))
        self.assertEqual(json.loads(manifest.read_text()), {'release_packages': 'elsewhere/inventory.json'})
        other = self.directory / 'ci2'
        self.output = other
        self.fetch('--merge', str(manifest), '--replace')
        self.assertEqual(json.loads(manifest.read_text())['release_packages'], 'ci2/packages-assembled/inventory.json')

    def test_wrong_runs(self):
        cases = {'head_sha': ('0' * 40, 'is not'), 'event': ('push', 'event push'), 'conclusion': ('failure', 'not completed/success'),
                 'path': ('.github/workflows/platforms.yml', 'workflow'),
                 'head_repository': ({'full_name': 'fork/repo'}, 'own branch')}
        for key, (value, message) in cases.items():
            with self.subTest(key):
                self.github = FakeGitHub()
                self.github.run[key] = value
                self.refused(message, '--run', '7')
                # Searching by commit never picks it either.
                self.refused('No successful candidate run')

    def test_missing_and_expired_artifacts(self):
        del self.github.artifacts['stress-windows-x64']
        self.refused('stress-windows-x64', '--run', '7')
        self.refused('No successful candidate run')
        self.github = FakeGitHub()
        self.github.artifacts['packages-assembled']['expired'] = True
        self.refused('packages-assembled', '--run', '7')

    def test_missing_evidence_file(self):
        self.github.set_zip('installed-windows-x64-real-model', zip_bytes({'installed.json': b'{}'}))
        self.refused('windows-x64:electron')

    def test_digest_mismatch(self):
        self.github.set_zip('evidence-windows-x64', zip_bytes({'report.json': b'{"edited": true}'}), digest=False)
        self.refused('does not match the artifact digest')

    def test_missing_digest(self):
        self.github.artifacts['evidence-windows-x64']['digest'] = None
        self.refused('no sha256 digest')

    def test_unsafe_entries(self):
        for label, data in {'parent': zip_bytes({'report.json': b'{}', '../escape': b'x'}),
                            'absolute': zip_bytes({'report.json': b'{}', '/etc/escape': b'x'}),
                            'backslash': zip_bytes({'report.json': b'{}', '..\\escape': b'x'}),
                            'drive': zip_bytes({'report.json': b'{}', 'C:/escape': b'x'}),
                            'link': zip_bytes({'report.json': b'{}'}, link='linked'),
                            'duplicate': zip_bytes({'report.json': b'{}', 'REPORT.json': b'{}'})}.items():
            with self.subTest(label):
                self.github = FakeGitHub()
                self.github.set_zip('evidence-linux-arm64', data)
                self.refused('evidence-linux-arm64')

    def test_existing_output(self):
        self.output.mkdir()
        with self.assertRaises(SystemExit):
            self.fetch()


if __name__ == '__main__':
    unittest.main()
