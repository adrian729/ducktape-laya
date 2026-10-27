#!/usr/bin/env python3
"""Verify a batch: run the stale specialized checks locally and in CI, and print one verdict.

Run it once per batch, before merging (AGENTS.md, Development workflow). It works out which jobs
are stale with scripts/ci_paths.py:

- CI jobs (the push profile of native.yml and platforms.yml, and the real-model profile with
  --real-model) from their own trusted job records, as the workflows' `changes` job does
  (scripts/ci-changes.py). It dispatches native.yml with mode=verify and scope=affected (and
  platforms.yml when a macOS job is stale), which runs exactly those jobs.
- Local jobs (ci_paths.LOCAL_JOBS) from the state file at `git rev-parse --git-path
  verify-state.json` (per job, the last commit it verified on this checkout; per linked worktree).

The local jobs run one after another while CI runs; then it waits for CI and prints the verdict,
each job's duration against its p90 budget (scripts/ci_budgets.py) and exits 0 only when
everything selected passed. Windows, arm64 and macOS run only in CI.

  --local / --ci       restrict to one side (--ci refuses a dirty tree or a HEAD not pushed to its branch)
  --all                every local job and a scope=all dispatch, whatever is stale
  --only JOB[,JOB]     the named local or CI jobs only (CI: a scope list)
  --rerun-failed       rerun what failed in the last verify of this HEAD (local jobs, `gh run rerun --failed`)
  --real-model         also the real-model jobs; the model is --model, $DUCKTAPE_TEST_MODEL or the pinned
                       manifest's file in the Hugging Face cache (NOT RUN: real_model and exit 1 without one)
  --emsdk DIR          also the browser builds (local-browser-wasm, local-browser-webgpu)
  --list               print the jobs, their categories and whether they are stale; run nothing
"""
import argparse
import datetime
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ci_budgets  # noqa: E402
import ci_paths  # noqa: E402

_spec = importlib.util.spec_from_file_location('ci_changes', HERE / 'ci-changes.py')
ci_changes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ci_changes)

ROOT = ci_paths.ROOT
PY = sys.executable
REPO = 'adrian729/ducktape-laya'
POLL_SECONDS = 30


# ------------------------------------------------------------------------------------------------
# State
# ------------------------------------------------------------------------------------------------
def state_path(root=ROOT):
    """verify-state.json in this checkout's git directory (a linked worktree has its own; `.git` may be a file)."""
    result = subprocess.run(['git', '-C', str(root), 'rev-parse', '--git-path', 'verify-state.json'], capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(f'verify: not a git checkout: {result.stderr.strip()}')
    path = pathlib.Path(result.stdout.strip())
    return path if path.is_absolute() else (pathlib.Path(root) / path).resolve()


def load_state(path):
    try:
        state = json.loads(pathlib.Path(path).read_text())
        if isinstance(state, dict) and state.get('schema_version') == 1:
            state.setdefault('jobs', {})
            return state
    except (OSError, ValueError):
        pass
    return {'schema_version': 1, 'jobs': {}, 'last': None}


def save_state(path, state):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, indent=2) + '\n')
    temporary.replace(path)


# ------------------------------------------------------------------------------------------------
# Model discovery
# ------------------------------------------------------------------------------------------------
def pinned_cache_path(root=ROOT, environ=None):
    environ = os.environ if environ is None else environ
    manifest = json.loads((pathlib.Path(root) / 'models/manifest.json').read_text())
    home = pathlib.Path(environ.get('HOME', str(pathlib.Path.home())))
    hub = pathlib.Path(environ['HF_HUB_CACHE']) if environ.get('HF_HUB_CACHE') else \
        pathlib.Path(environ.get('HF_HOME') or home / '.cache/huggingface') / 'hub'
    return hub / f'models--{manifest["repository"].replace("/", "--")}' / 'snapshots' / manifest['revision'] / manifest['filename']


def discover_model(explicit=None, root=ROOT, environ=None):
    """(path or None, where it came from): --model, then $DUCKTAPE_TEST_MODEL, then the pinned Hugging Face cache path."""
    environ = os.environ if environ is None else environ
    if explicit:
        path = pathlib.Path(explicit)
        return (path, '--model') if path.is_file() else (None, f'--model {path} is not a file')
    if environ.get('DUCKTAPE_TEST_MODEL'):
        path = pathlib.Path(environ['DUCKTAPE_TEST_MODEL'])
        return (path, '$DUCKTAPE_TEST_MODEL') if path.is_file() else (None, f'$DUCKTAPE_TEST_MODEL {path} is not a file')
    path = pinned_cache_path(root, environ)
    return (path, 'the Hugging Face cache') if path.is_file() else (None, f'not in the Hugging Face cache ({path})')


# ------------------------------------------------------------------------------------------------
# Staleness
# ------------------------------------------------------------------------------------------------
def is_dirty(git):
    return bool(git.run('status', '--porcelain', '--untracked-files=normal').strip())


def local_staleness(git, state, head, dirty):
    """{local job: {'stale', 'base', 'reason'}} from the state file; the working tree counts when dirty. Fails open."""
    result, diffs = {}, {}
    for job, spec in ci_paths.LOCAL_JOBS.items():
        record = state['jobs'].get(job)
        if not record or not record.get('commit'):
            result[job] = {'stale': True, 'base': None, 'reason': 'never verified in this checkout'}
            continue
        base = record['commit']
        try:
            if not git.is_ancestor(base, head):
                result[job] = {'stale': True, 'base': base, 'reason': f'verified at {base[:12]}, which is not an ancestor of HEAD'}
                continue
            if base not in diffs:
                paths, _ = git.diff(base, None if dirty else head)
                # No local job reads .github/ (CI's job definitions and actions), so a workflow-only change, which makes
                # every CI job stale, leaves the local jobs verified; the classifier (scripts/ci_paths.py) still counts.
                if paths is not None:
                    paths = [path for path in paths if not path.startswith('.github/')]
                diffs[base] = ci_paths.touched_categories(paths)
        except ci_changes.Unavailable as error:
            result[job] = {'stale': True, 'base': base, 'reason': f'fail open ({error})'}
            continue
        touched = diffs[base]
        stale = ci_paths.job_stale_by(spec['categories'], touched)
        if ci_paths.EVERYTHING in touched:
            reason = f'the classifier, a workflow or an unclassified path changed since {base[:12]}'
        elif stale:
            reason = f'inputs changed since {base[:12]}: ' + ', '.join(sorted(set(spec['categories']) & touched))
        else:
            reason = f'verified at {base[:12]}'
        result[job] = {'stale': stale, 'base': base, 'reason': reason}
    return result


def ci_staleness(git, records, head, real_model):
    """{workflow: {job: state}} for the jobs a verify dispatch can run; every job stale when records are unavailable."""
    result = {}
    profiles = ['push'] + (['real_model'] if real_model else [])
    for workflow in ci_paths.WORKFLOWS:
        jobs = ci_changes.ordered({job for profile in profiles for job in ci_paths.jobs_of(workflow, profile)})
        try:
            parents = git.parents(head)
            states = (ci_changes.merge_staleness if len(parents) > 1 else ci_changes.stale_at)(git, records[workflow], head, jobs)
        except ci_changes.Unavailable as error:
            states = {job: {'stale': True, 'base': None, 'reason': f'fail open ({error})'} for job in jobs}
        result[workflow] = {job: {key: state[key] for key in ('stale', 'base', 'reason')} for job, state in states.items()}
    return result


# ------------------------------------------------------------------------------------------------
# Plan
# ------------------------------------------------------------------------------------------------
def make_plan(*, local_states, ci_states, side='both', all_jobs=False, only=None, real_model=False, model=None, model_note='',
              emsdk=None):
    """What to run: {'local': [jobs], 'dispatch': {workflow: scope}, 'not_run': {job: reason}, 'notes': [...], 'failures': [...]}."""
    plan = {'local': [], 'dispatch': {}, 'not_run': {}, 'notes': [], 'failures': []}
    ci_jobs = {job: workflow for workflow, states in ci_states.items() for job in states}
    if only:
        unknown = [name for name in only if name not in ci_paths.LOCAL_JOBS and name not in ci_paths.JOBS]
        if unknown:
            raise ValueError('unknown jobs: ' + ', '.join(unknown) + ' (see --list)')
    for job, spec in ci_paths.LOCAL_JOBS.items():
        if side == 'ci':
            continue
        wanted = (job in only) if only else (all_jobs or local_states[job]['stale'])
        if not wanted:
            continue
        requires = spec.get('requires')
        if requires == 'real_model' and not real_model:
            plan['not_run'][job] = 'real-model job: add --real-model'
        elif requires == 'real_model' and not model:
            plan['not_run'][job] = f'no model ({model_note})'
            plan['failures'].append('NOT RUN: real_model')
        elif requires == 'emsdk' and not emsdk:
            plan['not_run'][job] = 'browser build: add --emsdk'
        else:
            plan['local'].append(job)
    if side != 'local':
        for workflow, states in ci_states.items():
            if only:
                names = [name for name in only if ci_paths.JOBS.get(name, {}).get('workflow') == workflow and name in ci_paths.JOBS]
                if names:
                    plan['dispatch'][workflow] = ','.join(names)
            elif all_jobs:
                plan['dispatch'][workflow] = 'all'
            elif any(state['stale'] for state in states.values()):
                plan['dispatch'][workflow] = 'affected'
        if not real_model:
            stale_real = [job for job, workflow in ci_jobs.items() if 'real_model' in ci_paths.JOBS[job]['profiles']
                          and 'push' not in ci_paths.JOBS[job]['profiles'] and ci_states[workflow][job]['stale']]
            if stale_real:
                plan['notes'].append('real-model jobs not requested: add --real-model')
    return plan


# ------------------------------------------------------------------------------------------------
# Local jobs
# ------------------------------------------------------------------------------------------------
def local_commands(job, root, model=None, emsdk=None):
    """[(argv, env overrides)] for a local job; outputs in build-verify/<job>/, browser builds in build-browser-<backend>."""
    root = pathlib.Path(root)
    out = root / 'build-verify' / job
    launcher = 'ccache' if shutil.which('ccache') else 'none'
    validate = [PY, 'scripts/validate.py', '--compiler-launcher', launcher]
    commands = {
        'local-bindings': [validate + ['--library-only', '--bindings', 'all', '--build', root / 'build-verify/build-library', '--output', out]],
        'local-packaging': [validate + ['--library-only', '--packages', '--build', root / 'build-verify/build-library', '--output', out]],
        'local-sanitizers': [['cmake', '--preset', 'sanitize'], ['cmake', '--build', root / 'build-sanitize'], ['ctest', '--preset', 'sanitize']],
        'local-fuzz': [['cmake', '--preset', 'fuzz'],
                       [PY, 'scripts/check-fuzz.py', '--seconds', '60', '--compiler-launcher', launcher, '--output', out]],
        'local-real-model': [validate + ['--model', model, '--parity', 'smoke', '--upstream-reference',
                                         '--build', root / 'build-verify/build-real-model', '--output', out]],
        'local-reproducibility': [[PY, 'scripts/check-reproducibility.py', '--output', out]],
        'local-browser-wasm': [[PY, 'scripts/build-browser.py', '--emsdk', emsdk, '--backend', 'wasm', '--reference',
                                '--build', root / 'build-browser-wasm']],
        'local-browser-webgpu': [[PY, 'scripts/build-browser.py', '--emsdk', emsdk, '--backend', 'webgpu', '--reference',
                                  '--build', root / 'build-browser-webgpu']],
    }
    return [[str(part) for part in command] for command in commands[job]]


def run_local(job, root, model, emsdk):
    out = pathlib.Path(root) / 'build-verify' / job
    logs = pathlib.Path(root) / 'build-verify' / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(out, ignore_errors=True)
    started, code = time.monotonic(), 0
    with (logs / f'{job}.log').open('w') as log:
        for command in local_commands(job, root, model, emsdk):
            log.write('$ ' + ' '.join(command) + '\n')
            log.flush()
            try:
                code = subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT).returncode
            except OSError as error:
                log.write(f'verify: cannot run {command[0]}: {error}\n')
                code = 127
            if code:
                break
    return code, round(time.monotonic() - started, 1), logs / f'{job}.log'


# ------------------------------------------------------------------------------------------------
# CI
# ------------------------------------------------------------------------------------------------
def gh(*args, check=True):
    result = subprocess.run(['gh', *args], capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(f'gh {" ".join(args)}: {result.stderr.strip()}')
    return result.stdout


def ci_preconditions(git, root=ROOT):
    """(branch, problems): a dispatch runs the branch tip, so HEAD must be clean and be that tip on the remote."""
    problems = []
    if is_dirty(git):
        problems.append('the working tree has changes (commit them; a dispatch tests the pushed branch)')
    try:
        branch = git.run('symbolic-ref', '--quiet', '--short', 'HEAD').strip()
    except ci_changes.Unavailable:
        return None, problems + ['HEAD is detached (a dispatch targets a branch, not a SHA)']
    head = git.resolve('HEAD')
    try:
        remote = git.run('config', f'branch.{branch}.remote').strip() or 'origin'
    except ci_changes.Unavailable:
        remote = 'origin'
    try:
        tip = git.run('ls-remote', remote, f'refs/heads/{branch}').split()
    except ci_changes.Unavailable as error:
        return branch, problems + [f'cannot read {remote}/{branch}: {error}']
    if not tip or tip[0] != head:
        problems.append(f'HEAD {head[:12]} is not pushed: {remote}/{branch} is ' + (tip[0][:12] if tip else 'missing') + f' (git push {remote} {branch})')
    return branch, problems


def dispatch(workflow, branch, head, scope, real_model):
    started = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=10)
    fields = ['-f', 'mode=verify', '-f', f'scope={scope}'] + (['-f', f'real_model={str(real_model).lower()}'] if workflow == 'native.yml' else [])
    gh('workflow', 'run', workflow, '--ref', branch, *fields)
    for _ in range(30):
        time.sleep(4)
        runs = json.loads(gh('run', 'list', '--workflow', workflow, '--branch', branch, '--event', 'workflow_dispatch', '--limit', '20',
                             '--json', 'databaseId,headSha,createdAt'))
        found = [r for r in runs if r['headSha'] == head and datetime.datetime.fromisoformat(r['createdAt'].replace('Z', '+00:00')) >= started]
        if found:
            return max(found, key=lambda r: r['createdAt'])['databaseId']
    raise RuntimeError(f'the {workflow} dispatch did not appear in gh run list')


def wait_run(run_id):
    while True:
        view = json.loads(gh('run', 'view', str(run_id), '--json', 'status,conclusion,jobs,createdAt,updatedAt,url'))
        if view['status'] == 'completed':
            return view
        time.sleep(POLL_SECONDS)


def run_end(view):
    """When the run's last job completed. Not updatedAt: GitHub updates a finished run later (run 40520083654: its last
    job completed at 7 minutes, updatedAt at 21), which made the wall time three times the work."""
    ends = [job['completedAt'] for job in view.get('jobs', []) if job.get('completedAt')]
    return max(ends) if ends else view.get('updatedAt')


def seconds_between(start, end):
    try:
        parse = lambda value: datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))  # noqa: E731
        return round((parse(end) - parse(start)).total_seconds(), 1)
    except (AttributeError, ValueError, TypeError):
        return None


def ci_results(view):
    """[(job, conclusion, seconds)] of a completed run, skipped jobs left out."""
    rows = []
    for job in view.get('jobs', []):
        if job.get('conclusion') == 'skipped':
            continue
        rows.append((job['name'], job.get('conclusion') or job.get('status'), seconds_between(job.get('startedAt'), job.get('completedAt'))))
    return rows


# ------------------------------------------------------------------------------------------------
# Output
# ------------------------------------------------------------------------------------------------
def timing_table(rows, walls):
    """rows: [(job, where, outcome, seconds)]; walls: [(label, seconds, budget key)]."""
    lines = [f'{"job":38} {"where":6} {"result":9} {"seconds":>8} {"p90 budget":>10}']
    for job, where, outcome, seconds in rows:
        budget = ci_budgets.JOB_BUDGETS.get(job)
        over = seconds is not None and budget is not None and seconds > budget
        lines.append(f'{job:38} {where:6} {outcome:9} {"" if seconds is None else f"{seconds:.0f}":>8} {"" if budget is None else budget:>10}'
                     + ('  OVER BUDGET' if over else ''))
    for label, seconds, key in walls:
        budget = ci_budgets.BUDGETS.get(key)
        over = seconds is not None and budget is not None and seconds > budget
        lines.append(f'{label:38} {"wall":6} {"":9} {"" if seconds is None else f"{seconds:.0f}":>8} {"" if budget is None else budget:>10}'
                     + ('  OVER BUDGET' if over else ''))
    return '\n'.join(lines)


def print_states(title, states):
    print(title)
    for job, state in states.items():
        print(f'  {"STALE" if state["stale"] else "ok   "} {job}: {state["reason"]}')


# ------------------------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------------------------
def main(argv=None, root=ROOT):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    side = p.add_mutually_exclusive_group()
    side.add_argument('--local', action='store_true')
    side.add_argument('--ci', action='store_true')
    p.add_argument('--all', action='store_true')
    p.add_argument('--only')
    p.add_argument('--rerun-failed', action='store_true')
    p.add_argument('--real-model', action='store_true')
    p.add_argument('--model', type=pathlib.Path)
    p.add_argument('--emsdk', type=pathlib.Path)
    p.add_argument('--list', action='store_true')
    p.add_argument('--repository', '--repo', dest='repo', default=os.environ.get('GITHUB_REPOSITORY', REPO))
    a = p.parse_args(argv)
    if sum(map(bool, (a.all, a.only, a.rerun_failed))) > 1:
        p.error('--all, --only and --rerun-failed exclude each other')
    git = ci_changes.Git(root)
    head = git.resolve('HEAD')
    path = state_path(root)
    state = load_state(path)
    dirty = is_dirty(git)
    records = {workflow: ci_changes.Records(ci_changes.GitHubAPI(a.repo), a.repo, workflow) for workflow in ci_paths.WORKFLOWS}
    side_name = 'local' if a.local else 'ci' if a.ci else 'both'

    if a.list:
        print(f'verify: HEAD {head[:12]}{" (working tree changed)" if dirty else ""}; state {path}')
        print_states('local jobs:', local_staleness(git, state, head, dirty))
        for workflow, states in ci_staleness(git, records, head, real_model=True).items():
            print_states(f'CI jobs ({workflow}):', states)
        return 0

    model, model_note = (None, 'not requested')
    if a.real_model:
        model, model_note = discover_model(a.model, root)
    if a.rerun_failed:
        last = state.get('last') or {}
        if last.get('head') != head:
            print('verify: --rerun-failed: the last verify was not of this HEAD; run verify.py again', file=sys.stderr)
            return 2
        plan = {'local': [] if a.ci else list(last.get('local_failed', [])), 'dispatch': {}, 'not_run': {}, 'notes': [], 'failures': [],
                'rerun': {} if a.local else {wf: run for wf, run in (last.get('ci_runs') or {}).items() if (last.get('ci_failed') or {}).get(wf)}}
        a.real_model = a.real_model or bool(last.get('real_model'))
        if a.real_model and not model:
            model, model_note = discover_model(a.model, root)
    else:
        only = [name.strip() for name in a.only.split(',') if name.strip()] if a.only else None
        local_states = local_staleness(git, state, head, dirty) if side_name != 'ci' else {}
        ci_states = ci_staleness(git, records, head, a.real_model) if side_name != 'local' else {}
        try:
            plan = make_plan(local_states=local_states, ci_states=ci_states, side=side_name, all_jobs=a.all, only=only,
                             real_model=a.real_model, model=model, model_note=model_note, emsdk=a.emsdk)
        except ValueError as error:
            p.error(str(error))
        plan['rerun'] = {}
        if local_states:
            print_states('local jobs:', local_states)
        for workflow, states in ci_states.items():
            print_states(f'CI jobs ({workflow}):', states)

    branch = None
    if plan['dispatch'] or plan.get('rerun'):
        branch, problems = ci_preconditions(git, root)
        if problems:
            for problem in problems:
                print(f'verify: cannot dispatch CI: {problem}', file=sys.stderr)
            return 2
    for job, reason in plan['not_run'].items():
        print(f'verify: not run: {job} ({reason})')
    for note in plan['notes']:
        print(f'verify: note: {note}')
    if not plan['local'] and not plan['dispatch'] and not plan.get('rerun'):
        print('verify: nothing stale' + ('; ' + '; '.join(plan['failures']) if plan['failures'] else ''))
        return 1 if plan['failures'] else 0

    runs = {}
    for workflow, scope in plan['dispatch'].items():
        runs[workflow] = dispatch(workflow, branch, head, scope, a.real_model)
        print(f'verify: dispatched {workflow} scope={scope} real_model={str(a.real_model).lower()}: run {runs[workflow]}', flush=True)
    for workflow, run_id in plan.get('rerun', {}).items():
        gh('run', 'rerun', str(run_id), '--failed')
        runs[workflow] = run_id
        print(f'verify: reran the failed jobs of {workflow} run {run_id}', flush=True)

    rows, local_failed, clean = [], [], not dirty
    for job in plan['local']:
        print(f'verify: {job} ...', flush=True)
        code, seconds, log = run_local(job, root, model, a.emsdk)
        rows.append((job, 'local', 'FAILED' if code else 'passed', seconds))
        print(f'verify: {job} {"FAILED (" + str(log) + ")" if code else "passed"} in {seconds:.0f} s', flush=True)
        if code:
            local_failed.append(job)
        elif clean and git.resolve('HEAD') == head and not is_dirty(git):
            state['jobs'][job] = {'commit': head, 'seconds': seconds,
                                  'finished': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')}
            save_state(path, state)

    walls, ci_failed = [], {}
    for workflow, run_id in runs.items():
        print(f'verify: waiting for {workflow} run {run_id} ...', flush=True)
        view = wait_run(run_id)
        for job, conclusion, seconds in ci_results(view):
            rows.append((job, 'ci', 'passed' if conclusion == 'success' else str(conclusion).upper(), seconds))
            if conclusion != 'success':
                ci_failed.setdefault(workflow, []).append(job)
        if view.get('conclusion') != 'success' and workflow not in ci_failed:
            ci_failed[workflow] = [f'run {view.get("conclusion")}']
        walls.append((f'{workflow} run {run_id}', seconds_between(view.get('createdAt'), run_end(view)),
                      'real-model-dispatch' if a.real_model and workflow == 'native.yml' else 'verify-ci-model-free'))
        print(f'verify: {workflow} run {run_id}: {view.get("conclusion")} ({view.get("url", "")})', flush=True)

    state['last'] = {'head': head, 'local_failed': local_failed, 'ci_runs': runs, 'ci_failed': ci_failed, 'real_model': a.real_model}
    save_state(path, state)
    print()
    print(timing_table(rows, walls))
    failures = list(plan['failures']) + [f'local: {job}' for job in local_failed] + \
        [f'{workflow}: {", ".join(jobs)}' for workflow, jobs in ci_failed.items()]
    print()
    if failures:
        print('verify: FAILED: ' + '; '.join(failures) + ' (fix, then python3 scripts/verify.py --rerun-failed)')
        return 1
    print('verify: passed' + ('' if clean else ' (the working tree had changes: local results not recorded)'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
