#!/usr/bin/env python3
"""Download a release candidate run's evidence from GitHub Actions and record it.

The candidate run is .github/workflows/native.yml dispatched with candidate=true
on the exact candidate commit (docs/RELEASE_CHECKS.md). This finds the newest
successful such run for COMMIT (or takes --run), requires its head commit, event,
conclusion, workflow and repository, and downloads the artifacts the candidate's
own release/compatibility.json targets need:

  packages-assembled                 the release packages (multi-platform assembly)
  evidence-<platform>                validate.py report, outputs and reference reports
  packages-<platform>                the platform's fragment (package-native.py output)
  installed-<platform>-real-model    installed release packages (and Electron) on that runner
  stress-<platform>, reproducibility-<platform>   tier-2 targets only

plus, when present, the native-x64-* validation artifacts (native matrix and
cross-host bundle for the tier-1 gates). Each zip's sha256 must equal the
artifact digest GitHub reports; extraction refuses absolute paths, '..', drive
or backslash names, links and duplicates. Nothing downloaded is executed or
imported.

Writes OUTPUT/ci-run.json (the run, the artifacts and the sha256 of every
extracted file; scripts/check-candidate.py rejects a CI file that is not listed
there or has changed) and OUTPUT/evidence.json (evidence keys for
check-candidate.py, relative to OUTPUT). --merge adds the keys to an existing
evidence manifest, relative to its directory, without overwriting a key unless
--replace. Needs git and an authenticated gh. Run with python3 -I.
"""
import argparse
import hashlib
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = '.github/workflows/native.yml'
EVENT, CONCLUSION = 'workflow_dispatch', 'success'
OPTIONAL_PREFIX = 'native-x64-'


class Refused(Exception):
    pass


def gh(*args, output=None):
    """gh api; JSON unless output (a file to stream binary content into)."""
    command = ['gh', 'api', *args]
    if output is not None:
        with open(output, 'wb') as stream:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.PIPE)
        if result.returncode:
            raise Refused(f'gh api {args[0]}: {result.stderr.decode(errors="replace").strip()}')
        return None
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise Refused(f'gh api {args[0]}: {result.stderr.strip()}')
    return json.loads(result.stdout)


def sha256(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def expected_artifacts(record):
    """{artifact name: description} the candidate's targets need."""
    names = {'packages-assembled': 'release packages'}
    for item in record['targets']:
        platform = item['platform']
        names[f'evidence-{platform}'] = f'{platform} validation'
        names[f'packages-{platform}'] = f'{platform} fragment'
        names[f'installed-{platform}-real-model'] = f'{platform} installed release packages'
        if item['tier'] != 1:
            names[f'stress-{platform}'] = f'{platform} short stress'
            names[f'reproducibility-{platform}'] = f'{platform} reproducibility'
    return names


def evidence_keys(record):
    """check-candidate.py evidence key -> path relative to the output directory."""
    keys = {'ci_run': 'ci-run.json', 'release_packages': 'packages-assembled/inventory.json'}
    for item in record['targets']:
        platform = item['platform']
        keys[f'{platform}:native'] = f'evidence-{platform}/report.json'
        keys[f'{platform}:fragment'] = f'packages-{platform}/inventory.json'
        keys[f'{platform}:installed'] = f'installed-{platform}-real-model/installed.json'
        if item['electron']:
            keys[f'{platform}:electron'] = f'installed-{platform}-real-model/electron.json'
        if item['tier'] != 1:
            keys[f'{platform}:stress'] = f'stress-{platform}/report.json'
            keys[f'{platform}:reproducibility'] = f'reproducibility-{platform}/report.json'
    return keys


def check_run(run, commit, repository):
    problems = []
    if run.get('head_sha') != commit: problems.append(f'head {run.get("head_sha")} is not {commit}')
    if run.get('event') != EVENT: problems.append(f'event {run.get("event")}, not {EVENT}')
    if run.get('status') != 'completed' or run.get('conclusion') != CONCLUSION:
        problems.append(f'{run.get("status")}/{run.get("conclusion")}, not completed/{CONCLUSION}')
    if run.get('path') != WORKFLOW: problems.append(f'workflow {run.get("path")}, not {WORKFLOW}')
    if (run.get('repository') or {}).get('full_name') != repository or (run.get('head_repository') or {}).get('full_name') != repository:
        problems.append(f'not a run of {repository} on its own branch')
    if problems:
        raise Refused(f'Run {run.get("id")}: ' + '; '.join(problems))


def run_artifacts(repository, run_id):
    artifacts, page = [], 1
    while True:
        batch = gh(f'repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100&page={page}')['artifacts']
        artifacts += batch
        if len(batch) < 100: return {item['name']: item for item in artifacts}
        page += 1


def find_run(repository, commit, run_id, required):
    if run_id:
        run = gh(f'repos/{repository}/actions/runs/{run_id}')
        check_run(run, commit, repository)
        return run, run_artifacts(repository, run['id'])
    runs = gh(f'repos/{repository}/actions/workflows/{pathlib.PurePosixPath(WORKFLOW).name}/runs'
              f'?head_sha={commit}&event={EVENT}&status={CONCLUSION}&per_page=100')['workflow_runs']
    rejected = []
    for run in sorted(runs, key=lambda item: (item.get('created_at', ''), item.get('run_attempt', 0)), reverse=True):
        try:
            check_run(run, commit, repository)
        except Refused as error:
            rejected.append(str(error))
            continue
        artifacts = run_artifacts(repository, run['id'])
        if set(required) <= {name for name, item in artifacts.items() if not item.get('expired')}:
            return run, artifacts
        rejected.append(f'Run {run["id"]}: lacks {sorted(set(required) - set(artifacts))[:4]} (not a complete candidate run)')
    raise Refused(f'No successful candidate run of {WORKFLOW} for {commit}' + (': ' + '; '.join(rejected) if rejected else ''))


def safe_extract(archive_path, destination):
    """Extract a downloaded artifact zip, refusing anything that could land outside destination."""
    seen = set()
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            name = info.filename
            parts = name.rstrip('/').split('/')
            mode = info.external_attr >> 16
            if (not name or name.startswith('/') or '\\' in name or ':' in name or any(part in ('', '.', '..') for part in parts)
                    or stat.S_ISLNK(mode)):
                raise Refused(f'{archive_path.name}: unsafe entry {name!r}')
            key = '/'.join(parts).casefold()
            if key in seen:
                raise Refused(f'{archive_path.name}: duplicate entry {name!r}')
            seen.add(key)
        for info in archive.infolist():
            target = destination.joinpath(*info.filename.rstrip('/').split('/'))
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(target, 'xb') as stream:
                shutil.copyfileobj(source, stream)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--candidate', required=True, help='Candidate commit')
    p.add_argument('--output', type=pathlib.Path, required=True, help='New directory for the downloaded evidence')
    p.add_argument('--run', type=int, help='Run ID (default: the newest complete successful candidate run of the commit)')
    p.add_argument('--repository', default=os.environ.get('GITHUB_REPOSITORY', 'adrian729/ducktape-laya'))
    p.add_argument('--merge', type=pathlib.Path, help='Evidence manifest to add the keys to (created if missing)')
    p.add_argument('--replace', action='store_true', help='With --merge, overwrite keys that already exist')
    a = p.parse_args()
    if a.output.exists():
        p.error(f'{a.output} exists; choose a new path')
    commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', '--verify', a.candidate + '^{commit}'], text=True).strip()
    record = json.loads(subprocess.check_output(['git', '-C', str(ROOT), 'show', f'{commit}:release/compatibility.json'], text=True))
    if not record.get('targets'):
        raise Refused(f'{commit} has no release targets in release/compatibility.json')
    required, keys = expected_artifacts(record), evidence_keys(record)
    output = a.output.resolve()
    if a.merge:
        # Refuse a clash before downloading anything.
        manifest = a.merge.resolve()
        current = json.loads(manifest.read_text()) if manifest.exists() else {}
        added = {key: os.path.relpath(output / relative, manifest.parent) for key, relative in keys.items()}
        clashes = sorted(key for key in added if key in current and current[key] != added[key])
        if clashes and not a.replace:
            raise Refused(f'{manifest} already has {", ".join(clashes)} (pass --replace to overwrite)')
    run, artifacts = find_run(a.repository, commit, a.run, required)
    missing = sorted(name for name in required if name not in artifacts or artifacts[name].get('expired'))
    if missing:
        raise Refused(f'Run {run["id"]} lacks or has expired artifacts: {", ".join(missing)}')
    chosen = sorted(set(required) | {name for name, item in artifacts.items()
                                     if name.startswith(OPTIONAL_PREFIX) and not item.get('expired')})
    now = datetime.now(timezone.utc)
    for name in chosen:
        expires = datetime.fromisoformat(artifacts[name]['expires_at'].replace('Z', '+00:00'))
        if expires - now < timedelta(days=7):
            print(f'warning: {name} expires {artifacts[name]["expires_at"]}', file=sys.stderr)
    staging = pathlib.Path(tempfile.mkdtemp(prefix=output.name + '.partial-', dir=output.parent))
    try:
        files, listing = {}, {}
        for name in chosen:
            item = artifacts[name]
            digest = item.get('digest') or ''
            if not digest.startswith('sha256:'):
                raise Refused(f'{name}: GitHub reports no sha256 digest')
            download = staging / f'.{name}.zip'
            gh(f'repos/{a.repository}/actions/artifacts/{item["id"]}/zip', output=download)
            if sha256(download) != digest.removeprefix('sha256:'):
                raise Refused(f'{name}: download does not match the artifact digest')
            safe_extract(download, staging / name)
            download.unlink()
            listing[name] = {'id': item['id'], 'digest': digest, 'size': item.get('size_in_bytes'), 'expires_at': item['expires_at']}
        for path in sorted(staging.rglob('*')):
            if path.is_symlink():
                raise Refused(f'{path}: link in the extracted evidence')
            if path.is_file():
                files[path.relative_to(staging).as_posix()] = sha256(path)
        absent = sorted(key for key, relative in keys.items() if relative != 'ci-run.json' and relative not in files)
        if absent:
            raise Refused('The run lacks evidence files: ' + ', '.join(f'{key} ({keys[key]})' for key in absent))
        summary = {'schema_version': 1, 'repository': a.repository, 'workflow_path': run['path'], 'run_id': run['id'],
                   'run_attempt': run.get('run_attempt'), 'head_sha': run['head_sha'], 'head_branch': run.get('head_branch'),
                   'event': run['event'], 'status': run['status'], 'conclusion': run['conclusion'], 'created_at': run.get('created_at'),
                   'html_url': run.get('html_url'), 'candidate_commit': commit, 'artifacts': listing, 'files': files}
        (staging / 'ci-run.json').write_text(json.dumps(summary, indent=2) + '\n')
        (staging / 'evidence.json').write_text(json.dumps(keys, indent=2) + '\n')
        staging.rename(output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if a.merge:
        current.update(added)
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps(current, indent=2) + '\n')
    print(f'Run {run["id"]} (attempt {run.get("run_attempt")}, {run.get("html_url")}): {len(chosen)} artifacts, {len(files)} files in {output}')
    tier2 = [item['platform'] for item in record['targets'] if item['tier'] != 1]
    rel = output.as_posix()
    print('Next, on this host (see docs/RELEASE_CHECKS.md):')
    print(f'  compare library_sha256 and addon_sha256 of build-candidate/native/report.json and {rel}/evidence-linux-x64/report.json')
    print(f'  python3 tests/installed-packages.py {rel}/packages-assembled MODEL --platform linux-x64 --report build-candidate/installed-release.json')
    for platform in tier2:
        print(f'  python3 tests/compare-profiles.py build-candidate/native/outputs.json {rel}/evidence-{platform}/outputs.json '
              f'--target-platform {platform} --report build-candidate/drift-{platform}.json')


if __name__ == '__main__':
    try:
        main()
    except Refused as error:
        raise SystemExit(f'fetch-ci-evidence: {error}')
