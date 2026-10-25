#!/usr/bin/env python3
"""scripts/qualify-host.py: CPU sets from a fake /sys topology, the step order and evidence keys (--dry-run), reuse (offline)."""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import tarfile
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('qualify_host', ROOT / 'scripts/qualify-host.py')
qualify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qualify)

GIT_ENV = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.invalid', 'GIT_COMMITTER_NAME': 't',
           'GIT_COMMITTER_EMAIL': 't@example.invalid', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}


def fake_sys(root, online, siblings):
    """A /sys/devices/system/cpu tree: `online` list and each CPU's thread_siblings_list."""
    base = pathlib.Path(root) / 'devices/system/cpu'
    base.mkdir(parents=True)
    (base / 'online').write_text(online + '\n')
    for cpu, group in siblings.items():
        topology = base / f'cpu{cpu}/topology'
        topology.mkdir(parents=True)
        (topology / 'thread_siblings_list').write_text(group + '\n')
    return root


class Topology(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.n = 0

    def tearDown(self):
        self.tmp.cleanup()

    def sets(self, online, siblings, cores=1):
        self.n += 1
        root = fake_sys(pathlib.Path(self.tmp.name) / str(self.n), online, siblings)
        soak, other = qualify.cpu_sets(root, cores)
        return qualify.format_cpu_list(soak), qualify.format_cpu_list(other)

    def test_lists(self):
        self.assertEqual(qualify.parse_cpu_list('0-2,4,6-7\n'), {0, 1, 2, 4, 6, 7})
        self.assertEqual(qualify.format_cpu_list({7, 0, 1, 2, 4, 6}), '0-2,4,6-7')
        self.assertEqual(qualify.format_cpu_list({3}), '3')

    def test_smt_pairs(self):
        """This host: 4 cores, siblings n and n+4; the soak gets core 3 (CPUs 3 and 7), never one thread of a shared core."""
        siblings = {cpu: f'{cpu % 4},{cpu % 4 + 4}' for cpu in range(8)}
        self.assertEqual(self.sets('0-7', siblings), ('3,7', '0-2,4-6'))
        self.assertEqual(self.sets('0-7', siblings, cores=2), ('2-3,6-7', '0-1,4-5'))

    def test_adjacent_siblings(self):
        siblings = {cpu: f'{cpu - cpu % 2}-{cpu - cpu % 2 + 1}' for cpu in range(8)}
        self.assertEqual(self.sets('0-7', siblings), ('6-7', '0-5'))

    def test_no_smt(self):
        self.assertEqual(self.sets('0-3', {cpu: str(cpu) for cpu in range(4)}), ('3', '0-2'))

    def test_offline_cpu(self):
        siblings = {cpu: f'{cpu % 4},{cpu % 4 + 4}' for cpu in range(8)}
        del siblings[7]
        self.assertEqual(self.sets('0-6', siblings), ('3', '0-2,4-6'))

    def test_too_few_cores(self):
        with self.assertRaises(SystemExit):
            self.sets('0-1', {0: '0-1', 1: '0-1'})
        with self.assertRaises(SystemExit):
            self.sets('0-3', {cpu: str(cpu) for cpu in range(4)}, cores=4)

    def test_print_cpus(self):
        root = fake_sys(pathlib.Path(self.tmp.name) / 'p', '0-7', {cpu: f'{cpu % 4},{cpu % 4 + 4}' for cpu in range(8)})
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(qualify.main(['--print-cpus', '--sys-root', str(root)]), 0)
        self.assertEqual(out.getvalue(), 'SOAK_CPUS=3,7\nOTHER_CPUS=0-2,4-6\n')


class DryRun(unittest.TestCase):
    """--dry-run against this checkout: the documented order, the CPU sets and every evidence key."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = fake_sys(pathlib.Path(cls.tmp.name) / 'sys', '0-7', {cpu: f'{cpu % 4},{cpu % 4 + 4}' for cpu in range(8)})
        cls.out = pathlib.Path(cls.tmp.name) / 'candidate'
        argv = ['--dry-run', '--candidate', 'HEAD', '--sys-root', str(root), '--output', str(cls.out), '--model', '/m/primary.gguf',
                '--extra-model', '/m/laya-multilingual-f16.gguf', '--extra-model', '/m/laya-typed-decisions-q8_0.gguf',
                '--emsdk', '/emsdk', '--lkg-packages', '/lkg', '--rootfs', '/rootfs']
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            cls.code = qualify.main(argv)
        cls.lines = out.getvalue().splitlines()
        cls.steps = [[part.rstrip(':') for part in line.split()[1:3]] for line in cls.lines if line.startswith('  step ')]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def step(self, name):
        return next(line for line in self.lines if line.startswith('  step ') and line.split()[2].rstrip(':') == name)

    def test_exit_status(self):
        self.assertEqual(self.code, 0)
        self.assertIn('soak CPUs 3,7, other CPUs 0-2,4-6', self.lines[0])

    def test_order(self):
        names = [name for _, name in self.steps]
        phases = [phase for phase, _ in self.steps]
        self.assertEqual(names[0], 'native')
        self.assertEqual(phases[:names.index('soak')].count('1'), 1)
        self.assertTrue(all(phase == '2' for phase in phases[1:names.index('soak')]))
        expected = ['soak', 'reproducibility', 'model-upgrade', 'browser-build-wasm', 'browser-build-webgpu',
                    'browser-build-wasm-constrained', 'browser-package', 'browser-package-extract', 'browser', 'browser-isolated',
                    'installed-release', 'userspaces', 'rollback', 'cross-cpu-record', 'cross-cpu', 'evidence']
        self.assertEqual(names[names.index('soak'):], expected)
        self.assertEqual(phases[names.index('soak'):names.index('model-upgrade') + 1], ['3', '3a', '3b'])
        self.assertEqual(phases[-1], '4')
        performance = names[1:names.index('soak')]
        self.assertTrue(performance and all(name.startswith('performance-') for name in performance))
        self.assertEqual(len([n for n in performance if n.startswith('performance-budget-')]) * 2, len(performance))

    def test_native_build_alone_with_gcc(self):
        line = self.step('native')
        self.assertIn('[alone]', line)
        self.assertIn('CC=gcc CXX=g++ python3 scripts/validate.py --full', line)
        self.assertIn('--test-jobs 1', line)
        self.assertIn('--model /m/primary.gguf --extra-model /m/laya-multilingual-f16.gguf', line)
        self.assertIn('--upstream-reference', line)
        for name, _ in [(n, p) for p, n in self.steps if p == '2']:
            self.assertIn('[alone]', self.step(name))

    def test_cpu_pinning(self):
        self.assertIn('[taskset -c 3,7, background]', self.step('soak'))
        budgets = json.loads((ROOT / 'release/qualification.json').read_text())['budgets']
        self.assertIn(f"--seconds {budgets['candidate_soak_seconds']} --min-rounds {budgets['candidate_soak_rounds']}", self.step('soak'))
        for name in ('reproducibility', 'model-upgrade', 'browser', 'userspaces', 'cross-cpu'):
            self.assertIn('[taskset -c 0-2,4-6]', self.step(name))
        upgrade = self.step('model-upgrade')
        self.assertIn('--candidate-model /m/laya-typed-decisions-q8_0.gguf', upgrade)
        self.assertIn('--compiler-launcher ccache', upgrade)

    def test_evidence_keys(self):
        keys = next(line for line in self.lines if line.startswith('  evidence keys: '))[len('  evidence keys: '):].split(', ')
        for key in ('native', 'native_matrix', 'reproducibility', 'performance', 'soak', 'stress', 'model_upgrade', 'browser_package',
                    'browser', 'browser_isolated', 'installed_release', 'userspaces', 'rollback', 'cross_cpu'):
            self.assertIn(key, keys)
        self.assertTrue(any(key.startswith('performance_') for key in keys))


class Plan(unittest.TestCase):
    def test_reused_steps_are_left_out(self):
        class Args:
            model, extra_model = pathlib.Path('/m.gguf'), []
            upgrade_model, upgrade_manifest, emsdk, rootfs, lkg_packages = '/u.gguf', '/u.json', None, None, None
        record = {'models': ['models/manifest.json'], 'runtime': {'commit': 'abc'}}
        policy = {'budgets': {'candidate_soak_seconds': 60, 'candidate_soak_rounds': 3}}
        out = pathlib.Path('/out')
        steps = qualify.plan(Args, record, policy, out, out / 'ci', {})
        soak = next(step for step in steps if step.name == 'soak')
        self.assertEqual(soak.command[soak.command.index('--min-rounds') + 1], 3)
        self.assertEqual(soak.evidence, {'soak': 'soak/report.json', 'stress': 'soak/report.json'})
        matrix = next(step for step in steps if step.name == 'evidence')
        self.assertEqual(matrix.evidence, {'native_matrix': 'native-matrix.json'})
        with tempfile.TemporaryDirectory() as tmp:
            steps = qualify.plan(Args, record, policy, pathlib.Path(tmp), pathlib.Path(tmp) / 'ci', {})
            next(step for step in steps if step.name == 'evidence').action()
            # check-candidate.py reads native_matrix as a list of report paths relative to the list file.
            self.assertEqual(json.loads((pathlib.Path(tmp) / 'native-matrix.json').read_text()),
                             ['native/report.json', 'ci/native-x64-clang-node-<VERSION>/report.json'])
        names = [step.name for step in qualify.plan(Args, record, policy, out, out / 'ci',
                                                    {'soak': 'x', 'model-upgrade': 'x', 'performance': 'x'})]
        self.assertNotIn('soak', names)
        self.assertNotIn('model-upgrade', names)
        self.assertFalse([name for name in names if name.startswith('performance')])
        self.assertIn('reproducibility', names)


class Reuse(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.tmp.name) / 'repo'
        self.repo.mkdir()
        self.git('init', '-q')
        self.saved = qualify.ROOT
        qualify.ROOT = self.repo
        self.rules = qualify.reuse_rules((ROOT / 'scripts/check-candidate.py').read_text())

    def tearDown(self):
        qualify.ROOT = self.saved
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], env=dict(os.environ, **GIT_ENV), capture_output=True, text=True,
                              check=True).stdout.strip()

    def commit(self, files):
        for name, content in files.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'c')
        return self.git('rev-parse', 'HEAD')

    def report(self, commit, **fields):
        path = pathlib.Path(self.tmp.name) / f'report-{len(list(pathlib.Path(self.tmp.name).glob("report-*")))}.json'
        path.write_text(json.dumps({'source_commit': commit, 'source_tracked_dirty': False, 'status': 'passed', **fields}))
        return path

    def test_rules_of_check_candidate(self):
        self.assertIn('src/*', self.rules['LIBRARY_INPUTS'])
        self.assertIn('soak', self.rules['HARNESS'])
        self.assertIn('tests/parity.py', self.rules['HARNESS']['model_upgrade'])
        with self.assertRaises(SystemExit):
            qualify.reuse_rules('LIBRARY_INPUTS = []\n')

    def test_reusable(self):
        base = self.commit({'src/a.cpp': '1', 'docs/x.md': '1', 'tests/stress.cpp': '1'})
        docs = self.commit({'docs/x.md': '2'})
        self.assertIn('produced at the candidate', qualify.reusable(self.report(docs), 'soak', docs, self.rules))
        self.assertIn('no input of soak changed', qualify.reusable(self.report(base), 'soak', docs, self.rules))
        stress = self.commit({'tests/stress.cpp': '2'})
        self.assertIsNone(qualify.reusable(self.report(base), 'soak', stress, self.rules))
        self.assertIsNotNone(qualify.reusable(self.report(base), 'performance', stress, self.rules))
        library = self.commit({'src/a.cpp': '2'})
        self.assertIsNone(qualify.reusable(self.report(stress), 'performance', library, self.rules))
        self.assertIsNone(qualify.reusable(self.report(docs, source_tracked_dirty=True), 'soak', docs, self.rules))
        self.assertIsNone(qualify.reusable(self.report(docs, status='failed'), 'soak', docs, self.rules))
        self.assertIsNone(qualify.reusable(self.report(library), 'soak', docs, self.rules))  # not an ancestor
        self.assertIsNone(qualify.reusable(pathlib.Path(self.tmp.name) / 'missing.json', 'soak', docs, self.rules))


class Extract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def tarball(self, members):
        package = self.dir / 'pkg'
        package.mkdir(exist_ok=True)
        with tarfile.open(package / 'p-1.0.0.tgz', 'w:gz') as archive:
            for name, kind in members:
                info = tarfile.TarInfo(name)
                if kind == 'file':
                    data = name.encode()
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
                elif kind == 'symlink':
                    info.type, info.linkname = tarfile.SYMTYPE, '/etc/passwd'
                    archive.addfile(info)
        return package

    def test_extracts_the_package_directory(self):
        package = self.tarball([('package/index.mjs', 'file'), ('package/dist/a.wasm', 'file'), ('other/x', 'file')])
        qualify.extract_package(package, self.dir / 'out')
        self.assertEqual(sorted(str(p.relative_to(self.dir / 'out')) for p in (self.dir / 'out').rglob('*') if p.is_file()),
                         ['dist/a.wasm', 'index.mjs'])

    def test_refuses_unsafe_members(self):
        for members in ([('package/../../evil', 'file')], [('/abs/evil', 'file')], [('package/link', 'symlink')]):
            with self.subTest(members=members):
                package = self.tarball(members)
                with self.assertRaises(RuntimeError):
                    qualify.extract_package(package, self.dir / f'out-{members[0][0].replace("/", "_")}')
                (package / 'p-1.0.0.tgz').unlink()

    def test_needs_exactly_one_tarball(self):
        (self.dir / 'empty').mkdir()
        with self.assertRaises(RuntimeError):
            qualify.extract_package(self.dir / 'empty', self.dir / 'out')


class RunEvidence(unittest.TestCase):
    def test_failed_evidence_build_writes_no_keys_for_steps_not_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            steps = [qualify.Step('native', 1, ['false'], evidence={'native': 'native/report.json'}),
                     qualify.Step('reproducibility', '3a', ['true'], cpus='other', evidence={'reproducibility': 'r.json'}),
                     qualify.Step('evidence', 4, ['(write)'], action=lambda: self.fail('wrote the matrix'),
                                  evidence={'native_matrix': 'm.json'})]
            evidence = {'native': 'native/report.json', 'reproducibility': 'r.json', 'soak': 'old/soak.json', 'native_matrix': 'm.json'}
            keys = {'native': ['native'], 'reproducibility': ['reproducibility'], 'evidence': ['native_matrix']}
            with contextlib.redirect_stdout(io.StringIO()):
                code = qualify.run(steps, {'other': '0', 'soak': '0', 'all': None}, out, evidence, out / 'evidence.json', keys)
            self.assertEqual(code, 1)
            self.assertEqual(json.loads((out / 'evidence.json').read_text()), {'native': 'native/report.json', 'soak': 'old/soak.json'})

    def test_native_matrix_written_after_the_soak(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            written = []
            steps = [qualify.Step('native', 1, ['true'], evidence={'native': 'native/report.json'}),
                     qualify.Step('soak', 3, ['true'], cpus='soak', background=True, evidence={'soak': 's.json'}),
                     qualify.Step('evidence', 4, ['(write)'], action=lambda: written.append(True), evidence={'native_matrix': 'm.json'})]
            evidence = {'native': 'native/report.json', 'soak': 's.json', 'native_matrix': 'm.json'}
            keys = {step.name: list(step.evidence) for step in steps}
            with contextlib.redirect_stdout(io.StringIO()):
                code = qualify.run(steps, {'other': '0', 'soak': '0', 'all': None}, out, evidence, out / 'evidence.json', keys)
            self.assertEqual((code, written), (0, [True]))
            self.assertEqual(json.loads((out / 'evidence.json').read_text()), evidence)


    def test_model_upgrade_passes_on_expected_model_drift(self):
        # check-upgrade.py exits 1 when the candidate model's results differ; the step judges the report instead.
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            report = out / 'model-upgrade/report.json'
            report.parent.mkdir()
            gates = {g: {'status': 'passed'} for g in ('native_contracts', 'binding_conformance', 'abi')}
            judge = lambda code: qualify.model_upgrade_completed(report)
            cases = [({'gates': gates, 'comparison': {'status': 'failed'}}, 0),
                     ({'gates': gates, 'comparison': {'status': 'passed'}}, 0),
                     ({'gates': gates, 'comparison': {'status': 'failed'}, 'failure': 'Public export mismatch'}, 1),
                     ({'gates': dict(gates, abi={'status': 'not_run'}), 'comparison': {'status': 'failed'}}, 1)]
            for content, expected in cases:
                report.write_text(json.dumps(content))
                steps = [qualify.Step('model-upgrade', '3b', ['false'], cpus='other', judge=judge,
                                      evidence={'model_upgrade': 'model-upgrade/report.json'})]
                with contextlib.redirect_stdout(io.StringIO()):
                    code = qualify.run(steps, {'other': '0', 'soak': '0', 'all': None}, out, {}, out / 'evidence.json', {})
                self.assertEqual(code, expected, content)
            report.unlink()
            self.assertFalse(qualify.model_upgrade_completed(report))


if __name__ == '__main__':
    unittest.main()
