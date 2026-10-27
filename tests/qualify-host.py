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
    base.mkdir(parents=True, exist_ok=True)
    (base / 'online').write_text(online + '\n')
    for cpu, group in siblings.items():
        topology = base / f'cpu{cpu}/topology'
        topology.mkdir(parents=True, exist_ok=True)
        (topology / 'thread_siblings_list').write_text(group + '\n')
    return root


def fake_inputs(base):
    """Every path the preflight validates, as files and directories in `base`."""
    base = pathlib.Path(base)
    paths = {name: base / 'in' / name for name in ('model', 'laya-multilingual-f16.gguf', 'laya-typed-decisions-q8_0.gguf')}
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'x')
    lkg, rootfs, ci = base / 'in/lkg', base / 'in/rootfs', base / 'candidate/ci'
    (lkg).mkdir()
    (lkg / 'inventory.json').write_text('{}')
    (rootfs / 'etc').mkdir(parents=True)
    (rootfs / 'etc/os-release').write_text('ID=ubuntu\n')
    (ci / 'packages-assembled').mkdir(parents=True)
    emsdk = base / 'in/emsdk'
    emsdk.mkdir()
    return {'model': paths['model'], 'multilingual': paths['laya-multilingual-f16.gguf'], 'typed': paths['laya-typed-decisions-q8_0.gguf'],
            'lkg': lkg, 'rootfs': rootfs, 'emsdk': emsdk}


def inputs_argv(inputs):
    return ['--model', str(inputs['model']), '--extra-model', str(inputs['multilingual']), '--extra-model', str(inputs['typed']),
            '--emsdk', str(inputs['emsdk']), '--lkg-packages', str(inputs['lkg']), '--rootfs', str(inputs['rootfs'])]


def patch_checkout(module):
    """Replace the checks of this checkout (the submodule, the emsdk pin) with passing ones; returns the undo."""
    saved = module.runtime_problem, module.emsdk_pin, module.subprocess.run, module.rehearsal_stages
    module.runtime_problem = lambda record: None
    module.rehearsal_stages = lambda *args, **kwargs: []  # the plan check and the rehearsal have their own tests
    module.emsdk_pin = lambda: 'fake'
    real = module.subprocess.run

    def run(command, *args, **kwargs):  # the emsdk's git HEAD
        if isinstance(command, list) and command[:2] == ['git', '-C'] and command[3:] == ['rev-parse', 'HEAD'] \
                and pathlib.Path(command[2]).name == 'emsdk':
            return subprocess.CompletedProcess(command, 0, 'fake\n', '')
        return real(command, *args, **kwargs)
    module.subprocess.run = run

    def undo():
        module.runtime_problem, module.emsdk_pin, module.subprocess.run, module.rehearsal_stages = saved
    return undo


def dry_run(argv):
    with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
        code = qualify.main(argv)
    return code, out.getvalue().splitlines(), err.getvalue()


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
        self.assertEqual(out.getvalue(), 'SOAK_CPUS=2-3,6-7\nOTHER_CPUS=0-1,4-5\n')  # qualify.SOAK_CORES


class DryRun(unittest.TestCase):
    """--dry-run against this checkout: the documented order, the CPU sets and every evidence key."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = pathlib.Path(cls.tmp.name)
        root = fake_sys(cls.base / 'sys', '0-7', {cpu: f'{cpu % 4},{cpu % 4 + 4}' for cpu in range(8)})
        cls.out = cls.base / 'candidate'
        cls.inputs = fake_inputs(cls.base)
        cls.argv = ['--dry-run', '--candidate', 'HEAD', '--sys-root', str(root), '--output', str(cls.out), *inputs_argv(cls.inputs)]
        cls.patches = patch_checkout(qualify)
        cls.code, cls.lines, cls.err = dry_run(cls.argv)
        cls.steps = [[part.rstrip(':') for part in line.split()[1:3]] for line in cls.lines if line.startswith('  step ')]

    @classmethod
    def tearDownClass(cls):
        cls.patches()
        cls.tmp.cleanup()

    def step(self, name):
        return next(line for line in self.lines if line.startswith('  step ') and line.split()[2].rstrip(':') == name)

    def test_exit_status(self):
        self.assertEqual(self.code, 0)
        self.assertIn('soak CPUs 2-3,6-7, other CPUs 0-1,4-5', self.lines[0])

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
        self.assertIn(f'--model {self.inputs["model"]} --extra-model {self.inputs["multilingual"]}', line)
        self.assertIn('--upstream-reference', line)
        for name, _ in [(n, p) for p, n in self.steps if p == '2']:
            self.assertIn('[alone]', self.step(name))

    def test_cpu_pinning(self):
        self.assertIn('[taskset -c 2-3,6-7, background]', self.step('soak'))
        budgets = json.loads((ROOT / 'release/qualification.json').read_text())['budgets']
        self.assertIn(f"--seconds {budgets['candidate_soak_seconds']} --min-rounds {budgets['candidate_soak_rounds']}", self.step('soak'))
        for name in ('reproducibility', 'model-upgrade', 'browser', 'userspaces', 'cross-cpu'):
            self.assertIn('[taskset -c 0-1,4-5]', self.step(name))
        upgrade = self.step('model-upgrade')
        self.assertIn(f'--candidate-model {self.inputs["typed"]}', upgrade)
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
            settle_temp_c, settle_load, settle_timeout = 75.0, 1.0, 900.0
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


class Preflight(unittest.TestCase):
    """The preflight fails in seconds, with a message naming the flag, before any step runs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self.tmp.name)
        self.inputs = fake_inputs(self.base)
        self.out = self.base / 'candidate'
        self.undo = patch_checkout(qualify)

    def tearDown(self):
        self.undo()
        self.tmp.cleanup()

    def run_dry(self, *extra, drop=()):
        argv = ['--dry-run', '--candidate', 'HEAD', '--sys-root', str(fake_sys(self.base / 'sys', '0-7', {c: f'{c % 4},{c % 4 + 4}' for c in range(8)})),
                '--output', str(self.out), *inputs_argv(self.inputs)]
        for flag in drop:  # a flag and its values
            while flag in argv:
                at = argv.index(flag)
                del argv[at:at + 2]
        return dry_run(argv + list(extra))

    def test_passes_with_everything_present(self):
        code, lines, err = self.run_dry()
        self.assertEqual(code, 0)
        self.assertNotIn('preflight failed', err)
        self.assertIn('qualify-host: preflight passed', lines)

    def test_missing_paths_are_errors_not_fallbacks(self):
        for flag in ('--emsdk', '--lkg-packages', '--rootfs'):
            code, _, err = self.run_dry(drop=(flag,))
            self.assertEqual(code, 2, flag)
            self.assertIn(f'{flag} is required', err)

    def test_nonexistent_paths_are_named(self):
        missing = self.base / 'nowhere'
        code, _, err = self.run_dry('--emsdk', str(missing), '--lkg-packages', str(missing), '--rootfs', str(missing), drop=('--emsdk', '--lkg-packages', '--rootfs'))
        self.assertEqual(code, 2)
        for flag in ('--emsdk', '--lkg-packages', '--rootfs'):
            self.assertIn(f'{flag} {missing} is not an existing directory', err)

    def test_relative_paths_are_resolved(self):
        cwd = os.getcwd()
        try:
            os.chdir(self.base)
            code, lines, err = self.run_dry('--rootfs', 'in/rootfs', drop=('--rootfs',))
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 0, err)
        self.assertIn(f'{self.inputs["rootfs"]} ', ' '.join(lines))

    def test_incomplete_directories_and_missing_models(self):
        (self.inputs['lkg'] / 'inventory.json').unlink()
        (self.inputs['rootfs'] / 'etc/os-release').unlink()
        self.inputs['typed'].unlink()
        code, _, err = self.run_dry()
        self.assertEqual(code, 2)
        self.assertIn('has no inventory.json', err)
        self.assertIn('has no etc/os-release', err)
        self.assertIn('--extra-model', err)

    def test_emsdk_must_match_the_pin(self):
        qualify.emsdk_pin = lambda: 'other'
        code, _, err = self.run_dry()
        self.assertEqual(code, 2)
        self.assertIn('release/browser-toolchain.json pins', err)

    def test_no_ci_evidence(self):
        (self.out / 'ci/packages-assembled').rmdir()
        code, _, err = self.run_dry()
        self.assertEqual(code, 2)
        self.assertIn('no CI evidence', err)

    def test_existing_outputs_need_replace(self):
        (self.out / 'performance').mkdir()
        (self.out / 'performance/laya-english-q8_0.json').write_text('{}')
        code, _, err = self.run_dry()
        self.assertEqual(code, 2)
        self.assertIn('outputs exist (nothing is overwritten', err)
        code, lines, err = self.run_dry('--replace')
        self.assertEqual(code, 0, err)
        self.assertTrue((self.out / 'performance/laya-english-q8_0.json').is_file())  # a dry run moves nothing

    def test_archive_keeps_the_evidence(self):
        (self.out / 'performance').mkdir()
        report = self.out / 'performance/a.json'
        report.write_text('evidence')
        destination = qualify.archive_outputs([report], self.out, '20260101T000000')
        self.assertFalse(report.exists())
        self.assertEqual((destination / 'performance/a.json').read_text(), 'evidence')

    def test_benchmarks_settle_first(self):
        _, lines, _ = self.run_dry('--settle-temp-c', '70', '--settle-timeout', '30')
        line = next(l for l in lines if l.startswith('  step 2 performance-laya-english'))
        self.assertIn('--settle-temp-c 70.0 --settle-load 1.0 --settle-timeout 30.0', line)

    def test_runtime_submodule_must_match_the_record(self):
        undo_all = self.undo
        self.undo = lambda: None
        undo_all()  # the real runtime_problem, against a throwaway repository
        env = dict(os.environ, **GIT_ENV)
        repo = self.base / 'repo'
        (repo / 'third_party/llama.cpp').mkdir(parents=True)
        subprocess.run(['git', 'init', '-q', str(repo)], check=True, env=env)
        record = {'runtime': {'path': 'third_party/llama.cpp', 'commit': 'a' * 40}}
        saved = qualify.ROOT
        qualify.ROOT = repo
        try:
            self.assertIn('git submodule update --init', qualify.runtime_problem(record))  # empty directory inside the parent repo
            inner = repo / 'third_party/llama.cpp'
            subprocess.run(['git', 'init', '-q', str(inner)], check=True, env=env)
            subprocess.run(['git', '-C', str(inner), 'commit', '-q', '--allow-empty', '-m', 'x'], check=True, env=env)
            head = subprocess.run(['git', '-C', str(inner), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
            self.assertIn('compatibility record pins', qualify.runtime_problem(record))
            self.assertIsNone(qualify.runtime_problem({'runtime': {'path': 'third_party/llama.cpp', 'commit': head}}))
        finally:
            qualify.ROOT = saved
            self.undo = patch_checkout(qualify)


class NpmTrees(unittest.TestCase):
    def test_dry_run_only_reports_and_a_current_tree_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = pathlib.Path(tmp)
            (tree / 'package-lock.json').write_text('{}')
            saved = qualify.ROOT
            qualify.ROOT = tree.parent
            try:
                problems, notes = qualify.ensure_npm([tree], install=False)
                self.assertEqual((problems, len(notes)), ([], 1))
                (tree / 'node_modules').mkdir()
                (tree / 'node_modules/.package-lock.json').write_text('{}')
                self.assertTrue(qualify.npm_tree_current(tree))
                self.assertEqual(qualify.ensure_npm([tree], install=True), ([], []))  # idempotent: no npm run
                os.utime(tree / 'package-lock.json', (4e9, 4e9))  # the lockfile changed after the install
                self.assertFalse(qualify.npm_tree_current(tree))
            finally:
                qualify.ROOT = saved


class HostState(unittest.TestCase):
    def fake(self, root, temp, khz, throttles=0):
        zone = pathlib.Path(root) / 'class/thermal/thermal_zone0'
        zone.mkdir(parents=True, exist_ok=True)
        (zone / 'type').write_text('x86_pkg_temp\n')
        (zone / 'temp').write_text(f'{temp}\n')
        for cpu, value in enumerate(khz):
            base = pathlib.Path(root) / f'devices/system/cpu/cpu{cpu}'
            (base / 'cpufreq').mkdir(parents=True, exist_ok=True)
            (base / 'cpufreq/scaling_cur_freq').write_text(f'{value}\n')
            (base / 'thermal_throttle').mkdir(exist_ok=True)
            (base / 'thermal_throttle/package_throttle_count').write_text(f'{throttles}\n')

    def test_sample_and_summary(self):
        import sys
        sys.path.insert(0, str(ROOT / 'scripts'))
        import release_support as rs
        with tempfile.TemporaryDirectory() as tmp:
            self.fake(tmp, 91000, [1200000, 1500000], throttles=3)
            hot = rs.host_state(tmp)
            self.fake(tmp, 70000, [3000000, 3000000], throttles=9)
            cool = rs.host_state(tmp)
            self.assertEqual(hot['thermal_c'], {'x86_pkg_temp': 91.0})
            self.assertEqual(hot['cpu_khz']['mean'], 1350000)
            self.assertEqual(rs.host_state(tmp + '/missing').keys(), {'time_unix', 'load_average'})  # nothing guessed
            summary = rs.host_summary({'host_state': {'before': hot, 'after': cool}})
            self.assertEqual((summary['max_package_temp_c'], summary['min_mean_cpu_mhz'], summary['throttle_events']), (91.0, 1350.0, 12))
            self.assertIsNone(rs.host_summary({}))

    def test_settle_waits_and_never_skips(self):
        import sys
        sys.path.insert(0, str(ROOT / 'scripts'))
        import release_support as rs
        temps = iter([90.0, 82.0, 74.0])
        now = [0.0]
        sample = lambda: {'load_average': [0.1], 'thermal_c': {'x86_pkg_temp': next(temps)}}  # noqa: E731
        sleep = lambda seconds: now.__setitem__(0, now[0] + seconds)  # noqa: E731
        record = rs.wait_settled(75, 1, 600, poll=5, sample=sample, sleep=sleep, clock=lambda: now[0])
        self.assertEqual((record['settled'], record['waited_seconds'], record['start']['thermal_c']['x86_pkg_temp']), (True, 10.0, 90.0))
        now[0] = 0.0
        hot = lambda: {'load_average': [5.0], 'thermal_c': {'x86_pkg_temp': 95.0}}  # noqa: E731
        record = rs.wait_settled(75, 1, 12, poll=5, sample=hot, sleep=sleep, clock=lambda: now[0])
        self.assertEqual((record['settled'], record['waited_seconds']), (False, 12.0))  # timed out: recorded, measurement still runs


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
            steps = [qualify.Step('native', 1, ['false'], stop=True, evidence={'native': 'native/report.json'}),
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


class FailureHandling(unittest.TestCase):
    CPUS = {'other': '0', 'soak': '0', 'all': None}

    def go(self, steps, evidence=None, keys=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out = pathlib.Path(tmp.name)
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = qualify.run(steps, self.CPUS, self.out, evidence or {}, self.out / 'evidence.json', keys or {})
        self.stdout, self.stderr = out.getvalue(), err.getvalue()
        self.status = json.loads((self.out / 'qualify-status.json').read_text())
        return code

    def test_failure_is_loud_and_machine_readable(self):
        code = self.go([qualify.Step('a', 1, ['true']), qualify.Step('b', 2, ['false']), qualify.Step('c', 3, ['true'])])
        self.assertEqual(code, 1)
        self.assertIn('***** FAILED: b', self.stdout)
        self.assertIn('***** FAILED: b', self.stderr)
        self.assertEqual((self.status['state'], self.status['failed']), ('failed', ['b']))
        self.assertEqual([self.status['steps'][n]['state'] for n in 'abc'], ['passed', 'failed', 'passed'])  # no stop: all failures are collected
        self.assertFalse((self.out / 'qualify-status.json.tmp').exists())

    def test_stop_marks_the_rest_not_run(self):
        code = self.go([qualify.Step('native', 1, ['false'], stop=True), qualify.Step('later', 2, ['true']),
                        qualify.Step('evidence', 4, ['(w)'], action=lambda: self.fail('ran'))])
        self.assertEqual(code, 1)
        self.assertEqual(self.status['stopped_because'], 'native')
        self.assertEqual([self.status['steps'][n]['state'] for n in ('later', 'evidence')], ['not_run', 'not_run'])

    def test_background_failure_is_reported_while_the_foreground_runs(self):
        qualify.POLL_SECONDS = 0.2
        self.addCleanup(setattr, qualify, 'POLL_SECONDS', 5)
        soak = qualify.Step('soak', 3, ['false'], cpus='soak', background=True)
        slow = qualify.Step('slow', '3a', [qualify.PY, '-c', 'import time; time.sleep(2)'], cpus='other')
        self.go([soak, slow])
        self.assertLess(self.stdout.index('FAILED: soak'), self.stdout.index('slow passed'))

    def remeasure_steps(self, first_ok, second_ok):
        """A budget check that fails (or not) on the first report and again on the re-measure's."""
        report, retry = self.out_dir / 'p.json', self.out_dir / 'p.remeasure.json'
        write = lambda path: [qualify.PY, '-c', f'import pathlib; pathlib.Path({str(path)!r}).write_text(\'{{"library_sha256": "x"}}\')']  # noqa: E731
        exit_code = lambda ok: [qualify.PY, '-c', f'raise SystemExit({0 if ok else 1})']  # noqa: E731
        return [qualify.Step('performance-m', 2, write(report), evidence={'performance': 'p.json'}),
                qualify.Step('performance-budget-m', 2, exit_code(first_ok), stop=True, outputs=[retry], remeasure={
                    'measure': qualify.Step('performance-m-remeasure', 2, write(retry)),
                    'check': qualify.Step('performance-budget-m-remeasure', 2, exit_code(second_ok)),
                    'report': retry, 'first': report, 'key': 'performance'}),
                qualify.Step('later', 3, ['true'])]

    def run_remeasure(self, first_ok, second_ok):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.out_dir = pathlib.Path(self.tmp_dir.name)
        steps = self.remeasure_steps(first_ok, second_ok)
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            code = qualify.run(steps, self.CPUS, self.out_dir, {'performance': 'p.json'}, self.out_dir / 'evidence.json',
                               {'performance-m': ['performance']})
        self.stdout = out.getvalue()
        return code

    def test_budget_failure_gets_one_recorded_remeasure(self):
        self.assertEqual(self.run_remeasure(False, True), 0)
        evidence = json.loads((self.out_dir / 'evidence.json').read_text())
        self.assertEqual(evidence['performance'], 'p.remeasure.json')  # the passing attempt is the evidence
        retry = json.loads((self.out_dir / 'p.remeasure.json').read_text())
        self.assertEqual(retry['remeasure']['attempt'], 2)
        self.assertEqual(retry['remeasure']['first_attempt_report'], 'p.json')
        self.assertTrue((self.out_dir / 'p.json').is_file())  # the failed first attempt stays
        status = json.loads((self.out_dir / 'qualify-status.json').read_text())
        self.assertEqual((status['state'], status['steps']['performance-budget-m']['remeasured']), ('passed', True))
        self.assertEqual(status['steps']['later']['state'], 'passed')

    def test_second_failure_fails_and_stops(self):
        self.assertEqual(self.run_remeasure(False, False), 1)
        status = json.loads((self.out_dir / 'qualify-status.json').read_text())
        self.assertEqual((status['stopped_because'], status['steps']['later']['state']), ('performance-budget-m', 'not_run'))

    def test_no_remeasure_after_a_pass_and_none_for_other_steps(self):
        self.assertEqual(self.run_remeasure(True, False), 0)
        self.assertFalse((self.out_dir / 'p.remeasure.json').exists())
        self.assertEqual(self.go([qualify.Step('performance-x', 2, ['false'])]), 1)  # a benchmark itself failing is not re-measured


class Rehearsal(unittest.TestCase):
    """The preflight's rehearsal stages run cheapest first and stop at the first one that fails."""

    def setUp(self):
        self.record = json.loads((ROOT / 'release/compatibility.json').read_text())
        self.calls = []
        self.saved = qualify.plan_check, qualify.configure_rehearsal, qualify.subprocess.run
        qualify.plan_check = lambda *args: self.calls.append('plan') or self.plan_problems
        qualify.configure_rehearsal = lambda runtime, model: self.calls.append('configure') or self.configure_problem
        self.plan_problems, self.configure_problem = [], None
        self.args = qualify.argparse.Namespace(runtime='a' * 40, model=pathlib.Path('/m'), upgrade_manifest=None)
        self.ci = pathlib.Path('/nonexistent/ci')

    def tearDown(self):
        qualify.plan_check, qualify.configure_rehearsal, qualify.subprocess.run = self.saved

    def stages(self):
        real = self.saved[2]
        qualify.subprocess.run = lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 0, '', '') if 'rev-parse' in cmd else real(cmd, *a, **k)
        return qualify.rehearsal_stages(self.args, self.record, 'C', self.ci, pathlib.Path('/e.json'), [], True)

    def test_order_and_stop(self):
        self.assertEqual([len(s[2]) for s in self.stages()], [0, 0, 0])
        self.assertEqual(self.calls, ['plan', 'configure'])
        self.calls.clear()
        self.plan_problems = ['the rehearsal runtime is the pin']
        self.assertEqual(len(self.stages()), 1)
        self.assertNotIn('configure', self.calls)
        self.calls.clear()
        self.plan_problems, self.configure_problem = [], 'patch conflict'
        self.assertEqual(self.stages()[-1][2], ['patch conflict'])

    def test_runtime_must_be_in_the_checkout(self):
        qualify.subprocess.run = lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 1, '', 'bad')
        stages = qualify.rehearsal_stages(self.args, self.record, 'C', self.ci, pathlib.Path('/e.json'), [], True)
        self.assertIn('git -C', stages[-1][2][0])
        self.assertNotIn('configure', self.calls)

    def test_downloaded_report_must_agree(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = pathlib.Path(tmp) / 'qualify/upgrade/report.json'
            report.parent.mkdir(parents=True)
            report.write_text(json.dumps({'candidate_runtime': 'b' * 40}))
            self.ci = pathlib.Path(tmp)
            self.assertIn('differs from the downloaded', self.stages()[-1][2][0])
            self.args.runtime = None
            self.assertEqual(qualify.downloaded_runtime(self.ci), 'b' * 40)

    def test_plan_check_against_the_gates(self):
        """The real `check-candidate.py --plan` on this checkout: the pin is refused, a runtime past it and the host's keys pass."""
        keys = ['native', 'native_matrix', 'performance', 'soak', 'stress', 'reproducibility', 'model_upgrade', 'browser_package', 'browser',
                'browser_isolated', 'installed_release', 'userspaces', 'rollback', 'cross_cpu']
        args = qualify.argparse.Namespace(upgrade_manifest=ROOT / 'models/laya-typed-decisions.json')
        with tempfile.TemporaryDirectory() as tmp:
            ci = pathlib.Path(tmp)
            evidence = ci / 'evidence.json'
            evidence.write_text(json.dumps({key: key for key in ('ci_run', 'electron', 'linux-x64:installed', 'release_packages')}))
            pin = self.record['runtime']['commit']
            self.assertTrue(any('is the pin' in p for p in self.saved[0]('HEAD', pin, args, keys, evidence, ci)))
            self.assertEqual(self.saved[0]('HEAD', 'a' * 40, args, keys, evidence, ci), [])
            self.assertTrue(any('nothing will produce' in p and 'rollback' in p for p in self.saved[0]('HEAD', 'a' * 40, args, [k for k in keys if k != 'rollback'], evidence, ci)))
            self.assertTrue(any('no rehearsal runtime' in p for p in self.saved[0]('HEAD', None, args, keys, evidence, ci)))


class GatePlan(unittest.TestCase):
    def setUp(self):
        import sys
        sys.path.insert(0, str(ROOT / 'scripts'))
        import gate_plan
        self.gate_plan = gate_plan

    def test_keys_found_in_the_sources(self):
        source = """
def g():
    load('a'); long_gate('b'); ctx.ci_json('c'); paths['d']; paths.get('e'); tested_source(r, 'f')
    for name in ('x', 'y'):
        load(name)
    for name, kinds in (('z', 1),):
        load(name)
    load(name)
"""
        keys, tested = self.gate_plan.gate_sources(source)
        self.assertEqual(keys, set('abcde') | {'x', 'y', 'z'})  # a loop variable means its literals only inside its loop
        self.assertEqual(tested, {'f'})

    def test_every_gate_key_is_accounted_for(self):
        """The keys check-candidate.py reads are the host plan's, the CI download's, the qualify run's or the maintainer's."""
        keys, tested = self.gate_plan.gate_sources((ROOT / 'scripts/check-candidate.py').read_text())
        self.assertTrue({'native', 'rollback', 'upgrade', 'security_review'} <= keys)
        self.assertTrue(tested)

    def test_qualify_keys_match_the_fetcher(self):
        spec = importlib.util.spec_from_file_location('fetch_ci_evidence', ROOT / 'scripts/fetch-ci-evidence.py')
        fetch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fetch)
        self.assertEqual(tuple(self.gate_plan.QUALIFY_KEYS), tuple(fetch.QUALIFY_ARTIFACTS))

    def test_problems(self):
        record = {'targets': [{'platform': 'linux-x64', 'tier': 1}, {'platform': 'windows-x64', 'tier': 2}]}
        source = "load('native'); load('ghost'); tested_source(r, 'nope')"
        problems = self.gate_plan.plan_problems(record, 'p' * 40, [source], {'soak': []}, ['native'], [], 'p' * 40,
                                                {'candidate_runtime': 'p' * 40, 'baseline_runtime': 'p' * 40}, 's', 's')
        text = ' | '.join(problems)
        for expected in ('nope have no HARNESS', 'ghost', 'is the pin', 'against itself', 'is the primary model'):
            self.assertIn(expected, text)
        self.assertEqual(self.gate_plan.plan_problems(record, 'p' * 40, ["load('native')"], {}, ['native'], [], 'q' * 40), [])


if __name__ == '__main__':
    unittest.main()
