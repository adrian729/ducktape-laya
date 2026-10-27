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
gates.json has schema 2 and names the candidate run, and a CI linux-x64 library
or addon that differs from the locally validated one fails the tier-1
artifact_integrity gate. Reproduced builds must hold the validated addon too;
reports configured with a compiler cache (build_options) are refused, the native
matrix's included; evidence reused from an ancestor fails when one of its gate's
own inputs changed since (fixtures for fuzz, routing references for sanitizers,
package manifests for the security review), and stress, soak and fuzz must meet
the candidate's budgets; an additional model without its tokenization-stop run fails the
native contracts; an untracked module under scripts/ makes the checker dirty.

Evidence profiles (M1, F2): a validation report is evidence only for its profile (host-full for this host's
validate.py --full run, ci-full for a target's merged CI report, ci-compiler for the CI clang entry of the native
matrix); the Profiles class refuses, for each rule, a report that breaks it (a generic run, a missing or failed
check or policy run, dev options, a cache on the host build, an incomplete ctest run, a merge with a shard missing,
a ci-compiler report with packages, a host report claiming ci-full, a host-full report from GitHub Actions, the
retired installed suite). The CI qualify run's reports count only through ci-run.json qualify_run; the soak serves the
stress gate with its rounds floor; the browser pages pass their own stage lists and agree on primitive.json.

A World is built once per class (it runs the real package assembly) and cloned per test."""
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
from release_targets import (PLATFORM_POLICY, POLICY_LABELS, Context, Mismatch, evaluate_targets, require_complete_validation,  # noqa: E402
                             run_gate, target_gates)
from release_world import World, digest  # noqa: E402

TIER2 = ('linux-arm64', 'windows-x64')


class Targets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template_directory = tempfile.TemporaryDirectory(prefix='ducktape-targets-template-')
        cls.template = World(cls.template_directory.name, 'a' * 40, 's' * 64)

    @classmethod
    def tearDownClass(cls):
        cls.template_directory.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ducktape-targets-')
        self.addCleanup(self.temp.cleanup)
        self.world = self.template.clone(self.temp.name)

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
                 # The candidate's own rounds floor, whatever the run was measured against.
                 'rounds': (lambda r: r['result'].update(rounds=self.world.policy['budgets']['short_stress_rounds'] - 1), 'fewer than the budget'),
                 'no rounds': (lambda r: r.pop('result'), 'None rounds'),
                 'platform': (lambda r: r.update(target_platform='linux-x64'), 'ran on linux-x64'),
                 'model': (lambda r: r.update(model_sha256='0' * 64), 'another model'),
                 'failed': (lambda r: r.update(status='failed'), 'below budget')}
        for label, (change, message) in cases.items():
            with self.subTest(label):
                self.setUp()
                self.world.edit('stress-windows-x64/report.json', change)
                self.assertGate('windows-x64', 'short_stress', 'failed', message)

    def test_reproducibility(self):
        def other_addon(report):
            for files in report['builds'].values():
                files['lib/ducktape-laya/node/ducktape_laya.node'] = {'sha256': 'f' * 64, 'mode': 420}
        # Every reproduced build must hold the validated library and Node addon, on the validated build's runner image.
        cases = {'differences': ('windows-x64', lambda r: r.update(differences=['bin/ducktape_laya.dll']), 'differ'),
                 'platform': ('linux-arm64', lambda r: r.update(target_platform='windows-x64'), 'ran on windows-x64'),
                 'addon': ('windows-x64', other_addon, 'does not contain the validated addon'),
                 'second addon': ('linux-arm64', lambda r: r['builds']['second'].pop('lib/ducktape-laya/node/ducktape_laya.node'),
                                  'reproduced second build does not contain the validated addon'),
                 'environment': ('windows-x64', lambda r: r.update(build_environment={'ImageVersion': '20261021.278'}), 'toolchain environment')}
        for label, (platform, change, message) in cases.items():
            with self.subTest(label):
                self.setUp()
                for path in (f'evidence-{platform}/report.json', f'reproducibility-{platform}/report.json'):
                    self.world.edit(path, lambda r: r.update(build_environment={'ImageVersion': '20261007.264'}))
                self.assertGate(platform, 'reproducibility', 'passed')
                self.world.edit(f'reproducibility-{platform}/report.json', change)
                self.assertGate(platform, 'reproducibility', 'failed', message)

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
        unregister = lambda name: lambda r: (r['ctest_registered'].remove(name), r['ctest_ran'].remove(name))
        self.world.edit('evidence-linux-arm64/report.json', unregister('real-faults'))
        self.assertGate('linux-arm64', 'native_contracts', 'failed', 'not registered')
        self.world.edit('evidence-windows-x64/report.json', lambda r: r['skipped'].pop('real-c-faults'))
        self.assertGate('windows-x64', 'native_contracts', 'failed', 'neither ran nor was reported as skipped')
        self.world.edit('evidence-windows-x64/report.json', unregister('real-control'))
        self.assertGate('windows-x64', 'native_contracts', 'failed', 'not registered')

    def test_skipped_required_tests_fail(self):
        # A registered test that reported itself skipped, or a check skipped inside a passing test, is no evidence.
        cases = {'whole test': ('real-runtime-faults', 'RLIMIT_NPROC is not enforced'),
                 'inside a passing test': ('real-tokenizer (partly)', 'SKIPPED: tokenization timing not checked')}
        for platform in ('linux-arm64', 'windows-x64'):
            for label, (key, reason) in cases.items():
                with self.subTest(platform=platform, case=label):
                    self.setUp()
                    self.world.edit(f'evidence-{platform}/report.json', lambda r: r['skipped'].update({key: reason}))
                    message = 'required tests skipped'
                    if platform == 'windows-x64' and key == 'real-runtime-faults':
                        message = ''  # not registered on Windows: not applicable
                    result = self.statuses(platform)['native_contracts']
                    self.assertEqual(result['status'], 'failed' if message else 'passed', result)
                    self.assertIn(message, result.get('detail', ''))

    def test_platform_inapplicable_checks_are_not_skips(self):
        # Checks that do not exist on a platform print NOT APPLICABLE and are not recorded; the world's Windows report has none.
        self.assertGate('windows-x64', 'native_contracts', 'passed')
        # A check the host prevented still fails, on any platform.
        for platform in TIER2:
            self.setUp()
            self.world.edit(f'evidence-{platform}/report.json', lambda r: r['skipped'].update(
                {'real-control (partly)': 'SKIPPED: the automatic thread count probe: setting the CPU affinity failed (Operation not permitted)'}))
            self.assertGate(platform, 'native_contracts', 'failed', 'required tests skipped')

    def test_incomplete_or_cached_validation(self):
        # Selected runs are not release evidence; a CI build may compile through its cache, as recorded and configured.
        cases = {'binding left out': (lambda r: r['skipped'].update(go='not selected (--bindings)'), 'left out go'),
                 'parity left out': (lambda r: r['skipped'].update(bindings='not selected (--checks)'), 'left out bindings'),
                 'native tests left out': (lambda r: r['skipped'].update(native='not selected (--checks)'), 'left out native'),
                 'generic profile': (lambda r: r['selection'].update(profile='generic'), 'profile generic is not ci-full'),
                 # What the build directory was configured with, whatever validate.py was asked for.
                 'configured launcher': (lambda r: r['build_options'].update(CMAKE_CXX_COMPILER_LAUNCHER='sccache'), 'are not the recorded ccache'),
                 'ggml cache': (lambda r: r['build_options'].update(GGML_CCACHE='ON'), 'GGML_CCACHE=ON'),
                 'ccache base_dir': (lambda r: r['ccache_config'].update(base_dir='/home/runner'), 'base_dir'),
                 'no build options': (lambda r: r.pop('build_options'), 'no build_options recorded')}
        for label, (change, message) in cases.items():
            with self.subTest(label):
                self.setUp()
                self.world.edit('evidence-linux-arm64/report.json', change)
                for gate in ('native_contracts', 'abi', 'artifact_integrity'):
                    self.assertGate('linux-arm64', gate, 'failed', message)
        self.setUp()
        self.world.edit('evidence-windows-x64/report.json', lambda r: (r.update(compiler_launcher='none'), r['build_options'].update(
            CMAKE_C_COMPILER_LAUNCHER='', CMAKE_CXX_COMPILER_LAUNCHER='')))
        self.assertGate('windows-x64', 'native_contracts', 'passed')

    def test_installed_packages_need_no_validation_gate(self):
        # validate.py has no installed suite: the tier-2 gate reads the assembled packages' installed run only.
        self.world.edit('evidence-linux-arm64/report.json', lambda r: r['gates']['installed_packages'].update(status='not_run'))
        self.assertGate('linux-arm64', 'installed_packages', 'passed')

    def test_missing_evidence(self):
        self.world.paths.pop('linux-arm64:stress')
        self.assertGate('linux-arm64', 'short_stress', 'not_run')
        self.world.paths.pop('ci_run')
        self.assertGate('windows-x64', 'abi', 'not_run')


class Profiles(unittest.TestCase):
    """require_complete_validation per profile (§3.4): one mutation per rule, on the world's reports (no files)."""

    @classmethod
    def setUpClass(cls):
        cls.template_directory = tempfile.TemporaryDirectory(prefix='ducktape-profiles-')
        cls.world = World(cls.template_directory.name, 'a' * 40, 's' * 64, platforms=('linux-x64',))

    @classmethod
    def tearDownClass(cls):
        cls.template_directory.cleanup()

    CALLS = {'host-full': ({'host-full'}, False), 'ci-full': ({'ci-full'}, True), 'ci-compiler': ({'host-full', 'ci-compiler'}, True)}

    def refusal(self, profile, change, platform='linux-x64', call=None):
        report = self.world.validation(platform, profile)
        change(report)
        profiles, cache_allowed = call or self.CALLS[profile]
        try:
            require_complete_validation(report, 'report', profiles=profiles, cache_allowed=cache_allowed)
        except Mismatch as error:
            return str(error)
        return None

    def test_consistent_reports_pass(self):
        for profile in self.CALLS:
            for platform in ('linux-x64', 'windows-x64'):
                with self.subTest(profile=profile, platform=platform):
                    self.assertIsNone(self.refusal(profile, lambda r: None, platform))

    def test_every_rule(self):
        def drop_runs(*labels):
            return lambda r: [r['runs'].pop(label) for label in labels]
        def fail_run(label):
            return lambda r: r['runs'][label].update(exit_code=1)
        cases = [
            # M1: a generic or selected run, with or without passing checks, is no evidence.
            ('ci-full', lambda r: r['selection'].update(profile='generic'), 'profile generic'),
            ('host-full', lambda r: r['selection'].update(profile='selected'), 'profile selected'),
            ('host-full', lambda r: r.pop('selection'), 'profile missing'),
            ('host-full', lambda r: r.update(selected_checks='failed'), 'selected checks failed'),
            ('ci-full', lambda r: r.update(error='bindings-a failed'), 'bindings-a failed'),
            ('host-full', lambda r: r.update(phase='test'), 'phase test'),
            ('ci-full', lambda r: r.update(phase='all'), 'phase all'),
            # Missing or failed policy runs.
            ('host-full', drop_runs('docs-links'), 'policy checks did not pass: docs-links'),
            ('host-full', fail_run('release-targets'), 'policy checks did not pass: release-targets'),
            ('host-full', lambda r: r['selection'].update(policy='platform'), 'without every policy check'),
            ('ci-full', drop_runs('abi-layouts'), 'abi-layouts'),
            ('ci-full', fail_run('packaging-identities'), 'packaging-identities (exit 1)'),
            # F2.4: a merged ci-full report without the four platform policy runs (listed as covered by the tooling job).
            ('ci-full', lambda r: [(r['runs'].pop(label), r['skipped'].update({f'policy:{label}': 'tooling job of the same run'}))
                                   for label in PLATFORM_POLICY], 'did not run or pass'),
            ('ci-full', lambda r: r['skipped'].pop('policy:docs-links'), 'neither run nor covered by the tooling job: docs-links'),
            ('ci-full', lambda r: r['skipped'].update({'policy:docs-links': 'not selected (--policy platform)'}), 'not covered by the tooling job'),
            # Required checks of every profile.
            ('host-full', drop_runs('upstream-reference'), 'upstream-reference (missing)'),
            ('host-full', fail_run('package-smoke'), 'package-smoke (exit 1)'),
            ('ci-full', drop_runs('bindings-b'), 'bindings-b (missing)'),
            ('ci-compiler', fail_run('go'), 'go (exit 1)'),
            ('ci-full', lambda r: (r['runs'].pop('examples-smoke'), r['skipped'].update({'examples-smoke': 'compiles the examples with a POSIX driver'})),
             'examples-smoke'),
            # Merged reports missing a shard, or with shards that do not account for the checks.
            ('ci-full', lambda r: r['checks'].remove('bindings'), 'merged checks'),
            ('ci-full', lambda r: r['shards'].pop('native-linux-x64-bindings'), 'do not account for the checks'),
            ('ci-full', lambda r: r['checks'].append('ctest'), 'merged checks'),
            # F2.4: a ci-compiler report with package runs; a host report claiming ci-full; a host-full report from GitHub Actions.
            ('ci-compiler', lambda r: r['runs'].update({'package': {'exit_code': 0}}), 'a ci-compiler report with package runs'),
            ('ci-full', lambda r: r['host'].update(github_actions=False), 'not produced on GitHub Actions'),
            ('host-full', lambda r: r['host'].update(github_actions=True), 'produced on GitHub Actions'),
            ('host-full', lambda r: r.pop('host'), 'no host recorded'),
            # The retired installed suite, as a skip or a run.
            ('host-full', lambda r: r['skipped'].update(installed='loads the packages in every language'), 'retired suite installed'),
            ('ci-full', lambda r: r['runs'].update(installed={'exit_code': 0}), 'retired suite installed'),
            # Left-out suites per profile.
            ('host-full', lambda r: r['skipped'].update(policy='not selected'), 'left out policy'),
            ('ci-full', lambda r: r['skipped'].update(policy='not selected'), 'left out policy'),
            ('ci-compiler', lambda r: r['skipped'].update(rust='not selected (--bindings)'), 'left out rust'),
            ('host-full', lambda r: r['skipped'].update({'policy:verify': 'tooling job of the same run'}), 'policy checks left out'),
            # Dev options, and their absence.
            ('host-full', lambda r: r['build_options'].update(DUCKTAPE_DEV_BUILD='ON'), 'dev options'),
            ('ci-full', lambda r: r['build_options'].update(DUCKTAPE_DEV_PCH='ON'), 'dev options'),
            ('ci-compiler', lambda r: r['build_options'].pop('DUCKTAPE_DEV_PCH'), 'dev options'),
            # ctest: every registered test ran (no -L subset), every required test is registered.
            ('host-full', lambda r: r['ctest_ran'].remove('real-model'), 'ctest ran'),
            ('ci-full', lambda r: r['ctest_ran'].clear(), 'ctest ran'),
            ('ci-compiler', lambda r: (r['ctest_registered'].remove('real-features'), r['ctest_ran'].remove('real-features')),
             'not registered'),
            # Compiler caches: never on the host build, consistent and base_dir-free in CI, products never on the probe launcher.
            ('host-full', lambda r: (r.update(compiler_launcher='ccache'), r['build_options'].update(
                CMAKE_C_COMPILER_LAUNCHER='ccache', CMAKE_CXX_COMPILER_LAUNCHER='ccache')), 'compiler launcher ccache'),
            ('host-full', lambda r: r['build_options'].update(CMAKE_CXX_COMPILER_LAUNCHER='ccache'), 'configured with a compiler cache'),
            ('ci-full', lambda r: r['build_options'].update(GGML_CCACHE='ON'), 'GGML_CCACHE=ON'),
            ('ci-full', lambda r: r.update(compiler_launcher='none'), 'are not the recorded none'),
            ('ci-full', lambda r: r.update(ccache_config=None), 'base_dir'),
            ('ci-full', lambda r: r.pop('launcher_guard'), 'no launcher guard'),
            ('ci-full', lambda r: r['launcher_guard'].update(rule_launch='ccache'), 'no launcher guard'),
            ('ci-full', lambda r: r['launcher_guard']['product_targets'].remove('ducktape_laya_node'), 'product targets'),
            ('ci-full', lambda r: r['launcher_guard']['probe_targets'].update(llama='ccache'), 'probe launcher: llama'),
        ]
        for profile, change, message in cases:
            with self.subTest(profile=profile, message=message):
                refusal = self.refusal(profile, change)
                self.assertIsNotNone(refusal)
                self.assertIn(message, refusal)

    def test_profiles_are_per_call_site(self):
        # check-candidate.native() takes only host-full, uncached; a CI target only ci-full; the native matrix both kinds.
        self.assertIn('profile ci-full is not host-full', self.refusal('ci-full', lambda r: None, call=({'host-full'}, False)))
        self.assertIn('profile host-full is not ci-full', self.refusal('host-full', lambda r: None, call=({'ci-full'}, True)))
        self.assertIn('profile ci-compiler is not ci-full', self.refusal('ci-compiler', lambda r: None, call=({'ci-full'}, True)))
        # A CI report with a cache where the call site allows none.
        self.assertIn('compiler launcher ccache', self.refusal('ci-full', lambda r: None, call=({'ci-full'}, False)))
        # The Windows-only examples skip with its reason passes, on Windows only.
        windows = lambda r: None
        self.assertIsNone(self.refusal('ci-full', windows, 'windows-x64'))
        self.assertIn('examples-smoke', self.refusal('ci-full', lambda r: r['skipped'].update({'examples-smoke': 'other reason'}), 'windows-x64'))

    def test_hard_coded_lists_agree_with_validate(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('validate', ROOT / 'scripts/validate.py')
        validate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(validate)
        from release_targets import HOST_FULL_RUNS
        self.assertEqual(set(validate.FULL_CHECKS), set(HOST_FULL_RUNS))
        self.assertEqual(set(validate.FULL_POLICY_CHECKS), set(POLICY_LABELS))


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
        cls.template = World(directory / 'template', cls.commit, cls.source)

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
        return self.template.clone(pathlib.Path(self.temp.name) / name)

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
        # The candidate run the release packages came from (attest-release checks the published inventory against it).
        self.assertEqual({k: gates['ci_run'][k] for k in ('repository', 'run_id', 'head_sha')}, {'repository': 'adrian729/ducktape-laya', 'run_id': 7,
                                                                                                  'head_sha': self.commit})
        # The local packages' addon or the CI linux-x64 build differing from this host's fails tier 1.
        world.edit('evidence-linux-x64/report.json', lambda r: r.update(library_sha256=digest(b'other')))
        self.assertIn('CI linux-x64 library differs', self.check(world)['gates']['artifact_integrity']['detail'])
        world.edit('evidence-linux-x64/report.json', lambda r: r.update(library_sha256=digest(world.library('linux-x64')),
                                                                         addon_sha256=digest(b'other')))
        self.assertIn('CI linux-x64 Node addon differs', self.check(world)['gates']['artifact_integrity']['detail'])

    def test_incomplete_or_cached_validation(self):
        # The local native report and the CI linux-x64 one must both be complete runs without a compiler cache.
        world = self.world('incomplete')
        report = json.loads(world.paths['native'].read_text())
        world.paths['native'].write_text(json.dumps({**report, 'skipped': {**report.get('skipped', {}), 'native': 'not run (--skip-native-tests)'}}))
        gates = self.check(world)['gates']
        for gate in ('native_contracts', 'binding_conformance', 'abi', 'artifact_integrity'):
            self.assertEqual(gates[gate]['status'], 'failed', gates[gate])
            self.assertIn('native: left out native', gates[gate]['detail'])
        # This host's build is never cached; CI's linux-x64 report is a ci-full merge, never a selected run.
        world.paths['native'].write_text(json.dumps({**report, 'compiler_launcher': 'ccache'}))
        self.assertIn('native: compiled through the compiler launcher ccache', self.check(world)['gates']['native_contracts']['detail'])
        world.paths['native'].write_text(json.dumps(report))
        world.edit('evidence-linux-x64/report.json', lambda r: r['selection'].update(profile='selected'))
        self.assertIn('linux-x64 validation: profile selected is not ci-full', self.check(world)['gates']['artifact_integrity']['detail'])

    def test_installed_release_packages(self):
        # Assembled packages only: the host's --full report has no installed_packages gate of its own (not_run).
        world = self.world('installed')
        self.assertEqual(json.loads(world.paths['native'].read_text())['gates']['installed_packages'], {'status': 'not_run'})
        self.assertEqual(self.check(world)['gates']['installed_packages'], {'status': 'passed'})
        value = json.loads(world.paths['installed_release'].read_text())
        world.paths['installed_release'].write_text(json.dumps({**value, 'artifact_inventory_sha256': '0' * 64}))
        self.assertIn('not the release packages', self.check(world)['gates']['installed_packages']['detail'])

    def test_additional_model_native_gates(self):
        # Every additional model's tokenizer, decision-head and tokenization-stop runs belong to the native contracts.
        world = self.world('additional')
        model_id = json.loads((ROOT / world.record['additional_models'][0]).read_text())['id']
        self.assertEqual(self.check(world)['gates']['native_contracts'], {'status': 'passed'})
        report = json.loads(world.paths['native'].read_text())
        del report['additional_model_gates'][model_id]['control_tokenize']
        world.paths['native'].write_text(json.dumps(report))
        self.assertIn(f'native: control_tokenize for {model_id} not_run', self.check(world)['gates']['native_contracts']['detail'])

    def candidate_with_record(self, change):
        """A dangling commit: HEAD with release/compatibility.json changed (no branch, index or work tree is touched)."""
        record = json.loads(git(ROOT, 'show', f'{self.commit}:release/compatibility.json'))
        change(record)
        return self.candidate_with_files({'release/compatibility.json': json.dumps(record, indent=2) + '\n'})

    def candidate_with_files(self, files):
        """A dangling commit: HEAD with FILES ({path: text}) written (no branch, index or work tree is touched)."""
        env = {**os.environ, 'GIT_INDEX_FILE': str(pathlib.Path(self.temp.name) / f'index-{len(os.listdir(self.temp.name))}'),
               'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.invalid', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.invalid'}
        run = lambda *args, **kw: subprocess.run(['git', '-C', str(ROOT), *args], env=env, check=True, capture_output=True, text=True, **kw).stdout.strip()
        run('read-tree', self.commit)
        for path, text in files.items():
            blob = run('hash-object', '-w', '--stdin', input=text)
            run('update-index', '--add', '--cacheinfo', f'100644,{blob},{path}')
        return run('commit-tree', run('write-tree'), '-p', self.commit, '-m', 'test candidate')

    def check_commit(self, world, commit):
        manifest = world.evidence_manifest(world.directory / 'evidence.json')
        report = world.directory / 'gates-commit.json'
        subprocess.run([sys.executable, str(ROOT / 'scripts/check-candidate.py'), str(manifest), '--candidate', commit,
                        '--report', str(report)], capture_output=True, text=True)
        return json.loads(report.read_text())

    def evidence(self, world, name, value):
        path = world.directory / f'{name}.json'
        path.write_text(json.dumps(value))
        world.paths[name] = path

    def test_reuse_inputs(self):
        """Evidence from an ancestor is reused only when none of the gate's own inputs changed since."""
        world = self.world('reuse')
        record = world.record
        reports = {
            'sanitizers': {'status': 'passed', 'sanitizers': {'address': {'status': 'passed'}, 'thread': {'status': 'passed'}}, 'model_sha256': world.model},
            'fuzz': {'status': 'passed', 'qualification_budget_met': True, 'requested_seconds': world.policy['budgets']['fuzz_seconds_per_target']},
            'security_review': {'runtime_commit': record['runtime']['commit'], 'runtime_patches': [p['sha256'] for p in record['runtime']['patches']],
                                'release_blocking_code_issues': [], 'model_licence': 'Apache-2.0, reviewed'}}
        package = json.loads((ROOT / 'bindings/node/package.json').read_text())
        presets = (ROOT / 'CMakePresets.json').read_text()
        cases = {'fuzz seeds': ('tests/fixtures/zz-reuse-test.json', '{}\n', 'fuzz', 'sanitizers', 'fuzz: inputs changed'),
                 'fuzz preset': ('CMakePresets.json', presets + '\n', 'fuzz', 'sanitizers', 'fuzz: inputs changed'),
                 'routing references': ('tests/original-reference/routing/zz-reuse-test.json', '{}\n', 'sanitizers', 'sanitizers', 'sanitizers: inputs changed'),
                 'npm manifest': ('bindings/node/package.json', json.dumps({**package, 'scripts': {'install': 'node evil.js'}}, indent=2) + '\n',
                                  'security_review', 'security_review', 'security_review: inputs changed')}
        for label, (path, text, reused, gate, message) in cases.items():
            with self.subTest(label):
                candidate = self.candidate_with_files({path: text})
                for name, value in reports.items():
                    # The reused report comes from HEAD (an ancestor), the others from the candidate itself.
                    self.evidence(world, name, {**value, 'source_commit': self.commit if name == reused else candidate, 'source_tracked_dirty': False})
                result = self.check_commit(world, candidate)['gates'][gate]
                self.assertEqual(result['status'], 'failed', result)
                self.assertIn(message, result['detail'])
                self.assertIn(path, result['detail'])
                # The same evidence from the candidate itself passes that step.
                self.evidence(world, reused, {**reports[reused], 'source_commit': candidate, 'source_tracked_dirty': False})
                self.assertNotIn('inputs changed', self.check_commit(world, candidate)['gates'][gate].get('detail', ''))

    def test_budgets_are_the_candidates(self):
        world = self.world('budgets')
        library = json.loads(world.paths['native'].read_text())['library_sha256']
        budgets = world.policy['budgets']
        soak = {'source_commit': self.commit, 'source_tracked_dirty': False, 'status': 'passed', 'kind': 'candidate_soak',
                'qualification_budget_met': True, 'requested_seconds': budgets['candidate_soak_seconds'], 'library_sha256': library,
                'model_sha256': world.model, 'result': {'rounds': budgets['candidate_soak_rounds']}}
        self.evidence(world, 'stress', {**soak, 'kind': 'routine_stress', 'requested_seconds': budgets['routine_stress_seconds'], 'result': {'rounds': 900}})
        self.evidence(world, 'soak', soak)
        self.assertEqual(self.check(world)['gates']['stress'], {'status': 'passed'})
        # One soak run serves both keys.
        self.evidence(world, 'stress', soak)
        self.assertEqual(self.check(world)['gates']['stress'], {'status': 'passed'})
        # A soak below the candidate's rounds floor fails either key.
        for name in ('stress', 'soak'):
            with self.subTest(name):
                self.evidence(world, 'stress', soak)
                self.evidence(world, name, {**soak, 'result': {'rounds': budgets['candidate_soak_rounds'] - 1}})
                self.assertIn(f'{name}: {budgets["candidate_soak_rounds"] - 1} rounds, fewer than', self.check(world)['gates']['stress']['detail'])
        self.evidence(world, 'stress', soak)
        # A report that says it met its budget, measured against a shorter one than the candidate's.
        self.evidence(world, 'soak', {**soak, 'requested_seconds': budgets['candidate_soak_seconds'] - 1})
        self.assertIn('soak: shorter than', self.check(world)['gates']['stress']['detail'])
        self.evidence(world, 'sanitizers', {'source_commit': self.commit, 'source_tracked_dirty': False, 'status': 'passed',
                                            'sanitizers': {'address': {'status': 'passed'}, 'thread': {'status': 'passed'}},
                                            'model_sha256': world.model})
        self.evidence(world, 'fuzz', {'source_commit': self.commit, 'source_tracked_dirty': False, 'status': 'passed', 'qualification_budget_met': True,
                                      'requested_seconds': budgets['fuzz_seconds_per_target'] // 4})
        self.assertIn("fuzz: shorter than the candidate's fuzz budget", self.check(world)['gates']['sanitizers']['detail'])

    def test_native_matrix_reports_are_complete(self):
        # This host's gcc --full report and the CI linux-x64-clang merge (ci-compiler, read through ci-run.json).
        world = self.world('matrix')
        report = json.loads(world.paths['native'].read_text())
        clang = world.validation('linux-x64', 'ci-compiler')
        clang['diagnostics']['compiler'], clang['node_version'] = 'Clang 22.1.8', 'v24.21.0'
        clang_path = world.ci / 'native-x64-clang-node-24.21.0' / 'report.json'
        clang_path.parent.mkdir()
        clang_path.write_text(json.dumps(clang))
        world.rehash()
        (world.directory / 'host.json').write_text(json.dumps(report))
        (world.directory / 'matrix.json').write_text(json.dumps(['host.json', 'ci/native-x64-clang-node-24.21.0/report.json']))
        world.paths['native_matrix'] = world.directory / 'matrix.json'
        # Complete: the gate goes on to the cross-CPU evidence, which the world does not have.
        self.assertIn('cross_cpu evidence missing', self.check(world)['gates']['linux_matrix']['detail'])
        cases = {'cached host': ('host.json', lambda r: (r.update(compiler_launcher='ccache'), r['build_options'].update(
                     CMAKE_C_COMPILER_LAUNCHER='ccache', CMAKE_CXX_COMPILER_LAUNCHER='ccache')), 'compiled through the compiler launcher ccache'),
                 'ggml cache': ('host.json', lambda r: r['build_options'].update(GGML_CCACHE='ON'), 'GGML_CCACHE=ON'),
                 'generic run': ('host.json', lambda r: r['selection'].update(profile='generic'), 'profile generic'),
                 'clang with packages': ('ci', lambda r: r['runs'].update({'package': {'exit_code': 0}, 'package-smoke': {'exit_code': 0}}),
                                         'a ci-compiler report with package, package-smoke runs'),
                 'clang ci-full': ('ci', lambda r: r['selection'].update(profile='ci-full'), 'profile ci-full'),
                 'failed parity': ('ci', lambda r: r['gates']['binding_conformance'].update(status='failed'), 'required gates did not pass')}
        for label, (name, change, message) in cases.items():
            with self.subTest(label):
                value = json.loads(json.dumps(report if name == 'host.json' else clang))
                change(value)
                if name == 'ci':
                    world.edit('native-x64-clang-node-24.21.0/report.json', lambda r: (r.clear(), r.update(value)))
                else:
                    (world.directory / name).write_text(json.dumps(value))
                self.assertIn(message, self.check(world)['gates']['linux_matrix']['detail'])
                (world.directory / 'host.json').write_text(json.dumps(report))
                world.edit('native-x64-clang-node-24.21.0/report.json', lambda r: (r.clear(), r.update(clang)))
        # A CI report edited after download.
        world.edit('native-x64-clang-node-24.21.0/report.json', lambda r: r.update(note='edited'), rehash=False)
        self.assertIn('changed after download', self.check(world)['gates']['linux_matrix']['detail'])

    def test_qualify_run_evidence(self):
        """Sanitizer, fuzz and upgrade reports from the CI qualify run count only through ci-run.json qualify_run."""
        world = self.world('qualify')
        world.qualify_reports()
        gates = self.check(world)
        self.assertEqual(gates['gates']['sanitizers'], {'status': 'passed'})
        self.assertEqual(gates['qualify_run']['run_id'], 8)
        # The upgrade report is read too (the gate then needs the host's model-upgrade evidence).
        self.assertIn('model_upgrade evidence missing', gates['gates']['upgrade_rollback']['detail'])
        cases = {'edited': (lambda: world.edit('qualify/fuzz/report.json', lambda r: r.update(note='x'), rehash=False), 'sanitizers',
                            'changed after download'),
                 'unlisted': (lambda: (world.__setattr__('qualify', None), world.rehash()), 'sanitizers', 'no qualify run downloaded'),
                 'another commit': (lambda: world.qualify_reports(head='b' * 40), 'sanitizers', 'not the candidate or its ancestor'),
                 'not the run head': (lambda: world.edit('qualify/sanitizers/report.json', lambda r: r.update(source_commit='c' * 40)),
                                      'sanitizers', "not the qualify run's commit"),
                 'upgrade edited': (lambda: world.edit('qualify/upgrade/report.json', lambda r: r.update(note='x'), rehash=False),
                                    'upgrade_rollback', 'changed after download'),
                 # check-upgrade.py --configure-only (the preflight's rehearsal) builds and tests nothing.
                 'one sanitizer': (lambda: world.edit('qualify/sanitizers/report.json', lambda r: r.update(sanitizers={'address': {'status': 'passed'}})),
                                   'sanitizers', 'address and thread required'),
                 'configure-only upgrade': (lambda: world.edit('qualify/upgrade/report.json', lambda r: r.update(
                     configure_only=True, gates={}, comparison={})), 'upgrade_rollback', 'did not build and test the runtime')}
        for label, (change, gate, message) in cases.items():
            with self.subTest(label):
                world.qualify_reports()
                change()
                result = self.check(world)['gates'][gate]
                self.assertEqual(result['status'], 'failed', result)
                self.assertIn(message, result['detail'])
        # A file of the qualify run listed in the candidate run's own list does not count.
        world.qualify_reports()
        run = json.loads((world.ci / 'ci-run.json').read_text())
        run['files'].update(run['qualify_run'].pop('files'))
        run['qualify_run']['files'] = {'qualify/upgrade/report.json': run['files']['qualify/upgrade/report.json']}
        (world.ci / 'ci-run.json').write_text(json.dumps(run))
        self.assertIn('is not a file of the qualify run', self.check(world)['gates']['sanitizers']['detail'])
        # A local run (this host's, today's way) is still accepted.
        local = world.directory / 'sanitizers.json'
        local.write_bytes(world.paths['sanitizers'].read_bytes())
        fuzz = world.directory / 'fuzz.json'
        fuzz.write_bytes(world.paths['fuzz'].read_bytes())
        world.paths['sanitizers'], world.paths['fuzz'] = local, fuzz
        self.assertEqual(self.check(world)['gates']['sanitizers'], {'status': 'passed'})

    def browser_reports(self, world):
        inventory = json.loads(world.paths['browser_package'].read_text())
        stages = {'browser': {'wasm': ['execution', 'reference', 'controls', 'assets', 'upstream_numeric', 'memory', 'cross_profile_drift'],
                              'webgpu': ['execution', 'reference', 'controls', 'assets', 'upstream_numeric', 'device_loss', 'gpu_memory',
                                         'cross_profile_drift']},
                  'browser_isolated': {'wasm': ['controls', 'isolated_execution', 'running_cancellation'], 'webgpu': ['controls', 'isolated_execution']}}
        browser = world.policy['platforms']['browser']
        native_outputs = sha256(world.paths['native'].parent / 'outputs.json')
        for page, backends in stages.items():
            report = {'source_commit': self.commit, 'source_tracked_dirty': False, 'model_sha256': world.model, 'package_dir': 'package',
                      'isolated': page == 'browser_isolated', 'headless': False, 'launch_flags': [],
                      'browser_version': browser['browser_versions'][0].split('-', 1)[1], 'native_report_sha256': native_outputs,
                      'environment': {'gpu': {'vendor': 'intel', 'architecture': 'gen-12lp', 'fallback': False}},
                      'asset_hashes': inventory['assets'], 'backends': {}}
            for backend, names in backends.items():
                report['backends'][backend] = {name: {'status': 'passed'} for name in names}
                if page == 'browser':
                    report['backends'][backend]['execution']['fixture_sha256'] = {'primitive.json': f'{backend}-primitive'}
                else:
                    report['backends'][backend]['isolated_execution'].update(fixture='primitive.json', result_sha256=f'{backend}-primitive')
                    if backend == 'wasm':
                        report['backends'][backend]['running_cancellation'].update(cancel_status=5, next_result_sha256=f'{backend}-primitive')
            self.evidence(world, page, report)

    def test_browser_pages(self):
        world = self.world('browser')
        self.browser_reports(world)
        gates = self.check(world)['gates']
        self.assertEqual((gates['browser_wasm'], gates['browser_webgpu']), ({'status': 'passed'}, {'status': 'passed'}))
        def edit(page, change):
            value = json.loads(world.paths[page].read_text())
            change(value)
            world.paths[page].write_text(json.dumps(value))
        cases = {'isolated hash differs': ('browser_isolated', lambda r: r['backends']['webgpu']['isolated_execution'].update(result_sha256='other'),
                                           'browser_webgpu', 'primitive.json result differs'),
                 'isolated fixture': ('browser_isolated', lambda r: r['backends']['wasm']['isolated_execution'].update(fixture='batch.json'),
                                      'browser_wasm', 'primitive.json result differs'),
                 'no running cancellation': ('browser_isolated', lambda r: r['backends']['wasm'].pop('running_cancellation'),
                                             'browser_wasm', 'wasm/running_cancellation missing'),
                 'cancel status': ('browser_isolated', lambda r: r['backends']['wasm']['running_cancellation'].update(cancel_status=0),
                                   'browser_wasm', 'running cancellation returned 0'),
                 'next result changed': ('browser_isolated', lambda r: r['backends']['wasm']['running_cancellation'].update(next_result_sha256='x'),
                                         'browser_wasm', 'changed the next result'),
                 'non-isolated stage': ('browser', lambda r: r['backends']['webgpu'].pop('device_loss'), 'browser_webgpu', 'webgpu/device_loss missing'),
                 'no execution hash': ('browser', lambda r: r['backends']['wasm']['execution'].pop('fixture_sha256'), 'browser_wasm',
                                       'no primitive.json result hash')}
        for label, (page, change, gate, message) in cases.items():
            with self.subTest(label):
                self.browser_reports(world)
                edit(page, change)
                result = self.check(world)['gates'][gate]
                self.assertEqual(result['status'], 'failed', result)
                self.assertIn(message, result['detail'])

    def test_reproduced_addon(self):
        world = self.world('reproduced')
        report = json.loads(world.paths['native'].read_text())
        builds = {label: {'lib/libducktape_laya.so.0': {'sha256': report['library_sha256']}} for label in ('first', 'second')}
        self.evidence(world, 'reproducibility', {'source_commit': self.commit, 'source_tracked_dirty': False, 'status': 'passed', 'differences': [],
                                                 'builds': builds})
        self.assertIn('reproducibility: first build does not contain the candidate addon', self.check(world)['gates']['reproducibility']['detail'])
        for files in builds.values():
            files['lib/ducktape-laya/node/ducktape_laya.node'] = {'sha256': report['addon_sha256']}
        self.evidence(world, 'reproducibility', {'source_commit': self.commit, 'source_tracked_dirty': False, 'status': 'passed', 'differences': [],
                                                 'builds': builds})
        self.assertEqual(self.check(world)['gates']['reproducibility'], {'status': 'passed'})

    def test_untracked_checker_module_is_dirty(self):
        world = self.world('dirty')
        probe = ROOT / 'scripts' / 'zz_untracked_probe.py'
        self.assertFalse(probe.exists())
        try:
            probe.write_text('# an untracked module next to the checker\n')
            self.assertTrue(self.check(world)['checker_tree_dirty'])
        finally:
            probe.unlink()
        clean = not git(ROOT, 'diff', 'HEAD', '--name-only') and not [n for n in git(ROOT, 'ls-files', '--others', '--', 'scripts').splitlines()
                                                                       if '__pycache__/' not in n]
        if clean:
            self.assertFalse(self.check(world)['checker_tree_dirty'])

    def test_checker_must_be_the_candidate_commit(self):
        world = self.world('checker')
        report = self.check_commit(world, self.candidate_with_files({}))
        self.assertFalse(report['checker_is_candidate'])
        self.assertFalse(report['release_ready'])
        self.assertNotEqual(report['checker_commit'], report['candidate_commit'])

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
        # The CI linux-x64 runner's Electron report (fetch-ci-evidence.py's electron key).
        world = self.world('electron')
        self.assertEqual(world.paths['electron'], world.ci / 'installed-linux-x64-real-model/electron.json')
        self.assertIn('native_matrix evidence missing', self.check(world)['gates']['linux_matrix']['detail'])
        value = json.loads(world.paths['electron'].read_text())
        for label, change, message in (('platform', {'platform': 'windows-x64'}, 'did not pass on linux-x64'),
                                       ('no platform', {'platform': None}, 'did not pass on linux-x64'),
                                       ('artifact', {'artifact': f'ducktape-laya-{world.version}-linux-x64.tar.gz'}, 'not the candidate npm package')):
            with self.subTest(label):
                world.edit('installed-linux-x64-real-model/electron.json', lambda r: r.update({**value, **change}))
                self.assertIn(message, self.check(world)['gates']['linux_matrix']['detail'])
        world.edit('installed-linux-x64-real-model/electron.json', lambda r: r.update(note='edited'), rehash=False)
        self.assertIn('changed after download', self.check(world)['gates']['linux_matrix']['detail'])


if __name__ == '__main__':
    unittest.main()
