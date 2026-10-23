#!/usr/bin/env python3
"""Model-free checks of the release-target gates (scripts/release_targets.py) and of
scripts/check-candidate.py's targets, on synthetic candidate evidence (tests/release_world.py).

Every tier-2 gate passes on consistent evidence, and each kind of wrong evidence
fails the gate that must catch it: another commit or a dirty tree, a CI file
edited after download, a run of another commit or event, a library or addon that
is not the validated one, another platform's library in the release npm package,
native files for a platform outside the targets, a platform missing from the
release packages, a fragment the release was not assembled from, a stress run
that is not the short budget, reproduced builds without the library, an
undocumented or over-cap deviation, a changed decision, installed checks of
other packages, unregistered tests. Missing evidence is not_run. The end-to-end
part runs check-candidate.py on evidence bound to HEAD: the tier-2 targets pass,
gates.json has schema 2, and a CI linux-x64 library or addon that differs from
the locally validated one fails the tier-1 artifact_integrity gate."""
import json
import os
import pathlib
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
from release_support import git, sha256, source_identity  # noqa: E402
from release_targets import Context, evaluate_targets, run_gate, target_gates  # noqa: E402
from release_world import World, digest  # noqa: E402

TIER2 = ('linux-arm64', 'windows-x64')


class Targets(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ducktape-targets-')
        self.addCleanup(self.temp.cleanup)
        self.world = World(self.temp.name, 'a' * 40, 's' * 64)

    def statuses(self, platform):
        ctx = Context(self.world.commit, self.world.source, self.world.record, self.world.policy, ROOT, self.world.paths)
        return {gate: run_gate(check) for gate, check in target_gates(ctx, platform).items()}

    def assertGate(self, platform, gate, status, detail=''):
        result = self.statuses(platform)[gate]
        self.assertEqual(result['status'], status, result)
        self.assertIn(detail, result.get('detail', ''))

    def test_all_targets_pass(self):
        for platform in TIER2:
            self.assertEqual({k: v['status'] for k, v in self.statuses(platform).items()},
                             {gate: 'passed' for gate in self.world.targets[platform]['required_gates']})
        ctx = Context(self.world.commit, self.world.source, self.world.record, self.world.policy, ROOT, self.world.paths)
        tier1 = {gate: {'status': 'not_run'} for gate in self.world.record['required_release_gates']}
        result = evaluate_targets(ctx, tier1)
        self.assertEqual(sorted(result), ['linux-arm64', 'linux-x64', 'windows-x64'])
        self.assertEqual(result['linux-x64'], {'tier': 1, 'gates': tier1})
        self.assertEqual(result['windows-x64']['ci_run']['run_id'], 7)

    def test_report_commit_and_dirty_tree(self):
        self.world.edit('evidence-windows-x64/report.json', lambda r: r.update(source_commit='b' * 40))
        for gate in ('native_contracts', 'abi', 'short_stress', 'artifact_integrity'):
            self.assertGate('windows-x64', gate, 'failed', 'is not candidate')
        self.assertGate('linux-arm64', 'native_contracts', 'passed')
        self.world.edit('evidence-windows-x64/report.json', lambda r: r.update(source_commit='a' * 40, source_tracked_dirty=True))
        self.assertGate('windows-x64', 'abi', 'failed', 'dirty tree')
        self.world.edit('stress-linux-arm64/report.json', lambda r: r.update(source_tracked_dirty=True))
        self.assertGate('linux-arm64', 'short_stress', 'failed', 'dirty tree')

    def test_edited_after_download(self):
        self.world.edit('evidence-linux-arm64/report.json', lambda r: r.update(note='edited'), rehash=False)
        self.assertGate('linux-arm64', 'abi', 'failed', 'changed after download')
        self.world.edit('installed-windows-x64-real-model/installed.json', lambda r: r.update(note='edited'), rehash=False)
        self.assertGate('windows-x64', 'installed_packages', 'failed', 'changed after download')

    def test_run_of_another_repository(self):
        self.world.edit('ci-run.json', lambda r: r.update(repository='owner/fork'), rehash=False)
        self.assertGate('windows-x64', 'abi', 'failed', 'repository owner/fork')
        self.assertGate('linux-arm64', 'reproducibility', 'failed', 'not adrian729/ducktape-laya')

    def test_wrong_run(self):
        self.world.rehash(head='b' * 40)
        self.assertGate('windows-x64', 'abi', 'failed', 'not the candidate')
        self.world.rehash(event='push')
        self.assertGate('linux-arm64', 'reproducibility', 'failed', 'not a successful dispatch')

    def test_validation_identity(self):
        cases = {'mode': lambda r: r.update(mode='model_free'), 'model': lambda r: r['model'].update(sha256='0' * 64),
                 'platform': lambda r: r.update(platform='linux-x64'),
                 'profile': lambda r: r['diagnostics']['numerical_profile'].update(id='native-cpu:avx'),
                 'cpu features': lambda r: r['diagnostics'].update(required_cpu_features=['avx']),
                 'runtime': lambda r: r['diagnostics'].update(llama_cpp_commit='0' * 40),
                 'node': lambda r: r.update(node_version='v26.7.0'), 'gate': lambda r: r['gates']['abi'].update(status='failed')}
        for label, change in cases.items():
            with self.subTest(label):
                self.setUp()
                self.world.edit('evidence-windows-x64/report.json', change)
                self.assertGate('windows-x64', 'abi', 'failed')

    def test_library_and_addon(self):
        self.world.edit('evidence-linux-arm64/report.json', lambda r: r.update(library_sha256='0' * 64))
        self.assertGate('linux-arm64', 'artifact_integrity', 'failed', 'packaged library')
        self.assertGate('linux-arm64', 'short_stress', 'failed', 'validated library')
        self.assertGate('linux-arm64', 'reproducibility', 'failed', 'does not contain the validated library')
        self.setUp()
        self.world.edit('evidence-windows-x64/report.json', lambda r: r.update(addon_sha256='0' * 64))
        self.assertGate('windows-x64', 'artifact_integrity', 'failed', 'Node addon')

    def test_swapped_and_foreign_native_files(self):
        dll = 'package/native/win32-x64/ducktape_laya.dll'
        self.world.repack_release(f'ducktape-laya-{self.world.version}.tgz', lambda entries: {**entries, dll: b'other library'})
        self.assertGate('windows-x64', 'artifact_integrity', 'failed', 'npm native files differ')
        # Each target checks its own files; the names under package/native/ are still the targets'.
        self.assertGate('linux-arm64', 'artifact_integrity', 'passed')
        # The release npm package is no longer what the installed and Electron checks ran.
        self.assertGate('windows-x64', 'installed_packages', 'failed', 'not the release packages')
        self.setUp()
        self.world.repack_release(f'ducktape-laya-{self.world.version}.tgz',
                                  lambda entries: {**entries, 'package/native/darwin-arm64/ducktape_laya.node': b'x'})
        self.assertGate('linux-arm64', 'artifact_integrity', 'failed', 'npm: native files outside')
        self.setUp()
        self.world.repack_release(f'ducktape-laya-{self.world.version}.jar',
                                  lambda entries: {**entries, 'native/macos-arm64/libducktape_laya.dylib': (b'x', True)})
        self.assertGate('linux-arm64', 'artifact_integrity', 'failed', 'java: native files outside')
        self.setUp()
        self.world.repack_release(f'DucktapeLaya.{self.world.version}.nupkg',
                                  lambda entries: {k: v for k, v in entries.items() if not k.startswith('runtimes/linux-arm64/')})
        self.assertGate('linux-arm64', 'artifact_integrity', 'failed', 'nuget native files differ')

    def test_release_inventory(self):
        self.world.edit('packages-assembled/inventory.json', lambda r: r.update(platforms=['linux-arm64', 'linux-x64']))
        self.assertGate('windows-x64', 'installed_packages', 'failed')
        self.assertGate('linux-arm64', 'artifact_integrity', 'failed', 'are not the release targets')
        self.setUp()
        self.world.edit('packages-assembled/inventory.json', lambda r: r.update(packaging_source_commit='b' * 40))
        self.assertGate('linux-arm64', 'artifact_integrity', 'failed', 'not packaged from the clean candidate')
        self.setUp()
        self.world.edit('packages-assembled/inventory.json', lambda r: r['fragments']['windows-x64'].update(inventory_sha256='0' * 64))
        self.assertGate('windows-x64', 'artifact_integrity', 'failed', 'not the fragment the release packages were assembled from')
        self.setUp()
        (self.world.release / f'ducktape-laya-{self.world.version}.pom').write_bytes(b'edited')
        self.world.rehash()
        self.assertGate('linux-arm64', 'artifact_integrity', 'failed', 'size/hash mismatch')

    def test_fragment(self):
        self.world.edit('packages-windows-x64/inventory.json', lambda r: r.update(platform='linux-x64'))
        self.assertGate('windows-x64', 'artifact_integrity', 'failed', 'built for linux-x64')

    def test_stress(self):
        cases = {'kind': (lambda r: r.update(kind='routine_stress'), 'below budget'),
                 'budget': (lambda r: r.update(qualification_budget_met=False), 'below budget'),
                 'seconds': (lambda r: r.update(requested_seconds=60), 'shorter than the budget'),
                 'platform': (lambda r: r.update(target_platform='linux-x64'), 'ran on linux-x64'),
                 'model': (lambda r: r.update(model_sha256='0' * 64), 'another model'),
                 'failed': (lambda r: r.update(status='failed'), 'below budget')}
        for label, (change, message) in cases.items():
            with self.subTest(label):
                self.setUp()
                self.world.edit('stress-windows-x64/report.json', change)
                self.assertGate('windows-x64', 'short_stress', 'failed', message)

    def test_reproducibility(self):
        self.world.edit('reproducibility-windows-x64/report.json', lambda r: r.update(differences=['bin/ducktape_laya.dll']))
        self.assertGate('windows-x64', 'reproducibility', 'failed', 'differ')
        self.setUp()
        self.world.edit('reproducibility-linux-arm64/report.json', lambda r: r.update(target_platform='windows-x64'))
        self.assertGate('linux-arm64', 'reproducibility', 'failed', 'ran on windows-x64')

    def test_reproduced_in_another_environment(self):
        self.world.edit('evidence-windows-x64/report.json', lambda r: r.update(build_environment={'ImageVersion': '20261007.264'}))
        self.world.edit('reproducibility-windows-x64/report.json', lambda r: r.update(build_environment={'ImageVersion': '20261021.278'}))
        self.assertGate('windows-x64', 'reproducibility', 'failed', 'toolchain environment')
        self.world.edit('reproducibility-windows-x64/report.json', lambda r: r.update(build_environment={'ImageVersion': '20261007.264'}))
        self.assertGate('windows-x64', 'reproducibility', 'passed')

    def test_deviations(self):
        self.world.edit('evidence-linux-arm64/original-reference.json', lambda r: r['summary'].update(known_deviations_used=['json_escaping/spam']))
        self.assertGate('linux-arm64', 'original_reference', 'failed', 'undocumented deviations')
        self.world.edit('evidence-windows-x64/original-reference.json',
                        lambda r: r['cases']['json_escaping']['answers']['spam'].update(max_probability_drift=0.07))
        self.assertGate('windows-x64', 'original_reference', 'failed', 'above its cap')
        self.world.edit('evidence-windows-x64/original-reference.json', lambda r: r['summary'].update(status='failed'))
        self.assertGate('windows-x64', 'original_reference', 'failed', 'did not pass')

    def test_drift(self):
        path = self.world.paths['windows-x64:drift']
        value = json.loads(path.read_text())
        path.write_text(json.dumps({**value, 'decision_differences': [{'path': '$.x.choice'}]}))
        self.assertGate('windows-x64', 'cross_profile_drift', 'failed', 'changed decisions')
        path.write_text(json.dumps({**value, 'target_report_sha256': '0' * 64}))
        self.assertGate('windows-x64', 'cross_profile_drift', 'failed', 'drift target')
        path.write_text(json.dumps({**value, 'reference_report_sha256': '0' * 64}))
        self.assertGate('windows-x64', 'cross_profile_drift', 'failed', 'drift reference')
        path.unlink()
        self.assertGate('windows-x64', 'cross_profile_drift', 'not_run')

    def test_installed_and_electron(self):
        cases = {'installed.json': [(lambda r: r.update(platform='linux-x64'), 'on linux-x64'),
                                    (lambda r: r.update(artifact_inventory_sha256='0' * 64), 'not the release packages'),
                                    (lambda r: r['consumers'].update(go='0' * 64), 'disagree')],
                 'electron.json': [(lambda r: r.update(artifact_sha256='0' * 64), 'release npm package'),
                                   (lambda r: r.update(results=r['results'][:1]), 'ASAR'),
                                   (lambda r: r['results'][0].update(electron='1.0.0'), 'unlisted Electron')]}
        for name, changes in cases.items():
            for change, message in changes:
                with self.subTest(name, message=message):
                    self.setUp()
                    self.world.edit(f'installed-linux-arm64-real-model/{name}', change)
                    self.assertGate('linux-arm64', 'installed_packages', 'failed', message)

    def test_native_contracts_registration(self):
        self.world.edit('evidence-linux-arm64/report.json', lambda r: r['ctest_registered'].remove('real-faults'))
        self.assertGate('linux-arm64', 'native_contracts', 'failed', 'not registered')
        self.world.edit('evidence-windows-x64/report.json', lambda r: r['skipped'].pop('real-c-faults'))
        self.assertGate('windows-x64', 'native_contracts', 'failed', 'neither ran nor was reported as skipped')
        self.world.edit('evidence-windows-x64/report.json', lambda r: r['ctest_registered'].remove('real-control'))
        self.assertGate('windows-x64', 'native_contracts', 'failed', 'not registered')

    def test_missing_evidence(self):
        self.world.paths.pop('linux-arm64:stress')
        self.assertGate('linux-arm64', 'short_stress', 'not_run')
        self.world.paths.pop('ci_run')
        self.assertGate('windows-x64', 'abi', 'not_run')


class CheckCandidate(unittest.TestCase):
    """scripts/check-candidate.py end to end on evidence bound to HEAD."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='ducktape-check-candidate-')
        directory = pathlib.Path(cls.temp.name)
        commit = git(ROOT, 'rev-parse', 'HEAD')
        tree = directory / 'tree'
        tree.mkdir()
        with subprocess.Popen(['git', '-C', str(ROOT), 'archive', commit], stdout=subprocess.PIPE) as archive:
            with tarfile.open(fileobj=archive.stdout, mode='r|') as stream:
                stream.extractall(tree, filter='data')
        cls.commit, cls.source = commit, source_identity(tree)['sha256']

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def check(self, world):
        manifest = world.evidence_manifest(world.directory / 'evidence.json')
        report = world.directory / 'gates.json'
        subprocess.run([sys.executable, str(ROOT / 'scripts/check-candidate.py'), str(manifest), '--candidate', self.commit,
                        '--report', str(report)], capture_output=True, text=True)
        return json.loads(report.read_text())

    def world(self, name):
        directory = pathlib.Path(self.temp.name) / name
        directory.mkdir()
        return World(directory, self.commit, self.source)

    def test_targets_and_ci_reproduction(self):
        world = self.world('pass')
        gates = self.check(world)
        self.assertEqual(gates['schema_version'], 2)
        # Evidence paths are relative to the evidence manifest, never absolute local paths.
        self.assertEqual(gates['evidence'], {key: os.path.relpath(path, world.directory) for key, path in world.paths.items()})
        self.assertFalse(any(os.path.isabs(path) for path in gates['evidence'].values()))
        self.assertEqual(sorted(gates['targets']), ['linux-arm64', 'linux-x64', 'windows-x64'])
        for platform in TIER2:
            self.assertEqual({k: v['status'] for k, v in gates['targets'][platform]['gates'].items()},
                             {gate: 'passed' for gate in world.targets[platform]['required_gates']}, gates['targets'][platform])
        self.assertEqual(gates['targets']['linux-x64']['gates'], gates['gates'])
        self.assertEqual(gates['gates']['artifact_integrity'], {'status': 'passed'})
        for gate in ('native_contracts', 'binding_conformance', 'upstream_reference', 'abi'):
            self.assertEqual(gates['gates'][gate]['status'], 'passed', gates['gates'][gate])
        # Synthetic evidence has no sanitizer, stress, browser or upgrade runs.
        self.assertEqual(gates['gates']['stress']['status'], 'not_run')
        self.assertFalse(gates['release_ready'])
        self.assertEqual(gates['inventories_sha256']['native'], sha256(world.release / 'inventory.json'))
        # The local packages' addon or the CI linux-x64 build differing from this host's fails tier 1.
        world.edit('evidence-linux-x64/report.json', lambda r: r.update(library_sha256=digest(b'other')))
        self.assertIn('CI linux-x64 library differs', self.check(world)['gates']['artifact_integrity']['detail'])
        world.edit('evidence-linux-x64/report.json', lambda r: r.update(library_sha256=digest(world.library('linux-x64')),
                                                                         addon_sha256=digest(b'other')))
        self.assertIn('CI linux-x64 Node addon differs', self.check(world)['gates']['artifact_integrity']['detail'])

    def test_installed_release_packages(self):
        world = self.world('installed')
        self.assertEqual(self.check(world)['gates']['installed_packages'], {'status': 'passed'})
        value = json.loads(world.paths['installed_release'].read_text())
        world.paths['installed_release'].write_text(json.dumps({**value, 'artifact_inventory_sha256': '0' * 64}))
        self.assertIn('not the release packages', self.check(world)['gates']['installed_packages']['detail'])

    def candidate_with_record(self, change):
        """A dangling commit: HEAD with release/compatibility.json changed (no branch, index or work tree is touched)."""
        record = json.loads(git(ROOT, 'show', f'{self.commit}:release/compatibility.json'))
        change(record)
        env = {**os.environ, 'GIT_INDEX_FILE': str(pathlib.Path(self.temp.name) / f'index-{len(os.listdir(self.temp.name))}'),
               'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.invalid', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.invalid'}
        run = lambda *args, **kw: subprocess.run(['git', '-C', str(ROOT), *args], env=env, check=True, capture_output=True, text=True, **kw).stdout.strip()
        run('read-tree', self.commit)
        blob = run('hash-object', '-w', '--stdin', input=json.dumps(record, indent=2) + '\n')
        run('update-index', '--cacheinfo', f'100644,{blob},release/compatibility.json')
        return run('commit-tree', run('write-tree'), '-p', self.commit, '-m', 'test candidate')

    def test_candidate_record_is_validated(self):
        world = self.world('record')
        manifest = world.evidence_manifest(world.directory / 'evidence.json')
        cases = {'empty required_gates': lambda r: r['targets'][1].update(required_gates=[]),
                 'unknown tier-2 gate': lambda r: r['targets'][1]['required_gates'].append('no_such_gate'),
                 'missing build workflow': lambda r: r['targets'][1]['build'].update(built_by='.github/workflows/missing.yml')}
        for label, change in cases.items():
            with self.subTest(label):
                commit = self.candidate_with_record(change)
                result = subprocess.run([sys.executable, str(ROOT / 'scripts/check-candidate.py'), str(manifest), '--candidate', commit,
                                         '--report', str(world.directory / 'rejected.json')], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('invalid release record', result.stderr)
                self.assertFalse((world.directory / 'rejected.json').exists())

    def test_local_package_run_equals_ci_fragment(self):
        """Entry by entry: headers, CMake/pkg-config files, npm, Java, C#, Python and Rust code; not container bytes."""
        archive = lambda v: f'ducktape-laya-{v}-linux-x64.tar.gz'
        prefix = lambda v: f'ducktape-laya-{v}-linux-x64'
        cases = {
            'header': (archive, lambda v, e: {**e, f'{prefix(v)}/include/ducktape_laya.h': b'#define EVIL 1\n'}, 'include/ducktape_laya.h'),
            'cmake': (archive, lambda v, e: {k: v_ for k, v_ in e.items() if not k.endswith('ducktape_laya-config.cmake')}, 'ducktape_laya-config.cmake'),
            'pkg-config': (archive, lambda v, e: {**e, f'{prefix(v)}/lib/pkgconfig/ducktape-laya.pc': b'Libs: -lother\n'}, 'ducktape-laya.pc'),
            'npm code': (lambda v: f'ducktape-laya-{v}.tgz', lambda v, e: {**e, 'package/index.cjs': b'module.exports = 2;\n'}, 'package/index.cjs'),
            'java class': (lambda v: f'ducktape-laya-{v}.jar', lambda v, e: {**e, 'io/Laya.class': (b'other', False)}, 'io/Laya.class'),
            'csharp code': (lambda v: f'DucktapeLaya.{v}.nupkg', lambda v, e: {**e, 'lib/net10.0/DucktapeLaya.dll': (b'other', False)}, 'DucktapeLaya.dll'),
            'python code': (lambda v: f'ducktape_laya-{v}-py3-none-manylinux_2_38_x86_64.whl',
                            lambda v, e: {**e, 'ducktape_laya/__init__.py': (b'evil = 1\n', False)}, '__init__.py'),
            'rust crate': (lambda v: f'ducktape-laya-{v}.crate', lambda v, e: {**e, 'crate/src/lib.rs': b'evil'}, 'crate/src/lib.rs'),
            'pom': (lambda v: f'ducktape-laya-{v}.pom', lambda v, e: b'other pom', 'contents differ')}
        for label, (name, change, message) in cases.items():
            with self.subTest(label):
                world = self.world('local-' + label.replace(' ', '-'))
                world.repack_local(name(world.version), lambda e: change(world.version, e))
                gate = self.check(world)['gates']['artifact_integrity']
                self.assertEqual(gate['status'], 'failed', gate)
                self.assertIn('differs from this host', gate['detail'])
                self.assertIn(message, gate['detail'])
        # The documented toolchain stamps (COMPARISON_NOTES) are the only things allowed to differ.
        world = self.world('local-toolchain-stamps')
        world.repack_local(f'ducktape-laya-{world.version}-linux-x64.tar.gz', lambda e: {
            k: v.replace(b'3.29', b'4.2') if k.endswith('-targets.cmake') else v for k, v in e.items()})
        world.repack_local(f'DucktapeLaya.{world.version}.nupkg', lambda e: {
            k: (v[0].replace(b'.NET 8.0', b'.NET 10.0'), v[1]) if k.endswith('nuget.psmdcp') else v for k, v in e.items()})
        world.repack_local(f'ducktape-laya-{world.version}-javadoc.jar', lambda e: {
            **e, 'index.html': (b'jdk 27 page', False), 'io.github.adrian729.laya/io/github/adrian729/laya/Laya.html': (b'jdk 27 page', False),
            'resource-files/jquery-ui.min.css': (b'only in some JDKs', False)})
        gates = self.check(world)
        self.assertEqual(gates['gates']['artifact_integrity'], {'status': 'passed'})
        self.assertEqual(sorted(gates['package_comparison']), ['*-javadoc.jar', 'ducktape_laya-targets.cmake', 'nuget.psmdcp'])
        # ... but a change beyond the stamp, or a missing API page, still fails.
        for label, name, change, message in (
                ('cmake beyond the policy', f'ducktape-laya-{world.version}-linux-x64.tar.gz',
                 lambda e: {k: v + b'target_link_libraries(x evil)\n' if k.endswith('-targets.cmake') else v for k, v in e.items()}, 'targets.cmake'),
                ('javadoc API page', f'ducktape-laya-{world.version}-javadoc.jar',
                 lambda e: {k: v for k, v in e.items() if not k.startswith('io.github')}, 'Laya.html')):
            with self.subTest(label):
                world.repack_local(name, change)
                self.assertIn(message, self.check(world)['gates']['artifact_integrity']['detail'])
        # Entries are compared, not container bytes: the same entries in a differently compressed container pass.
        world = self.world('local-recompressed')
        world.repack_local(f'ducktape-laya-{world.version}.tgz', lambda e: dict(reversed(list(e.items()))))
        self.assertEqual(self.check(world)['gates']['artifact_integrity'], {'status': 'passed'})

    def test_installed_release_is_this_host(self):
        world = self.world('host')
        path = world.paths['installed_release']
        value = json.loads(path.read_text())
        for label, host, message in (
                ('the CI report copied', json.loads(world.paths['linux-x64:installed'].read_text())['host'], 'not run on a local linux-x64 host'),
                ('no host', None, 'no host recorded'),
                ('arm64 host', {**value['host'], 'machine': 'aarch64'}, 'not run on a local linux-x64 host'),
                ('windows host', {**value['host'], 'system': 'Windows'}, 'not run on a local linux-x64 host'),
                ('same hostname', {**value['host'], 'hostname': 'runner-linux-x64'}, 'ran on the CI host')):
            with self.subTest(label):
                path.write_text(json.dumps({**{k: v for k, v in value.items() if k != 'host'}, **({'host': host} if host else {})}))
                self.assertIn(message, self.check(world)['gates']['installed_packages']['detail'])
        path.write_text(json.dumps(value))
        self.assertEqual(self.check(world)['gates']['installed_packages'], {'status': 'passed'})
        # The CI report itself is not the local run.
        path.write_bytes(world.paths['linux-x64:installed'].read_bytes())
        self.assertIn('is the CI linux-x64 report', self.check(world)['gates']['installed_packages']['detail'])

    def test_electron_is_linux_x64_npm_package(self):
        world = self.world('electron')
        self.assertIn('native_matrix evidence missing', self.check(world)['gates']['linux_matrix']['detail'])
        path = world.paths['electron']
        value = json.loads(path.read_text())
        for label, change, message in (('platform', {'platform': 'windows-x64'}, 'did not pass on linux-x64'),
                                       ('no platform', {'platform': None}, 'did not pass on linux-x64'),
                                       ('artifact', {'artifact': f'ducktape-laya-{world.version}-linux-x64.tar.gz'}, 'not the candidate npm package')):
            with self.subTest(label):
                path.write_text(json.dumps({**value, **change}))
                self.assertIn(message, self.check(world)['gates']['linux_matrix']['detail'])


if __name__ == '__main__':
    unittest.main()
