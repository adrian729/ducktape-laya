#!/usr/bin/env python3
"""Model-free checks of the release assets: scripts/release-assets.py assemble,
scripts/check-release-assets.sh (what attest-release.yml runs) and
scripts/release-gates.jq (shared with verify-release-assets.sh).

On synthetic release packages (tests/release_world.py) and a schema-2
gates.json, the assembled assets pass; an extra, unlisted or hidden asset, a
missing wheel, a failed target gate, a gate or target that the tagged commit's
release record and policy do not require (or one they require that is missing,
tier-2 gate sets trimmed included), no candidate run, schema 1 with targets, two
tier-1 targets, native platforms that are not the qualified targets, a fragment
from another commit, another commit and a tag that is not the record's version
are refused, and assembly refuses gates that are not ready, not schema 2 or
without a candidate run, packages that are not the qualified ones or differ from
their inventory, and platforms that are not the targets. A schema-1 gates.json
passes the shared filter for 0.1.0 and 0.2.0 only, and the published 0.1.0 and
0.2.0 gates.json still pass against their tagged records.

scripts/check-release-run.py (attest-release) runs against a fake GitHub API: the
published native inventory must be the packages-assembled inventory of the
successful, complete candidate run on the tagged commit that gates.json names.
scripts/registry-state.py (publish-release retries) runs against canned registry
answers: a version a registry already holds is skipped only with the same contents.

scripts/verify-release-assets.sh runs against a fake gh: the signer is pinned by the exact
certificate identity and the certificate's repository, ref, trigger and runner are
checked. scripts/pypi-missing-wheels.py (the PyPI publish job) runs against fake PyPI
payloads: published wheels with equal bytes are skipped, differing or foreign files fail.

With --published DIR TAG it instead checks a downloaded release (gh release
download TAG -D DIR) as attest-release.yml would, against the tagged commit's
release record; 0.2.0 must pass.
"""
import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT / 'scripts'))
from release_support import sha256  # noqa: E402
from release_world import World  # noqa: E402

CHECK = ROOT / 'scripts/check-release-assets.sh'
FILTER = ROOT / 'scripts/release-gates.jq'
COMMIT = 'a' * 40
RECORD = json.loads((ROOT / 'release/compatibility.json').read_text())
POLICY = json.loads((ROOT / 'release/qualification.json').read_text())
RELEASES = pathlib.Path(tempfile.mkdtemp(prefix='ducktape-release-records-'))


def release_dir(version=None, change=None):
    """A release/ directory: this checkout's record and policy, the record's version (or VERSION) and CHANGE applied."""
    record = json.loads(json.dumps(RECORD))
    record['package_version'] = version or RECORD['package_version']
    if change:
        change(record)
    directory = RELEASES / str(len(list(RELEASES.iterdir())))
    directory.mkdir()
    (directory / 'compatibility.json').write_text(json.dumps(record, indent=2))
    shutil.copy(ROOT / 'release/qualification.json', directory / 'qualification.json')
    return directory


def check(directory, commit=COMMIT, tag=None, release=None):
    return subprocess.run(['bash', str(CHECK), str(directory), commit, tag or 'v' + RECORD['package_version'], str(release or release_dir())],
                          capture_output=True, text=True)


def gates_filter(gates, commit=COMMIT, version=None, record=None):
    record = {**(record or RECORD), 'package_version': version or RECORD['package_version']}
    return subprocess.run(['jq', '-e', '--arg', 'commit', commit, '--argjson', 'record', json.dumps(record), '--argjson', 'policy', json.dumps(POLICY),
                           '-f', str(FILTER)], input=json.dumps(gates), capture_output=True, text=True).returncode == 0


def write_sums(directory):
    (directory / 'SHA256SUMS').write_text(''.join(f'{sha256(path)}  {path.name}\n' for path in sorted(directory.iterdir())
                                                  if path.name != 'SHA256SUMS'))


class Assets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='ducktape-release-assets-')
        cls.directory = pathlib.Path(cls.temp.name)
        cls.world = World(cls.directory / 'world', COMMIT, 's' * 64)
        gates = {'schema_version': 2, 'candidate_commit': COMMIT, 'checker_commit': COMMIT, 'checker_tree_dirty': False,
                 'release_ready': True, 'gates': {g: {'status': 'passed'} for g in cls.world.record['required_release_gates']},
                 'inventories_sha256': {'native': sha256(cls.world.release / 'inventory.json'),
                                        'browser': sha256(cls.world.paths['browser_package'])}}
        gates['targets'] = {item['platform']: {'tier': item['tier'], 'gates': {g: {'status': 'passed'} for g in item['required_gates']},
                                               **({'ci_run': {'run_id': 7}} if item['tier'] != 1 else {})}
                            for item in cls.world.record['targets']}
        gates['ci_run'] = {'repository': 'adrian729/ducktape-laya', 'run_id': 7, 'head_sha': COMMIT}
        cls.gates = gates
        cls.assets = cls.directory / 'assets'
        result = cls.assemble(gates, cls.assets)
        assert result.returncode == 0, result.stderr

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def assemble(cls, gates, output, packages=None):
        path = output.parent / (output.name + '-gates.json')
        path.write_text(json.dumps(gates))
        return subprocess.run([sys.executable, str(ROOT / 'scripts/release-assets.py'), 'assemble', '--packages', str(packages or cls.world.release),
                               '--browser-package', str(cls.world.paths['browser_package'].parent), '--gates', str(path),
                               '--output', str(output)], capture_output=True, text=True)

    def copy(self, name):
        target = self.directory / name
        shutil.copytree(self.assets, target)
        return target

    def edit_json(self, directory, name, change):
        value = json.loads((directory / name).read_text())
        change(value)
        (directory / name).write_text(json.dumps(value, indent=2) + '\n')

    def refused(self, directory, message, commit=COMMIT, **options):
        result = check(directory, commit, **options)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)

    def test_assembled_assets_pass(self):
        result = check(self.assets)
        self.assertEqual(result.returncode, 0, result.stderr)
        names = {path.name for path in self.assets.iterdir()}
        version = self.world.version
        self.assertTrue({'gates.json', 'SHA256SUMS', 'native-inventory.json', 'npm-browser-inventory.json', 'native-sbom.cdx.json',
                         'npm-browser-sbom.cdx.json', f'ducktape-laya-{version}-windows-x64.zip', f'ducktape-laya-{version}-linux-arm64.tar.gz',
                         f'ducktape_laya-{version}-py3-none-win_amd64.whl', f'ducktape_laya-{version}-py3-none-manylinux_2_38_aarch64.whl'} <= names)
        self.assertNotIn('sbom.cdx.json', names)

    def test_extra_and_unlisted_assets(self):
        extra = self.copy('extra')
        (extra / 'notes.txt').write_text('x')
        self.refused(extra, 'Not listed in SHA256SUMS: notes.txt')
        write_sums(extra)
        self.refused(extra, 'Unqualified asset: notes.txt')
        # Hidden files are directory entries too.
        hidden = self.copy('hidden')
        (hidden / '.notes').write_text('x')
        self.refused(hidden, 'Not listed in SHA256SUMS: .notes')

    def test_tag_and_record(self):
        # The tag names the tagged commit's package version, and the record and policy come from that commit.
        self.refused(self.assets, 'is not the package version', tag='v9.9.9')
        self.refused(self.assets, 'Not a release tag', tag='0.2.0')
        self.refused(self.assets, 'Not a release tag', tag='v1.2.3-rc1')
        self.refused(self.assets, 'Not a commit', commit='HEAD')
        empty = RELEASES / 'empty'
        empty.mkdir()
        self.refused(self.assets, 'lacks compatibility.json', release=empty)

    def test_missing_wheel(self):
        missing = self.copy('missing')
        (missing / f'ducktape_laya-{self.world.version}-py3-none-win_amd64.whl').unlink()
        write_sums(missing)
        self.refused(missing, 'differs from native-inventory.json')

    def test_gates(self):
        cases = {'failed target gate': lambda g: g['targets']['windows-x64']['gates']['short_stress'].update(status='failed'),
                 'empty target gates': lambda g: g['targets']['linux-arm64'].update(gates={}),
                 'schema 1 with targets': lambda g: g.update(schema_version=1),
                 'two tier-1 targets': lambda g: g['targets']['linux-arm64'].update(tier=1),
                 'no targets': lambda g: g.update(targets={}),
                 'not ready': lambda g: g.update(release_ready=False),
                 'dirty checker': lambda g: g.update(checker_tree_dirty=True),
                 'checker at another commit': lambda g: g.update(checker_commit='c' * 40),
                 'failed release gate': lambda g: g['gates']['stress'].update(status='not_run'),
                 # The gate set is the record's, not whatever gates.json lists.
                 'release gate left out': lambda g: g['gates'].pop('stress'),
                 'unknown release gate': lambda g: g['gates'].update(extra={'status': 'passed'}),
                 'tier-2 gates trimmed': lambda g: g['targets']['windows-x64'].update(gates={'abi': {'status': 'passed'}}),
                 'tier-2 gate left out': lambda g: g['targets']['linux-arm64']['gates'].pop('reproducibility'),
                 'tier-1 gate left out': lambda g: g['targets']['linux-x64']['gates'].pop('performance'),
                 'tier changed': lambda g: g['targets']['windows-x64'].update(tier=1),
                 'target added': lambda g: g['targets'].update({'macos-arm64': g['targets']['linux-arm64']}),
                 'no candidate run': lambda g: g.pop('ci_run'),
                 'candidate run of another commit': lambda g: g['ci_run'].update(head_sha='b' * 40),
                 'run id not a number': lambda g: g['ci_run'].update(run_id='7'),
                 'tier-2 evidence of another run': lambda g: g['targets']['linux-arm64']['ci_run'].update(run_id=8)}
        for label, change in cases.items():
            with self.subTest(label):
                directory = self.copy('gates-' + label.replace(' ', '-'))
                self.edit_json(directory, 'gates.json', change)
                write_sums(directory)
                self.refused(directory, 'does not qualify')

    def test_record_decides_the_gates(self):
        # A tagged commit whose record or policy asks for more than gates.json has fails, whatever gates.json says.
        cases = {'record needs another tier-2 gate': lambda r: r['targets'][1]['required_gates'].pop(),
                 'record has another target': lambda r: r['targets'].pop(),
                 'record schema 1': lambda r: r.update(schema_version=1)}
        for label, change in cases.items():
            with self.subTest(label):
                self.refused(self.assets, 'does not qualify', release=release_dir(change=change))

    def test_platforms_must_be_the_targets(self):
        directory = self.copy('platforms')
        self.edit_json(directory, 'gates.json', lambda g: g['targets'].pop('windows-x64'))
        write_sums(directory)
        # A record without windows-x64 qualifies these gates, but the native packages still carry it.
        without_windows = release_dir(change=lambda r: r.update(targets=[t for t in r['targets'] if t['platform'] != 'windows-x64']))
        self.refused(directory, 'not the qualified targets', release=without_windows)
        self.refused(directory, 'does not qualify')

    def verify(self, certificate_changes=None, predicate_changes=None, asset='ducktape-laya-%s.pom'):
        """scripts/verify-release-assets.sh against a fake gh that serves self.assets and a canned attestation."""
        work = self.directory / f'verify-{len(list(self.directory.iterdir()))}'
        (work / 'bin').mkdir(parents=True)
        repo, signer = 'adrian729/ducktape-laya', 'https://github.com/adrian729/ducktape-laya/.github/workflows/attest-release.yml@refs/heads/main'
        certificate = {'subjectAlternativeName': signer, 'buildSignerURI': signer, 'buildConfigURI': signer,
                       'sourceRepositoryURI': f'https://github.com/{repo}', 'githubWorkflowRepository': repo,
                       'sourceRepositoryRef': 'refs/heads/main', 'githubWorkflowRef': 'refs/heads/main',
                       'githubWorkflowTrigger': 'workflow_dispatch', 'runnerEnvironment': 'github-hosted', **(certificate_changes or {})}
        gates = hashlib.sha256((self.assets / 'gates.json').read_bytes()).hexdigest()
        predicate = {'release': 'v9.9.9', 'repository': repo, 'source_commit': COMMIT, 'gates_sha256': gates, 'release_ready': True,
                     **(predicate_changes or {})}
        (work / 'attestation.json').write_text(json.dumps([{'verificationResult': {'signature': {'certificate': certificate},
                                                                                    'statement': {'predicate': predicate}}}]))
        # The tagged commit's record and policy, as the contents API serves them (raw).
        release = release_dir('9.9.9')
        fake = work / 'bin' / 'gh'
        fake.write_text(f'''#!/usr/bin/env bash
case "$*" in
  "release download "*) while [ $# -gt 0 ]; do case "$1" in -D) dir=$2; shift;; -p) cp "{self.assets}/$2" "$dir/"; shift;; esac; shift; done;;
  "api repos/{repo}/git/ref/tags/v9.9.9 --jq "*) echo "refs/tags/v9.9.9 tag {'c' * 40}";;
  "api repos/{repo}/git/tags/{'c' * 40} --jq "*) echo "commit {COMMIT}";;
  "api -H Accept: application/vnd.github.raw+json repos/{repo}/contents/release/compatibility.json?ref={COMMIT}") cat "{release}/compatibility.json";;
  "api -H Accept: application/vnd.github.raw+json repos/{repo}/contents/release/qualification.json?ref={COMMIT}") cat "{release}/qualification.json";;
  "attestation verify "*) echo "$@" >> "{work}/calls"; cat "{work}/attestation.json";;
  *) echo "unexpected gh $*" >&2; exit 9;;
esac
''')
        fake.chmod(0o755)
        name = asset % self.world.version
        result = subprocess.run(['bash', str(ROOT / 'scripts/verify-release-assets.sh'), 'v9.9.9', str(work / 'out'), name], capture_output=True,
                                text=True, env={**os.environ, 'PATH': f'{work / "bin"}:{os.environ["PATH"]}'})
        calls = (work / 'calls').read_text() if (work / 'calls').exists() else ''
        return result, calls

    def test_signer_is_pinned_exactly(self):
        result, calls = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)
        # The exact certificate identity (workflow path and ref), never the prefix-matching --signer-workflow.
        self.assertIn('--cert-identity https://github.com/adrian729/ducktape-laya/.github/workflows/attest-release.yml@refs/heads/main', calls)
        self.assertIn('--source-ref refs/heads/main', calls)
        self.assertNotIn('--signer-workflow', calls)
        for label, changes in {'another ref': {'sourceRepositoryRef': 'refs/heads/feature'},
                               'workflow ref': {'githubWorkflowRef': 'refs/tags/v9.9.9'},
                               'another workflow': {'subjectAlternativeName': 'https://github.com/adrian729/ducktape-laya/.github/workflows/attest-release.yml.evil@refs/heads/main'},
                               'another signer URI': {'buildSignerURI': 'https://github.com/adrian729/ducktape-laya/.github/workflows/other.yml@refs/heads/main'},
                               'another repository': {'sourceRepositoryURI': 'https://github.com/adrian729/other'},
                               'push trigger': {'githubWorkflowTrigger': 'push'},
                               'self-hosted': {'runnerEnvironment': 'self-hosted'}}.items():
            with self.subTest(label):
                result, _ = self.verify(changes)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('has no attestation for', result.stderr)
        result, _ = self.verify(predicate_changes={'source_commit': 'b' * 40})
        self.assertNotEqual(result.returncode, 0)

    def test_schema_is_compared_numerically(self):
        # jq prints 2.0 as "2.0": a shell string comparison with 2 would skip the platform check.
        directory = self.copy('schema-2.0')
        self.edit_json(directory, 'gates.json', lambda g: (g.update(schema_version=2.0), g['targets'].pop('windows-x64')))
        self.assertIn('"schema_version": 2.0', (directory / 'gates.json').read_text())
        write_sums(directory)
        without_windows = release_dir(change=lambda r: r.update(targets=[t for t in r['targets'] if t['platform'] != 'windows-x64']))
        self.refused(directory, 'not the qualified targets', release=without_windows)
        # The same value with the right targets passes.
        directory = self.copy('schema-2.0-ok')
        self.edit_json(directory, 'gates.json', lambda g: g.update(schema_version=2.0))
        write_sums(directory)
        self.assertEqual(check(directory).returncode, 0)

    def test_multi_platform_inventory_needs_schema_2(self):
        directory = self.copy('schema-1-multi')
        self.edit_json(directory, 'gates.json', lambda g: (g.update(schema_version=1), g.pop('targets')))
        write_sums(directory)
        # Schema 1 is a 0.2.0 shape; with a 0.2.0 record it passes the filter, and still fails here.
        self.assertTrue(gates_filter(json.loads((directory / 'gates.json').read_text()), version='0.2.0'))
        self.refused(directory, 'needs a schema 2 gates.json', tag='v0.2.0', release=release_dir('0.2.0'))

    def test_fragment_from_another_commit(self):
        directory = self.copy('fragment')
        self.edit_json(directory, 'native-inventory.json', lambda i: i['fragments']['windows-x64'].update(packaging_source_commit='b' * 40))
        self.edit_json(directory, 'gates.json', lambda g: g['inventories_sha256'].update(native=sha256(directory / 'native-inventory.json')))
        write_sums(directory)
        self.refused(directory, 'a fragment is not from')

    def test_tampered_artifact_and_wrong_commit(self):
        directory = self.copy('tampered')
        (directory / f'ducktape-laya-{self.world.version}.tgz').write_bytes(b'other')
        self.refused(directory, 'did NOT match')
        self.refused(self.assets, 'does not qualify', commit='b' * 40)

    def test_assembly_refusals(self):
        cases = {'not ready': ({**self.gates, 'release_ready': False}, 'not release ready'),
                 'schema 1': ({**self.gates, 'schema_version': 1}, 'schema 2'),
                 'no candidate run': ({k: v for k, v in self.gates.items() if k != 'ci_run'}, 'names no CI candidate run'),
                 'other inventory': ({**self.gates, 'inventories_sha256': {**self.gates['inventories_sha256'], 'native': '0' * 64}},
                                     'not the package directory gates.json qualified'),
                 'targets': ({**self.gates, 'targets': {k: v for k, v in self.gates['targets'].items() if k != 'linux-arm64'}},
                             'not the qualified targets')}
        for label, (gates, message) in cases.items():
            with self.subTest(label):
                output = self.directory / ('refused-' + label.replace(' ', '-'))
                result = self.assemble(gates, output)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertFalse(output.exists())
        packages = self.directory / 'tampered-packages'
        shutil.copytree(self.world.release, packages)
        (packages / f'ducktape-laya-{self.world.version}.jar').write_bytes(b'other')
        result = self.assemble(self.gates, self.directory / 'refused-tampered', packages)
        self.assertIn('differs from its inventory', result.stderr)

    def test_schema_1_filter(self):
        # The shape of the 0.1.0 and 0.2.0 gates.json: release-level gates, no targets; accepted for those versions only.
        gates = {k: v for k, v in self.gates.items() if k not in ('targets', 'ci_run')}
        gates['schema_version'] = 1
        self.assertTrue(gates_filter(gates, version='0.1.0'))
        self.assertTrue(gates_filter(gates, version='0.2.0'))
        self.assertFalse(gates_filter(gates, version='0.3.0'))
        self.assertFalse(gates_filter(gates, version='1.0.0'))
        self.assertFalse(gates_filter({**gates, 'targets': self.gates['targets']}, version='0.2.0'))
        self.assertFalse(gates_filter({**gates, 'schema_version': 3, 'targets': self.gates['targets']}))
        self.assertTrue(gates_filter(self.gates))
        self.assertTrue(gates_filter(self.gates, version='0.3.0'))
        self.assertFalse(gates_filter(self.gates, 'b' * 40))

    def test_published_releases_still_verify(self):
        # The 0.1.0 and 0.2.0 gates.json, as published, against the records of their tagged commits.
        for tag in ('v0.1.0', 'v0.2.0'):
            commit = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--verify', '-q', tag + '^{commit}'], capture_output=True, text=True).stdout.strip()
            if not commit:
                continue  # a shallow checkout without tags
            show = lambda path: json.loads(subprocess.check_output(['git', '-C', str(ROOT), 'show', f'{commit}:{path}'], text=True))
            with self.subTest(tag):
                gates = {'schema_version': 1, 'candidate_commit': commit, 'checker_tree_dirty': False, 'release_ready': True,
                         'gates': {g: {'status': 'passed'} for g in show('release/compatibility.json')['required_release_gates']}}
                self.assertTrue(gates_filter(gates, commit, record=show('release/compatibility.json'), version=tag[1:]))


class CandidateRun(unittest.TestCase):
    """scripts/check-release-run.py (attest-release.yml) against a fake GitHub API: the published native
    inventory must be the packages-assembled inventory of the successful candidate run gates.json names."""
    SCRIPT = ROOT / 'scripts/check-release-run.py'
    REPO = 'adrian729/ducktape-laya'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ducktape-release-run-')
        self.addCleanup(self.temp.cleanup)
        self.work = pathlib.Path(self.temp.name)
        self.inventory = self.work / 'native-inventory.json'
        self.inventory.write_text(json.dumps({'kind': 'multi-platform', 'packaging_source_commit': COMMIT}, indent=2) + '\n')
        self.gates = {'schema_version': 2, 'ci_run': {'run_id': 7, 'head_sha': COMMIT}}
        self.record = json.loads(json.dumps(RECORD))
        self.run = {'id': 7, 'run_attempt': 1, 'head_sha': COMMIT, 'event': 'workflow_dispatch', 'status': 'completed', 'conclusion': 'success',
                    'path': '.github/workflows/native.yml', 'html_url': 'https://example.invalid/runs/7',
                    'repository': {'full_name': self.REPO}, 'head_repository': {'full_name': self.REPO}}
        sys.path.insert(0, str(ROOT / 'scripts'))
        import importlib.util
        spec = importlib.util.spec_from_file_location('fetch_ci_evidence', ROOT / 'scripts/fetch-ci-evidence.py')
        fetch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fetch)
        self.names = sorted(fetch.expected_artifacts(self.record))
        self.assembled = self.inventory.read_bytes()

    def zip_bytes(self, entries):
        import io
        import zipfile
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        return stream.getvalue()

    def check(self, digest=None, artifacts=None):
        api = self.work / f'api-{len(list(self.work.iterdir()))}'
        base = api / 'repos' / self.REPO / 'actions'
        (base / 'runs' / '7').mkdir(parents=True)
        (base / 'artifacts' / '55').mkdir(parents=True)
        data = self.zip_bytes({'inventory.json': self.assembled, 'ducktape-laya-1.0.0.tgz': b'x'})
        (base / 'artifacts' / '55' / 'zip.zip').write_bytes(data)
        (base / 'runs' / '7.json').write_text(json.dumps(self.run))
        listing = [{'id': 55 if name == 'packages-assembled' else 60 + index, 'name': name, 'expired': False,
                    'digest': 'sha256:' + (digest or hashlib.sha256(data).hexdigest())} for index, name in enumerate(self.names)]
        (base / 'runs' / '7' / 'artifacts.json').write_text(json.dumps({'artifacts': artifacts(listing) if artifacts else listing}))
        for name, value in (('gates.json', self.gates), ('compatibility.json', self.record)):
            (self.work / name).write_text(json.dumps(value))
        return subprocess.run([sys.executable, '-I', str(self.SCRIPT), '--gates', str(self.work / 'gates.json'), '--inventory', str(self.inventory),
                               '--commit', COMMIT, '--record', str(self.work / 'compatibility.json'), '--api', str(api)],
                              capture_output=True, text=True)

    def refused(self, message, **options):
        result = self.check(**options)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)

    def test_the_candidate_runs_inventory_passes(self):
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('packages-assembled inventory of candidate run 7', result.stdout)

    def test_other_inventory(self):
        self.assembled = self.assembled.replace(b'multi-platform', b'other')
        self.refused('is not the packages-assembled inventory of run 7')

    def test_wrong_run(self):
        for key, value, message in (('head_sha', 'b' * 40, 'is not'), ('event', 'push', 'event push'), ('conclusion', 'failure', 'not completed/success'),
                                    ('path', '.github/workflows/platforms.yml', 'workflow'), ('head_repository', {'full_name': 'fork/x'}, 'own branch')):
            with self.subTest(key):
                self.setUp()
                self.run[key] = value
                self.refused(message)

    def test_not_a_candidate_run(self):
        # A real_model dispatch also assembles packages, but keeps no tier-2 stress or reproducibility artifacts.
        self.refused('not a complete candidate run', artifacts=lambda items: [i for i in items if not i['name'].startswith('stress-')])

    def test_artifact_digest_and_expiry(self):
        self.refused('does not match its digest', digest='0' * 64)
        self.refused('expired', artifacts=lambda items: [{**i, 'expired': i['name'] == 'packages-assembled'} for i in items])
        self.refused('no sha256 digest', artifacts=lambda items: [{**i, 'digest': None} for i in items])

    def test_gates_name_the_run(self):
        for label, ci_run in (('none', None), ('other commit', {'run_id': 7, 'head_sha': 'b' * 40}), ('string id', {'run_id': '7', 'head_sha': COMMIT})):
            with self.subTest(label):
                self.gates = {'schema_version': 2, **({'ci_run': ci_run} if ci_run else {})}
                self.refused('names no candidate run')

    def test_schema_1_only_for_0_1_and_0_2(self):
        self.gates = {'schema_version': 1}
        self.record['package_version'] = '0.2.0'
        self.assertEqual(self.check().returncode, 0)
        self.record['package_version'] = '0.3.0'
        self.refused('qualifies only 0.1.0, 0.2.0')


class Registries(unittest.TestCase):
    """scripts/registry-state.py (publish-release retries) against canned registry answers (no network)."""
    SCRIPT = ROOT / 'scripts/registry-state.py'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ducktape-registries-')
        self.addCleanup(self.temp.cleanup)
        self.work = pathlib.Path(self.temp.name)

    def file(self, name, data):
        path = self.work / name
        path.write_bytes(data)
        return path

    def run_script(self, responses, *args):
        answers = {}
        for url, answer in responses.items():
            items = answer if isinstance(answer, list) else [answer]
            converted = []
            for item in items:
                status, body = item if isinstance(item, tuple) else (200, item)
                path = self.file(f'body-{len(list(self.work.iterdir()))}', body if isinstance(body, bytes) else json.dumps(body).encode()) if body is not None else None
                converted.append({'status': status, **({'file': str(path)} if path else {})})
            answers[url] = converted if isinstance(answer, list) else converted[0]
        (self.work / 'responses.json').write_text(json.dumps(answers))
        output = self.work / 'github-output'
        output.write_text('')
        result = subprocess.run([sys.executable, '-I', str(self.SCRIPT), *args, '--responses', str(self.work / 'responses.json')],
                                capture_output=True, text=True, env={**os.environ, 'GITHUB_OUTPUT': str(output)})
        return result, output.read_text().strip()

    def assertState(self, expected, responses, *args):
        result, output = self.run_script(responses, *args)
        self.assertEqual((result.returncode, result.stdout.strip(), output), (0, expected, f'state={expected}'), result.stderr)

    def assertRefused(self, message, responses, *args):
        result, _ = self.run_script(responses, *args)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)

    def test_npm(self):
        import base64
        data = b'tarball'
        tarball = self.file('t.tgz', data)
        url = 'https://registry.npmjs.org/ducktape-laya/1.2.3'
        args = ('npm', '--name', 'ducktape-laya', '--version', '1.2.3', '--file', str(tarball))
        same = {'dist': {'integrity': 'sha512-' + base64.b64encode(hashlib.sha512(data).digest()).decode(), 'shasum': hashlib.sha1(data).hexdigest()}}
        self.assertState('absent', {url: (404, None)}, *args)
        self.assertState('published', {url: same}, *args)
        self.assertRefused('npm holds ducktape-laya@1.2.3 with integrity', {url: {'dist': {**same['dist'], 'integrity': 'sha512-other'}}}, *args)
        self.assertRefused('npm holds', {url: {'dist': {**same['dist'], 'shasum': '0' * 40}}}, *args)
        self.assertRefused('answered 500', {url: (500, None)}, *args)

    def test_crates(self):
        crate = self.file('c.crate', b'crate')
        url = 'https://crates.io/api/v1/crates/ducktape-laya-sys/1.2.3'
        args = ('crates', '--name', 'ducktape-laya-sys', '--version', '1.2.3', '--file', str(crate))
        self.assertState('absent', {url: (404, None)}, *args)
        self.assertState('published', {url: {'version': {'checksum': hashlib.sha256(b'crate').hexdigest()}}}, *args)
        self.assertRefused('crates.io holds ducktape-laya-sys 1.2.3 with checksum', {url: {'version': {'checksum': '0' * 64}}}, *args)

    def zip_bytes(self, entries):
        import io
        import zipfile
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        return stream.getvalue()

    def test_nuget(self):
        entries = {'DucktapeLaya.nuspec': b'spec', 'lib/net10.0/DucktapeLaya.dll': b'dll'}
        nupkg = self.file('DucktapeLaya.1.2.3.nupkg', self.zip_bytes(entries))
        index, package = 'https://api.nuget.org/v3-flatcontainer/ducktapelaya/index.json', \
            'https://api.nuget.org/v3-flatcontainer/ducktapelaya/1.2.3/ducktapelaya.1.2.3.nupkg'
        args = ('nuget', '--name', 'DucktapeLaya', '--version', '1.2.3', '--file', str(nupkg))
        self.assertState('absent', {index: (404, None)}, *args)
        self.assertState('absent', {index: {'versions': ['0.2.0']}}, *args)
        # nuget.org adds its repository signature; every other entry is compared.
        signed = self.zip_bytes({**entries, '.signature.p7s': b'signature'})
        self.assertState('published', {index: {'versions': ['0.2.0', '1.2.3']}, package: signed}, *args)
        self.assertRefused('with other contents: lib/net10.0/DucktapeLaya.dll',
                           {index: {'versions': ['1.2.3']}, package: self.zip_bytes({**entries, 'lib/net10.0/DucktapeLaya.dll': b'other'})}, *args)
        self.assertRefused('with other contents: extra', {index: {'versions': ['1.2.3']}, package: self.zip_bytes({**entries, 'extra': b'x'})}, *args)
        # After a push answered "already exists", wait for the version to be listed, then compare.
        self.assertState('published', {index: [{'versions': []}, {'versions': ['1.2.3']}], package: signed}, *args, '--wait', '60', '--poll', '0')

    def test_maven(self):
        files = [self.file(name, name.encode()) for name in ('a-1.2.3.jar', 'a-1.2.3.pom')]
        base = 'https://repo1.maven.org/maven2/io/github/x/a/1.2.3/'
        args = ('maven', '--group', 'io.github.x', '--name', 'a', '--version', '1.2.3', *map(str, files))
        self.assertState('absent', {base + 'a-1.2.3.jar': (404, None), base + 'a-1.2.3.pom': (404, None)}, *args)
        self.assertState('published', {base + 'a-1.2.3.jar': b'a-1.2.3.jar', base + 'a-1.2.3.pom': b'a-1.2.3.pom'}, *args)
        self.assertRefused('other or missing files: a-1.2.3.pom', {base + 'a-1.2.3.jar': b'a-1.2.3.jar', base + 'a-1.2.3.pom': b'other'}, *args)
        self.assertRefused('other or missing files: a-1.2.3.jar', {base + 'a-1.2.3.jar': (404, None), base + 'a-1.2.3.pom': b'a-1.2.3.pom'}, *args)

    def test_workflow_compares_before_uploading(self):
        import re
        workflow = (ROOT / '.github/workflows/publish-release.yml').read_text()
        parts = re.split(r'(?m)^  ([a-z][\w-]*):\n', workflow.split('\njobs:\n', 1)[1])
        jobs = dict(zip(parts[1::2], parts[2::2]))
        for name in ('npm', 'crates', 'nuget', 'maven'):
            job = jobs[name]
            self.assertIn(f'scripts/registry-state.py {name}', job)
            # Asked before the upload command.
            upload = {'npm': 'npm publish "./dist', 'crates': 'cargo publish --manifest-path', 'nuget': 'dotnet nuget push "', 'maven': '/upload?name='}[name]
            self.assertLess(job.index(f'scripts/registry-state.py {name}'), job.index(upload))
        self.assertIn('--skip-duplicate', jobs['nuget'])
        # Every publish job waits for the crates to be reproduced from the tag, so a crate that does not
        # reproduce stops the whole publication before any upload.
        self.assertIn('cargo package', jobs['reproduce'])
        for name in ('npm', 'crates', 'nuget', 'maven', 'pypi'):
            self.assertRegex(jobs[name].split('steps:')[0], r'needs: reproduce\b')


class PyPI(unittest.TestCase):
    """scripts/pypi-missing-wheels.py against fake PyPI JSON payloads (no network)."""
    SCRIPT = ROOT / 'scripts/pypi-missing-wheels.py'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ducktape-pypi-')
        self.addCleanup(self.temp.cleanup)
        self.work = pathlib.Path(self.temp.name)
        self.dist = self.work / 'dist'
        self.dist.mkdir()
        self.wheels = {f'ducktape_laya-1.2.3-py3-none-{tag}.whl': f'wheel {tag}'.encode() for tag in ('manylinux_2_38_x86_64', 'manylinux_2_38_aarch64', 'win_amd64')}
        for name, data in self.wheels.items():
            (self.dist / name).write_bytes(data)
        self.sums = self.work / 'SHA256SUMS'
        self.sums.write_text(''.join(f'{hashlib.sha256(data).hexdigest()}  {name}\n' for name, data in self.wheels.items()) + 'f' * 64 + '  gates.json\n')

    def run_script(self, payload=None, status=None):
        command = [sys.executable, str(self.SCRIPT), '--package', 'ducktape-laya', '--version', '1.2.3', '--dist', str(self.dist), '--sums', str(self.sums)]
        if payload is not None:
            (self.work / 'pypi.json').write_text(json.dumps(payload))
            command += ['--pypi-json', str(self.work / 'pypi.json')]
        else:
            command += ['--pypi-status', str(status)]
        output = self.work / 'github-output'
        output.write_text('')
        result = subprocess.run(command, capture_output=True, text=True, env={**os.environ, 'GITHUB_OUTPUT': str(output)})
        return result, output.read_text().strip()

    def listing(self, names=None, **changes):
        return {'info': {'name': 'ducktape-laya'}, 'urls': [
            {'filename': name, 'digests': {'sha256': changes.get(name, hashlib.sha256(self.wheels[name]).hexdigest())}} for name in (names or self.wheels)]}

    def remaining(self):
        return sorted(path.name for path in self.dist.iterdir())

    def test_workflow_uses_the_comparison_not_skip_existing(self):
        workflow = (ROOT / '.github/workflows/publish-release.yml').read_text()
        self.assertNotRegex(workflow, r'(?m)^\s*skip-existing:')
        job = workflow[workflow.index('\n  pypi:\n'):workflow.index('\n  nuget:\n')]
        self.assertIn('scripts/pypi-missing-wheels.py', job)
        self.assertLess(job.index('scripts/pypi-missing-wheels.py'), job.index('pypa/gh-action-pypi-publish'))
        self.assertIn("steps.plan.outputs.upload == 'true'", job)
        self.assertIn('id-token: write', job)

    def test_not_published_uploads_everything(self):
        result, output = self.run_script(status=404)
        self.assertEqual((result.returncode, output), (0, 'upload=true'), result.stderr)
        self.assertEqual(self.remaining(), sorted(self.wheels))
        result, output = self.run_script(payload={'info': {}, 'urls': []})
        self.assertEqual((result.returncode, output), (0, 'upload=true'), result.stderr)

    def test_retry_uploads_only_missing_wheels(self):
        done = list(self.wheels)[:2]
        result, output = self.run_script(self.listing(done))
        self.assertEqual((result.returncode, output), (0, 'upload=true'), result.stderr)
        self.assertEqual(self.remaining(), [list(self.wheels)[2]])
        self.assertEqual(sorted(path.name for path in (self.work / 'already-published').iterdir()), sorted(done))

    def test_everything_already_published(self):
        result, output = self.run_script(self.listing())
        self.assertEqual((result.returncode, output), (0, 'upload=false'), result.stderr)
        self.assertEqual(self.remaining(), [])

    def test_wheel_with_other_bytes_fails(self):
        name = list(self.wheels)[1]
        result, output = self.run_script(self.listing(**{name: '0' * 64}))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f'{name} is on PyPI with sha256', result.stderr)
        self.assertEqual((output, self.remaining()), ('', sorted(self.wheels)))

    def test_foreign_file_and_bad_inputs_fail(self):
        payload = self.listing()
        payload['urls'].append({'filename': 'ducktape_laya-1.2.3.tar.gz', 'digests': {'sha256': '1' * 64}})
        result, _ = self.run_script(payload)
        self.assertIn('not part of this release', result.stderr)
        self.assertNotEqual(result.returncode, 0)
        result, _ = self.run_script(status=500)
        self.assertNotEqual(result.returncode, 0)
        # A local wheel that differs from SHA256SUMS never goes up.
        (self.dist / list(self.wheels)[0]).write_bytes(b'tampered')
        result, _ = self.run_script(status=404)
        self.assertIn('does not match SHA256SUMS', result.stderr)
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--published', nargs=2, metavar=('DIR', 'TAG'), help='Check a downloaded release instead')
    args, rest = parser.parse_known_args()
    if args.published:
        directory, tag = args.published
        commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', tag + '^{commit}'], text=True).strip()
        record = RELEASES / 'published'
        record.mkdir()
        subprocess.run(f'git -C "{ROOT}" archive {commit} release | tar -x -C "{record}"', shell=True, check=True)
        result = check(directory, commit, tag, record / 'release')
        print(result.stdout + result.stderr, end='')
        raise SystemExit(result.returncode)
    unittest.main(argv=[sys.argv[0], *rest])
