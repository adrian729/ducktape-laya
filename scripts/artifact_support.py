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


def archive_zip(source, destination, prefix, epoch):
    """A deterministic zip of a directory tree under prefix/ (the Windows native archive)."""
    source = pathlib.Path(source)
    files = {f'{prefix}/{path.relative_to(source).as_posix()}': (path.read_bytes(), bool(path.stat().st_mode & 0o111))
             for path in sorted(source.rglob('*')) if path.is_file()}
    write_zip(destination, files, epoch)


def read_zip(path):
    """The entries of a zip as {name: (bytes, executable)}, in archive order."""
    import zipfile
    with zipfile.ZipFile(path) as archive:
        return {info.filename: (archive.read(info), bool((info.external_attr >> 16) & 0o111))
                for info in archive.infolist() if not info.is_dir()}


def read_tgz(path):
    """The regular files of a tar.gz as {name: (bytes, mode)}, in archive order; other entries are refused."""
    import tarfile
    entries = {}
    with tarfile.open(path, 'r:gz') as archive:
        for member in archive.getmembers():
            if not member.isfile():
                raise ValueError(f'{pathlib.Path(path).name}: unexpected entry {member.name}')
            entries[member.name] = (archive.extractfile(member).read(), member.mode)
    return entries


# Where each package carries a platform's native files.
PAYLOAD_PREFIXES = {'npm': lambda t: f'package/native/{t.npm}/', 'java': lambda t: f'native/{t.name}/',
                    'nuget': lambda t: f'runtimes/{t.nuget}/native/', 'wheel': lambda t: 'ducktape_laya/_native/'}


def payload_names(target, version):
    """The package file holding each kind of payload."""
    return {'npm': f'ducktape-laya-{version}.tgz', 'java': f'ducktape-laya-{version}.jar',
            'nuget': f'DucktapeLaya.{version}.nupkg', 'wheel': f'ducktape_laya-{version}-py3-none-{target.wheel}.whl'}


def native_payload(directory, version, target):
    """{npm|java|nuget|wheel: {entry name: sha256}} of this platform's native files in its packages."""
    import hashlib
    payload = {}
    for kind, name in payload_names(target, version).items():
        path = pathlib.Path(directory) / name
        entries = read_tgz(path) if kind == 'npm' else read_zip(path)
        prefix = PAYLOAD_PREFIXES[kind](target)
        payload[kind] = {entry: hashlib.sha256(data).hexdigest() for entry, (data, _) in sorted(entries.items()) if entry.startswith(prefix)}
        if not payload[kind]:
            raise ValueError(f'{name} has no native files under {prefix}')
    return payload


def set_npm_platforms(manifest, targets):
    """package.json text with its os and cpu arrays naming exactly these targets.

    npm checks os and cpu separately, so they list every OS and every CPU among
    the targets; resolve-addon.cjs rejects a combination without an addon.

    "libc": ["glibc"] keeps npm from installing glibc binaries on musl (Alpine), but
    npm treats an undefined libc (every non-Linux OS) as a mismatch, so the field is
    present only when every target is Linux and removed otherwise."""
    import re
    def unique(values):
        return list(dict.fromkeys(values))
    for key, values in (('os', unique(t.npm.split('-')[0] for t in targets)), ('cpu', unique(t.npm.split('-')[1] for t in targets))):
        array = '[\n' + ',\n'.join(f'    "{value}"' for value in values) + '\n  ]'
        manifest, count = re.subn(rf'"{key}": \[[^\]]*\]', lambda _: f'"{key}": {array}', manifest)
        if count != 1: raise ValueError(f'package.json must have exactly one "{key}" array')
    manifest = re.sub(r'\n  "libc": \[[^\]]*\],?', '', manifest)
    if all(t.npm.startswith('linux-') for t in targets):
        manifest, count = re.subn(r'("cpu": \[[^\]]*\],?)', lambda m: m.group(1) + ('' if m.group(1).endswith(',') else ',') + '\n  "libc": [\n    "glibc"\n  ]' + (',' if m.group(1).endswith(',') else ''), manifest)
        if count != 1: raise ValueError('package.json must have exactly one "cpu" array')
    return manifest


def write_zip(destination, files, epoch, first=()):
    """Deterministic zip: sorted entries, fixed timestamps and permissions.

    files maps archive names to (bytes, executable); names in `first` come first,
    in that order (a jar's manifest must lead for JarInputStream)."""
    import time
    import zipfile
    stamp = time.gmtime(max(epoch, 315532800))[:6]  # zip dates start in 1980
    with zipfile.ZipFile(destination, 'w') as archive:
        for name in [*first, *sorted(set(files) - set(first))]:
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


def platform_section(inventory, platform):
    """One platform's identities in a package inventory: a multi-platform (assembled) inventory's
    fragments[platform], or a single-platform inventory itself (package-native.py output, or a
    0.1.0/0.2.0 release's native-inventory.json, which has no platform field)."""
    if inventory.get('kind') == 'multi-platform':
        if platform not in inventory.get('fragments', {}):
            raise ValueError(f'The packages hold no {platform} fragment')
        return inventory['fragments'][platform]
    if inventory.get('platform', 'linux-x64') != platform:
        raise ValueError(f'The packages are for {inventory.get("platform")}, not {platform}')
    return inventory
