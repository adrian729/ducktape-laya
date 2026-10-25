#!/usr/bin/env python3
"""Run the policy and release-tooling checks concurrently and write policy.json.

The only owner of the policy command table (scripts/ci_paths.py POLICY_CHECKS holds their
inputs). scripts/validate.py --policy runs this script and folds the runs of its policy.json into
its report under the same labels; the CI `policy` job runs it with --stale and the candidate's
`tooling` job with --all.

Selections (one of):
  --all            every check
  --stale          the checks whose inputs changed since the `policy` job's last trusted success:
                   in CI from $CI_POLICY_BASE (ci-changes.py's base_policy; empty: no verified base,
                   every check), otherwise computed the same way from the trusted records; every
                   check when git or the API fails or the classifier, a workflow or an unclassified
                   path changed
  --only A,B       the named checks
  --platform-only  abi-layouts, packaging-identities, browser-backend-identities, typescript-consumers
  --list           print the checks and their inputs, run nothing

policy.json: {"schema_version": 1, "source_commit", "source_tracked_dirty", "selection":
"all"|"platform"|"stale"|"only", "runs": {label: {"command", "exit_code", "seconds", "log"}},
"skipped": {label: reason}}. Exit status 1 when any selected check fails.
"""
import argparse
import concurrent.futures
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ci_paths  # noqa: E402

ROOT = ci_paths.ROOT
WINDOWS = sys.platform == 'win32'
PY = sys.executable
NODE_TEST = ['node', '--experimental-vm-modules', '--test']

# label -> command (relative paths are under the checkout). Labels are validate.py's: release_targets.py
# and check-candidate.py read `packaging-identities` and `browser-backend-identities` by name.
COMMANDS = {
    'result-comparison': [PY, 'tests/result-comparison.py'],
    'qualification-policy': [PY, 'tests/qualification.py'],
    'docs-links': [PY, 'tests/docs-links.py'],
    'docs-links-self-test': [PY, 'tests/docs-links.py', '--self-test'],
    'release-notes': [PY, 'tests/release-notes.py'],
    'ci-changes': [PY, 'tests/ci-changes.py'],
    'abi-layouts': [PY, 'tests/abi-layout.py'],
    'typescript-consumers': [PY, 'tests/typescript/check.py'],
    'packaging-identities': [PY, 'tests/packaging-identity.py'],
    'package-assembly': [PY, 'tests/package-assembly.py'],
    'fetch-ci-evidence': [PY, 'tests/fetch-ci-evidence.py'],
    'release-targets': [PY, 'tests/release-targets.py'],
    'release-assets': [PY, 'tests/release-assets.py'],
    'browser-backend-identities': NODE_TEST + ['tests/browser-backend.mjs'],
    'browser-lifecycle': NODE_TEST + ['tests/browser-lifecycle.mjs'],
    'browser-binding': NODE_TEST + ['bindings/browser/test.mjs'],
    'model-download': [PY, 'tests/download-model.py'],
    'runtime-sources': [PY, 'tests/runtime-sources.py'],
    'merge-validation': [PY, 'tests/merge-validation.py'],
    'validate': [PY, 'tests/validate.py'],
    'check-sanitizers': [PY, 'tests/check-sanitizers.py'],
    'verify': [PY, 'tests/verify.py'],
    'qualify-host': [PY, 'tests/qualify-host.py'],
    'ci-contracts': [PY, 'tests/ci-contracts.py'],
}
# The release checker and the release assets run on the Linux release host and in attest-release.
WINDOWS_SKIPS = {label: 'release checks run on the Linux release host and in attest-release (bash, jq, sha256sum)'
                 for label in ('release-targets', 'release-assets')}
SELECTIONS = ('all', 'platform', 'stale', 'only')


def windows_layout_env(c_compiler):
    """The C probes of abi-layouts need a GNU-style driver: on Windows the clang next to the clang-cl CMake uses."""
    if not c_compiler:
        raise SystemExit('check-policy: on Windows abi-layouts needs CC and CXX or the clang-cl compiler (--c-compiler or '
                         '--cmake-arg=-DCMAKE_C_COMPILER=...)')
    clang = pathlib.Path(c_compiler).with_name('clang.exe')
    return {'CC': str(clang), 'CXX': str(clang.with_name('clang++.exe'))}


def git(*args):
    result = subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def stale_checks(environ=None):
    """The policy checks the `policy` job's staleness selects (ci-changes.py's rule); every check on any failure."""
    environ = os.environ if environ is None else environ
    spec = importlib.util.spec_from_file_location('ci_changes', HERE / 'ci-changes.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    g = module.Git(ROOT)
    if 'CI_POLICY_BASE' in environ:
        base = environ['CI_POLICY_BASE'].strip()
        if not base:
            return list(COMMANDS), 'no verified base ($CI_POLICY_BASE empty): every check'
        try:
            paths, deleted = g.diff(base, g.resolve('HEAD'))
        except module.Unavailable as error:
            return list(COMMANDS), f'fail open ({error}): every check'
        if ci_paths.EVERYTHING in ci_paths.touched_categories(paths):
            return list(COMMANDS), f'the classifier, a workflow or an unclassified path changed since {base[:12]}: every check'
        return [c for c in ci_paths.policy_checks_touched(paths, deleted) if c in COMMANDS], f'changes since {base[:12]}'
    repo = environ.get('GITHUB_REPOSITORY', 'adrian729/ducktape-laya')
    try:
        head = g.resolve('HEAD')
        dirty = bool(git('status', '--porcelain', '--untracked-files=all'))
        state = module.stale_at(g, module.Records(module.GitHubAPI(repo), repo, 'native.yml'), head, ['policy'],
                                working_tree=dirty)['policy']
    except module.Unavailable as error:
        return list(COMMANDS), f'fail open ({error}): every check'
    if state['paths'] is None or ci_paths.EVERYTHING in state['touched']:
        return list(COMMANDS), state['reason'] + ': every check'
    return [c for c in ci_paths.policy_checks_touched(state['paths'], state['deleted']) if c in COMMANDS], state['reason']


def run_check(label, command, out, env):
    started = time.monotonic()
    with (out / f'{label}.log').open('w') as log:
        try:
            code = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=3600).returncode
        except subprocess.TimeoutExpired:
            code = 124
        except OSError as error:
            log.write(f'check-policy: cannot run {command[0]}: {error}\n')
            code = 127
    return {'command': [str(part) for part in command], 'exit_code': code, 'seconds': round(time.monotonic() - started, 3),
            'log': f'{label}.log'}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--all', action='store_true')
    group.add_argument('--stale', action='store_true')
    group.add_argument('--only', help='Comma-separated labels')
    group.add_argument('--platform-only', action='store_true')
    group.add_argument('--list', action='store_true')
    p.add_argument('--jobs', type=int, default=min(os.cpu_count() or 2, 8), help='Checks run at once (default: CPUs, at most 8)')
    p.add_argument('--output', type=pathlib.Path, default=ROOT / 'build-policy', help='Directory for policy.json and the logs')
    p.add_argument('--c-compiler', help='Windows: the clang-cl given to CMake (abi-layouts uses the clang next to it; '
                   'without it, CC and CXX of the environment)')
    p.add_argument('--cmake-arg', action='append', default=[], help='Windows: validate.py\'s --cmake-arg values; '
                   '-DCMAKE_C_COMPILER=... selects the compiler as --c-compiler does')
    a = p.parse_args(argv)
    if a.list:
        for label in COMMANDS:
            marks = '  [platform]' if label in ci_paths.PLATFORM_POLICY_CHECKS else ''
            print(f'{label}{marks}  {" ".join(map(str, COMMANDS[label][1:] if COMMANDS[label][0] == PY else COMMANDS[label]))}')
            print(f'    inputs: {", ".join(ci_paths.POLICY_CHECKS.get(label, ["(none: always stale)"]))}')
        return 0
    if a.jobs < 1:
        p.error('--jobs must be positive')
    note = ''
    if a.all:
        selection, labels = 'all', list(COMMANDS)
    elif a.platform_only:
        selection, labels = 'platform', list(ci_paths.PLATFORM_POLICY_CHECKS)
    elif a.stale:
        selection = 'stale'
        labels, note = stale_checks()
    else:
        selection = 'only'
        labels = list(dict.fromkeys(label.strip() for label in a.only.split(',') if label.strip()))
        unknown = [label for label in labels if label not in COMMANDS]
        if unknown or not labels:
            p.error('unknown policy checks: ' + ', '.join(unknown) + '; see --list')
    c_compiler = a.c_compiler or next((arg.split('=', 1)[1] for arg in a.cmake_arg if arg.startswith('-DCMAKE_C_COMPILER=')), None)
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    report = {'schema_version': 1, 'source_commit': git('rev-parse', 'HEAD'),
              'source_tracked_dirty': bool(git('diff', 'HEAD', '--name-only')), 'selection': selection, 'runs': {}, 'skipped': {}}
    if note:
        report['note'] = note
    env = dict(os.environ)
    env.pop('CI_POLICY_BASE', None)
    jobs = {}
    for label in labels:
        if WINDOWS and label in WINDOWS_SKIPS:
            report['skipped'][label] = WINDOWS_SKIPS[label]
            continue
        command_env = env
        if WINDOWS and label == 'abi-layouts' and (c_compiler or not env.get('CC')):
            command_env = dict(env, **windows_layout_env(c_compiler))  # a CC set by the caller stands without --c-compiler
        jobs[label] = (list(COMMANDS[label]), command_env)
    for label in COMMANDS:
        if label not in labels:
            report['skipped'][label] = f'not selected (--{"platform-only" if selection == "platform" else selection})'
    print(f'check-policy: {selection}{" (" + note + ")" if note else ""}: {", ".join(jobs) or "nothing to run"}', flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.jobs) as pool:
        futures = {label: pool.submit(run_check, label, command, out, command_env) for label, (command, command_env) in jobs.items()}
        for label in jobs:
            result = futures[label].result()
            report['runs'][label] = result
            print(f'{label}: {"FAILED" if result["exit_code"] else "passed"} ({result["seconds"]:.1f} s)', flush=True)
    report['runs'] = {label: report['runs'][label] for label in COMMANDS if label in report['runs']}
    (out / 'policy.json').write_text(json.dumps(report, indent=2) + '\n')
    failed = [label for label, run in report['runs'].items() if run['exit_code']]
    print(f'check-policy: {len(report["runs"]) - len(failed)} passed, {len(failed)} failed'
          + (f' ({", ".join(failed)}; logs in {out})' if failed else '') + f'; {out / "policy.json"}')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
