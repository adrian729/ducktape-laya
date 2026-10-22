#!/usr/bin/env python3
"""Merge per-platform package directories into one multi-platform package set.

Each FRAGMENT is a scripts/package-native.py output directory from one runner
(linux-x64, linux-arm64, macos-arm64 or windows-x64), verified against its
inventory. All fragments must come from one candidate: the same version, packaging
commit, build sources, runtime and SOURCE_DATE_EPOCH, each platform at most once.

The output directory holds:
- each platform's native archive and Python wheel, unchanged;
- ducktape-laya-VERSION.tgz: one npm package with every platform's
  native/<platform>-<arch>/ directory, its os and cpu fields naming them all;
- ducktape-laya-VERSION.jar: one jar with every platform's native/<platform>/;
- DucktapeLaya.VERSION.nupkg: one NuGet package with every platform's
  runtimes/<rid>/native/;
- the platform-independent files once, as the first fragment (in the order
  linux-x64, linux-arm64, macos-arm64, windows-x64) has them: both .crate
  files, the Maven sources and javadoc jars and POM, the SBOM;
- inventory.json: the artifacts, the sorted platforms, and per platform its
  fragment inventory's hash, build identities, diagnostics, toolchain, archive,
  wheel and native payload (entry name to sha256 in the npm, jar, NuGet
  package and wheel).

Apart from each platform's own native files, the npm, jar and NuGet entries and
the platform-independent files must be identical in every fragment. Archives
are compared by their entries, not their compressed bytes, which differ between
hosts' zlib builds. Each platform's native entries must be the payload its
inventory records. The result depends only on the fragments' contents and this
host's zlib: sorted entries, fixed timestamps and permissions. Nothing is built,
signed or published.
"""
import argparse
import atexit
import gzip
import hashlib
import io
import json
import pathlib
import re
import shutil
import tarfile
import tempfile

from release_support import TARGETS, target, sha256
from artifact_support import PAYLOAD_PREFIXES, native_payload, read_tgz, read_zip, set_npm_platforms, verify_inventory, write_zip
import java_package
import python_wheel

NPM_MTIME = 499162500  # 1985-10-26T08:15:00Z, the fixed time npm pack writes
CONTENT_TYPES = '[Content_Types].xml'

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument('fragments', nargs='+', type=pathlib.Path, metavar='FRAGMENT', help='package-native.py output directory')
parser.add_argument('--output', type=pathlib.Path, required=True)
a = parser.parse_args()
if a.output.exists():
    parser.error(f'{a.output} exists; choose a new path')


class Fragment:
    def __init__(self, directory):
        self.directory = directory.resolve()
        try:
            self.inventory = verify_inventory(self.directory)
        except (OSError, ValueError, KeyError) as error:
            raise SystemExit(f'{directory}: {error}') from None
        if 'platform' not in self.inventory:
            raise SystemExit(f'{directory}: not a per-platform package directory (package-native.py output)')
        self.target = target(self.inventory['platform'])

    def path(self, name):
        if name not in self.inventory['artifacts']:
            raise SystemExit(f'{self.target.name}: the inventory lists no {name}')
        return self.directory / name


fragments = sorted((Fragment(directory) for directory in a.fragments), key=lambda f: [t.name for t in TARGETS].index(f.target.name))
platforms = [f.target.name for f in fragments]
if len(set(platforms)) != len(platforms):
    raise SystemExit(f'Each platform may be given once: {", ".join(platforms)}')
primary = fragments[0]
for key in ('package_version', 'packaging_source_commit', 'build_source', 'runtime', 'source_date_epoch'):
    if any(f.inventory[key] != primary.inventory[key] for f in fragments):
        raise SystemExit(f'The fragments differ in {key}; they are not packages of one candidate')
version, epoch = primary.inventory['package_version'], primary.inventory['source_date_epoch']


def same(label, entries):
    """Entries every fragment must carry with the same bytes; returns the primary's."""
    first = entries[0]
    for fragment, other in zip(fragments[1:], entries[1:]):
        if other.keys() != first.keys():
            raise SystemExit(f'{label}: {fragment.target.name} has different files than {primary.target.name}: '
                             f'{sorted(other.keys() ^ first.keys())}')
        changed = sorted(name for name in first if other[name][0] != first[name][0])
        if changed:
            raise SystemExit(f'{label}: {fragment.target.name} differs from {primary.target.name} in {changed}')
    return first


def split(kind, fragment, entries, prefix):
    """(common entries, this platform's native entries); a fragment carries only its own
    platform, exactly the payload its inventory records."""
    natives = {name: value for name, value in entries.items() if name.startswith(prefix)}
    own = {name: value for name, value in natives.items() if name.startswith(PAYLOAD_PREFIXES[kind](fragment.target))}
    if own.keys() != natives.keys() or not own:
        raise SystemExit(f'{kind}: {fragment.target.name} must carry native files for its own platform only: {sorted(natives)}')
    if {name: hashlib.sha256(data).hexdigest() for name, (data, _) in own.items()} != fragment.inventory['payload'][kind]:
        raise SystemExit(f'{kind}: {fragment.target.name} native files differ from its inventory payload')
    return {name: value for name, value in entries.items() if name not in natives}, own


def write_tgz(path, entries):
    """An npm package tarball: package/ entries, npm's fixed time, no owners, gzip without a timestamp."""
    with open(path, 'wb') as stream:
        with gzip.GzipFile(filename='', mode='wb', fileobj=stream, mtime=0, compresslevel=9) as zipped:
            with tarfile.open(fileobj=zipped, mode='w', format=tarfile.PAX_FORMAT) as archive:
                for name in sorted(entries):
                    data, mode = entries[name]
                    info = tarfile.TarInfo(name)
                    info.size, info.mtime, info.mode = len(data), NPM_MTIME, mode
                    info.uid = info.gid = 0
                    info.uname = info.gname = ''
                    archive.addfile(info, io.BytesIO(data))


def content_types(texts):
    """[Content_Types].xml declaring every fragment's file extensions (.so, .dylib, .dll)."""
    defaults, overrides = {}, {}
    for text in texts:
        for line in text.splitlines():
            if match := re.fullmatch(r'\s*<Default Extension="([^"]+)" ContentType="[^"]+" />', line):
                defaults.setdefault(match[1], line)
            elif match := re.fullmatch(r'\s*<Override PartName="([^"]+)" ContentType="[^"]+" />', line):
                overrides.setdefault(match[1], line)
    lines = texts[0].splitlines()
    start = next(i for i, line in enumerate(lines) if '<Default ' in line)
    end = max(i for i, line in enumerate(lines) if '<Default ' in line or '<Override ' in line) + 1
    if any('<Default ' not in line and '<Override ' not in line for line in lines[start:end]):
        raise SystemExit(f'Unexpected {CONTENT_TYPES} layout')
    # NuGet writes the package's own part types first and the file extensions in order.
    first = [extension for extension in ('rels', 'psmdcp') if extension in defaults]
    ordered = [*first, *sorted(set(defaults) - set(first))]
    body = [defaults[extension] for extension in ordered] + [overrides[name] for name in sorted(overrides)]
    return '\n'.join(lines[:start] + body + lines[end:]) + ('\n' if texts[0].endswith('\n') else '')


# Work in a temporary sibling, so a refused assembly leaves no partial output.
a.output.resolve().parent.mkdir(parents=True, exist_ok=True)
out = pathlib.Path(tempfile.mkdtemp(prefix=a.output.name + '.partial-', dir=a.output.resolve().parent))
atexit.register(shutil.rmtree, out, ignore_errors=True)
everyone = [f.target for f in fragments]

# Per-platform files, unchanged; the wheel must carry the payload its inventory records.
for fragment in fragments:
    if native_payload(fragment.directory, version, fragment.target)['wheel'] != fragment.inventory['payload']['wheel']:
        raise SystemExit(f'wheel: {fragment.target.name} native files differ from its inventory payload')
    for name in (f'ducktape-laya-{version}-{fragment.target.name}{fragment.target.archive}',
                 f'ducktape_laya-{version}-{python_wheel.tag(fragment.target)}.whl'):
        shutil.copy(fragment.path(name), out / name)

# Platform-independent files: the same contents everywhere, written once.
def contents(path):
    if path.suffix == '.crate':
        return read_tgz(path)
    if path.suffix == '.jar':
        return read_zip(path)
    return {path.name: (path.read_bytes(), False)}


for name in (f'ducktape-laya-sys-{version}.crate', f'ducktape-laya-{version}.crate', f'{java_package.ARTIFACT}-{version}-sources.jar',
             f'{java_package.ARTIFACT}-{version}-javadoc.jar', f'{java_package.ARTIFACT}-{version}.pom', 'sbom.cdx.json'):
    same(name, [contents(f.path(name)) for f in fragments])
    shutil.copy(primary.path(name), out / name)

# npm: one package.json naming every OS and CPU, and every platform's native directory.
name = f'ducktape-laya-{version}.tgz'
merged, commons = {}, []
for fragment in fragments:
    common, natives = split('npm', fragment, read_tgz(fragment.path(name)), 'package/native/')
    manifest = common['package/package.json']
    common['package/package.json'] = (set_npm_platforms(manifest[0].decode(), everyone).encode(), manifest[1])
    commons.append(common)
    merged.update(natives)
write_tgz(out / name, {**same(name, commons), **merged})

# Maven jar: every platform's native/<platform>/ library.
name = f'{java_package.ARTIFACT}-{version}.jar'
merged, commons = {}, []
for fragment in fragments:
    common, natives = split('java', fragment, read_zip(fragment.path(name)), 'native/')
    commons.append(common)
    merged.update(natives)
write_zip(out / name, {**same(name, commons), **merged}, epoch, java_package.LEAD)

# NuGet: every platform's runtimes/<rid>/native/ library and its extension in [Content_Types].xml.
name = f'DucktapeLaya.{version}.nupkg'
merged, commons, types = {}, [], []
for fragment in fragments:
    common, natives = split('nuget', fragment, read_zip(fragment.path(name)), 'runtimes/')
    types.append(common.pop(CONTENT_TYPES)[0].decode('utf-8'))
    commons.append(common)
    merged.update(natives)
common = same(name, commons)
order = list(read_zip(primary.path(name)))
# Keep NuGet's entry order, with the runtimes where the first fragment had its own.
position = next(i for i, entry in enumerate(order) if entry.startswith('runtimes/'))
order = [entry for entry in order if not entry.startswith('runtimes/')]
order[position:position] = sorted(merged)
files = {**common, **merged, CONTENT_TYPES: (content_types(types).encode('utf-8'), False)}
write_zip(out / name, files, epoch, order)

inventory = {'schema_version': 2, 'kind': 'multi-platform', 'package_version': version,
             'packaging_source_commit': primary.inventory['packaging_source_commit'],
             'packaging_source_tracked_dirty': any(f.inventory['packaging_source_tracked_dirty'] for f in fragments),
             'runtime': primary.inventory['runtime'], 'build_source': primary.inventory['build_source'],
             'source_date_epoch': epoch, 'platforms': sorted(platforms),
             'fragments': {f.target.name: {
                 'inventory_sha256': sha256(f.directory / 'inventory.json'),
                 **{key: f.inventory[key] for key in ('packaging_source_commit', 'packaging_source_tracked_dirty', 'build_identities',
                                                      'diagnostics', 'toolchain', 'cpu_build_options')},
                 'archive': f'ducktape-laya-{version}-{f.target.name}{f.target.archive}',
                 'wheel': f'ducktape_laya-{version}-{python_wheel.tag(f.target)}.whl', 'payload': f.inventory['payload']}
                 for f in fragments},
             'artifacts': {f.name: {'sha256': sha256(f), 'bytes': f.stat().st_size} for f in sorted(out.iterdir())},
             'status': 'local unsigned development artifacts; not release approval'}
(out / 'inventory.json').write_text(json.dumps(inventory, indent=2) + '\n')
out.chmod(0o755)
out.rename(a.output)
print(a.output.resolve())
