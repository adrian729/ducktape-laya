#!/usr/bin/env python3
"""Model-free checks of the release assets: scripts/release-assets.py assemble,
scripts/check-release-assets.sh (what attest-release.yml runs) and
scripts/release-gates.jq (shared with verify-release-assets.sh).

On synthetic release packages (tests/release_world.py) and a schema-2
gates.json, the assembled assets pass; an extra or unlisted asset, a missing
wheel, a failed target gate, schema 1 with targets, two tier-1 targets, native
platforms that are not the qualified targets, a fragment from another commit and
another commit are refused, and assembly refuses gates that are not ready or not
schema 2, packages that are not the qualified ones or differ from their
inventory, and platforms that are not the targets. A schema-1 gates.json (0.1.0,
0.2.0) still passes the shared filter.

scripts/verify-release-assets.sh runs against a fake gh: the signer is pinned by the exact
certificate identity and the certificate's repository, ref, trigger and runner are
checked. scripts/pypi-missing-wheels.py (the PyPI publish job) runs against fake PyPI
payloads: published wheels with equal bytes are skipped, differing or foreign files fail.

With --published DIR COMMIT it instead checks a downloaded release (gh release
download TAG -D DIR) as attest-release.yml would; 0.2.0 must pass.
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


def check(directory, commit=COMMIT):
    return subprocess.run(['bash', str(CHECK), str(directory), commit], capture_output=True, text=True)


def gates_filter(gates, commit=COMMIT):
    return subprocess.run(['jq', '-e', '--arg', 'commit', commit, '-f', str(FILTER)], input=json.dumps(gates),
                          capture_output=True, text=True).returncode == 0


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
        gates['targets'] = {item['platform']: {'tier': item['tier'], 'gates': {g: {'status': 'passed'} for g in item['required_gates']}}
                            for item in cls.world.record['targets']}
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

    def refused(self, directory, message, commit=COMMIT):
        result = check(directory, commit)
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
                 'failed release gate': lambda g: g['gates']['stress'].update(status='not_run')}
        for label, change in cases.items():
            with self.subTest(label):
                directory = self.copy('gates-' + label.replace(' ', '-'))
                self.edit_json(directory, 'gates.json', change)
                write_sums(directory)
                self.refused(directory, 'does not qualify')

    def test_platforms_must_be_the_targets(self):
        directory = self.copy('platforms')
        self.edit_json(directory, 'gates.json', lambda g: g['targets'].pop('windows-x64'))
        write_sums(directory)
        self.refused(directory, 'not the qualified targets')

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
        fake = work / 'bin' / 'gh'
        fake.write_text(f'''#!/usr/bin/env bash
case "$1 $2" in
  "release download") while [ $# -gt 0 ]; do case "$1" in -D) dir=$2; shift;; -p) cp "{self.assets}/$2" "$dir/"; shift;; esac; shift; done;;
  "api repos/"*) echo {COMMIT};;
  "attestation verify") echo "$@" >> "{work}/calls"; cat "{work}/attestation.json";;
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
        self.refused(directory, 'not the qualified targets')
        # The same value with the right targets passes.
        directory = self.copy('schema-2.0-ok')
        self.edit_json(directory, 'gates.json', lambda g: g.update(schema_version=2.0))
        write_sums(directory)
        self.assertEqual(check(directory).returncode, 0)

    def test_multi_platform_inventory_needs_schema_2(self):
        directory = self.copy('schema-1-multi')
        self.edit_json(directory, 'gates.json', lambda g: (g.update(schema_version=1), g.pop('targets')))
        write_sums(directory)
        self.assertTrue(gates_filter(json.loads((directory / 'gates.json').read_text())))
        self.refused(directory, 'needs a schema 2 gates.json')

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
        # The shape of the 0.1.0 and 0.2.0 gates.json: release-level gates, no targets.
        gates = {k: v for k, v in self.gates.items() if k != 'targets'}
        gates['schema_version'] = 1
        self.assertTrue(gates_filter(gates))
        self.assertFalse(gates_filter({**gates, 'targets': self.gates['targets']}))
        self.assertFalse(gates_filter({**gates, 'schema_version': 3, 'targets': self.gates['targets']}))
        self.assertTrue(gates_filter(self.gates))
        self.assertFalse(gates_filter(self.gates, 'b' * 40))


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
    parser.add_argument('--published', nargs=2, metavar=('DIR', 'COMMIT'), help='Check a downloaded release instead')
    args, rest = parser.parse_known_args()
    if args.published:
        result = check(*args.published)
        print(result.stdout + result.stderr, end='')
        raise SystemExit(result.returncode)
    unittest.main(argv=[sys.argv[0], *rest])
