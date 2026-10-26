#!/usr/bin/env python3
"""Decide which CI jobs a workflow run executes: the `changes` job of native.yml and platforms.yml.

Staleness is per job (scripts/ci_paths.py JOBS), from that job's own trusted record:

- A job J is verified at commit X when a run of the same workflow in this repository with
  head_sha X and event push, schedule or workflow_dispatch has a job named exactly J that
  succeeded. pull_request runs never count (their tested tree is a synthetic merge); a partial
  dispatch counts only for the jobs it ran.
- B_J is the newest commit at which J is verified among HEAD, HEAD's parents and HEAD's
  first-parent ancestors (at most WALK_LIMIT). J is stale when there is no B_J, or
  diff(B_J, HEAD) touches one of J's categories, the `ci` category or a path in no category.
- Fail open: any API or git failure makes every candidate job stale (run everything).

What runs, by event:

- push to a branch other than main, single parent: the stale jobs among `policy` and
  `generic-linux-x64` (platforms.yml: none).
- push to main, or a commit with more than one parent: every push-profile job, never
  diff-based, unless a commit P among HEAD's parents and first-parent ancestors has HEAD's tree
  and every push-profile job is not stale at P, or every push-profile job is verified at HEAD
  itself (identical-tree skip: skip_all=true, nothing runs), or HEAD differs from a fully
  verified P (among its parents and their first FAST_PATH_ANCESTORS first-parent ancestors) only by
  documentation that no job but the policy checks reads (ci_paths.is_inert): then only the jobs
  that difference makes stale run (the policy checks of the docs). Any other path, an unclassified
  one, a workflow or classifier change, or an API or git failure keeps the full profile.
- pull_request: as a branch push, unconditionally (its runs are never records).
- workflow_dispatch: --mode verify with --scope affected (the stale jobs of the push profile, and
  of the real-model profile with --real-model; on a merge commit a job counts as verified only
  through a commit with HEAD's tree), all, or a comma list of job names; candidate, qualify,
  warm-cache and experiment run their whole profile.
- schedule: --mode warm-cache (nightly) or verify (weekly: scope all, real model).

The run set is then closed: a job whose `rebuild_with` names a running job joins (the packaging
chain), and every `artifact_needs` job of a running job in its profile joins (same-run artifacts).

Outputs ($GITHUB_OUTPUT, or printed): run (JSON list of job names), skip_all, docs_only,
policy_checks (JSON list, the stale policy checks for `policy`), categories (JSON object: job ->
its stale categories), shards (JSON list of shard jobs in the run with checks and side outputs),
and base_<job> (the commit each candidate job was verified at, for the log).
"""
import argparse
import concurrent.futures
import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ci_paths  # noqa: E402

ROOT = ci_paths.ROOT
WALK_LIMIT = 50
PREFETCH = 8  # commits whose records are fetched concurrently
TRUSTED_EVENTS = ('push', 'schedule', 'workflow_dispatch')
MODES = ('verify', 'candidate', 'qualify', 'warm-cache', 'experiment')
MODE_PROFILES = {'candidate': 'candidate', 'qualify': 'qualify', 'warm-cache': 'warm', 'experiment': 'experiment'}
MAIN_REF = 'refs/heads/main'


class Unavailable(Exception):
    """A git or API answer is missing: the caller fails open."""


class Git:
    def __init__(self, root=ROOT):
        self.root = pathlib.Path(root)
        self._trees = {}

    def run(self, *args):
        try:
            result = subprocess.run(['git', '-C', str(self.root), *args], capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as error:
            raise Unavailable(f'git {" ".join(args)}: {error}') from error
        if result.returncode:
            raise Unavailable(f'git {" ".join(args)}: {result.stderr.strip()}')
        return result.stdout

    def resolve(self, rev):
        return self.run('rev-parse', '--verify', '--end-of-options', f'{rev}^{{commit}}').strip()

    def parents(self, sha):
        return self.run('rev-list', '--parents', '-n', '1', sha).split()[1:]

    def tree(self, sha):
        if sha not in self._trees:
            self._trees[sha] = self.run('rev-parse', f'{sha}^{{tree}}').strip()
        return self._trees[sha]

    def walk(self, head, limit=WALK_LIMIT):
        """HEAD, then its parents, then its first-parent ancestors: at most `limit` commits after HEAD."""
        commits = [head] + self.parents(head)
        commits += self.run('rev-list', '--first-parent', f'--max-count={limit + 1}', head).split()
        return list(dict.fromkeys(commits))[:limit + 1]

    def diff(self, base, head=None):
        """(changed paths, deleted paths) from base to head; head None is the working tree, untracked files included."""
        lines = self.run('diff', '--name-status', '--no-renames', '-z', base, *([head] if head else []), '--').split('\0')
        lines = [line for line in lines if line]
        paths, deleted = [], []
        for status, path in zip(lines[::2], lines[1::2]):
            paths.append(path)
            if status.startswith('D'):
                deleted.append(path)
        if head is None:
            paths += [path for path in self.run('ls-files', '--others', '--exclude-standard', '-z').split('\0') if path]
        return paths, deleted

    def is_ancestor(self, ancestor, descendant):
        try:
            self.run('merge-base', '--is-ancestor', ancestor, descendant)
            return True
        except Unavailable:
            return False


class GitHubAPI:
    """Read-only REST calls: urllib with GH_TOKEN or GITHUB_TOKEN, else the gh CLI."""

    def __init__(self, repo, token=None):
        self.repo = repo
        self.token = token if token is not None else (os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN'))
        self.base = os.environ.get('GITHUB_API_URL', 'https://api.github.com').rstrip('/')

    def get(self, path):
        if self.token:
            request = urllib.request.Request(f'{self.base}/{path}', headers={
                'Accept': 'application/vnd.github+json', 'Authorization': f'Bearer {self.token}',
                'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'ducktape-laya-ci-changes'})
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.load(response)
            except (urllib.error.URLError, OSError, ValueError) as error:
                raise Unavailable(f'GET {path}: {error}') from error
        try:
            result = subprocess.run(['gh', 'api', path], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as error:
            raise Unavailable(f'gh api {path}: {error}') from error
        if result.returncode:
            raise Unavailable(f'gh api {path}: {result.stderr.strip()}')
        try:
            return json.loads(result.stdout)
        except ValueError as error:
            raise Unavailable(f'gh api {path}: {error}') from error

    def _pages(self, path, key):
        items, page = [], 1
        while True:
            answer = self.get(f'{path}{"&" if "?" in path else "?"}per_page=100&page={page}')
            if not isinstance(answer, dict) or not isinstance(answer.get(key), list):
                raise Unavailable(f'{path}: unexpected answer')
            items += answer[key]
            if not answer[key] or len(items) >= int(answer.get('total_count', 0)) or page >= 20:
                return items
            page += 1

    def runs_for(self, sha):
        return self._pages(f'repos/{self.repo}/actions/runs?head_sha={sha}', 'workflow_runs')

    def jobs_for(self, run_id):
        return self._pages(f'repos/{self.repo}/actions/runs/{run_id}/jobs?filter=latest', 'jobs')


class Records:
    """The job names verified at a commit (trusted runs of one workflow only)."""

    def __init__(self, api, repo, workflow):
        self.api, self.repo, self.path = api, repo, f'.github/workflows/{workflow}'
        self._cache = {}

    def trusted(self, run, sha):
        return (run.get('head_sha') == sha and run.get('event') in TRUSTED_EVENTS
                and (run.get('path') or '').split('@')[0] == self.path
                and (run.get('repository') or {}).get('full_name') == self.repo
                and (run.get('head_repository') or {}).get('full_name', self.repo) == self.repo)

    def prefetch(self, shas):
        """Fetch several commits' records concurrently (the walk asks for them in order)."""
        missing = [sha for sha in shas if sha not in self._cache]
        if len(missing) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(self.verified, missing))

    def verified(self, sha):
        if sha not in self._cache:
            names = set()
            for run in self.api.runs_for(sha):
                if self.trusted(run, sha):
                    names |= {job.get('name') for job in self.api.jobs_for(run['id'])
                              if job.get('conclusion') == 'success' and job.get('head_sha', sha) == sha}
            self._cache[sha] = names
        return self._cache[sha]


def stale_at(git, records, head, jobs, *, working_tree=False):
    """{job: {'stale', 'base', 'reason', 'touched', 'paths', 'deleted'}} by per-job records; raises Unavailable."""
    walk = git.walk(head)
    bases, remaining = {}, set(jobs)
    for index, commit in enumerate(walk):
        if not remaining:
            break
        if index % PREFETCH == 0 and hasattr(records, 'prefetch'):
            records.prefetch(walk[index:index + PREFETCH])
        found = records.verified(commit) & remaining
        for job in found:
            bases[job] = commit
        remaining -= found
    diffs, result = {}, {}
    for job in jobs:
        base = bases.get(job)
        if base is None:
            result[job] = {'stale': True, 'base': None, 'reason': f'not verified at HEAD or its last {WALK_LIMIT} ancestors',
                           'touched': {ci_paths.EVERYTHING}, 'paths': None, 'deleted': []}
            continue
        if base not in diffs:
            paths, deleted = git.diff(base, None if working_tree else head)
            diffs[base] = (paths, deleted, ci_paths.touched_categories(paths))
        paths, deleted, touched = diffs[base]
        stale = ci_paths.job_stale_by(ci_paths.JOBS[job]['categories'], touched)
        if ci_paths.EVERYTHING in touched:
            reason = 'the classifier, a workflow or an unclassified path changed since ' + base[:12]
        elif stale:
            reason = 'inputs changed since ' + base[:12] + ': ' + ', '.join(sorted(set(ci_paths.JOBS[job]['categories']) & touched))
        else:
            reason = 'verified at ' + base[:12] + (' (no input changed since)' if base != head else '')
        result[job] = {'stale': stale, 'base': base, 'reason': reason, 'touched': touched, 'paths': paths, 'deleted': deleted}
    return result


def identical_tree_commits(git, head):
    """HEAD's parents and first-parent ancestors that have HEAD's tree."""
    tree = git.tree(head)
    return [commit for commit in git.walk(head)[1:] if git.tree(commit) == tree]


def merge_staleness(git, records, head, jobs):
    """Staleness on a merge commit: a job counts as verified only at HEAD itself or through a commit with HEAD's tree."""
    verified_head = records.verified(head)
    result = {job: {'stale': job not in verified_head, 'base': head if job in verified_head else None,
                    'reason': 'verified at HEAD' if job in verified_head else 'merge commit: no verified commit with the same tree',
                    'touched': set() if job in verified_head else {ci_paths.EVERYTHING}, 'paths': [] if job in verified_head else None,
                    'deleted': []} for job in jobs}
    pending = [job for job in jobs if result[job]['stale']]
    for commit in identical_tree_commits(git, head) if pending else []:
        at = stale_at(git, records, commit, pending)
        for job in list(pending):
            if not at[job]['stale']:
                result[job] = dict(at[job], reason=f'same tree as {commit[:12]}, where ' + at[job]['reason'])
                pending.remove(job)
        if not pending:
            break
    return result


def identical_tree_skip(git, records, head, jobs):
    """The commit that lets a main push or a merge skip everything, or None."""
    if jobs and all(job in records.verified(head) for job in jobs):
        return head
    for commit in identical_tree_commits(git, head):
        if not any(state['stale'] for state in stale_at(git, records, commit, jobs).values()):
            return commit
    return None


FAST_PATH_ANCESTORS = 5  # parents and first-parent ancestors tried as the fully verified tree of the docs-only fast path


def inert_difference(git, records, head, jobs):
    """(P, paths, deleted) when HEAD differs from a fully verified tree P only by inert paths (ci_paths.is_inert), else None.

    P is among HEAD's parents and its first FAST_PATH_ANCESTORS first-parent ancestors, and every job of `jobs` is verified at P
    or not stale at P by its own record (the rule of identical_tree_skip). Any other path, an unclassified one, a workflow or
    classifier change included, returns None: the full profile."""
    for commit in git.walk(head, FAST_PATH_ANCESTORS)[1:]:
        paths, deleted = git.diff(commit, head)
        if not all(ci_paths.is_inert(path) for path in paths):
            continue
        if not any(state['stale'] for state in stale_at(git, records, commit, jobs).values()):
            return commit, paths, deleted
    return None


def close(run, profiles, workflow):
    """The run set closed under rebuild_with (downstream) and artifact_needs (upstream), in the active profiles."""
    def profile_of(name):
        return next((p for p in profiles if p in ci_paths.JOBS[name]['profiles']), None)
    run = set(run)
    while True:
        before = len(run)
        for name, job in ci_paths.JOBS.items():
            if job['workflow'] == workflow and name != 'changes' and profile_of(name) and set(job['rebuild_with']) & run:
                run.add(name)
        for name in list(run):
            run |= set(ci_paths.JOBS[name]['artifact_needs'].get(profile_of(name), []))
        if len(run) == before:
            return run


def ordered(names):
    return [name for name in ci_paths.JOBS if name in names]


def decide(*, git, records, workflow, event, ref, head, mode='verify', scope='affected', real_model=False):
    """The run decision as a dict (the outputs before formatting)."""
    if workflow not in ci_paths.WORKFLOWS:
        raise ValueError(f'unknown workflow {workflow}')
    if mode not in MODES:
        raise ValueError(f'unknown mode {mode}')
    push_jobs = ci_paths.jobs_of(workflow, 'push')
    decision = {'run': [], 'skip_all': False, 'docs_only': False, 'policy_checks': [], 'categories': {}, 'bases': {}, 'notes': [],
                'profiles': ['push']}
    states = {}

    def fail_open(error, candidates):
        decision['notes'].append(f'fail open ({error}): every candidate job runs')
        return {job: {'stale': True, 'base': None, 'reason': 'fail open', 'touched': {ci_paths.EVERYTHING}, 'paths': None,
                      'deleted': []} for job in candidates}

    if event == 'push':
        try:
            parents = git.parents(head)
        except Unavailable as error:
            parents, decision['notes'] = None, [f'fail open ({error}): parents unknown, full push profile']
        if parents is None or ref == MAIN_REF or len(parents) > 1:
            decision['notes'].append('main push or merge commit: every push-profile job unless the tree is verified')
            try:
                skip = identical_tree_skip(git, records, head, push_jobs)
            except Unavailable as error:
                decision['notes'].append(f'fail open ({error})')
                skip = None
            inert = None
            if not skip:
                try:
                    inert = inert_difference(git, records, head, push_jobs)
                except Unavailable as error:
                    decision['notes'].append(f'fail open ({error})')
            if skip:
                decision['skip_all'] = True
                decision['notes'].append(f'identical tree: every push-profile job is verified at {skip[:12]}')
            elif inert:
                base, paths, deleted = inert
                touched = ci_paths.touched_categories(paths)
                decision['notes'].append(f'docs-only difference from the fully verified {base[:12]}: only the jobs whose inputs it touches run')
                states = {job: {'stale': ci_paths.job_stale_by(ci_paths.JOBS[job]['categories'], touched), 'base': base,
                                'reason': f'only documentation changed since the fully verified {base[:12]}', 'touched': touched,
                                'paths': paths, 'deleted': deleted} for job in push_jobs}
            else:
                states = {job: {'stale': True, 'base': None, 'reason': 'main push or merge commit', 'touched': {ci_paths.EVERYTHING},
                                'paths': None, 'deleted': []} for job in push_jobs}
        else:
            candidates = ci_paths.BRANCH_PUSH_JOBS[workflow]
            try:
                states = stale_at(git, records, head, candidates)
            except Unavailable as error:
                states = fail_open(error, candidates)
    elif event == 'pull_request':
        candidates = ci_paths.BRANCH_PUSH_JOBS[workflow]
        decision['notes'].append('pull_request: the branch-push jobs, unconditionally')
        states = {job: {'stale': True, 'base': None, 'reason': 'pull_request', 'touched': {ci_paths.EVERYTHING}, 'paths': None,
                        'deleted': []} for job in candidates}
    elif event in ('workflow_dispatch', 'schedule') and mode in MODE_PROFILES:
        profile = MODE_PROFILES[mode]
        decision['profiles'] = [profile]
        states = {job: {'stale': True, 'base': None, 'reason': f'mode {mode}', 'touched': {ci_paths.EVERYTHING}, 'paths': None,
                        'deleted': []} for job in ci_paths.jobs_of(workflow, profile)}
    elif event in ('workflow_dispatch', 'schedule'):
        decision['profiles'] = ['push'] + (['real_model'] if real_model else [])
        candidates = ordered({job for profile in decision['profiles'] for job in ci_paths.jobs_of(workflow, profile)})
        if scope == 'all':
            states = {job: {'stale': True, 'base': None, 'reason': 'scope all', 'touched': {ci_paths.EVERYTHING}, 'paths': None,
                            'deleted': []} for job in candidates}
        elif scope == 'affected':
            try:
                parents = git.parents(head)
                states = (merge_staleness if len(parents) > 1 else stale_at)(git, records, head, candidates)
            except Unavailable as error:
                states = fail_open(error, candidates)
        else:
            names = [name.strip() for name in scope.split(',') if name.strip()]
            unknown = [name for name in names if name not in ci_paths.JOBS or name == 'changes']
            if unknown:
                raise ValueError('scope names unknown jobs: ' + ', '.join(unknown))
            elsewhere = [name for name in names if ci_paths.JOBS[name]['workflow'] != workflow]
            if elsewhere:
                decision['notes'].append(f'scope: {", ".join(elsewhere)} belong to another workflow')
            # An explicit name runs in whichever of its profiles this dispatch can run.
            chosen = [name for name in names if ci_paths.JOBS[name]['workflow'] == workflow]
            outside = [name for name in chosen if not set(ci_paths.JOBS[name]['profiles']) & {'push', 'real_model'}]
            if outside:
                decision['notes'].append(f'scope: {", ".join(outside)} run only in another mode')
            decision['profiles'] = list(dict.fromkeys(decision['profiles'] + [
                p for name in chosen for p in ci_paths.JOBS[name]['profiles'] if p in ('push', 'real_model')]))
            states = {name: {'stale': True, 'base': None, 'reason': 'named in scope', 'touched': {ci_paths.EVERYTHING}, 'paths': None,
                             'deleted': []} for name in chosen
                      if set(ci_paths.JOBS[name]['profiles']) & set(decision['profiles'])}
    else:
        decision['notes'].append(f'unknown event {event!r}: fail open, every push-profile job')
        states = {job: {'stale': True, 'base': None, 'reason': 'unknown event', 'touched': {ci_paths.EVERYTHING}, 'paths': None,
                        'deleted': []} for job in push_jobs}

    stale = {job for job, state in states.items() if state['stale']}
    run = close(stale, decision['profiles'], workflow) if stale else set()
    decision['run'] = ordered(run)
    decision['states'] = states
    decision['bases'] = {job: state['base'] or '' for job, state in states.items()}
    for job in decision['run']:
        touched = states.get(job, {}).get('touched', {ci_paths.EVERYTHING})
        categories = ci_paths.JOBS[job]['categories']
        decision['categories'][job] = list(categories) if ci_paths.EVERYTHING in touched else [c for c in categories if c in touched]
    for job in ('policy', 'tooling'):
        if job in decision['run']:
            state = states.get(job, {})
            if state.get('paths') is None or ci_paths.EVERYTHING in state.get('touched', set()):
                decision['policy_checks'] = list(ci_paths.POLICY_CHECKS)
            else:
                decision['policy_checks'] = ci_paths.policy_checks_touched(state['paths'], state['deleted'])
    computed = [state['paths'] for state in states.values() if state.get('paths') is not None]
    changed = {path for paths in computed for path in paths}
    decision['docs_only'] = bool(changed) and all(ci_paths.matches(path, ci_paths.CATEGORIES['docs']) for path in changed) \
        and all(state.get('paths') is not None for state in states.values())
    decision['shards'] = [
        {'job': name, 'platform': platform, 'checks': checks, 'side_outputs': shards['side_outputs'].get(name, []),
         'profile': shards.get('profile', ''), 'mode': shard_mode}
        for shard_mode, profile in (('real_model', 'real_model'), ('candidate', 'candidate')) if profile in decision['profiles']
        for platform, shards in ci_paths.SHARDS[shard_mode].items() for name, checks in shards['jobs'].items() if name in run]
    return decision


def outputs(decision):
    """The decision as $GITHUB_OUTPUT lines (job output keys use `_` or `-`, never `:`)."""
    values = {'run': json.dumps(decision['run']), 'skip_all': str(decision['skip_all']).lower(),
              'docs_only': str(decision['docs_only']).lower(), 'policy_checks': json.dumps(decision['policy_checks']),
              'categories': json.dumps(decision['categories'], sort_keys=True), 'shards': json.dumps(decision['shards'])}
    values.update({f'base_{job}': base for job, base in decision['bases'].items()})
    return values


def dispatch_inputs(event_path):
    try:
        with open(event_path) as payload:
            return json.load(payload).get('inputs') or {}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--workflow', required=True, choices=ci_paths.WORKFLOWS)
    p.add_argument('--event', default=os.environ.get('GITHUB_EVENT_NAME', ''), help='github.event_name (default $GITHUB_EVENT_NAME)')
    p.add_argument('--ref', default=os.environ.get('GITHUB_REF', ''), help='github.ref (default $GITHUB_REF)')
    p.add_argument('--head', default=os.environ.get('GITHUB_SHA') or 'HEAD', help='Commit under test (default $GITHUB_SHA, else HEAD)')
    p.add_argument('--repository', '--repo', dest='repo', default=os.environ.get('GITHUB_REPOSITORY', 'adrian729/ducktape-laya'))
    inputs = dispatch_inputs(os.environ.get('GITHUB_EVENT_PATH', '')) if os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch' else {}
    p.add_argument('--mode', choices=MODES, help='Dispatch mode (default: the dispatch input, else verify; a schedule passes '
                   'warm-cache for the nightly)')
    p.add_argument('--scope', help='affected, all, or a comma list of job names (default: the dispatch input, else affected; '
                   'all for a schedule)')
    p.add_argument('--real-model', choices=('true', 'false'), help='default: the dispatch input, else false; true for a schedule')
    p.add_argument('--output', default=os.environ.get('GITHUB_OUTPUT'), help='Append outputs here (default $GITHUB_OUTPUT; else print)')
    a = p.parse_args(argv)
    # A schedule without an explicit mode is the weekly full run (fail open: everything).
    weekly = a.event == 'schedule'
    a.mode = a.mode or inputs.get('mode') or 'verify'
    a.scope = a.scope or inputs.get('scope') or ('all' if weekly else 'affected')
    a.real_model = a.real_model or str(inputs.get('real_model', 'true' if weekly else 'false')).lower()
    if a.real_model not in ('true', 'false'):
        p.error('real_model must be true or false')
    git = Git()
    try:
        head = git.resolve(a.head)
    except Unavailable as error:
        print(f'ci-changes: {error}; failing open', file=sys.stderr)
        head = a.head
    records = Records(GitHubAPI(a.repo), a.repo, a.workflow)
    try:
        decision = decide(git=git, records=records, workflow=a.workflow, event=a.event, ref=a.ref, head=head, mode=a.mode,
                          scope=a.scope, real_model=a.real_model == 'true')
    except ValueError as error:
        p.error(str(error))
    print(f'ci-changes: {a.workflow} {a.event or "?"} {a.ref or "?"} {head[:12]} mode={a.mode} scope={a.scope} '
          f'real_model={a.real_model}')
    for note in decision['notes']:
        print(f'  note: {note}')
    for job, state in decision['states'].items():
        print(f'  {"STALE" if state["stale"] else "ok   "} {job}: {state["reason"]}')
    print(f'  run ({len(decision["run"])}): {", ".join(decision["run"]) or "nothing"}' + ('  [skip_all]' if decision['skip_all'] else ''))
    if decision['policy_checks']:
        print(f'  policy checks: {", ".join(decision["policy_checks"])}')
    lines = [f'{key}={value}' for key, value in outputs(decision).items()]
    if a.output:
        with open(a.output, 'a') as output:
            output.write(''.join(line + '\n' for line in lines))
    else:
        print('\n'.join(lines))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
