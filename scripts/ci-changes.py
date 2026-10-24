#!/usr/bin/env python3
"""Classify the files a CI run changed, for the workflows' `changes` job.

Writes to $GITHUB_OUTPUT (or prints) two flags:
- docs_only: every changed file is documentation that neither the build, the tests
  nor any package reads (DOCS_ONLY below). The heavy jobs are then skipped and the
  docs job runs the link and policy checks instead.
- library: a library input changed (LIBRARY_INPUTS, check-candidate.py's list, and
  the harness of the Windows independent-build job). Only then does that job run on
  a push.

The base is --base (a commit); without one, or when it cannot be read, everything
counts as changed, so nothing is skipped by mistake. Workflow dispatches never skip.
"""
import argparse
import fnmatch
import os
import subprocess
import sys

# Documentation that no build, test or package reads: the docs, the top-level notes and the
# logo files they show.
# Package READMEs (bindings/), THIRD_PARTY_NOTICES.md (packaged), patches/README.md (a
# library input) and test data are not documentation here.
DOCS_ONLY = ['docs/*', 'assets/*', 'README.md', 'CHANGELOG.md', 'SECURITY.md', 'AGENTS.md', 'CLAUDE.md', 'examples/README.md']
# What the library and Node addon are built from (scripts/check-candidate.py LIBRARY_INPUTS, without
# the models), and what the independent-build job runs.
LIBRARY_INPUTS = ['src/*', 'include/*', 'cmake/*', 'patches/*', 'CMakeLists.txt', 'release/compatibility.json',
                  'third_party/*', 'third_party', '.gitmodules', 'bindings/node/*.cpp',
                  'scripts/check-reproducibility.py', 'scripts/release_support.py', 'scripts/fetch-node-sdk.py',
                  'scripts/node-sdk-sha256.json', '.github/workflows/native.yml']


def matches(path, patterns):
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def classify(paths):
    """{'docs_only': bool, 'library': bool} for a list of changed paths; None means unknown (everything changed)."""
    if paths is None:
        return {'docs_only': False, 'library': True}
    return {'docs_only': bool(paths) and all(matches(path, DOCS_ONLY) for path in paths),
            'library': any(matches(path, LIBRARY_INPUTS) for path in paths)}


def changed_paths(base, head):
    if not base or set(base) == {'0'}:
        return None
    result = subprocess.run(['git', 'diff', '--name-only', '--no-renames', base, head], capture_output=True, text=True)
    if result.returncode:
        print(f'ci-changes: cannot compare {base} and {head}; treating everything as changed', file=sys.stderr)
        return None
    return [line for line in result.stdout.splitlines() if line]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--event', required=True, help='github.event_name')
    p.add_argument('--base', default='', help='Commit to compare with')
    p.add_argument('--head', default='HEAD')
    a = p.parse_args()
    paths = None if a.event == 'workflow_dispatch' else changed_paths(a.base, a.head)
    result = classify(paths)
    lines = [f'{key}={str(value).lower()}' for key, value in result.items()]
    print(f'ci-changes: {a.event}, base {a.base or "none"}: ' + ('unknown changes' if paths is None else f'{len(paths)} changed files')
          + '; ' + ', '.join(lines))
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write(''.join(line + '\n' for line in lines))


if __name__ == '__main__':
    main()
