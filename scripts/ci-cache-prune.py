#!/usr/bin/env python3
"""Prune the GitHub Actions caches of this repository (the nightly `cache-prune` job; needs actions: write).

- Keeps the newest KEEP (2) entries per key prefix and ref. The prefix is the key without the
  save timestamp ccache-action appends (`-2026-10-25T05:17:46.069Z`) and without a trailing hash
  segment (the pin hash, the model or archive digest), which is how the jobs restore by prefix.
- Deletes every entry of a branch ref whose branch no longer exists or whose head is merged into
  main (`compare main...BRANCH` is identical or behind), and of a pull-request ref whose pull
  request is closed. main's entries are only ever thinned to KEEP per prefix.
- When a ref's state cannot be read, its entries are kept (only the KEEP rule applies).

--dry-run lists what would be deleted. Exit status 1 when a listing or a deletion fails.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

KEEP = 2
MAIN = 'main'
TIMESTAMP = re.compile(r'-\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$')
HASH = re.compile(r'-[0-9a-f]{8,}$')


class APIError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class GitHub:
    def __init__(self, repo, token=None):
        self.repo = repo
        self.token = token if token is not None else (os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN'))
        self.base = os.environ.get('GITHUB_API_URL', 'https://api.github.com').rstrip('/')

    def request(self, method, path):
        if not self.token:
            command = ['gh', 'api', '--method', method, path]
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode:
                status = 404 if 'HTTP 404' in result.stderr else None
                raise APIError(f'{method} {path}: {result.stderr.strip()}', status)
            return json.loads(result.stdout) if result.stdout.strip() else None
        request = urllib.request.Request(f'{self.base}/{path}', method=method, headers={
            'Accept': 'application/vnd.github+json', 'Authorization': f'Bearer {self.token}', 'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'ducktape-laya-cache-prune'})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
                return json.loads(body) if body.strip() else None
        except urllib.error.HTTPError as error:
            raise APIError(f'{method} {path}: HTTP {error.code}', error.code) from error
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise APIError(f'{method} {path}: {error}') from error

    def caches(self):
        entries, page = [], 1
        while True:
            answer = self.request('GET', f'repos/{self.repo}/actions/caches?per_page=100&page={page}&sort=created_at&direction=desc')
            entries += answer['actions_caches']
            if not answer['actions_caches'] or len(entries) >= answer.get('total_count', 0):
                return entries
            page += 1

    def branch_merged_or_gone(self, branch):
        """True when the branch is gone or its head is in main, False when it is live, None when unknown."""
        try:
            answer = self.request('GET', f'repos/{self.repo}/compare/{MAIN}...{branch}?per_page=1')
        except APIError as error:
            if error.status == 404:
                try:
                    self.request('GET', f'repos/{self.repo}/branches/{branch}')
                except APIError as missing:
                    return True if missing.status == 404 else None
                return None
            return None
        return answer.get('status') in ('identical', 'behind')

    def pull_closed(self, number):
        try:
            return self.request('GET', f'repos/{self.repo}/pulls/{number}').get('state') == 'closed'
        except APIError as error:
            return True if error.status == 404 else None

    def delete(self, cache_id):
        self.request('DELETE', f'repos/{self.repo}/actions/caches/{cache_id}')


def prefix(key):
    """The restore prefix of a cache key: no save timestamp, no trailing hash segment."""
    return HASH.sub('', TIMESTAMP.sub('', key))


def ref_is_dead(api, ref):
    if ref == f'refs/heads/{MAIN}':
        return False
    if ref.startswith('refs/heads/'):
        return api.branch_merged_or_gone(ref[len('refs/heads/'):])
    match = re.fullmatch(r'refs/pull/(\d+)/(?:merge|head)', ref)
    if match:
        return api.pull_closed(match.group(1))
    return False


def plan(entries, api):
    """[(entry, reason)] to delete."""
    deletions, dead = [], {}
    for ref in sorted({entry['ref'] for entry in entries}):
        dead[ref] = ref_is_dead(api, ref)
    groups = {}
    for entry in entries:
        if dead[entry['ref']]:
            deletions.append((entry, 'ref merged into main or gone'))
        else:
            groups.setdefault((prefix(entry['key']), entry['ref']), []).append(entry)
    for (key_prefix, ref), group in sorted(groups.items()):
        group.sort(key=lambda entry: (entry.get('created_at') or '', entry.get('id', 0)), reverse=True)
        deletions += [(entry, f'beyond the newest {KEEP} of {key_prefix} on {ref}') for entry in group[KEEP:]]
    return deletions, dead


def main(argv=None, api=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--repository', '--repo', dest='repo', default=os.environ.get('GITHUB_REPOSITORY', 'adrian729/ducktape-laya'))
    p.add_argument('--dry-run', action='store_true', help='List the deletions only')
    a = p.parse_args(argv)
    api = api or GitHub(a.repo)
    try:
        entries = api.caches()
    except (APIError, KeyError, TypeError) as error:
        print(f'ci-cache-prune: cannot list caches: {error}', file=sys.stderr)
        return 1
    deletions, dead = plan(entries, api)
    unknown = [ref for ref, state in dead.items() if state is None]
    for ref in unknown:
        print(f'ci-cache-prune: state of {ref} unknown; keeping its newest {KEEP} per prefix')
    size = sum(entry.get('size_in_bytes', 0) for entry, _ in deletions)
    failed = 0
    for entry, reason in deletions:
        print(f'{"would delete" if a.dry_run else "delete"} {entry["id"]} {entry["key"]} ({entry["ref"]}): {reason}')
        if not a.dry_run:
            try:
                api.delete(entry['id'])
            except APIError as error:
                print(f'ci-cache-prune: {error}', file=sys.stderr)
                failed += 1
    total = sum(entry.get('size_in_bytes', 0) for entry in entries)
    print(f'ci-cache-prune: {len(entries)} entries ({total / 1e9:.2f} GB); {"would delete" if a.dry_run else "deleted"} '
          f'{len(deletions) - failed} ({size / 1e9:.2f} GB)' + (f'; {failed} deletions failed' if failed else ''))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
