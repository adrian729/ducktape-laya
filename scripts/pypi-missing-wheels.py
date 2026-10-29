#!/usr/bin/env python3
"""Decide which verified wheels still have to be uploaded to PyPI, without ever replacing one.

PyPI never accepts a different file under an existing name, but a publish step that
skips files which already exist (`skip-existing`) would also pass over a wheel that is
there with other bytes. This asks PyPI what the release version already holds
(https://pypi.org/pypi/NAME/VERSION/json; 404 means nothing is published yet) and, for each
wheel in DIST:

  - not on PyPI: it stays in DIST and is uploaded;
  - on PyPI with the wheel's own sha256: it is moved to DIST/../already-published
    (a retry after a partly failed upload skips it);
  - on PyPI with any other sha256, or a file on PyPI that is not one of these wheels: fails.

The wheels in DIST must already be verified against their attestations
(scripts/verify-release-assets.sh): their hashes are the reference, not a SHA256SUMS file,
which is not attested itself. With GITHUB_OUTPUT set, writes `upload=true` when files remain
in DIST and `upload=false` otherwise. Standard library only.

Usage: scripts/pypi-missing-wheels.py --package NAME --version V --dist DIST
       [--pypi-json FILE | --pypi-status 404]   (offline tests: a saved payload or a status instead of the network)
"""
import argparse
import hashlib
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request


class Refused(Exception):
    pass


def published_files(package, version, payload_file=None, status=None):
    """{file name: sha256} PyPI holds for the version; {} when it is not published (404)."""
    if status is not None:
        if status == 404:
            return {}
        raise Refused(f'PyPI answered {status}')
    if payload_file:
        payload = json.loads(pathlib.Path(payload_file).read_text())
    else:
        try:
            with urllib.request.urlopen(f'https://pypi.org/pypi/{package}/{version}/json', timeout=60) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return {}
            raise Refused(f'PyPI answered {error.code} for {package} {version}') from None
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            raise Refused(f'PyPI could not be queried: {error}') from None
    files = {}
    for item in payload.get('urls', []):
        digest = (item.get('digests') or {}).get('sha256')
        if not item.get('filename') or not digest:
            raise Refused(f'PyPI lists a file without a name or sha256: {item.get("filename")}')
        files[item['filename']] = digest
    return files


def plan(published, wheels):
    """(upload, skip): wheel names to upload and to skip; Refused on a differing or foreign file.

    wheels: {name: sha256 of the verified local file}."""
    foreign = sorted(set(published) - set(wheels))
    if foreign:
        raise Refused(f'PyPI holds files that are not part of this release: {", ".join(foreign)}')
    for name in sorted(set(published) & set(wheels)):
        if published[name] != wheels[name]:
            raise Refused(f'{name} is on PyPI with sha256 {published[name]}, but the verified wheel has {wheels[name]}')
    skip = sorted(set(published) & set(wheels))
    return sorted(set(wheels) - set(skip)), skip


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--package', required=True)
    p.add_argument('--version', required=True)
    p.add_argument('--dist', type=pathlib.Path, required=True)
    p.add_argument('--pypi-json', type=pathlib.Path)
    p.add_argument('--pypi-status', type=int)
    a = p.parse_args(argv)
    wheels = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(a.dist.iterdir())}
    if not wheels or not all(name.endswith('.whl') for name in wheels):
        raise Refused(f'{a.dist} must hold only wheels, and at least one')
    published = published_files(a.package, a.version, a.pypi_json, a.pypi_status)
    upload, skip = plan(published, wheels)
    if skip:
        done = a.dist.resolve().parent / 'already-published'
        done.mkdir(exist_ok=True)
        for name in skip:
            (a.dist / name).rename(done / name)
    print(f'{a.package} {a.version}: upload {upload or "nothing"}; already on PyPI with the same bytes: {skip or "none"}')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write(f'upload={"true" if upload else "false"}\n')


if __name__ == '__main__':
    try:
        main()
    except Refused as error:
        raise SystemExit(f'pypi-missing-wheels: {error}')
