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
