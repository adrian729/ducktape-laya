#!/usr/bin/env python3
"""Whether a registry already holds a release's package, and whether it is the same package.

publish-release.yml asks this before each upload, so that a run that failed after some
uploads can be retried without uploading twice and without passing over a package that a
registry holds with other contents. It prints `absent` (upload it) or `published` (the
registry has this version with the same contents: skip it), with GITHUB_OUTPUT set also
`state=...`, and fails when the registry has the version with other contents, or holds
only part of it, or cannot be asked.

  npm     https://registry.npmjs.org/NAME/VERSION: dist.integrity (sha512) and dist.shasum
          (sha1) must be those of FILE. npm serves the uploaded tarball unchanged.
  crates  https://crates.io/api/v1/crates/NAME/VERSION: the version's checksum must be the
          sha256 of FILE (crates.io stores the .crate cargo publish uploads).
  nuget   https://api.nuget.org/v3-flatcontainer/ID/index.json lists the version; its .nupkg
          is then downloaded and compared entry by entry with FILE: the same entry names with
          the same bytes, apart from .signature.p7s, the repository signature nuget.org adds
          (so the package file's own hash differs). With --wait SECONDS a missing version is
          polled for until it appears (a push that the registry answered with "already
          exists" while the version was still being validated).
  maven   https://repo1.maven.org/maven2/GROUP/ARTIFACT/VERSION/<file> for each FILE: all
          absent, or all present with FILE's sha256. (A version published but not yet on repo1
          is uploaded again, and the Central Portal refuses it: that attempt fails, nothing is
          replaced; rerun once repo1 serves it.)

PyPI has its own check (scripts/pypi-missing-wheels.py). Standard library only.

Usage: scripts/registry-state.py npm|crates --name NAME --version V --file FILE
       scripts/registry-state.py nuget --name ID --version V --file FILE [--wait SECONDS]
       scripts/registry-state.py maven --group G --name ARTIFACT --version V FILE...
       [--responses FILE]   offline tests: {url: {"status": N, "file": path}} instead of the network
"""
import argparse
import base64
import hashlib
import io
import json
import os
import pathlib
import time
import urllib.error
import urllib.request
import zipfile

USER_AGENT = 'ducktape-laya release (github.com/adrian729/ducktape-laya)'
SIGNATURE = '.signature.p7s'


class Refused(Exception):
    pass


class Network:
    def __init__(self, responses=None):
        self.responses = json.loads(pathlib.Path(responses).read_text()) if responses else None

    def get(self, url, headers=None):
        """(status, bytes): 200 with the body, or 404 with b''. Anything else is Refused."""
        if self.responses is not None:
            answer = self.responses.get(url)
            if answer is None:
                raise Refused(f'no canned response for {url}')
            if isinstance(answer, list):  # successive answers (polling)
                answer = answer.pop(0) if len(answer) > 1 else answer[0]
            status = answer['status']
            body = pathlib.Path(answer['file']).read_bytes() if answer.get('file') else b''
        else:
            request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, **(headers or {})})
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    status, body = response.status, response.read()
            except urllib.error.HTTPError as error:
                status, body = error.code, b''
            except (urllib.error.URLError, TimeoutError) as error:
                raise Refused(f'{url} could not be fetched: {error}') from None
        if status not in (200, 404):
            raise Refused(f'{url} answered {status}')
        return status, body


def digest(data, name):
    return hashlib.new(name, data).digest()


def npm(net, a):
    data = pathlib.Path(a.file).read_bytes()
    status, body = net.get(f'https://registry.npmjs.org/{a.name}/{a.version}')
    if status == 404:
        return 'absent'
    dist = json.loads(body).get('dist') or {}
    integrity = 'sha512-' + base64.b64encode(digest(data, 'sha512')).decode()
    if dist.get('integrity') != integrity or dist.get('shasum') != digest(data, 'sha1').hex():
        raise Refused(f'npm holds {a.name}@{a.version} with integrity {dist.get("integrity")}, not {integrity} ({pathlib.Path(a.file).name})')
    return 'published'


def crates(net, a):
    data = pathlib.Path(a.file).read_bytes()
    status, body = net.get(f'https://crates.io/api/v1/crates/{a.name}/{a.version}')
    if status == 404:
        return 'absent'
    checksum = (json.loads(body).get('version') or {}).get('checksum')
    if checksum != hashlib.sha256(data).hexdigest():
        raise Refused(f'crates.io holds {a.name} {a.version} with checksum {checksum}, not {hashlib.sha256(data).hexdigest()}')
    return 'published'


def nupkg_entries(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {info.filename: hashlib.sha256(archive.read(info)).hexdigest() for info in archive.infolist() if info.filename != SIGNATURE}


def nuget(net, a):
    package, version = a.name.lower(), a.version.lower()
    base = f'https://api.nuget.org/v3-flatcontainer/{package}'
    deadline = time.monotonic() + a.wait
    while True:
        status, body = net.get(f'{base}/index.json')
        if status == 200 and version in json.loads(body).get('versions', []):
            break
        if time.monotonic() >= deadline:
            if a.wait:
                raise Refused(f'NuGet does not list {a.name} {a.version} after {a.wait} s')
            return 'absent'
        time.sleep(min(a.poll, max(0, deadline - time.monotonic())))
    status, body = net.get(f'{base}/{version}/{package}.{version}.nupkg')
    if status == 404:
        raise Refused(f'NuGet lists {a.name} {a.version} but does not serve its package')
    mine, theirs = nupkg_entries(pathlib.Path(a.file).read_bytes()), nupkg_entries(body)
    if mine != theirs:
        changed = sorted(name for name in mine.keys() | theirs.keys() if mine.get(name) != theirs.get(name))
        raise Refused(f'NuGet holds {a.name} {a.version} with other contents: {", ".join(changed[:6])}')
    return 'published'


def maven(net, a):
    base = f'https://repo1.maven.org/maven2/{a.group.replace(".", "/")}/{a.name}/{a.version}'
    found = {}
    for path in a.files:
        status, body = net.get(f'{base}/{pathlib.Path(path).name}')
        found[pathlib.Path(path).name] = None if status == 404 else hashlib.sha256(body).hexdigest()
    if all(value is None for value in found.values()):
        # A version uploaded by an earlier attempt that repo1 does not serve yet is uploaded again; the
        # Central Portal refuses an existing version (the deployment fails, nothing is replaced).
        return 'absent'
    differing = sorted(name for name, path in ((pathlib.Path(p).name, p) for p in a.files)
                       if found[name] != hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest())
    if differing:
        raise Refused(f'Maven Central holds {a.group}:{a.name}:{a.version} with other or missing files: {", ".join(differing)}')
    return 'published'


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('registry', choices=['npm', 'crates', 'nuget', 'maven'])
    p.add_argument('--name', required=True)
    p.add_argument('--version', required=True)
    p.add_argument('--file')
    p.add_argument('--group')
    p.add_argument('files', nargs='*')
    p.add_argument('--wait', type=int, default=0)
    p.add_argument('--poll', type=int, default=30, help=argparse.SUPPRESS)
    p.add_argument('--responses', help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    if a.registry == 'maven':
        if not a.group or not a.files or a.file:
            p.error('maven takes --group and the files')
    elif not a.file or a.files:
        p.error(f'{a.registry} takes --file')
    if a.wait < 0 or (a.wait and a.registry != 'nuget'):
        p.error('--wait is for nuget')
    state = {'npm': npm, 'crates': crates, 'nuget': nuget, 'maven': maven}[a.registry](Network(a.responses), a)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
            stream.write(f'state={state}\n')
    print(state)


if __name__ == '__main__':
    try:
        main()
    except Refused as error:
        raise SystemExit(f'registry-state: {error}')
