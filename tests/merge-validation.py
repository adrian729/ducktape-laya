#!/usr/bin/env python3
"""Offline checks of scripts/merge-validation.py on shards made by scripts/validate.py with tests/validate.py's fake system.

A fake CI build (--phase build) is tested in one shard per check of the profile (--phase test --from BUILD), with the
cross-host side output in the linux-x64 ctest shard, and merged. The merge of parity halves a and b equals a single
--full run of the same build (outputs.json byte for byte); its gates equal that run's gates, abi included, apart from
binding_conformance.run naming the two halves, and so do the additional models' gates (CN1); the merged report is
ci-full (or ci-compiler) evidence for scripts/release_targets.require_complete_validation. Its files equal the files of
the recorded artifacts of candidate run 40165155452 (tests/merge-validation-files.json) minus the dropped plus the added
ones, per platform. Refused, leaving no output: a missing or failed shard; a shard with another library, commit or
source; overlapping checks; unequal C results of the halves; a ci-compiler merge with packages; a ci-full merge without
the platform policy runs; a run label its shard's checks do not own; two shards with cross-host/, a linux-x64 merge
without it, another platform's merge with it, a bundle without the build's library."""
import contextlib
import fnmatch
import importlib.util
import io
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


merge = load('merge_validation', ROOT / 'scripts/merge-validation.py')
fake = load('validate_tests', ROOT / 'tests/validate.py')
from release_support import LIBRARY, read_json  # noqa: E402
from release_targets import PROFILE_CHECKS, Mismatch, require_complete_validation  # noqa: E402

FILES = read_json(ROOT / 'tests/merge-validation-files.json')


def slug(check):
    return check.replace(':', '-')


class Merge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='ducktape-merge-')
        cls.directory = pathlib.Path(cls.temp.name)
        cls.model, cls.extra = fake.models(cls.directory)
        cls.extra_args = [item for path in cls.extra for item in ('--extra-model', path)]
        cls.sets = {}
        for name, platform, profile, compiler, extra in (
                ('x64', 'linux-x64', 'ci-full', 'GNU 13.3.0', False), ('arm64', 'linux-arm64', 'ci-full', 'GNU 13.3.0', False),
                ('clang', 'linux-x64', 'ci-compiler', 'Clang 22.1.8', False),
                # The binding suites in two shards (bindings:a with the platform policy, bindings:b), as the Windows candidate does.
                ('arm64-halves', 'linux-arm64', 'ci-full', 'GNU 13.3.0', False),
                # The references shard qualifies the additional models too, as the host's --full does (for the CN1 comparison).
                ('x64-extra', 'linux-x64', 'ci-full', 'GNU 13.3.0', True)):
            cls.sets[name] = cls.ci_build(name, platform, profile, compiler, extra, halves=name.endswith('-halves'))
        # The single --full run of the same (deterministic) build, on the release host.
        system = fake.FakeSystem(cls.directory / 'full-system')
        code, cls.full = fake.run(system, '--full', '--model', cls.model, *cls.extra_args, '--build', cls.directory / 'full-build',
                                  '--output', cls.directory / 'full', '--cmake-arg=-GNinja')
        assert code == 0, cls.full.get('error')

    @classmethod
    def ci_build(cls, name, platform, profile, compiler, with_extra, halves=False):
        root = cls.directory / name
        system = fake.FakeSystem(root / 'system', ci=True, platform=platform, compiler=compiler)
        build = root / 'build'
        code, report = fake.run(system, '--phase', 'build', '--model', cls.model, '--upstream-reference', '--compiler-launcher', 'ccache',
                                '--build', build, '--output', root / 'build-report', '--cmake-arg=-GNinja')
        assert code == 0, report.get('error')
        shards = []
        # One shard per check, except linux-arm64's parity halves, which share a shard as in
        # scripts/ci_paths.py SHARDS (each half writes its own outputs-<half>.json).
        groups = [[check] for check in PROFILE_CHECKS[profile] if not (platform == 'linux-arm64' and check == 'parity:full-b')]
        if platform == 'linux-arm64':
            next(group for group in groups if group == ['parity:full-a']).append('parity:full-b')
        if halves:
            groups = [group for group in groups if group not in (['bindings'], ['policy-platform'])]
            groups += [['bindings:a', 'policy-platform'], ['bindings:b']]
        for checks in groups:
            shard = root / f'shard-{platform}-{"+".join(map(slug, checks))}'
            extra = cls.extra_args if 'references' in checks and with_extra else []
            code, report = fake.run(system, '--phase', 'test', '--from', build, '--model', cls.model, *extra, '--checks', ','.join(checks),
                                    '--test-jobs', '4', '--output', shard)
            assert code == 0, (checks, report.get('error'))
            if 'ctest' in checks and platform == 'linux-x64' and profile == 'ci-full':
                cls.cross_host(shard, build)
            shards.append(shard)
        return {'build': build, 'shards': shards, 'profile': profile, 'platform': platform}

    @staticmethod
    def cross_host(shard, build):
        """tests/cross-host.py bundle and record, as the native-linux-x64 shard writes them."""
        bundle = shard / 'cross-host' / 'bundle'
        bundle.mkdir(parents=True)
        for name in ('libducktape_laya.so', 'libducktape_laya.so.1'):
            shutil.copyfile(build / LIBRARY, bundle / name)
        (bundle / 'laya-conformance-c').write_bytes(b'consumer')
        (shard / 'cross-host' / 'record.json').write_text('{}')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.work = pathlib.Path(tempfile.mkdtemp(prefix='ducktape-merge-case-'))
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)

    def copy(self, name):
        """A private copy of one build's shards (tests mutate them)."""
        source = self.sets[name]
        root = pathlib.Path(tempfile.mkdtemp(prefix=name + '-', dir=self.work))
        shards = []
        for shard in source['shards']:
            shutil.copytree(shard, root / shard.name)
            shards.append(root / shard.name)
        return {**source, 'shards': shards}

    def merge(self, item, profile=None, shards=None):
        output = self.work / f'merged-{len(list(self.work.iterdir()))}'
        argv = ['--profile', profile or item['profile'], '--output', str(output), '--build', str(item['build']),
                *map(str, shards if shards is not None else item['shards'])]
        stderr = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            code = merge.main(argv)
        return code, output, stderr.getvalue()

    def merged(self, name):
        code, output, error = self.merge(self.sets[name])
        self.assertEqual(code, 0, error)
        return output, read_json(output / 'report.json')

    def refused(self, message, item, profile=None, shards=None):
        code, output, error = self.merge(item, profile, shards)
        self.assertEqual(code, 1, error)
        self.assertIn(message, error)
        self.assertFalse(output.exists())
        self.assertEqual([path.name for path in output.parent.iterdir() if '.partial-' in path.name], [])

    def shard(self, item, check):
        prefix = f'shard-{item["platform"]}-'
        return next(shard for shard in item['shards'] if slug(check) in shard.name[len(prefix):].split('+'))

    def edit(self, path, change):
        value = json.loads(path.read_text())
        change(value)
        path.write_text(json.dumps(value, indent=2) + '\n')

    def test_halves_equal_a_full_run(self):
        output, report = self.merged('x64-extra')
        full = self.directory / 'full'
        self.assertEqual((output / 'outputs.json').read_text(), (full / 'outputs.json').read_text())
        gates = {gate: dict(value) for gate, value in report['gates'].items()}
        self.assertEqual(gates.pop('binding_conformance'), {'status': 'passed', 'run': ['bindings-a', 'bindings-b']})
        expected = {gate: dict(value) for gate, value in self.full['gates'].items()}
        self.assertEqual(expected.pop('binding_conformance'), {'status': 'passed', 'run': 'bindings'})
        self.assertEqual(gates, expected)
        self.assertEqual(gates['abi'], {'status': 'passed', 'run': 'abi'})
        self.assertEqual(report['additional_model_gates'], self.full['additional_model_gates'])
        self.assertTrue(report['additional_model_gates'])
        timing = read_json(output / 'parity-timing.json')
        self.assertEqual({half: timing['halves'][half]['bindings'] for half in ('a', 'b')}, {'a': 'a', 'b': 'b'})

    def test_merged_reports_are_ci_evidence(self):
        for name, profiles in (('x64', {'ci-full'}), ('arm64', {'ci-full'}), ('clang', {'host-full', 'ci-compiler'})):
            with self.subTest(name):
                _, report = self.merged(name)
                self.assertEqual((report['phase'], report['selection']['profile']), ('merged', self.sets[name]['profile']))
                self.assertEqual(report['checks'], list(PROFILE_CHECKS[self.sets[name]['profile']]))
                self.assertEqual(sorted(c for shard in report['shards'].values() for c in shard['checks']),
                                 sorted(PROFILE_CHECKS[self.sets[name]['profile']]))
                require_complete_validation(report, name, profiles=profiles, cache_allowed=True)
                # Uncached call sites refuse the CI build's cache.
                with self.assertRaisesRegex(Mismatch, 'compiler launcher ccache'):
                    require_complete_validation(report, name, profiles=profiles, cache_allowed=False)
        _, report = self.merged('x64')
        self.assertEqual(report['skipped']['policy:docs-links'], 'tooling job of the same run')
        self.assertNotIn('bindings', report['skipped'])
        self.assertNotIn('policy', report['skipped'])
        _, report = self.merged('clang')
        self.assertTrue(report['skipped']['package'].startswith('not selected'))
        self.assertEqual(report['skipped']['policy:abi-layouts'], 'tooling job of the same run')

    def test_binding_halves_cover_the_bindings(self):
        # Both halves together are the whole bindings check: the merged evidence equals the one-shard merge's.
        _, whole = self.merged('arm64')
        output, halves = self.merged('arm64-halves')
        self.assertEqual(halves['checks'], list(PROFILE_CHECKS['ci-full']))
        self.assertEqual(halves['selection'], whole['selection'])
        self.assertEqual(sorted(halves['runs']), sorted(whole['runs']))
        self.assertEqual(halves['gates'], whole['gates'])
        self.assertEqual(halves['skipped'], whole['skipped'])
        self.assertEqual(sorted(c for shard in halves['shards'].values() for c in shard['checks']),
                         sorted(PROFILE_CHECKS['ci-full'] + ('bindings:a', 'bindings:b')) and
                         sorted([c for c in PROFILE_CHECKS['ci-full'] if c != 'bindings'] + ['bindings:a', 'bindings:b']))
        for suite in ('rust', 'node', 'python', 'java', 'dotnet', 'go'):
            self.assertTrue((output / f'{suite}.log').is_file(), suite)
        require_complete_validation(halves, 'halves', profiles={'ci-full'}, cache_allowed=True)

    def test_a_missing_binding_half_fails_the_evidence(self):
        _, halves = self.merged('arm64-halves')
        halves['shards'] = {name: {**shard, 'checks': [c for c in shard['checks'] if c != 'bindings:b']} for name, shard in halves['shards'].items()}
        with self.assertRaisesRegex(Mismatch, 'do not account for the checks'):
            require_complete_validation(halves, 'halves', profiles={'ci-full'}, cache_allowed=True)

    def test_file_lists(self):
        dropped, added = set(FILES['dropped']), set(FILES['added'])
        for name, platform in (('x64', 'linux-x64'), ('arm64', 'linux-arm64')):
            with self.subTest(platform):
                recorded = set(FILES['artifacts'][platform]['files'])
                self.assertTrue(dropped <= recorded, sorted(dropped - recorded))
                output, _ = self.merged(name)
                files = {path.relative_to(output).as_posix() for path in output.rglob('*') if path.is_file()}
                files = {name for name in files if not name.startswith(('packages/', 'assembled/'))}
                self.assertEqual(sorted(files), sorted((recorded - dropped) | added))
                # A recorded file the merge does not write needs a dropped entry with its reason.
                self.assertTrue(all(FILES['dropped'][name] for name in recorded - files))
        self.assertFalse(any(fnmatch.fnmatch(name, 'shard-*:*') for name in added))

    def test_refusals(self):
        def drop(check):
            return lambda item: [shard for shard in item['shards'] if not shard.name.endswith('-' + slug(check))]

        def change(check, path, edit):
            def apply(item):
                self.edit(self.shard(item, check) / path, edit)
                return item['shards']
            return apply

        def duplicate(check):
            def apply(item):
                copy = item['shards'][0].parent / 'shard-copy'
                shutil.copytree(self.shard(item, check), copy)
                return item['shards'] + [copy]
            return apply

        def cross_host_in(check):
            def apply(item):
                self.cross_host(self.shard(item, check), item['build'])
                return item['shards']
            return apply

        def remove_cross_host(item):
            shutil.rmtree(self.shard(item, 'ctest') / 'cross-host')
            return item['shards']

        def other_bundle(item):
            (self.shard(item, 'ctest') / 'cross-host/bundle/libducktape_laya.so').write_bytes(b'other')
            return item['shards']

        def drop_policy_run(item):
            shard = self.shard(item, 'policy-platform')
            self.edit(shard / 'report.json', lambda r: (r['runs'].pop('abi-layouts'), r['skipped'].update({'policy:abi-layouts': 'not selected'})))
            return item['shards']

        def as_checks(check, *checks):
            return change(check, 'report.json', lambda r: r.update(checks=list(checks)))

        cases = [
            ('missing binding half b', 'arm64-halves', None, drop('bindings:b'), 'only one binding half'),
            ('missing binding half a', 'arm64-halves', None, lambda item: [s for s in item['shards'] if 'bindings-a' not in s.name], 'only one binding half'),
            ('half twice', 'arm64-halves', None, as_checks('bindings:b', 'bindings:a'), 'bindings:a ran in both'),
            ('whole and half', 'arm64-halves', None, as_checks('bindings:b', 'bindings'), 'both whole and in halves'),
            ('missing shard', 'x64', None, drop('bindings'), 'not the ci-full checks'),
            ('failed shard', 'x64', None, change('references', 'report.json', lambda r: r.update(selected_checks='failed', error='x')),
             'selected checks failed'),
            ('library', 'x64', None, change('bindings', 'report.json', lambda r: r.update(library_sha256='0' * 64)), 'library_sha256 differs'),
            ('commit', 'arm64', None, change('ctest', 'report.json', lambda r: r.update(source_commit='b' * 40)), 'source_commit differs'),
            ('sources', 'arm64', None, change('packages', 'report.json', lambda r: r.update(source_files_sha256='0' * 64)),
             'source_files_sha256 differs'),
            ('overlapping checks', 'arm64', None, duplicate('ctest'), 'ctest ran in both'),
            ('C results', 'x64', None, change('parity:full-b', 'outputs-b.json',
                                             lambda r: r['fixtures']['primitive.json']['C']['answers']['department'].update(choice='sales')),
             'C results of primitive.json differ'),
            ('open-config C results', 'arm64', None, change('parity:full-a', 'outputs-a.json', lambda r: r['open_config']['results']['C'].update(x=1)),
             'open-config pass differ'),
            ('ci-compiler with packages', 'x64', 'ci-compiler', drop('policy-platform'), 'not the ci-compiler checks'),
            ('package run in another check', 'clang', None, change('bindings', 'report.json',
                                                                   lambda r: r['runs'].update(package={'exit_code': 0, 'seconds': 1, 'log': 'package.log'})),
             'ran package, which none of its checks'),
            ('ci-full without the platform policy', 'arm64', None, drop('policy-platform'), 'not the ci-full checks'),
            ('a platform policy run missing', 'arm64', None, drop_policy_run, 'did not run abi-layouts'),
            ('two cross-hosts', 'x64', None, cross_host_in('bindings'), 'exactly one shard'),
            ('linux-x64 without cross-host', 'x64', None, remove_cross_host, 'exactly one shard, not none'),
            ('arm64 with cross-host', 'arm64', None, cross_host_in('ctest'), 'side output of the linux-x64 candidate only'),
            ('clang with cross-host', 'clang', None, cross_host_in('ctest'), 'side output of the linux-x64 candidate only'),
            ('bundle library', 'x64', None, other_bundle, 'does not hold the build\'s library'),
        ]
        for label, name, profile, mutate, message in cases:
            with self.subTest(label):
                item = self.copy(name)
                self.refused(message, item, profile, mutate(item))


if __name__ == '__main__':
    unittest.main()
