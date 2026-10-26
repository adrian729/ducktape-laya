#!/usr/bin/env python3
"""Download a release candidate run's evidence from GitHub Actions and record it.

The candidate run is .github/workflows/native.yml dispatched with mode=candidate
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
cross-host bundle for the tier-1 gates). The evidence key electron names the
linux-x64 runner's Electron run of the released npm package.

--qualify also downloads the long-gate evidence of a native.yml mode=qualify
run: the artifacts sanitizers, fuzz and upgrade, into OUTPUT/qualify/<artifact>/.
Without --qualify-run it takes the newest successful workflow_dispatch run of
the commit that carries all three unexpired; --qualify-run RUN takes that run,
whose head may be the candidate or an ancestor of it (evidence reuse; the
checker applies the reuse rules). The run must pass the same event,
conclusion, workflow and repository rules, and each artifact's report.json must
name the run's head as its source_commit. ci-run.json records the run as
qualify_run (with its own file list), and evidence.json gains the keys
sanitizers, fuzz and upgrade. Each zip's sha256 must equal the
artifact digest GitHub reports; extraction refuses absolute paths, '..', drive
or backslash names, links and duplicates. Nothing downloaded is executed or
imported.

An existing OUTPUT is refused, except with --qualify: then it must be an earlier
download of the same candidate run (ci-run.json names the same candidate commit,
head and, with --run, run id; every listed file still has its recorded sha256),
and only the qualify run's files are added under OUTPUT/qualify, with qualify_run
in ci-run.json and the qualify keys in evidence.json. Repeating it with the same
qualify run changes nothing; a different qualify run is refused.

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
# The long-gate evidence of a mode=qualify run: artifact name -> evidence key (each artifact's report.json).
QUALIFY_ARTIFACTS = ('sanitizers', 'fuzz', 'upgrade')
# The only repository whose runs are candidate evidence (scripts/release_targets.py checks ci-run.json against the same name).
REPOSITORY = 'adrian729/ducktape-laya'


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
        if platform == 'linux-x64' and item['electron']:
            # The tier-1 Electron gate: the released npm package in Electron on the linux-x64 runner.
            keys['electron'] = f'installed-{platform}-real-model/electron.json'
    return keys


def qualify_keys():
    """Evidence keys of a qualify run -> path relative to the output directory."""
    return {name: f'qualify/{name}/report.json' for name in QUALIFY_ARTIFACTS}


def is_ancestor(ancestor, commit):
    """Whether ancestor is commit or one of its ancestors in this checkout."""
    result = subprocess.run(['git', '-C', str(ROOT), 'merge-base', '--is-ancestor', ancestor, commit], capture_output=True, text=True)
    if result.returncode not in (0, 1):
        raise Refused(f'cannot relate {ancestor} to {commit}: {result.stderr.strip()}')
    return result.returncode == 0


def check_run(run, commit, repository, ancestor=False):
    """The run rules; ancestor allows a head that is an ancestor of commit (a reused qualify run)."""
    problems = []
    head = run.get('head_sha')
    if head != commit and not (ancestor and isinstance(head, str) and is_ancestor(head, commit)):
        problems.append(f'head {head} is not {commit}' + (' or an ancestor of it' if ancestor else ''))
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


def find_run(repository, commit, run_id, required, kind='candidate', ancestor=False):
    if run_id:
        run = gh(f'repos/{repository}/actions/runs/{run_id}')
        check_run(run, commit, repository, ancestor)
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
        rejected.append(f'Run {run["id"]}: lacks {sorted(set(required) - set(artifacts))[:4]} (not a complete {kind} run)')
    raise Refused(f'No successful {kind} run of {WORKFLOW} for {commit}' + (': ' + '; '.join(rejected) if rejected else ''))


def download(repository, artifacts, chosen, staging, prefix=''):
    """Download, digest-check and extract the chosen artifacts into staging/prefix/<name>; returns the artifact listing."""
    listing = {}
    for name in chosen:
        item = artifacts[name]
        digest = item.get('digest') or ''
        if not digest.startswith('sha256:'):
            raise Refused(f'{name}: GitHub reports no sha256 digest')
        archive = staging / f'.{name}.zip'
        gh(f'repos/{repository}/actions/artifacts/{item["id"]}/zip', output=archive)
        if sha256(archive) != digest.removeprefix('sha256:'):
            raise Refused(f'{name}: download does not match the artifact digest')
        safe_extract(archive, staging / prefix / name)
        archive.unlink()
        listing[name] = {'id': item['id'], 'digest': digest, 'size': item.get('size_in_bytes'), 'expires_at': item['expires_at']}
    return listing


def unexpired(run, artifacts, required):
    missing = sorted(name for name in required if name not in artifacts or artifacts[name].get('expired'))
    if missing:
        raise Refused(f'Run {run["id"]} lacks or has expired artifacts: {", ".join(missing)}')


def warn_expiry(artifacts, chosen):
    now = datetime.now(timezone.utc)
    for name in chosen:
        expires = datetime.fromisoformat(artifacts[name]['expires_at'].replace('Z', '+00:00'))
        if expires - now < timedelta(days=7):
            print(f'warning: {name} expires {artifacts[name]["expires_at"]}', file=sys.stderr)


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


def qualify_summary(staging, qualify, qualify_listing, qualify_files, repository):
    """The qualify_run record, after requiring each report.json to name the run's head as its source."""
    for name in QUALIFY_ARTIFACTS:
        try:
            report = json.loads((staging / qualify_keys()[name]).read_text())
        except ValueError as error:
            raise Refused(f'{name}: report.json is not JSON ({error})') from None
        if not isinstance(report, dict) or report.get('source_commit') != qualify['head_sha']:
            raise Refused(f'{name}: report source {report.get("source_commit") if isinstance(report, dict) else None} '
                          f'is not the qualify run\'s head {qualify["head_sha"]}')
    return {'repository': repository, 'workflow_path': qualify['path'], 'run_id': qualify['id'],
            'run_attempt': qualify.get('run_attempt'), 'head_sha': qualify['head_sha'],
            'html_url': qualify.get('html_url'), 'artifacts': qualify_listing, 'files': qualify_files}


def verify_listed(output, files):
    """Every file ci-run.json lists is still there with its recorded sha256."""
    for relative, digest in files.items():
        path = output / relative
        if path.is_symlink() or not path.is_file() or sha256(path) != digest:
            raise Refused(f'{output}: {relative} is missing or differs from ci-run.json')


def add_qualify_keys(output):
    evidence = json.loads((output / 'evidence.json').read_text())
    if any(evidence.get(key) != relative for key, relative in qualify_keys().items()):
        evidence.update(qualify_keys())
        (output / 'evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')


def extend_download(a, output, commit, keys):
    """Add the qualify run's files to the existing download OUTPUT of the same candidate run; returns (qualify run, summary)."""
    try:
        summary = json.loads((output / 'ci-run.json').read_text())
    except (OSError, ValueError) as error:
        raise Refused(f'{output} exists but is not a download of this tool ({error}); choose a new path') from None
    if not isinstance(summary, dict) or summary.get('candidate_commit') != commit or summary.get('head_sha') != commit:
        raise Refused(f'{output} holds a download of {summary.get("candidate_commit") if isinstance(summary, dict) else None} '
                      f'(head {summary.get("head_sha") if isinstance(summary, dict) else None}), not of {commit}')
    if a.run and summary.get('run_id') != a.run:
        raise Refused(f'{output} holds candidate run {summary.get("run_id")}, not {a.run}')
    verify_listed(output, summary['files'])
    run = gh(f'repos/{a.repository}/actions/runs/{summary["run_id"]}')
    check_run(run, commit, a.repository)
    if run['id'] != summary['run_id'] or run['head_sha'] != summary['head_sha']:
        raise Refused(f'{output}: ci-run.json does not match run {summary["run_id"]}')
    qualify, qualify_artifacts = find_run(a.repository, commit, a.qualify_run, QUALIFY_ARTIFACTS, 'qualify', ancestor=True)
    recorded = summary.get('qualify_run')
    if recorded:
        if recorded.get('run_id') != qualify['id']:
            raise Refused(f'{output} already holds qualify run {recorded.get("run_id")}, not {qualify["id"]}; '
                          f'use a new path to download a different qualify run')
        verify_listed(output, recorded['files'])
        add_qualify_keys(output)
        return qualify, summary, False
    unexpired(qualify, qualify_artifacts, QUALIFY_ARTIFACTS)
    warn_expiry(qualify_artifacts, QUALIFY_ARTIFACTS)
    if (output / 'qualify').exists():
        raise Refused(f'{output}/qualify exists but ci-run.json does not list it; remove it or choose a new path')
    staging = pathlib.Path(tempfile.mkdtemp(prefix=output.name + '.partial-', dir=output.parent))
    created = recorded_in_json = False
    try:
        listing = download(a.repository, qualify_artifacts, QUALIFY_ARTIFACTS, staging, 'qualify')
        files = {path.relative_to(staging).as_posix(): sha256(path) for path in sorted(staging.rglob('*'))
                 if path.is_file() and not path.is_symlink()}
        absent = sorted(name for name in QUALIFY_ARTIFACTS if qualify_keys()[name] not in files)
        if absent:
            raise Refused('The qualify run lacks evidence files: ' + ', '.join(f'{name} ({qualify_keys()[name]})' for name in absent))
        summary['qualify_run'] = qualify_summary(staging, qualify, listing, files, a.repository)
        (staging / 'qualify').rename(output / 'qualify')
        created = True
        add_qualify_keys(output)
        # ci-run.json is the commit point: until it lists the qualify run, a failure removes OUTPUT/qualify again.
        (staging / 'ci-run.json').write_text(json.dumps(summary, indent=2) + '\n')
        os.replace(staging / 'ci-run.json', output / 'ci-run.json')
        recorded_in_json = True
    except BaseException:
        if created and not recorded_in_json:
            shutil.rmtree(output / 'qualify', ignore_errors=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return qualify, summary, True


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--candidate', required=True, help='Candidate commit')
    p.add_argument('--output', type=pathlib.Path, required=True, help='New directory for the downloaded evidence')
    p.add_argument('--run', type=int, help='Run ID (default: the newest complete successful candidate run of the commit)')
    p.add_argument('--repository', default=os.environ.get('GITHUB_REPOSITORY', REPOSITORY),
               help=f'Only {REPOSITORY} is accepted')
    p.add_argument('--merge', type=pathlib.Path, help='Evidence manifest to add the keys to (created if missing)')
    p.add_argument('--replace', action='store_true', help='With --merge, overwrite keys that already exist')
    p.add_argument('--qualify', action='store_true', help='Also download the sanitizers, fuzz and upgrade evidence of a mode=qualify run')
    p.add_argument('--qualify-run', type=int, help='With --qualify, the qualify run ID (its head may be an ancestor of the candidate)')
    a = p.parse_args()
    if a.qualify_run and not a.qualify:
        p.error('--qualify-run needs --qualify')
    if a.repository != REPOSITORY:
        raise Refused(f'{a.repository} is not {REPOSITORY}; only its runs are release evidence')
    extending = a.output.exists()
    if extending and not a.qualify:
        p.error(f'{a.output} exists; choose a new path (or pass --qualify to add a qualify run to a download of the same candidate run)')
    commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', '--verify', a.candidate + '^{commit}'], text=True).strip()
    record = json.loads(subprocess.check_output(['git', '-C', str(ROOT), 'show', f'{commit}:release/compatibility.json'], text=True))
    if not record.get('targets'):
        raise Refused(f'{commit} has no release targets in release/compatibility.json')
    required, keys = expected_artifacts(record), evidence_keys(record)
    if a.qualify:
        keys.update(qualify_keys())
    output = a.output.resolve()
    if a.merge:
        # Refuse a clash before downloading anything.
        manifest = a.merge.resolve()
        current = json.loads(manifest.read_text()) if manifest.exists() else {}
        added = {key: pathlib.Path(os.path.relpath(output / relative, manifest.parent)).as_posix() for key, relative in keys.items()}
        clashes = sorted(key for key in added if key in current and current[key] != added[key])
        if clashes and not a.replace:
            raise Refused(f'{manifest} already has {", ".join(clashes)} (pass --replace to overwrite)')
    if extending:
        qualify, summary, added_files = extend_download(a, output, commit, keys)
        if a.merge:
            current.update(added)
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(json.dumps(current, indent=2) + '\n')
        qualify_files = summary['qualify_run']['files']
        print(f'Run {summary["run_id"]} already in {output}; qualify run {qualify["id"]} (head {qualify["head_sha"]}, {qualify.get("html_url")}): '
              f'{len(qualify_files)} files in {output}/qualify' + ('' if added_files else ' (already present)'))
        return
    run, artifacts = find_run(a.repository, commit, a.run, required)
    unexpired(run, artifacts, required)
    chosen = sorted(set(required) | {name for name, item in artifacts.items()
                                     if name.startswith(OPTIONAL_PREFIX) and not item.get('expired')})
    warn_expiry(artifacts, chosen)
    if a.qualify:
        qualify, qualify_artifacts = find_run(a.repository, commit, a.qualify_run, QUALIFY_ARTIFACTS, 'qualify', ancestor=True)
        unexpired(qualify, qualify_artifacts, QUALIFY_ARTIFACTS)
        warn_expiry(qualify_artifacts, QUALIFY_ARTIFACTS)
    staging = pathlib.Path(tempfile.mkdtemp(prefix=output.name + '.partial-', dir=output.parent))
    try:
        listing = download(a.repository, artifacts, chosen, staging)
        if a.qualify:
            qualify_listing = download(a.repository, qualify_artifacts, QUALIFY_ARTIFACTS, staging, 'qualify')
        files = {}
        for path in sorted(staging.rglob('*')):
            if path.is_symlink():
                raise Refused(f'{path}: link in the extracted evidence')
            if path.is_file():
                files[path.relative_to(staging).as_posix()] = sha256(path)
        absent = sorted(key for key, relative in keys.items() if relative != 'ci-run.json' and relative not in files)
        if absent:
            raise Refused('The run lacks evidence files: ' + ', '.join(f'{key} ({keys[key]})' for key in absent))
        # The qualify run's files are listed under qualify_run only, so a candidate-run accessor never accepts them.
        qualify_files = {path: digest for path, digest in files.items() if path.startswith('qualify/')}
        files = {path: digest for path, digest in files.items() if not path.startswith('qualify/')}
        summary = {'schema_version': 1, 'repository': a.repository, 'workflow_path': run['path'], 'run_id': run['id'],
                   'run_attempt': run.get('run_attempt'), 'head_sha': run['head_sha'], 'head_branch': run.get('head_branch'),
                   'event': run['event'], 'status': run['status'], 'conclusion': run['conclusion'], 'created_at': run.get('created_at'),
                   'html_url': run.get('html_url'), 'candidate_commit': commit, 'artifacts': listing, 'files': files}
        if a.qualify:
            summary['qualify_run'] = qualify_summary(staging, qualify, qualify_listing, qualify_files, a.repository)
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
    if a.qualify:
        print(f'Qualify run {qualify["id"]} (head {qualify["head_sha"]}, {qualify.get("html_url")}): {len(qualify_files)} files in {output}/qualify')
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
