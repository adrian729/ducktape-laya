#!/usr/bin/env python3
"""Explicitly provision signed, pinned Linux userspaces for isolated package tests."""
import argparse
import copy
import hashlib
import json
import pathlib
import posixpath
import subprocess
import tarfile
import urllib.request

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--output', type=pathlib.Path, required=True)
p.add_argument('--keyring', type=pathlib.Path, default=pathlib.Path('/usr/share/keyrings/ubuntu-archive-keyring.gpg'))
a = p.parse_args()
if not a.keyring.is_file(): p.error('Supply a trusted Ubuntu archive/image signing keyring')
policy = json.loads(pathlib.Path(__file__).with_name('linux-userspaces.json').read_text())
a.output.mkdir(parents=True, exist_ok=False)
def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()
def rootfs_filter(member, destination):
    # The test supplies /dev with bubblewrap. Convert absolute symlinks to
    # equivalent root-relative links so the unpacked tree also stays contained.
    if member.isdev(): return None
    member = copy.copy(member)
    if member.issym() and member.linkname.startswith('/'):
        member.linkname = posixpath.relpath(member.linkname.lstrip('/'), posixpath.dirname(member.name))
    return tarfile.data_filter(member, destination)
for image in policy['images']:
    directory = a.output / image['id']
    directory.mkdir()
    for filename in ['SHA256SUMS', 'SHA256SUMS.gpg', image['filename']]:
        with urllib.request.urlopen(image['directory_url'] + filename, timeout=60) as response, (directory / filename).open('wb') as stream:
            while chunk := response.read(1024 * 1024): stream.write(chunk)
    signature = subprocess.run(['gpgv', '--status-fd=1', '--keyring', str(a.keyring.resolve()),
                                str(directory / 'SHA256SUMS.gpg'), str(directory / 'SHA256SUMS')],
                               text=True, capture_output=True, check=True)
    if '[GNUPG:] VALIDSIG ' + policy['signing_fingerprint'] + ' ' not in signature.stdout:
        raise RuntimeError('Checksum signature did not use the pinned image key')
    sums = {line.split()[1].lstrip('*'): line.split()[0] for line in (directory / 'SHA256SUMS').read_text().splitlines()}
    archive = directory / image['filename']
    if sums.get(image['filename']) != image['sha256'] or digest(archive) != image['sha256']:
        raise RuntimeError('Image differs from pinned signed checksum: ' + image['id'])
    rootfs = directory / 'rootfs'
    rootfs.mkdir()
    with tarfile.open(archive) as stream:
        stream.extractall(rootfs, filter=rootfs_filter)
    record = {**image, 'status': 'provisioned_not_qualified', 'signing_fingerprint': policy['signing_fingerprint'],
              'checksum_file_sha256': digest(directory / 'SHA256SUMS'),
              'keyring_sha256': digest(a.keyring), 'gpg_status': signature.stdout,
              'extraction': 'tar data filter; device entries omitted; absolute symlinks made equivalent root-relative links'}
    (directory / 'provenance.json').write_text(json.dumps(record, indent=2) + '\n')
    print(image['id'] + ': signed checksum and pinned archive verified, extracted ' + str(rootfs), flush=True)
