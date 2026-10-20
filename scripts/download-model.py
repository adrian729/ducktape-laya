#!/usr/bin/env python3
"""Explicitly download and verify a model from its immutable manifest."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.request import urlopen


def default_manifest():
    script = Path(__file__).resolve()
    for candidate in (script.with_name('manifest.json'),
                      script.with_name('model-manifest.json'),
                      script.parent.parent / 'models/manifest.json'):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError('Model manifest not found; pass --manifest')


def digest(path):
    sha = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            sha.update(chunk)
    return sha.hexdigest()


def download(manifest, destination):
    if not manifest.get('url'):
        raise ValueError(f'{manifest["id"]} has no download URL; build it with scripts/convert-model.py')
    expected_size = manifest['size_bytes']
    expected_sha = manifest['sha256']
    if destination.exists():
        if (destination.is_file() and destination.stat().st_size == expected_size
                and digest(destination) == expected_sha):
            print(f'Already verified: {destination}')
            return
        raise ValueError(f'{destination} exists but does not match the manifest; move or remove it first')

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=f'.{destination.name}.',
                                         suffix='.part', delete=False) as output:
            temporary = Path(output.name)
            sha = hashlib.sha256()
            size = 0
            with urlopen(manifest['url'], timeout=60) as source:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > expected_size:
                        raise ValueError('Download exceeds the size in the manifest')
                    sha.update(chunk)
                    output.write(chunk)
            if size != expected_size or sha.hexdigest() != expected_sha:
                raise ValueError('Download does not match the manifest size and SHA-256')
        temporary.chmod(0o644)
        # Avoid silently replacing a file created while the download was running.
        os.link(temporary, destination)
        print(f'Downloaded and verified: {destination}')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', nargs='?', type=Path, default=Path('resources'),
                        help='Destination directory (default: ./resources)')
    parser.add_argument('--manifest', type=Path, help='Model manifest (default: bundled manifest)')
    args = parser.parse_args()
    try:
        manifest = json.loads((args.manifest or default_manifest()).read_text())
        destination = args.directory / manifest['filename']
        download(manifest, destination)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f'error: {error}\n')


if __name__ == '__main__':
    main()
