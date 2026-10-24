#!/usr/bin/env python3
"""Check that a release's native packages are the ones its CI candidate run assembled.

attest-release.yml runs this after scripts/check-release-assets.sh. From schema 2 a
release's gates.json names the candidate run (ci_run) its packages and tier-2 evidence
came from. This requires, through the GitHub API, that run to be a successful
workflow_dispatch of .github/workflows/native.yml in adrian729/ducktape-laya on the
tagged commit (the checks of scripts/fetch-ci-evidence.py), to have every artifact a
candidate run of the tagged commit's release targets keeps (a run without candidate=true
lacks the tier-2 stress and reproducibility artifacts), its packages-assembled artifact
to be unexpired with a sha256 digest, the downloaded zip to match that digest, and the
zip's inventory.json to be byte-identical to the published native-inventory.json
(check-release-assets.sh has checked every artifact that inventory lists against the
published files). Schema 1 gates.json (0.1.0 and 0.2.0, built on the release host) has
no candidate run and is accepted for those versions only.

Usage: scripts/check-release-run.py --gates gates.json --inventory native-inventory.json
           --commit COMMIT --record compatibility.json [--repository R]
       [--api DIR]   offline tests: answers from DIR/<endpoint>.json and DIR/<endpoint>.zip instead of gh api
Needs an authenticated gh (GH_TOKEN with actions: read). Standard library only; run with python3 -I.
"""
import argparse
import hashlib
import importlib.util
import json
import pathlib
import shutil
import sys
import tempfile
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('fetch_ci_evidence', HERE / 'fetch-ci-evidence.py')
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)
LEGACY = ('0.1.0', '0.2.0')


def offline(directory):
    """gh api replaced by files: DIR/<endpoint without query>.json, or .zip for a download."""
    def answer(endpoint, output=None):
        path = pathlib.Path(directory) / endpoint.split('?')[0]
        if output is not None:
            source = path.with_name(path.name + '.zip')
            if not source.is_file():
                raise fetch.Refused(f'gh api {endpoint}: not found')
            shutil.copy(source, output)
            return None
        source = path.with_name(path.name + '.json')
        if not source.is_file():
            raise fetch.Refused(f'gh api {endpoint}: not found')
        return json.loads(source.read_text())
    return answer


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--gates', type=pathlib.Path, required=True)
    p.add_argument('--inventory', type=pathlib.Path, required=True, help='The published native-inventory.json')
    p.add_argument('--commit', required=True, help='The tagged commit')
    p.add_argument('--record', type=pathlib.Path, required=True, help="The tagged commit's release/compatibility.json")
    p.add_argument('--repository', default=fetch.REPOSITORY)
    p.add_argument('--api', type=pathlib.Path, help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    if a.api:
        fetch.gh = offline(a.api)
    if a.repository != fetch.REPOSITORY:
        raise fetch.Refused(f'{a.repository} is not {fetch.REPOSITORY}')
    gates, record = json.loads(a.gates.read_text()), json.loads(a.record.read_text())
    if gates.get('schema_version') == 1:
        if record.get('package_version') not in LEGACY:
            raise fetch.Refused(f'A schema 1 gates.json qualifies only {", ".join(LEGACY)}, not {record.get("package_version")}')
        print(f'{record["package_version"]}: schema 1, packages built on the release host; no candidate run to compare')
        return
    run_id = (gates.get('ci_run') or {}).get('run_id')
    if type(run_id) is not int or (gates['ci_run'].get('head_sha') != a.commit):
        raise fetch.Refused(f'gates.json names no candidate run of {a.commit}: {gates.get("ci_run")}')
    run = fetch.gh(f'repos/{a.repository}/actions/runs/{run_id}')
    fetch.check_run(run, a.commit, a.repository)
    artifacts = fetch.run_artifacts(a.repository, run_id)
    missing = sorted(set(fetch.expected_artifacts(record)) - set(artifacts))
    if missing:
        raise fetch.Refused(f'Run {run_id} is not a complete candidate run of the release targets: it lacks {", ".join(missing)}')
    item = artifacts['packages-assembled']
    digest = item.get('digest') or ''
    if item.get('expired') or not digest.startswith('sha256:'):
        raise fetch.Refused(f'Run {run_id}: packages-assembled has expired or has no sha256 digest')
    with tempfile.TemporaryDirectory(prefix='ducktape-release-run-') as directory:
        download = pathlib.Path(directory) / 'packages-assembled.zip'
        fetch.gh(f'repos/{a.repository}/actions/artifacts/{item["id"]}/zip', output=download)
        if fetch.sha256(download) != digest.removeprefix('sha256:'):
            raise fetch.Refused(f'Run {run_id}: the packages-assembled download does not match its digest')
        with zipfile.ZipFile(download) as archive:
            names = [info.filename for info in archive.infolist() if info.filename == 'inventory.json']
            if len(names) != 1:
                raise fetch.Refused(f'Run {run_id}: packages-assembled has {len(names)} inventory.json entries')
            assembled = archive.read('inventory.json')
    published = a.inventory.read_bytes()
    if assembled != published:
        raise fetch.Refused(f'native-inventory.json (sha256 {hashlib.sha256(published).hexdigest()}) is not the packages-assembled '
                            f'inventory of run {run_id} (sha256 {hashlib.sha256(assembled).hexdigest()})')
    print(f'native-inventory.json is the packages-assembled inventory of candidate run {run_id} ({run.get("html_url")}) '
          f'on {a.commit}, artifact digest {digest}')


if __name__ == '__main__':
    try:
        main()
    except fetch.Refused as error:
        raise SystemExit(f'check-release-run: {error}')
