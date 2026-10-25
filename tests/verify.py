#!/usr/bin/env python3
"""scripts/verify.py: plans for sample diffs, preconditions, model discovery, --only, the state path (offline)."""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import ci_paths  # noqa: E402

spec = importlib.util.spec_from_file_location('verify', ROOT / 'scripts/verify.py')
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)
ci_changes = verify.ci_changes

GIT_ENV = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.invalid', 'GIT_COMMITTER_NAME': 't',
           'GIT_COMMITTER_EMAIL': 't@example.invalid', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}
FILES = {'src/laya.cpp': 'int x;\n', 'docs/API.md': '# API\n', 'bindings/python/x.py': '', 'scripts/package-native.py': '',
         'tests/parity.py': '', 'bindings/browser/worker.mjs': '', 'scripts/check-reproducibility.py': '',
         'models/manifest.json': json.dumps({'repository': 'ggml-org/Laya-GGUF', 'revision': 'abc123', 'filename': 'Laya-Q8_0.gguf'})}


def git(path, *args):
    return subprocess.run(['git', '-C', str(path), *args], env=dict(os.environ, **GIT_ENV), capture_output=True, text=True,
                          check=True).stdout.strip()


def write(path, files):
    for name, content in files.items():
        target = pathlib.Path(path) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


class Checkout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / 'repo'
        self.path.mkdir()
        git(self.path, 'init', '-q', '-b', 'topic')
        write(self.path, FILES)
        self.base = self.commit('base')
        self.git = ci_changes.Git(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def commit(self, message, files=None):
        if files:
            write(self.path, files)
        git(self.path, 'add', '-A')
        git(self.path, 'commit', '-q', '-m', message)
        return git(self.path, 'rev-parse', 'HEAD')

    def verified(self, commit, jobs=None):
        return {'schema_version': 1, 'jobs': {job: {'commit': commit} for job in (jobs or ci_paths.LOCAL_JOBS)}, 'last': None}

    def stale(self, state, head=None, dirty=False):
        head = head or self.git.resolve('HEAD')
        return sorted(job for job, s in verify.local_staleness(self.git, state, head, dirty).items() if s['stale'])


class StatePath(Checkout):
    def test_main_checkout(self):
        self.assertEqual(verify.state_path(self.path), (self.path / '.git/verify-state.json').resolve())

    def test_linked_worktree_has_its_own_state(self):
        linked = pathlib.Path(self.tmp.name) / 'linked'
        git(self.path, 'worktree', 'add', '-q', '-b', 'other', str(linked))
        self.assertTrue((linked / '.git').is_file())  # `.git` is a file: .git/verify-state.json would not work
        path = verify.state_path(linked)
        self.assertEqual(path, (self.path / '.git/worktrees/linked/verify-state.json').resolve())
        self.assertNotEqual(path, verify.state_path(self.path))
        verify.save_state(path, self.verified(self.base))
        self.assertEqual(verify.load_state(path)['jobs']['local-fuzz']['commit'], self.base)
        self.assertEqual(verify.load_state(verify.state_path(self.path))['jobs'], {})

    def test_unreadable_state_is_empty(self):
        path = pathlib.Path(self.tmp.name) / 'state.json'
        path.write_text('{not json')
        self.assertEqual(verify.load_state(path), {'schema_version': 1, 'jobs': {}, 'last': None})


class LocalPlans(Checkout):
    def test_never_verified(self):
        self.assertEqual(self.stale({'schema_version': 1, 'jobs': {}}), sorted(ci_paths.LOCAL_JOBS))

    def test_sample_diffs(self):
        cases = {
            'docs/API.md': [],
            'bindings/python/x.py': ['local-bindings', 'local-real-model'],  # parity smoke runs the bindings
            'scripts/package-native.py': ['local-packaging'],
            'tests/parity.py': ['local-real-model'],
            'bindings/browser/worker.mjs': ['local-browser-wasm', 'local-browser-webgpu', 'local-real-model'],  # bindings/*.mjs
            'scripts/check-reproducibility.py': ['local-reproducibility'],
            'src/laya.cpp': ['local-browser-wasm', 'local-browser-webgpu', 'local-fuzz', 'local-real-model', 'local-reproducibility',
                             'local-sanitizers'],
            'scripts/ci_paths.py': sorted(ci_paths.LOCAL_JOBS),
            'unknown/file.txt': sorted(ci_paths.LOCAL_JOBS),
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                git(self.path, 'reset', '-q', '--hard', self.base)
                head = self.commit(path, {path: f'changed {path}\n'})
                self.assertEqual(self.stale(self.verified(self.base), head), sorted(expected))

    def test_working_tree_changes_count(self):
        state = self.verified(self.base)
        write(self.path, {'src/laya.cpp': 'int dirty;\n'})
        self.assertEqual(self.stale(state, dirty=False), [])
        self.assertIn('local-sanitizers', self.stale(state, dirty=True))
        git(self.path, 'checkout', '-q', '--', 'src/laya.cpp')
        write(self.path, {'bindings/python/new.py': 'x\n'})  # untracked
        self.assertEqual(self.stale(state, dirty=True), ['local-bindings', 'local-real-model'])

    def test_rewritten_history(self):
        other = self.commit('other', {'docs/API.md': '# 2\n'})
        git(self.path, 'reset', '-q', '--hard', self.base)
        head = self.commit('rewritten', {'docs/API.md': '# 3\n'})
        self.assertEqual(self.stale(self.verified(other), head), sorted(ci_paths.LOCAL_JOBS))


def ci_states(stale_jobs=(), real_model=False):
    profiles = ['push'] + (['real_model'] if real_model else [])
    return {workflow: {job: {'stale': job in stale_jobs, 'base': None, 'reason': ''}
                       for profile in profiles for job in ci_paths.jobs_of(workflow, profile)} for workflow in ci_paths.WORKFLOWS}


def local_states(stale_jobs=()):
    return {job: {'stale': job in stale_jobs, 'base': None, 'reason': ''} for job in ci_paths.LOCAL_JOBS}


class MakePlan(unittest.TestCase):
    def plan(self, **kwargs):
        defaults = dict(local_states=local_states(), ci_states=ci_states())
        defaults.update(kwargs)
        return verify.make_plan(**defaults)

    def test_nothing_stale(self):
        self.assertEqual(self.plan(), {'local': [], 'dispatch': {}, 'not_run': {}, 'notes': [], 'failures': []})

    def test_stale_local_and_ci(self):
        plan = self.plan(local_states=local_states(['local-sanitizers', 'local-fuzz']),
                         ci_states=ci_states(['generic-windows-x64', 'generic-macos-arm64']))
        self.assertEqual(plan['local'], ['local-sanitizers', 'local-fuzz'])
        self.assertEqual(plan['dispatch'], {'native.yml': 'affected', 'platforms.yml': 'affected'})
        plan = self.plan(ci_states=ci_states(['sanitizers']))
        self.assertEqual(plan['dispatch'], {'native.yml': 'affected'})  # platforms.yml only when a macOS job is stale

    def test_sides(self):
        kwargs = dict(local_states=local_states(['local-fuzz']), ci_states=ci_states(['sanitizers']))
        self.assertEqual(self.plan(side='local', **kwargs)['dispatch'], {})
        self.assertEqual(self.plan(side='ci', **kwargs)['local'], [])

    def test_all(self):
        plan = self.plan(all_jobs=True, emsdk='/emsdk', real_model=True, model='/m.gguf')
        self.assertEqual(plan['local'], list(ci_paths.LOCAL_JOBS))
        self.assertEqual(plan['dispatch'], {'native.yml': 'all', 'platforms.yml': 'all'})
        plan = self.plan(all_jobs=True)
        self.assertEqual(set(plan['not_run']), {'local-real-model', 'local-browser-wasm', 'local-browser-webgpu'})
        self.assertEqual(plan['failures'], [])

    def test_only(self):
        plan = self.plan(only=['local-fuzz', 'generic-windows-x64', 'bindings-real-linux-x64', 'generic-macos-arm64'])
        self.assertEqual(plan['local'], ['local-fuzz'])
        self.assertEqual(plan['dispatch'], {'native.yml': 'generic-windows-x64,bindings-real-linux-x64',
                                            'platforms.yml': 'generic-macos-arm64'})
        with self.assertRaises(ValueError):
            self.plan(only=['no-such-job'])

    def test_real_model_without_a_model(self):
        plan = self.plan(local_states=local_states(['local-real-model']), real_model=True, model=None, model_note='not found')
        self.assertEqual(plan['failures'], ['NOT RUN: real_model'])
        self.assertIn('local-real-model', plan['not_run'])
        plan = self.plan(local_states=local_states(['local-real-model']), ci_states=ci_states(['native-linux-x64'], real_model=True))
        self.assertEqual(plan['failures'], [])
        self.assertEqual(plan['not_run'], {'local-real-model': 'real-model job: add --real-model'})
        self.assertEqual(plan['notes'], ['real-model jobs not requested: add --real-model'])

    def test_browser_needs_emsdk(self):
        plan = self.plan(local_states=local_states(['local-browser-wasm']))
        self.assertEqual(plan['local'], [])
        self.assertIn('--emsdk', plan['not_run']['local-browser-wasm'])
        self.assertEqual(self.plan(local_states=local_states(['local-browser-wasm']), emsdk='/e')['local'], ['local-browser-wasm'])


class Commands(unittest.TestCase):
    def test_local_commands_and_outputs(self):
        root = pathlib.Path('/r')
        for job in ci_paths.LOCAL_JOBS:
            with self.subTest(job=job):
                commands = verify.local_commands(job, root, model='/m.gguf', emsdk='/e')
                self.assertTrue(commands)
                flat = [part for command in commands for part in command]
                if job.startswith('local-browser-'):
                    self.assertIn(f'/r/build-browser-{job.rsplit("-", 1)[1]}', flat)
                elif job != 'local-sanitizers':
                    self.assertIn(f'/r/build-verify/{job}', flat)
        bindings = verify.local_commands('local-bindings', root)[0]
        self.assertEqual(bindings[bindings.index('--bindings') + 1], 'all')
        self.assertIn('--library-only', bindings)
        self.assertIn('--packages', verify.local_commands('local-packaging', root)[0])
        real = verify.local_commands('local-real-model', root, model='/m.gguf')[0]
        self.assertEqual(real[real.index('--parity') + 1], 'smoke')
        self.assertIn('--upstream-reference', real)
        fuzz = verify.local_commands('local-fuzz', root)
        self.assertEqual(fuzz[0], ['cmake', '--preset', 'fuzz'])
        self.assertEqual(fuzz[1][fuzz[1].index('--seconds') + 1], '60')
        self.assertEqual(verify.local_commands('local-sanitizers', root)[-1], ['ctest', '--preset', 'sanitize'])


class ModelDiscovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name) / 'root'
        write(self.root, {'models/manifest.json': FILES['models/manifest.json']})
        self.model = pathlib.Path(self.tmp.name) / 'm.gguf'
        self.model.write_text('gguf')

    def tearDown(self):
        self.tmp.cleanup()

    def test_order(self):
        env = {'HOME': self.tmp.name}
        self.assertEqual(verify.discover_model(self.model, self.root, env), (self.model, '--model'))
        self.assertEqual(verify.discover_model(None, self.root, dict(env, DUCKTAPE_TEST_MODEL=str(self.model)))[0], self.model)
        path, note = verify.discover_model(None, self.root, env)
        self.assertIsNone(path)
        self.assertIn('Hugging Face cache', note)
        cached = pathlib.Path(self.tmp.name) / '.cache/huggingface/hub/models--ggml-org--Laya-GGUF/snapshots/abc123/Laya-Q8_0.gguf'
        cached.parent.mkdir(parents=True)
        cached.write_text('gguf')
        self.assertEqual(verify.discover_model(None, self.root, env), (cached, 'the Hugging Face cache'))
        moved = pathlib.Path(self.tmp.name) / 'hf'
        self.assertEqual(verify.pinned_cache_path(self.root, dict(env, HF_HOME=str(moved))),
                         moved / 'hub/models--ggml-org--Laya-GGUF/snapshots/abc123/Laya-Q8_0.gguf')
        self.assertEqual(verify.pinned_cache_path(self.root, dict(env, HF_HUB_CACHE=str(moved))),
                         moved / 'models--ggml-org--Laya-GGUF/snapshots/abc123/Laya-Q8_0.gguf')
        self.assertIsNone(verify.discover_model(pathlib.Path(self.tmp.name) / 'missing.gguf', self.root, env)[0])
        self.assertIsNone(verify.discover_model(None, self.root, dict(env, DUCKTAPE_TEST_MODEL='/missing.gguf'))[0])

    def test_pinned_manifest_is_the_repository_s(self):
        manifest = json.loads((ROOT / 'models/manifest.json').read_text())
        self.assertTrue(str(verify.pinned_cache_path(ROOT, {'HOME': '/h'})).endswith(
            f'snapshots/{manifest["revision"]}/{manifest["filename"]}'))


class Preconditions(Checkout):
    def setUp(self):
        super().setUp()
        self.remote = pathlib.Path(self.tmp.name) / 'remote.git'
        git(self.tmp.name, 'init', '-q', '--bare', str(self.remote))
        git(self.path, 'remote', 'add', 'origin', str(self.remote))

    def problems(self):
        return verify.ci_preconditions(self.git, self.path)[1]

    def test_pushed_clean_head(self):
        git(self.path, 'push', '-q', 'origin', 'topic')
        self.assertEqual(verify.ci_preconditions(self.git, self.path), ('topic', []))

    def test_unpushed_head(self):
        git(self.path, 'push', '-q', 'origin', 'topic')
        self.commit('later', {'docs/API.md': '# later\n'})
        self.assertTrue(any('not pushed' in problem for problem in self.problems()))
        git(self.path, 'push', '-q', 'origin', 'topic')
        self.assertEqual(self.problems(), [])

    def test_never_pushed_branch(self):
        self.assertTrue(any('missing' in problem for problem in self.problems()))

    def test_dirty_tree(self):
        git(self.path, 'push', '-q', 'origin', 'topic')
        write(self.path, {'src/laya.cpp': 'int dirty;\n'})
        self.assertTrue(any('changes' in problem for problem in self.problems()))
        git(self.path, 'checkout', '-q', '--', 'src/laya.cpp')
        write(self.path, {'src/new.cpp': 'int n;\n'})
        self.assertTrue(any('changes' in problem for problem in self.problems()))

    def test_detached_head(self):
        git(self.path, 'checkout', '-q', '--detach')
        self.assertTrue(any('detached' in problem for problem in self.problems()))

    def test_main_refuses_before_dispatching(self):
        """--ci with a dirty tree exits 2 without dispatching (dispatch is never reached)."""
        write(self.path, {'src/laya.cpp': 'int dirty;\n'})
        called = []
        saved = verify.dispatch, verify.ci_staleness
        verify.dispatch = lambda *args: called.append(args)
        verify.ci_staleness = lambda git, records, head, real_model: ci_states(['generic-windows-x64'])
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(verify.main(['--ci'], root=self.path), 2)
        finally:
            verify.dispatch, verify.ci_staleness = saved
        self.assertEqual(called, [])
        self.assertIn('cannot dispatch', err.getvalue())

    def test_rerun_failed_needs_the_same_head(self):
        path = verify.state_path(self.path)
        verify.save_state(path, {'schema_version': 1, 'jobs': {}, 'last': {'head': self.base, 'local_failed': ['local-fuzz']}})
        self.commit('moved', {'docs/API.md': '# moved\n'})
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(verify.main(['--rerun-failed'], root=self.path), 2)
        self.assertIn('not of this HEAD', err.getvalue())


class Output(unittest.TestCase):
    def test_timing_table(self):
        table = verify.timing_table([('generic-linux-x64', 'ci', 'passed', 70.0), ('generic-windows-x64', 'ci', 'FAILURE', 200.0),
                                     ('local-fuzz', 'local', 'passed', None)],
                                    [('native.yml run 1', 290.0, 'verify-ci-model-free'), ('native.yml run 2', 500.0, 'real-model-dispatch')])
        lines = table.splitlines()
        self.assertNotIn('OVER BUDGET', lines[1])
        self.assertIn('OVER BUDGET', lines[2])
        self.assertNotIn('OVER BUDGET', lines[3])
        self.assertNotIn('OVER BUDGET', lines[4])
        self.assertIn('OVER BUDGET', lines[5])

    def test_ci_results_from_gh_run_view(self):
        view = {'status': 'completed', 'conclusion': 'failure', 'jobs': [
            {'name': 'changes', 'conclusion': 'success', 'startedAt': '2026-10-25T05:00:00Z', 'completedAt': '2026-10-25T05:00:20Z'},
            {'name': 'generic-windows-x64', 'conclusion': 'failure', 'startedAt': '2026-10-25T05:00:30Z',
             'completedAt': '2026-10-25T05:02:30Z'},
            {'name': 'sanitizers', 'conclusion': 'skipped', 'startedAt': None, 'completedAt': None}]}
        self.assertEqual(verify.ci_results(view), [('changes', 'success', 20.0), ('generic-windows-x64', 'failure', 120.0)])


if __name__ == '__main__':
    unittest.main()
