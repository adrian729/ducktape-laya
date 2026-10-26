#!/usr/bin/env python3
"""Model-free checks of scripts/fetch-ci-evidence.py against a fake GitHub API.

Serves canned runs and artifact zips in place of `gh api`: a complete candidate
run is downloaded, verified and recorded (ci-run.json, evidence.json, --merge);
runs of another commit, event, conclusion, workflow or repository, missing or
expired artifacts, a zip that does not match its digest, unsafe zip entries
(.., absolute, backslash, links, duplicates), an existing output directory and
evidence key collisions are refused, leaving no output behind. --qualify also
adds the qualify run to an existing download of the same candidate run (refusing
another run, commit or edited file; repeating it changes nothing). With --qualify,
the qualify run (found by commit, or an ancestor's run named by --qualify-run)
is recorded as qualify_run, and its run rules, artifacts, digests and report
sources are refused the same way."""
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
REPO = 'adrian729/ducktape-laya'


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


def run_record(run_id, head):
    return {'id': run_id, 'run_attempt': 1, 'head_sha': head, 'head_branch': 'main', 'event': 'workflow_dispatch',
            'status': 'completed', 'conclusion': 'success', 'path': '.github/workflows/native.yml',
            'created_at': '2026-10-22T00:00:00Z', 'html_url': f'https://example.invalid/runs/{run_id}',
            'repository': {'full_name': REPO}, 'head_repository': {'full_name': REPO}}


def artifact_records(zips, first_id):
    return {name: {'id': first_id + index, 'name': name, 'expired': False, 'expires_at': '2099-01-01T00:00:00Z',
                   'size_in_bytes': len(data), 'digest': 'sha256:' + hashlib.sha256(data).hexdigest()}
            for index, (name, data) in enumerate(zips.items())}


# A commit that the fake ancestry check treats as an ancestor of the candidate (a reused qualify run).
ANCESTOR = 'a' * 40


class FakeGitHub:
    """The candidate run (id 7) and a qualify run (id 8) of the candidate commit."""

    def __init__(self):
        self.run = run_record(7, COMMIT)
        names = sorted(fetch.expected_artifacts(RECORD)) + ['native-x64-clang-node-24.21.0']
        self.zips = {name: zip_bytes(contents(name)) for name in names}
        self.artifacts = artifact_records(self.zips, 100)
        self.qualify = run_record(8, COMMIT)
        self.qualify_zips = {name: self.qualify_zip(name) for name in fetch.QUALIFY_ARTIFACTS}
        self.qualify_artifacts = artifact_records(self.qualify_zips, 200)

    def qualify_zip(self, name, source=None):
        report = {'kind': name, 'status': 'passed', 'source_commit': source or self.qualify['head_sha']}
        return zip_bytes({'report.json': json.dumps(report).encode(), f'{name}.log': b'log'})

    def set_zip(self, name, data, digest=True):
        zips, artifacts = (self.qualify_zips, self.qualify_artifacts) if name in self.qualify_artifacts else (self.zips, self.artifacts)
        zips[name] = data
        if digest:
            artifacts[name]['digest'] = 'sha256:' + hashlib.sha256(data).hexdigest()

    def set_qualify_head(self, head):
        self.qualify['head_sha'] = head
        for name in fetch.QUALIFY_ARTIFACTS:
            self.set_zip(name, self.qualify_zip(name))

    def __call__(self, endpoint, output=None):
        runs = ((self.run, self.artifacts, self.zips), (self.qualify, self.qualify_artifacts, self.qualify_zips))
        if endpoint.startswith(f'repos/{REPO}/actions/workflows/native.yml/runs?'):
            return {'workflow_runs': [run for run, _, _ in runs if f'head_sha={run["head_sha"]}&' in endpoint]}
        for run, artifacts, zips in runs:
            if endpoint == f'repos/{REPO}/actions/runs/{run["id"]}':
                return run
            if endpoint.startswith(f'repos/{REPO}/actions/runs/{run["id"]}/artifacts?'):
                return {'artifacts': list(artifacts.values()) if 'page=1' in endpoint else []}
            for name, item in artifacts.items():
                if endpoint == f'repos/{REPO}/actions/artifacts/{item["id"]}/zip':
                    pathlib.Path(output).write_bytes(zips[name])
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
        ancestry = lambda ancestor, commit: ancestor in (commit, ANCESTOR)
        with patch.object(fetch, 'gh', self.github), patch.object(fetch, 'is_ancestor', ancestry), patch.object(sys, 'argv', argv), \
                contextlib.redirect_stdout(io.StringIO()):
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
        self.assertEqual(keys['electron'], 'installed-linux-x64-real-model/electron.json')
        self.assertNotIn('qualify_run', run)
        for key in fetch.QUALIFY_ARTIFACTS:
            self.assertNotIn(key, keys)
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

    def test_other_repository_refused(self):
        self.refused('only its runs are release evidence', '--repository', 'owner/repo')

    def test_repository_names_agree(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        import release_targets
        self.assertEqual(fetch.REPOSITORY, release_targets.RELEASE_REPOSITORY)

    def test_qualify_run_by_commit(self):
        manifest = self.directory / 'evidence.json'
        self.fetch('--qualify', '--merge', str(manifest))
        run = json.loads((self.output / 'ci-run.json').read_text())
        qualify = run['qualify_run']
        self.assertEqual({key: qualify[key] for key in ('repository', 'workflow_path', 'run_id', 'run_attempt', 'head_sha', 'html_url')},
                         {'repository': REPO, 'workflow_path': '.github/workflows/native.yml', 'run_id': 8, 'run_attempt': 1,
                          'head_sha': COMMIT, 'html_url': 'https://example.invalid/runs/8'})
        self.assertEqual(sorted(qualify['files']), sorted(f'qualify/{name}/{file}' for name in fetch.QUALIFY_ARTIFACTS
                                                          for file in ('report.json', f'{name}.log')))
        for relative, digest in qualify['files'].items():
            self.assertEqual(hashlib.sha256((self.output / relative).read_bytes()).hexdigest(), digest)
        self.assertFalse([path for path in run['files'] if path.startswith('qualify/')])
        keys = json.loads((self.output / 'evidence.json').read_text())
        for name in fetch.QUALIFY_ARTIFACTS:
            self.assertEqual(keys[name], f'qualify/{name}/report.json')
            self.assertEqual(json.loads(manifest.read_text())[name], f'ci/qualify/{name}/report.json')

    def test_qualify_run_of_an_ancestor(self):
        self.github.set_qualify_head(ANCESTOR)
        # Searching by commit never takes another commit's run.
        self.refused('No successful qualify run', '--qualify')
        self.fetch('--qualify', '--qualify-run', '8')
        qualify = json.loads((self.output / 'ci-run.json').read_text())['qualify_run']
        self.assertEqual((qualify['run_id'], qualify['head_sha']), (8, ANCESTOR))

    def test_qualify_run_not_an_ancestor(self):
        self.github.set_qualify_head('b' * 40)
        self.refused('or an ancestor of it', '--qualify', '--qualify-run', '8')

    def test_qualify_run_refusals(self):
        cases = {'event': ('push', 'event push'), 'path': ('.github/workflows/platforms.yml', 'workflow'),
                 'repository': ({'full_name': 'fork/repo'}, 'own branch'), 'conclusion': ('failure', 'not completed/success')}
        for key, (value, message) in cases.items():
            with self.subTest(key):
                self.github = FakeGitHub()
                self.github.qualify[key] = value
                self.refused(message, '--qualify', '--qualify-run', '8')
                self.refused('No successful qualify run', '--qualify')

    def test_qualify_report_of_another_commit(self):
        self.github.set_zip('fuzz', self.github.qualify_zip('fuzz', source=ANCESTOR))
        self.refused("is not the qualify run's head", '--qualify')

    def test_qualify_missing_expired_and_edited_artifacts(self):
        del self.github.qualify_artifacts['upgrade']
        self.refused('lacks or has expired artifacts: upgrade', '--qualify', '--qualify-run', '8')
        self.refused('No successful qualify run', '--qualify')
        self.github = FakeGitHub()
        self.github.qualify_artifacts['sanitizers']['expired'] = True
        self.refused('lacks or has expired artifacts: sanitizers', '--qualify', '--qualify-run', '8')
        self.github = FakeGitHub()
        self.github.set_zip('sanitizers', self.github.qualify_zip('sanitizers', source=ANCESTOR), digest=False)
        self.refused('sanitizers: download does not match the artifact digest', '--qualify')

    def test_qualify_run_needs_qualify(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.fetch('--qualify-run', '8')

    def test_existing_output(self):
        self.output.mkdir()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.fetch()
        # Also an earlier download, unless --qualify adds to it.
        self.output.rmdir()
        self.fetch()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.fetch()

    def test_qualify_added_to_an_existing_download(self):
        manifest = self.directory / 'evidence.json'
        self.fetch('--merge', str(manifest))
        before = json.loads((self.output / 'ci-run.json').read_text())
        candidate_files = {path: (self.output / path).read_bytes() for path in before['files']}
        downloads = []
        original = self.github.__call__
        self.github = lambda endpoint, output=None: (downloads.append(endpoint) if output else None) or original(endpoint, output)
        self.fetch('--qualify', '--merge', str(manifest))
        # Only the three qualify zips were downloaded; the candidate files are untouched.
        self.assertEqual(len(downloads), len(fetch.QUALIFY_ARTIFACTS))
        after = json.loads((self.output / 'ci-run.json').read_text())
        self.assertEqual(after['qualify_run']['run_id'], 8)
        self.assertEqual({k: v for k, v in after.items() if k != 'qualify_run'}, before)
        for path, data in candidate_files.items():
            self.assertEqual((self.output / path).read_bytes(), data)
        keys = json.loads((self.output / 'evidence.json').read_text())
        self.assertEqual(keys, {**fetch.evidence_keys(RECORD), **fetch.qualify_keys()})
        merged = json.loads(manifest.read_text())
        self.assertEqual(merged['fuzz'], 'ci/qualify/fuzz/report.json')
        self.assertEqual(merged['release_packages'], 'ci/packages-assembled/inventory.json')
        # Idempotent: the same qualify run again downloads nothing and changes nothing.
        snapshot = (self.output / 'ci-run.json').read_text(), (self.output / 'evidence.json').read_text(), manifest.read_text()
        downloads.clear()
        self.fetch('--qualify', '--merge', str(manifest))
        self.assertEqual(downloads, [])
        self.assertEqual(((self.output / 'ci-run.json').read_text(), (self.output / 'evidence.json').read_text(), manifest.read_text()), snapshot)

    def test_qualify_extension_refusals(self):
        self.fetch()
        ci_run = (self.output / 'ci-run.json').read_text()
        record = json.loads(ci_run)

        def refused(message, *extra):
            current = (self.output / 'ci-run.json').read_text()
            with self.assertRaises(fetch.Refused) as caught:
                self.fetch('--qualify', *extra)
            self.assertIn(message, str(caught.exception))
            self.assertEqual((self.output / 'ci-run.json').read_text(), current)
            self.assertFalse((self.output / 'qualify').exists())
            self.assertEqual([path.name for path in self.directory.iterdir() if '.partial-' in path.name], [])
        # Another candidate run than the one downloaded.
        refused('holds candidate run 7, not 9', '--run', '9')
        # ci-run.json of another commit.
        for key in ('candidate_commit', 'head_sha'):
            (self.output / 'ci-run.json').write_text(json.dumps(dict(record, **{key: '0' * 40})))
            refused('not of')
            (self.output / 'ci-run.json').write_text(ci_run)
        # A downloaded file that changed since ci-run.json listed it.
        (self.output / sorted(record['files'])[0]).write_text('edited')
        refused('differs from ci-run.json')
        # A directory that is no download at all.
        self.output = self.directory / 'other'
        self.output.mkdir()
        with self.assertRaises(fetch.Refused) as caught:
            self.fetch('--qualify')
        self.assertIn('not a download of this tool', str(caught.exception))

    def test_qualify_extension_other_qualify_run_and_failed_download(self):
        self.fetch('--qualify')
        ci_run = (self.output / 'ci-run.json').read_text()
        self.github.qualify['id'] = 9
        with self.assertRaises(fetch.Refused) as caught:
            self.fetch('--qualify')
        self.assertIn('already holds qualify run 8, not 9', str(caught.exception))
        self.assertEqual((self.output / 'ci-run.json').read_text(), ci_run)
        # A qualify download that fails leaves the existing download as it was.
        self.setUp()
        self.fetch()
        ci_run = (self.output / 'ci-run.json').read_text()
        self.github.set_zip('fuzz', self.github.qualify_zip('fuzz', source=ANCESTOR))
        self.refused_extension("is not the qualify run's head", ci_run)
        self.github = FakeGitHub()
        self.github.qualify_artifacts['upgrade']['digest'] = 'sha256:' + '0' * 64
        self.refused_extension('does not match the artifact digest', ci_run)
        self.github = FakeGitHub()
        self.fetch('--qualify')
        self.assertIn('qualify_run', json.loads((self.output / 'ci-run.json').read_text()))

    def refused_extension(self, message, ci_run):
        with self.assertRaises(fetch.Refused) as caught:
            self.fetch('--qualify')
        self.assertIn(message, str(caught.exception))
        self.assertEqual((self.output / 'ci-run.json').read_text(), ci_run)
        self.assertFalse((self.output / 'qualify').exists())
        self.assertEqual(sorted(json.loads((self.output / 'evidence.json').read_text())), sorted(fetch.evidence_keys(RECORD)))
        self.assertEqual([path.name for path in self.directory.iterdir() if '.partial-' in path.name], [])


if __name__ == '__main__':
    unittest.main()
