"""Deterministic local artifact helpers (build tooling only)."""
import gzip
import pathlib
import tarfile

def archive_tree(source, destination, prefix, epoch):
    source = pathlib.Path(source)
    def normalize(info):
        info.uid = info.gid = 0
        info.uname = info.gname = ''
        info.mtime = epoch
        info.pax_headers = {}
        return info
    # A fixed gzip header and normalized tar metadata remove host/time variance.
    with open(destination, 'wb') as stream:
        with gzip.GzipFile(filename='', mode='wb', fileobj=stream, mtime=epoch) as zipped:
            with tarfile.open(fileobj=zipped, mode='w', format=tarfile.PAX_FORMAT) as archive:
                archive.add(source, arcname=prefix, recursive=False, filter=normalize)
                for path in sorted(source.rglob('*')):
                    archive.add(path, arcname=prefix + '/' + path.relative_to(source).as_posix(), recursive=False, filter=normalize)


def verify_inventory(directory):
    """Verify every declared artifact before an installed-consumer test uses it."""
    from release_support import read_json, sha256
    directory = pathlib.Path(directory).resolve()
    inventory = read_json(directory / 'inventory.json')
    artifacts = inventory.get('artifacts')
    if not isinstance(artifacts, dict) or not artifacts:
        raise ValueError('Missing artifact inventory')
    for name, identity in artifacts.items():
        if pathlib.Path(name).name != name or name in ('.', '..'):
            raise ValueError('Artifact name must be a local filename')
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing or linked artifact: ' + name)
        if (type(identity.get('bytes')) is not int or identity['bytes'] < 0 or
                path.stat().st_size != identity['bytes'] or sha256(path) != identity.get('sha256')):
            raise ValueError('Artifact size/hash mismatch: ' + name)
    return inventory
