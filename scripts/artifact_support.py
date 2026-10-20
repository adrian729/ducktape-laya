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
        # Modes come from the host umask; keep only whether a file is executable.
        if info.isdir() or (info.isfile() and info.mode & 0o111): info.mode = 0o755
        elif info.isfile(): info.mode = 0o644
        return info
    # A fixed gzip header and normalized tar metadata remove host/time variance.
    with open(destination, 'wb') as stream:
        with gzip.GzipFile(filename='', mode='wb', fileobj=stream, mtime=epoch) as zipped:
            with tarfile.open(fileobj=zipped, mode='w', format=tarfile.PAX_FORMAT) as archive:
                archive.add(source, arcname=prefix, recursive=False, filter=normalize)
                for path in sorted(source.rglob('*')):
                    archive.add(path, arcname=prefix + '/' + path.relative_to(source).as_posix(), recursive=False, filter=normalize)


def write_zip(destination, files, epoch):
    """Deterministic zip: sorted entries, fixed timestamps and permissions.

    files maps archive names to (bytes, executable)."""
    import time
    import zipfile
    stamp = time.gmtime(max(epoch, 315532800))[:6]  # zip dates start in 1980
    with zipfile.ZipFile(destination, 'w') as archive:
        for name in sorted(files):
            data, executable = files[name]
            info = zipfile.ZipInfo(name, stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (0o100755 if executable else 0o100644) << 16
            archive.writestr(info, data, compresslevel=9)


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
