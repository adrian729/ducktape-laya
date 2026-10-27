#!/usr/bin/env python3
"""scripts/ci-changes.py and scripts/ci_paths.py: per-job staleness, trust rules, closure (offline: temporary git repositories, fake API)."""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import ci_paths  # noqa: E402

spec = importlib.util.spec_from_file_location('ci_changes', ROOT / 'scripts/ci-changes.py')
ci_changes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci_changes)

REPO = 'adrian729/ducktape-laya'
GIT_ENV = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.invalid', 'GIT_COMMITTER_NAME': 't',
           'GIT_COMMITTER_EMAIL': 't@example.invalid', 'GIT_AUTHOR_DATE': '2026-01-01T00:00:00Z', 'GIT_COMMITTER_DATE': '2026-01-01T00:00:00Z',
           'GIT_CONFIG_NOSYSTEM': '1', 'HOME': '/nonexistent'}
BASE_FILES = {'src/laya.cpp': 'int x;\n', 'include/ducktape_laya.h': '/* h */\n', 'docs/API.md': '# API\n', 'README.md': '# r\n',
              'bindings/python/src/ducktape_laya/__init__.py': '', 'bindings/node/index.cjs': '', 'scripts/package-native.py': '',
              'tests/parity.py': '', 'scripts/ci_paths.py': '', 'tests/release-targets.py': '', 'CMakeLists.txt': ''}


class Repo:
    """A repository built through the index only: commit(parents, changes) with explicit parents (merges, octopus, rewrites)."""

    def __init__(self, directory):
        self.path = pathlib.Path(directory)
        self.env = dict(os.environ, **GIT_ENV, GIT_INDEX_FILE=str(self.path / 'scratch-index'))
        self.git('init', '-q', '-b', 'main')
        self.counter = 0

    def git(self, *args, input=None):
        return subprocess.run(['git', '-C', str(self.path), *args], env=self.env, input=input, capture_output=True, text=True,
                              check=True).stdout.strip()

    def commit(self, parents, changes):
        self.counter += 1
        if parents:
            self.git('read-tree', parents[0])
        else:
            self.git('read-tree', '--empty')
        for path, content in changes.items():
            if content is None:
                self.git('update-index', '--force-remove', path)
            else:
                blob = self.git('hash-object', '-w', '--stdin', input=content)
                self.git('update-index', '--add', '--cacheinfo', f'100644,{blob},{path}')
        tree = self.git('write-tree')
        return self.git('commit-tree', tree, *[arg for parent in parents for arg in ('-p', parent)], '-m', f'c{self.counter}')


class FakeAPI:
    def __init__(self):
        self.runs, self.jobs, self.fail, self.calls, self.next_id = {}, {}, False, 0, 1

    def record(self, sha, names, *, event='push', workflow='native.yml', conclusion='success', repo=REPO, head_repo=REPO, head_sha=None):
        run_id = self.next_id
        self.next_id += 1
        self.runs.setdefault(sha, []).append({'id': run_id, 'head_sha': head_sha or sha, 'event': event,
                                              'path': f'.github/workflows/{workflow}', 'repository': {'full_name': repo},
                                              'head_repository': {'full_name': head_repo}})
        self.jobs[run_id] = [{'name': name, 'conclusion': conclusion, 'head_sha': head_sha or sha} for name in names]
        return run_id

    def runs_for(self, sha):
        self.calls += 1
        if self.fail:
            raise ci_changes.Unavailable('rate limited')
        return self.runs.get(sha, [])

    def jobs_for(self, run_id):
        self.calls += 1
        if self.fail:
            raise ci_changes.Unavailable('rate limited')
        return self.jobs[run_id]


PUSH = ci_paths.jobs_of('native.yml', 'push')
REAL = ci_paths.jobs_of('native.yml', 'real_model')


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(self.tmp.name)
        self.git = ci_changes.Git(self.repo.path)
        self.api = FakeAPI()
        self.root = self.repo.commit([], BASE_FILES)

    def tearDown(self):
        self.tmp.cleanup()

    def records(self, workflow='native.yml'):
        return ci_changes.Records(self.api, REPO, workflow)

    def decide(self, head, *, event='push', ref='refs/heads/topic', workflow='native.yml', **kwargs):
        return ci_changes.decide(git=self.git, records=self.records(workflow), workflow=workflow, event=event, ref=ref, head=head, **kwargs)

    def verify_all(self, sha, jobs=PUSH, **kwargs):
        self.api.record(sha, jobs, **kwargs)


class Categories(Base):
    def test_tables_are_consistent(self):
        self.assertEqual(ci_paths.self_check(), [])

    def test_every_category_selects_its_jobs(self):
        cases = {
            'docs/BUILDING.md': {'docs', 'policy'}, 'src/laya.cpp': {'native', 'sanitizers', 'fuzz', 'windows', 'arm64', 'clang', 'macos',
                                                                     'reproducibility', 'real_model', 'real_model_native', 'browser'},
            'tests/abi-layout.py': {'abi', 'policy', 'windows_policy'},
            'tests/packaging-identity.py': {'packaging_identity', 'policy', 'windows_policy'},
            'scripts/package-native.py': {'packaging', 'policy', 'windows_policy'}, 'scripts/check-sanitizers.py': {'sanitizers', 'policy'},
            'tests/input-libfuzzer.cpp': {'fuzz', 'native', 'sanitizers', 'windows', 'arm64', 'clang', 'macos', 'real_model',
                                          'real_model_native', 'browser'},
            'scripts/fetch-node-sdk.py': {'windows', 'reproducibility'}, 'src/exports.macos': None,
            'scripts/check-reproducibility.py': {'reproducibility'}, 'tests/parity.py': {'real_model'},
            'bindings/browser/worker.mjs': None, 'scripts/check-stress.py': {'host'}, 'scripts/verify.py': {'tooling', 'policy'},
            '.github/workflows/native.yml': None, 'scripts/ci_paths.py': None,
            '.github/workflows/publish-release.yml': {'policy'}, '.github/workflows/attest-release.yml': {'policy'},
        }
        for lang in ci_paths.LANGUAGES:
            cases[f'bindings/{lang}/x'] = None
        for path, expected in cases.items():
            with self.subTest(path=path):
                found = ci_paths.categories_of(path)
                self.assertTrue(found, path)
                if expected is not None:
                    self.assertLessEqual(expected, found)
                    self.assertLessEqual(found - expected, {'policy'})
        for lang in ci_paths.LANGUAGES:
            self.assertIn(f'binding_{lang}', ci_paths.categories_of(f'bindings/{lang}/x'))
        self.assertIn('macos', ci_paths.categories_of('src/exports.macos'))
        self.assertIn('browser', ci_paths.categories_of('bindings/browser/worker.mjs'))
        self.assertIn('ci', ci_paths.categories_of('.github/workflows/native.yml'))
        self.assertEqual(ci_paths.touched_categories(['scripts/ci_paths.py']), {ci_paths.EVERYTHING})

    def test_every_job_category_has_a_stale_example(self):
        """Every category a job names is touched by some tracked path, so no job is stale only through `ci`."""
        files = subprocess.run(['git', '-C', str(ROOT), 'ls-files'], capture_output=True, text=True, check=True).stdout.split()
        covered = set().union(*(ci_paths.categories_of(path) for path in files))
        for name, job in ci_paths.JOBS.items():
            with self.subTest(job=name):
                self.assertLessEqual(set(job['categories']), covered)

    def test_every_tracked_path_is_classified(self):
        """A path in no category reruns every job; a new file needs a category (scripts/ci_paths.py)."""
        files = subprocess.run(['git', '-C', str(ROOT), 'ls-files'], capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual([path for path in files if not ci_paths.categories_of(path)], [])

    def test_unmatched_and_ci_paths_touch_everything(self):
        self.assertEqual(ci_paths.touched_categories(['newdir/file.txt']), {ci_paths.EVERYTHING})
        self.assertEqual(ci_paths.touched_categories(['docs/API.md', 'newdir/file.txt']), {ci_paths.EVERYTHING})
        for path in ('.github/actions/setup-llvm/action.yml', 'scripts/ci-changes.py', '.github/workflows/native.yml'):
            self.assertEqual(ci_paths.touched_categories([path]), {ci_paths.EVERYTHING})
        # The classifier's own test and the release workflows are policy inputs only.
        for path in ('tests/ci-changes.py', '.github/workflows/publish-release.yml'):
            self.assertEqual(ci_paths.touched_categories([path]), {'policy'})
        self.assertEqual(ci_paths.touched_categories(None), {ci_paths.EVERYTHING})

    def test_policy_selection(self):
        self.assertEqual(ci_paths.policy_checks_touched(['tests/docs-links.py']), ['docs-links', 'docs-links-self-test', 'ci-changes'])
        docs = ci_paths.policy_checks_touched(['docs/API.md'])
        for check in ('docs-links', 'qualification-policy', 'release-notes'):
            self.assertIn(check, docs)
        self.assertNotIn('docs-links-self-test', docs)
        self.assertNotIn('abi-layouts', docs)
        self.assertEqual(ci_paths.policy_checks_touched(['src/laya.cpp']), [])
        self.assertIn('docs-links', ci_paths.policy_checks_touched(['src/old.cpp'], deleted=['src/old.cpp']))
        self.assertEqual(ci_paths.policy_checks_touched(['scripts/ci_paths.py']), list(ci_paths.POLICY_CHECKS))
        self.assertEqual(ci_paths.policy_checks_touched(None), list(ci_paths.POLICY_CHECKS))
        self.assertIn('abi-layouts', ci_paths.policy_checks_touched(['include/ducktape_laya.h']))

    def test_validate_checks_cover_the_shard_checks_and_policy_labels(self):
        for mode in ci_paths.SHARDS.values():
            for platform in mode.values():
                for checks in platform['jobs'].values():
                    for check in checks:
                        self.assertIn(check, ci_paths.VALIDATE_CHECKS)
        for label in ci_paths.POLICY_CHECKS:
            self.assertIn(label, ci_paths.VALIDATE_CHECKS)
            self.assertIn(f'policy:{label}', ci_paths.VALIDATE_CHECKS)

    def test_shards_contract(self):
        candidate = ci_paths.SHARDS['candidate']
        full = {'ctest', 'references', 'parity:full-a', 'parity:full-b', 'bindings', 'packages', 'policy-platform'}
        compiler = {'ctest', 'references', 'parity:full-a', 'parity:full-b', 'bindings'}
        for platform, shards in candidate.items():
            checks = [c for job in shards['jobs'].values() for c in job]
            self.assertEqual(len(checks), len(set(checks)), platform)
            # The binding suites in one shard, or bindings:a and bindings:b (both) covering them.
            if {'bindings:a', 'bindings:b'} <= set(checks):
                checks = [c for c in checks if not c.startswith('bindings:')] + ['bindings']
            self.assertEqual(set(checks), full if shards['profile'] == 'ci-full' else compiler, platform)
            self.assertEqual(shards['side_outputs'], {'native-linux-x64': ['cross-host']} if platform == 'linux-x64' else {}, platform)
        self.assertEqual(candidate['linux-x64-clang']['profile'], 'ci-compiler')
        for platform, shards in ci_paths.SHARDS['real_model'].items():
            self.assertNotIn('profile', shards)
            self.assertEqual(shards['side_outputs'], {})
        self.assertEqual(list(ci_paths.SHARDS['real_model']['linux-x64-clang']['jobs']), ['native-linux-x64-clang', 'references-linux-x64-clang'])

    def test_packages_have_per_profile_needs(self):
        job = ci_paths.JOBS['packages-linux-x64']
        self.assertEqual(job['profiles'], ['push', 'candidate'])
        self.assertEqual(job['needs'], {'push': ['changes'], 'candidate': ['build-linux-x64']})
        self.assertEqual(job['artifact_needs'], {'push': [], 'candidate': ['build-linux-x64']})
        self.assertEqual(job['rebuild_with'], [])
        self.assertEqual(ci_paths.JOBS['reproducibility-windows-x64']['needs'], {'push': ['changes'], 'candidate': ['changes']})
        self.assertEqual(ci_paths.JOBS['assemble']['rebuild_with'], ['packages-linux-x64', 'packages-linux-arm64', 'packages-windows-x64'])
        for name in ('assembled-linux-x64', 'assembled-linux-arm64', 'assembled-windows-x64', 'musl'):
            self.assertEqual(ci_paths.JOBS[name]['rebuild_with'], ['assemble'])
        others = [name for name, job in ci_paths.JOBS.items() if job['rebuild_with'] and name not in
                  ('assemble', 'assembled-linux-x64', 'assembled-linux-arm64', 'assembled-windows-x64', 'musl')]
        self.assertEqual(others, [], 'only the packaging chain sets rebuild_with')


class PolicyStale(unittest.TestCase):
    """check-policy.py --stale in CI: the checks touched since $CI_POLICY_BASE (ci-changes.py's base_policy)."""

    def test_ci_policy_base(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        spec = importlib.util.spec_from_file_location('check_policy_stale', ROOT / 'scripts/check-policy.py')
        check_policy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(check_policy)
        everything = list(check_policy.COMMANDS)
        self.assertEqual(check_policy.stale_checks({'CI_POLICY_BASE': ''})[0], everything)  # no verified base
        self.assertEqual(check_policy.stale_checks({'CI_POLICY_BASE': '0' * 40})[0], everything)  # git fails: fail open
        head = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(check_policy.stale_checks({'CI_POLICY_BASE': head})[0], [])


class PolicyImports(unittest.TestCase):
    """Each policy check's inputs cover the scripts and helper modules its entry file loads, transitively."""
    MODULE = re.compile(r'^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)', re.M)
    PATH = re.compile(r"(?:ROOT|HERE|root)\s*/\s*'((?:scripts|tests)/[A-Za-z0-9_./-]+\.py)'")

    def loads(self, path):
        text = (ROOT / path).read_text()
        found = set()
        for name in self.MODULE.findall(text):
            for directory in ('scripts', 'tests'):
                if (ROOT / directory / f'{name}.py').is_file():
                    found.add(f'{directory}/{name}.py')
        for match in self.PATH.findall(text):
            if (ROOT / match).is_file():
                found.add(match)
        return found

    def test_inputs_cover_imports(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        spec = importlib.util.spec_from_file_location('check_policy', ROOT / 'scripts/check-policy.py')
        check_policy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(check_policy)
        self.assertEqual(set(check_policy.COMMANDS), set(ci_paths.POLICY_CHECKS))
        for label, command in check_policy.COMMANDS.items():
            entry = next((part for part in command[1:] if isinstance(part, str) and part.startswith(('tests/', 'bindings/'))), None)
            if not entry or not (ROOT / entry).is_file() or not entry.endswith('.py'):
                continue
            pending, seen = [entry], set()
            while pending:
                path = pending.pop()
                if path in seen:
                    continue
                seen.add(path)
                pending += [found for found in self.loads(path) if found.startswith('scripts/')]
                pending += [found for found in self.loads(path) if found.startswith('tests/') and found.endswith('.py')
                            and found != path and '_' in pathlib.Path(found).stem]  # helper modules, not other test scripts
            uncovered = sorted(path for path in seen if path.endswith('.py') and not ci_paths.matches(path, ci_paths.POLICY_CHECKS[label]))
            with self.subTest(check=label):
                self.assertEqual(uncovered, [])


class JobImports(PolicyImports):
    """Each push or real-model CI job's categories cover the scripts its steps run and what they load, transitively.

    The candidate and qualify profiles run every job, so only these two profiles depend on the categories. A script
    missing here lets a change to it pass verify.py without the job that runs it (validate.py and release_support.py
    were missing from every binding and packaging job)."""
    RUN = re.compile(r'(?<![\w/.-])((?:scripts|tests)/[A-Za-z0-9_./-]+\.(?:py|sh|mjs|cjs))')

    def test_categories_cover_what_jobs_run(self):
        spec = importlib.util.spec_from_file_location('ci_contracts', ROOT / 'tests/ci-contracts.py')
        contracts = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(contracts)
        for workflow in contracts.WORKFLOWS:
            path = ROOT / '.github/workflows' / workflow
            jobs, blocks = contracts.read_jobs(path, []), contracts.job_blocks(path.read_text())
            for job_id, lines in blocks.items():
                pending = [p for p in self.RUN.findall('\n'.join(map(contracts.strip_comment, lines))) if (ROOT / p).is_file()]
                seen = self.closure(pending)
                names = contracts.served(workflow, job_id, jobs[job_id], ci_paths.JOBS, ci_paths.SHARDS, [])
                cross_host = {job for shards in ci_paths.SHARDS.values() for platform in shards.values()
                              for job, outputs in platform['side_outputs'].items() if 'cross-host' in outputs}
                for name, profiles in names.items():
                    if not set(profiles or ci_paths.JOBS[name]['profiles']) & {'push', 'real_model'}:
                        continue
                    # The shard jobs share one definition; the cross-host step runs only in the side-output shards.
                    seen_here = seen if name in cross_host else seen - {'tests/cross-host.py'}
                    globs = [glob for category in ci_paths.JOBS[name]['categories'] + ['ci'] for glob in ci_paths.CATEGORIES[category]]
                    with self.subTest(workflow=workflow, job=name):
                        self.assertEqual(sorted(path for path in seen_here if not ci_paths.matches(path, globs)), [])

    def test_categories_cover_what_local_jobs_run(self):
        spec = importlib.util.spec_from_file_location('verify_under_test', ROOT / 'scripts/verify.py')
        verify = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verify)
        for job, entry in ci_paths.LOCAL_JOBS.items():
            pending = [part for command in verify.local_commands(job, ROOT, model='MODEL', emsdk='EMSDK') for part in command
                       if part.startswith(('scripts/', 'tests/')) and (ROOT / part).is_file()]
            seen = self.closure(pending)
            globs = [glob for category in entry['categories'] + ['ci'] for glob in ci_paths.CATEGORIES[category]]
            with self.subTest(job=job):
                self.assertEqual(sorted(path for path in seen if not ci_paths.matches(path, globs)), [])

    def closure(self, pending):
        """The scripts and what they import, transitively (imports only: validate.py names every check's script by path)."""
        seen = set()
        while pending:
            script = pending.pop()
            if script not in seen:
                seen.add(script)
                if script.endswith('.py'):
                    text = (ROOT / script).read_text()
                    pending += [f'{directory}/{name}.py' for name in self.MODULE.findall(text) for directory in ('scripts', 'tests')
                                if (ROOT / directory / f'{name}.py').is_file()]
        return seen


class BranchPush(Base):
    def test_native_edit_runs_the_generic_job_only(self):
        self.verify_all(self.root)
        head = self.repo.commit([self.root], {'src/laya.cpp': 'int y;\n'})
        decision = self.decide(head)
        self.assertEqual(decision['run'], ['generic-linux-x64'])
        self.assertFalse(decision['skip_all'])
        self.assertEqual(decision['bases']['generic-linux-x64'], self.root)
        self.assertEqual(decision['categories'], {'generic-linux-x64': ['native']})

    def test_docs_only_runs_the_policy_checks_of_docs(self):
        self.verify_all(self.root)
        head = self.repo.commit([self.root], {'docs/API.md': '# API 2\n'})
        decision = self.decide(head)
        self.assertEqual(decision['run'], ['policy'])
        self.assertTrue(decision['docs_only'])
        self.assertIn('docs-links', decision['policy_checks'])
        self.assertNotIn('docs-links-self-test', decision['policy_checks'])
        out = ci_changes.outputs(decision)
        self.assertEqual(json.loads(out['run']), ['policy'])
        self.assertTrue(all(':' not in key for key in out))

    def test_binding_only_runs_nothing_on_push(self):
        self.verify_all(self.root)
        head = self.repo.commit([self.root], {'bindings/python/src/ducktape_laya/__init__.py': 'x = 1\n'})
        self.assertEqual(self.decide(head)['run'], [])

    def test_never_verified_is_stale(self):
        head = self.repo.commit([self.root], {'docs/API.md': '# 2\n'})
        decision = self.decide(head)
        self.assertEqual(decision['run'], ['policy', 'generic-linux-x64'])
        self.assertEqual(decision['policy_checks'], list(ci_paths.POLICY_CHECKS))

    def test_platforms_branch_push_runs_nothing(self):
        head = self.repo.commit([self.root], {'src/laya.cpp': 'int y;\n'})
        self.assertEqual(self.decide(head, workflow='platforms.yml')['run'], [])

    def test_partial_dispatch_counts_only_for_its_jobs(self):
        middle = self.repo.commit([self.root], {'src/laya.cpp': 'int y;\n'})
        self.api.record(middle, ['policy'], event='workflow_dispatch')  # e.g. scope=policy
        self.verify_all(self.root)
        head = self.repo.commit([middle], {'docs/API.md': '# 2\n'})
        decision = self.decide(head)
        self.assertEqual(decision['bases']['policy'], middle)
        self.assertEqual(decision['bases']['generic-linux-x64'], self.root)
        self.assertEqual(decision['run'], ['policy', 'generic-linux-x64'])  # generic: src changed since root

    def test_untrusted_runs_are_ignored(self):
        cases = {'pull_request': dict(event='pull_request'), 'other workflow': dict(workflow='platforms.yml'),
                 'fork head': dict(head_repo='someone/fork'), 'other repository': dict(repo='someone/fork'),
                 'failed job': dict(conclusion='failure'), 'skipped job': dict(conclusion='skipped'),
                 'other head': dict(head_sha='f' * 40), 'dynamic event': dict(event='dynamic')}
        for name, kwargs in cases.items():
            with self.subTest(case=name):
                self.api = FakeAPI()
                self.api.record(self.root, PUSH, **kwargs)
                head = self.repo.commit([self.root], {'docs/API.md': f'# {name}\n'})
                self.assertEqual(self.decide(head)['run'], ['policy', 'generic-linux-x64'])

    def test_trusted_events(self):
        for event in ('push', 'schedule', 'workflow_dispatch'):
            with self.subTest(event=event):
                self.api = FakeAPI()
                self.api.record(self.root, PUSH, event=event)
                head = self.repo.commit([self.root], {'docs/API.md': f'# {event}\n'})
                self.assertEqual(self.decide(head)['run'], ['policy'])

    def test_force_push_base_not_an_ancestor(self):
        rewritten = self.repo.commit([self.root], {'src/laya.cpp': 'int old;\n'})
        self.verify_all(rewritten)
        head = self.repo.commit([self.root], {'docs/API.md': '# new\n'})  # the branch was rewritten from root
        self.assertEqual(self.decide(head)['bases']['generic-linux-x64'], '')
        self.assertEqual(self.decide(head)['run'], ['policy', 'generic-linux-x64'])

    def test_base_beyond_the_walk_limit(self):
        self.verify_all(self.root)
        head = self.root
        for index in range(ci_changes.WALK_LIMIT):
            head = self.repo.commit([head], {'docs/API.md': f'# {index}\n'})
        self.assertEqual(self.decide(head)['run'], ['policy'])  # root is the 50th ancestor: still found
        head = self.repo.commit([head], {'docs/API.md': '# one more\n'})
        self.assertEqual(self.decide(head)['run'], ['policy', 'generic-linux-x64'])

    def test_api_failure_fails_open(self):
        self.verify_all(self.root)
        head = self.repo.commit([self.root], {'docs/API.md': '# 2\n'})
        self.api.fail = True
        decision = self.decide(head)
        self.assertEqual(decision['run'], ['policy', 'generic-linux-x64'])
        self.assertEqual(decision['policy_checks'], list(ci_paths.POLICY_CHECKS))
        self.assertTrue(any('fail open' in note for note in decision['notes']))

    def test_git_failure_fails_open(self):
        self.verify_all(self.root)
        decision = self.decide('0123456789abcdef0123456789abcdef01234567')
        self.assertEqual(decision['run'], ci_changes.ordered(PUSH))  # parents unknown: the full push profile

    def test_unmatched_path_and_classifier_change(self):
        for path in ('newdir/file.txt', 'scripts/ci_paths.py', '.github/workflows/native.yml'):
            with self.subTest(path=path):
                self.api = FakeAPI()
                self.verify_all(self.root)
                head = self.repo.commit([self.root], {path: f'{path}\n'})
                decision = self.decide(head)
                self.assertEqual(decision['run'], ['policy', 'generic-linux-x64'])
                self.assertEqual(decision['policy_checks'], list(ci_paths.POLICY_CHECKS))
                dispatch = self.decide(head, event='workflow_dispatch', real_model=True)
                self.assertEqual(dispatch['run'], ci_changes.ordered(set(PUSH) | set(REAL)))

    def test_head_itself_verified(self):
        head = self.repo.commit([self.root], {'src/laya.cpp': 'int y;\n'})
        self.verify_all(head, ['generic-linux-x64'], event='workflow_dispatch')
        self.assertEqual(self.decide(head)['run'], ['policy'])

    def test_deleted_path_selects_docs_links(self):
        self.verify_all(self.root)
        head = self.repo.commit([self.root], {'scripts/package-native.py': None})
        decision = self.decide(head)
        self.assertIn('policy', decision['run'])
        self.assertIn('docs-links', decision['policy_checks'])

    def test_pull_request_runs_branch_jobs_unconditionally(self):
        self.verify_all(self.root)
        head = self.repo.commit([self.root], {'docs/API.md': '# 2\n'})
        self.assertEqual(self.decide(head, event='pull_request', ref='refs/pull/1/merge')['run'], ['policy', 'generic-linux-x64'])


class MainAndMerges(Base):
    def test_main_push_runs_everything_for_a_unverified_parent(self):
        head = self.repo.commit([self.root], {'docs/API.md': '# 2\n'})
        self.api.record(self.root, ['generic-linux-x64'])  # the parent is not fully verified
        decision = self.decide(head, ref='refs/heads/main')
        self.assertEqual(decision['run'], ci_changes.ordered(PUSH))
        self.assertFalse(decision['skip_all'])

    def test_docs_only_main_push_runs_only_the_policy_checks(self):
        """Commit 350171b: docs and AGENTS.md on a fully verified tree ran the whole push profile (4.9 min)."""
        self.verify_all(self.root)
        head = self.repo.commit([self.root], {'docs/API.md': '# 2\n', 'AGENTS.md': 'x\n', 'docs/NEW.md': 'n\n'})
        decision = self.decide(head, ref='refs/heads/main')
        self.assertEqual(decision['run'], ['policy'])
        self.assertFalse(decision['skip_all'])
        self.assertEqual(decision['policy_checks'], ci_paths.policy_checks_touched(['docs/API.md', 'AGENTS.md', 'docs/NEW.md']))
        self.assertNotEqual(decision['policy_checks'], list(ci_paths.POLICY_CHECKS))
        self.assertEqual(decision['bases']['policy'], self.root)

    def test_docs_only_fast_path_through_a_staleness_chain(self):
        """The parent was itself a docs-only fast path (only `policy` ran there): the tree is still fully verified."""
        self.verify_all(self.root)
        first = self.repo.commit([self.root], {'docs/API.md': '# 2\n'})
        self.api.record(first, ['policy'])
        second = self.repo.commit([first], {'docs/API.md': '# 3\n'})
        decision = self.decide(second, ref='refs/heads/main')
        self.assertEqual(decision['run'], ['policy'])

    def test_docs_only_fast_path_with_a_merge_commit(self):
        self.verify_all(self.root)
        side = self.repo.commit([self.root], {'docs/SIDE.md': 's\n'})
        merge = self.repo.commit([self.root, side], {'docs/SIDE.md': 's\n'})
        self.assertEqual(self.decide(merge, ref='refs/heads/main')['run'], ['policy'])

    def test_fast_path_deleted_docs_select_docs_links(self):
        self.verify_all(self.root)
        head = self.repo.commit([self.root], {'docs/API.md': None})
        decision = self.decide(head, ref='refs/heads/main')
        self.assertEqual(decision['run'], ['policy'])
        self.assertIn('docs-links', decision['policy_checks'])

    def test_fast_path_is_fail_safe(self):
        """One path beyond documentation (source, an unclassified file, a workflow, the classifier, a package README) keeps the full profile."""
        self.verify_all(self.root)
        for path in ('src/laya.cpp', 'unclassified/thing.xyz', '.github/workflows/native.yml', 'scripts/ci_paths.py',
                     'scripts/ci-changes.py', 'bindings/node/README.md', 'release/compatibility.json'):
            with self.subTest(path=path):
                head = self.repo.commit([self.root], {'docs/API.md': '# 2\n', path: 'changed\n'})
                decision = self.decide(head, ref='refs/heads/main')
                self.assertEqual(decision['run'], ci_changes.ordered(PUSH))
                self.assertFalse(decision['skip_all'])
                self.assertEqual(decision['policy_checks'], list(ci_paths.POLICY_CHECKS))

    def test_fast_path_api_failure_runs_everything(self):
        self.verify_all(self.root)
        head = self.repo.commit([self.root], {'docs/API.md': '# 2\n'})
        self.api.fail = True
        decision = self.decide(head, ref='refs/heads/main')
        self.assertEqual(decision['run'], ci_changes.ordered(PUSH))

    def test_inert_paths(self):
        for path in ('docs/RELEASING.md', 'AGENTS.md', 'README.md', 'CHANGELOG.md'):
            self.assertTrue(ci_paths.is_inert(path), path)
        for path in ('src/laya.cpp', 'unclassified', 'scripts/verify.py', 'bindings/node/README.md', '.github/workflows/native.yml',
                     'scripts/ci_paths.py', 'release/qualification.json'):
            self.assertFalse(ci_paths.is_inert(path), path)

    def test_merge_of_verified_parents_with_a_new_tree_runs_everything(self):
        left = self.repo.commit([self.root], {'src/laya.cpp': 'int left;\n'})
        right = self.repo.commit([self.root], {'bindings/node/index.cjs': 'right\n'})
        self.verify_all(left)
        self.verify_all(right)
        merge = self.repo.commit([left, right], {'src/laya.cpp': 'int left;\n', 'bindings/node/index.cjs': 'right\n'})
        for ref in ('refs/heads/main', 'refs/heads/topic'):
            with self.subTest(ref=ref):
                decision = self.decide(merge, ref=ref)
                self.assertEqual(decision['run'], ci_changes.ordered(PUSH))
                self.assertFalse(decision['skip_all'])

    def test_octopus_merge(self):
        parents = [self.repo.commit([self.root], {f'src/{index}.cpp': 'x\n'}) for index in range(3)]
        for parent in parents:
            self.verify_all(parent)
        merge = self.repo.commit(parents, {'src/0.cpp': 'x\n', 'src/1.cpp': 'x\n', 'src/2.cpp': 'x\n'})
        self.assertEqual(len(self.git.parents(merge)), 3)
        self.assertEqual(self.decide(merge, ref='refs/heads/main')['run'], ci_changes.ordered(PUSH))

    def test_identical_tree_skip(self):
        branch = self.repo.commit([self.root], {'src/laya.cpp': 'int y;\n'})
        self.verify_all(branch)
        merge = self.repo.commit([self.root, branch], {'src/laya.cpp': 'int y;\n'})  # --no-ff merge: the branch's tree
        self.assertEqual(self.git.tree(merge), self.git.tree(branch))
        decision = self.decide(merge, ref='refs/heads/main')
        self.assertTrue(decision['skip_all'])
        self.assertEqual(decision['run'], [])
        self.assertEqual(ci_changes.outputs(decision)['skip_all'], 'true')

    def test_identical_tree_skip_through_staleness_at_p(self):
        """At P every job is not stale: some verified at P, the others at P's parent with no input changed since."""
        self.verify_all(self.root)
        branch = self.repo.commit([self.root], {'src/laya.cpp': 'int y;\n'})
        self.api.record(branch, [job for job in PUSH if 'native' in ci_paths.JOBS[job]['categories'] or job in
                                 ('generic-linux-x64-clang', 'generic-linux-arm64', 'generic-windows-x64', 'sanitizers', 'fuzzer',
                                  'reproducibility-windows-x64')], event='workflow_dispatch')
        merge = self.repo.commit([self.root, branch], {'src/laya.cpp': 'int y;\n'})
        self.assertTrue(self.decide(merge, ref='refs/heads/main')['skip_all'])

    def test_identical_tree_with_one_job_unverified_does_not_skip(self):
        branch = self.repo.commit([self.root], {'src/laya.cpp': 'int y;\n'})
        self.verify_all(branch, [job for job in PUSH if job != 'sanitizers'])
        merge = self.repo.commit([self.root, branch], {'src/laya.cpp': 'int y;\n'})
        decision = self.decide(merge, ref='refs/heads/main')
        self.assertFalse(decision['skip_all'])
        self.assertEqual(decision['run'], ci_changes.ordered(PUSH))

    def test_fast_forward_main_push_needs_every_job_at_head(self):
        head = self.repo.commit([self.root], {'src/laya.cpp': 'int y;\n'})
        self.verify_all(self.root)
        self.api.record(head, ['generic-linux-x64'], event='workflow_dispatch')
        self.assertEqual(self.decide(head, ref='refs/heads/main')['run'], ci_changes.ordered(PUSH))  # never diff-based
        self.verify_all(head, event='workflow_dispatch')
        self.assertTrue(self.decide(head, ref='refs/heads/main')['skip_all'])

    def test_dispatch_on_a_merge_commit(self):
        left = self.repo.commit([self.root], {'src/laya.cpp': 'int left;\n'})
        self.verify_all(left)
        same = self.repo.commit([self.root, left], {'src/laya.cpp': 'int left;\n'})
        self.assertEqual(self.decide(same, event='workflow_dispatch')['run'], [])
        right = self.repo.commit([self.root], {'docs/API.md': '# r\n'})
        merge = self.repo.commit([left, right], {'docs/API.md': '# r\n'})
        self.assertEqual(self.decide(merge, event='workflow_dispatch')['run'], ci_changes.ordered(PUSH))

    def test_identical_tree_skip_with_api_failure_runs_everything(self):
        branch = self.repo.commit([self.root], {'src/laya.cpp': 'int y;\n'})
        self.verify_all(branch)
        merge = self.repo.commit([self.root, branch], {'src/laya.cpp': 'int y;\n'})
        self.api.fail = True
        decision = self.decide(merge, ref='refs/heads/main')
        self.assertFalse(decision['skip_all'])
        self.assertEqual(decision['run'], ci_changes.ordered(PUSH))


class Dispatch(Base):
    def test_scope_affected_runs_the_stale_jobs(self):
        self.verify_all(self.root)
        head = self.repo.commit([self.root], {'src/laya.cpp': 'int y;\n'})
        run = set(self.decide(head, event='workflow_dispatch')['run'])
        self.assertEqual(run, {'generic-linux-x64', 'generic-linux-x64-clang', 'generic-linux-arm64', 'generic-windows-x64', 'sanitizers',
                               'fuzzer', 'reproducibility-windows-x64'})

    def test_scope_all_and_lists(self):
        self.verify_all(self.root)
        self.assertEqual(self.decide(self.root, event='workflow_dispatch', scope='all')['run'], ci_changes.ordered(PUSH))
        decision = self.decide(self.root, event='workflow_dispatch', scope='policy,generic-linux-x64')
        self.assertEqual(decision['run'], ['policy', 'generic-linux-x64'])
        self.assertEqual(self.decide(self.root, event='workflow_dispatch', scope='bindings-real-linux-x64')['run'],
                         ['build-linux-x64', 'bindings-real-linux-x64'])
        with self.assertRaises(ValueError):
            self.decide(self.root, event='workflow_dispatch', scope='no-such-job')
        self.assertEqual(self.decide(self.root, event='workflow_dispatch', scope='generic-macos-arm64')['run'], [])

    def test_only_packages_windows_stale(self):
        head = self.repo.commit([self.root], {'docs/API.md': '# 2\n'})
        self.verify_all(head, [job for job in PUSH if job != 'packages-windows-x64'], event='workflow_dispatch')
        run = set(self.decide(head, event='workflow_dispatch')['run'])
        self.assertEqual(run, {'packages-windows-x64', 'assemble', 'assembled-linux-x64', 'assembled-linux-arm64', 'assembled-windows-x64',
                               'musl', 'packages-linux-x64', 'packages-linux-arm64'})

    def test_only_bindings_real_linux_x64_stale(self):
        head = self.repo.commit([self.root], {'docs/API.md': '# 2\n'})
        self.verify_all(head, [job for job in PUSH + REAL if job != 'bindings-real-linux-x64'], event='workflow_dispatch')
        decision = self.decide(head, event='workflow_dispatch', real_model=True)
        self.assertEqual(set(decision['run']), {'bindings-real-linux-x64', 'build-linux-x64'})
        self.assertEqual(decision['shards'], [{'job': 'bindings-real-linux-x64', 'platform': 'linux-x64', 'checks': ['bindings'],
                                               'side_outputs': [], 'profile': '', 'mode': 'real_model'}])

    def test_binding_change_with_real_model(self):
        self.verify_all(self.root, PUSH + REAL)
        head = self.repo.commit([self.root], {'bindings/node/index.cjs': 'changed\n'})
        run = set(self.decide(head, event='workflow_dispatch', real_model=True)['run'])
        # Real-model binding suites and parity (it runs every consumer) on every platform, each with its build only.
        self.assertIn('bindings-real-linux-x64', run)
        self.assertIn('parity-linux-x64', run)
        self.assertIn('binding-node-linux-x64', run)
        self.assertNotIn('native-linux-x64', run)
        self.assertNotIn('ctest-windows-x64', run)
        self.assertNotIn('generic-linux-x64', run)
        self.assertIn('build-linux-x64', run)
        self.assertNotIn('native-linux-x64-clang', run)
        self.assertNotIn('build-linux-x64-clang', run)

    def test_push_and_candidate_packages_share_a_name(self):
        candidate = self.decide(self.root, event='workflow_dispatch', mode='candidate')
        self.assertEqual(set(candidate['run']), set(ci_paths.jobs_of('native.yml', 'candidate')))
        self.assertIn('packages-linux-x64', candidate['run'])
        self.assertIn('build-linux-x64', candidate['run'])
        self.assertEqual(sum(1 for shard in candidate['shards'] if shard['job'] == 'packages-linux-x64'), 1)
        self.assertEqual(next(s for s in candidate['shards'] if s['job'] == 'native-linux-x64')['side_outputs'], ['cross-host'])
        # In the push profile the same name pulls no build.
        head = self.repo.commit([self.root], {'docs/API.md': '# 2\n'})
        self.verify_all(head, [job for job in PUSH + REAL if job != 'packages-linux-x64'], event='workflow_dispatch')
        run = set(self.decide(head, event='workflow_dispatch', real_model=True)['run'])
        self.assertIn('packages-linux-x64', run)
        self.assertNotIn('build-linux-x64', run)
        # A packages success in either profile verifies the name.
        self.api.record(head, ['packages-linux-x64'], event='workflow_dispatch')
        self.assertEqual(self.decide(head, event='workflow_dispatch')['run'], [])

    def test_ordering_only_needs_does_not_pull_or_drop(self):
        """A job behind an ordering-only `needs` that is not stale runs alone; the closure adds no upstream for it."""
        saved = json.loads(json.dumps(ci_paths.JOBS))
        try:
            ci_paths.JOBS['generic-linux-arm64']['needs']['push'] = ['changes', 'generic-linux-x64']
            head = self.repo.commit([self.root], {'docs/API.md': '# 2\n'})
            self.verify_all(head, [job for job in PUSH if job != 'generic-linux-arm64'], event='workflow_dispatch')
            self.assertEqual(self.decide(head, event='workflow_dispatch')['run'], ['generic-linux-arm64'])
        finally:
            ci_paths.JOBS.clear()
            ci_paths.JOBS.update(saved)

    def test_modes(self):
        for mode, profile in (('candidate', 'candidate'), ('qualify', 'qualify'), ('warm-cache', 'warm'), ('experiment', 'experiment')):
            with self.subTest(mode=mode):
                self.assertEqual(set(self.decide(self.root, event='workflow_dispatch', mode=mode)['run']),
                                 set(ci_paths.jobs_of('native.yml', profile)))
        self.assertEqual(self.decide(self.root, event='workflow_dispatch', mode='candidate', workflow='platforms.yml')['run'], [])
        self.assertNotIn('warm', ' '.join(self.decide(self.root, event='schedule', mode='warm-cache', workflow='platforms.yml')['run']))
        qualify = set(self.decide(self.root, event='workflow_dispatch', mode='qualify')['run'])
        self.assertEqual(qualify, {'qualify-sanitizers-address', 'qualify-sanitizers-thread', 'qualify-sanitizers', 'qualify-fuzz',
                                   'qualify-upgrade'})
        self.assertEqual(set(self.decide(self.root, event='workflow_dispatch', mode='warm-cache')['run']),
                         set(ci_paths.WARM_JOBS + ['cache-prune']))

    def test_unknown_event_fails_open(self):
        self.assertEqual(self.decide(self.root, event='repository_dispatch')['run'], ci_changes.ordered(PUSH))


class CommandLine(Base):
    def run_main(self, args, environ, event=None):
        output = pathlib.Path(self.tmp.name) / 'github-output'
        output.write_text('')
        environ = dict(environ, GITHUB_OUTPUT=str(output))
        if event is not None:
            payload = pathlib.Path(self.tmp.name) / 'event.json'
            payload.write_text(json.dumps(event))
            environ['GITHUB_EVENT_PATH'] = str(payload)
        saved = dict(os.environ)
        api, git = ci_changes.GitHubAPI, ci_changes.Git
        try:
            os.environ.update(environ)
            ci_changes.GitHubAPI = lambda repo: self.api
            ci_changes.Git = lambda root=None: self.git
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(ci_changes.main(args), 0)
        finally:
            os.environ.clear()
            os.environ.update(saved)
            ci_changes.GitHubAPI, ci_changes.Git = api, git
        return dict(line.split('=', 1) for line in output.read_text().splitlines())

    def test_dispatch_inputs_from_the_event(self):
        self.verify_all(self.root, PUSH + REAL)
        values = self.run_main(['--workflow', 'native.yml'], {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/x',
                                                              'GITHUB_SHA': self.root},
                               event={'inputs': {'mode': 'verify', 'scope': 'all', 'real_model': True}})
        self.assertEqual(set(json.loads(values['run'])), set(PUSH) | set(REAL))
        self.assertEqual(values['skip_all'], 'false')
        self.assertIn('shards', values)
        self.assertEqual(values['base_policy'], '')

    def test_schedule_without_mode_is_the_weekly_full_run(self):
        self.verify_all(self.root, PUSH + REAL)
        values = self.run_main(['--workflow', 'native.yml'], {'GITHUB_EVENT_NAME': 'schedule', 'GITHUB_REF': 'refs/heads/main',
                                                              'GITHUB_SHA': self.root})
        self.assertEqual(set(json.loads(values['run'])), set(PUSH) | set(REAL))
        nightly = self.run_main(['--workflow', 'native.yml', '--mode', 'warm-cache'],
                                {'GITHUB_EVENT_NAME': 'schedule', 'GITHUB_REF': 'refs/heads/main', 'GITHUB_SHA': self.root})
        self.assertEqual(set(json.loads(nightly['run'])), set(ci_paths.WARM_JOBS + ['cache-prune']))


if __name__ == '__main__':
    unittest.main()
