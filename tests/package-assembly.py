#!/usr/bin/env python3
"""Model-free checks of scripts/assemble-packages.py on synthetic per-platform fragments.

The fragments have the layout of scripts/package-native.py outputs, with small
stand-in files. Checks the merged npm, jar and NuGet contents, that the result
does not depend on the order of the fragments and is byte-identical when
repeated, and that fragments of different candidates, a platform given twice,
another platform's native files, differing shared files, native files other than
the recorded payload and files that differ from their inventory are refused."""
import gzip
import io
import json
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

root = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'scripts'))
from artifact_support import native_payload, read_zip, set_npm_platforms, write_zip
from release_support import TARGETS, sha256, target
import java_package
import python_wheel

VERSION, EPOCH = '9.8.7', 1700000000
ASSEMBLE = root / 'scripts/assemble-packages.py'


def tgz(path, entries, level=9):
    with open(path, 'wb') as stream, gzip.GzipFile(filename='', mode='wb', fileobj=stream, mtime=0, compresslevel=level) as zipped:
        with tarfile.open(fileobj=zipped, mode='w') as archive:
            for name, data in entries.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))


def content_types(extensions):
    lines = ['<?xml version="1.0" encoding="utf-8"?>', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
             '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml" />',
             '  <Default Extension="psmdcp" ContentType="application/vnd.openxmlformats-package.core-properties+xml" />',
             *[f'  <Default Extension="{extension}" ContentType="application/octet" />' for extension in sorted(extensions)],
             '  <Override PartName="/LICENSE" ContentType="application/octet" />', '</Types>']
    return '\n'.join(lines).encode()


def fragment(directory, name, commit='a' * 40, shared=b'shared', foreign=None, level=9):
    """A package-native.py-shaped output for one platform; foreign adds another platform's native file,
    level compresses the shared archives differently (as another host's zlib would)."""
    item = target(name)
    out = directory / name
    out.mkdir()
    library = f'{name} library'.encode()
    manifest = set_npm_platforms((root / 'bindings/node/package.json').read_text(), [item]).encode()
    npm = {'package/package.json': manifest, 'package/index.cjs': b'module.exports = 1;\n',
           f'package/native/{item.npm}/ducktape_laya.node': f'{name} addon'.encode(),
           **{f'package/native/{item.npm}/{file}': library for file in item.node_libraries}}
    if foreign: npm[f'package/native/{target(foreign).npm}/ducktape_laya.node'] = b'foreign'
    tgz(out / f'ducktape-laya-{VERSION}.tgz', npm)
    write_zip(out / f'{java_package.ARTIFACT}-{VERSION}.jar',
              {'META-INF/MANIFEST.MF': (java_package.manifest_bytes(VERSION), False), 'io/Laya.class': (b'class', False),
               java_package.native_entry(item): (library, True)}, EPOCH, java_package.LEAD)
    extension = item.library.rsplit('.', 1)[1]
    nupkg = {'_rels/.rels': (b'rels', False), 'DucktapeLaya.nuspec': (b'nuspec', False), 'lib/net10.0/DucktapeLaya.dll': (b'managed', False),
             f'runtimes/{item.nuget}/native/{item.library}': (library, False),
             '[Content_Types].xml': (content_types({'dll', 'nuspec', extension}), False),
             'package/services/metadata/core-properties/nuget.psmdcp': (b'core', False)}
    write_zip(out / f'DucktapeLaya.{VERSION}.nupkg', nupkg, EPOCH, list(nupkg))
    (out / f'ducktape-laya-{VERSION}-{name}{item.archive}').write_bytes(f'{name} archive'.encode())
    write_zip(out / f'ducktape_laya-{VERSION}-{python_wheel.tag(item)}.whl',
              {'ducktape_laya/__init__.py': (b'', False), f'ducktape_laya/_native/{item.library}': (library, True)}, EPOCH)
    for file in (f'ducktape-laya-sys-{VERSION}.crate', f'ducktape-laya-{VERSION}.crate'):
        tgz(out / file, {'crate/src/lib.rs': shared + file.encode()}, level)
    for file in (f'ducktape-laya-{VERSION}-sources.jar', f'ducktape-laya-{VERSION}-javadoc.jar'):
        with zipfile.ZipFile(out / file, 'w', zipfile.ZIP_DEFLATED, compresslevel=level) as archive:
            archive.writestr(zipfile.ZipInfo('META-INF/MANIFEST.MF', (2023, 11, 14, 22, 13, 20)), shared + file.encode())
    for file in (f'ducktape-laya-{VERSION}.pom', 'sbom.cdx.json'):
        (out / file).write_bytes(shared + file.encode())
    inventory = {'schema_version': 2, 'package_version': VERSION, 'packaging_source_commit': commit,
                 'packaging_source_tracked_dirty': False, 'runtime': {'commit': 'b' * 40}, 'build_source': {'sha256': 'c' * 64},
                 'platform': name, 'source_date_epoch': EPOCH, 'build_identities': {}, 'diagnostics': {}, 'toolchain': {},
                 'cpu_build_options': {}, 'payload': native_payload(out, VERSION, item),
                 'artifacts': {f.name: {'sha256': sha256(f), 'bytes': f.stat().st_size} for f in sorted(out.iterdir())}}
    (out / 'inventory.json').write_text(json.dumps(inventory, indent=2) + '\n')
    return out


def assemble(output, *fragments):
    return subprocess.run([sys.executable, ASSEMBLE, '--output', output, *fragments], capture_output=True, text=True)


def refused(output, fragments, message):
    result = assemble(output, *fragments)
    assert result.returncode and message in result.stderr, (message, result.stderr)
    assert not output.exists() or not any(output.iterdir()) or message == 'exists', output


with tempfile.TemporaryDirectory(prefix='ducktape-assembly-') as directory:
    directory = pathlib.Path(directory)
    names = [t.name for t in TARGETS]
    # Shared archives compressed differently on one platform (another host's zlib) are still the same files.
    fragments = [fragment(directory, name, level=1 if name == 'windows-x64' else 9) for name in names]
    first, second = directory / 'first', directory / 'second'
    result = assemble(first, *fragments)
    assert result.returncode == 0, result.stderr
    result = assemble(second, *reversed(fragments))
    assert result.returncode == 0, result.stderr
    files = sorted(path.name for path in first.iterdir())
    assert files == sorted(path.name for path in second.iterdir())
    assert all((first / name).read_bytes() == (second / name).read_bytes() for name in files), 'fragment order changed the output'
    inventory = json.loads((first / 'inventory.json').read_text())
    assert inventory['kind'] == 'multi-platform' and inventory['platforms'] == sorted(names), inventory['platforms']
    assert set(inventory['artifacts']) == set(files) - {'inventory.json'}
    for item in TARGETS:
        assert f'ducktape-laya-{VERSION}-{item.name}{item.archive}' in files and f'ducktape_laya-{VERSION}-{python_wheel.tag(item)}.whl' in files
        section = inventory['fragments'][item.name]
        assert section['inventory_sha256'] == sha256(directory / item.name / 'inventory.json')
        assert section['archive'] in files and section['wheel'] in files
        assert section['payload'] == json.loads((directory / item.name / 'inventory.json').read_text())['payload']
    with tarfile.open(first / f'ducktape-laya-{VERSION}.tgz') as archive:
        members = {member.name: member for member in archive.getmembers()}
        manifest = json.loads(archive.extractfile('package/package.json').read())
    assert manifest['os'] == ['linux', 'darwin', 'win32'] and manifest['cpu'] == ['x64', 'arm64'], manifest
    assert {name.split('/')[2] for name in members if name.startswith('package/native/')} == {t.npm for t in TARGETS}
    assert list(members) == sorted(members) and {m.mtime for m in members.values()} == {499162500}
    jar = read_zip(first / f'{java_package.ARTIFACT}-{VERSION}.jar')
    assert list(jar)[0] == 'META-INF/MANIFEST.MF' and all(jar[java_package.native_entry(t)][1] for t in TARGETS)
    nupkg = read_zip(first / f'DucktapeLaya.{VERSION}.nupkg')
    assert {name for name in nupkg if name.startswith('runtimes/')} == {f'runtimes/{t.nuget}/native/{t.library}' for t in TARGETS}
    types = nupkg['[Content_Types].xml'][0].decode()
    assert all(f'Extension="{extension}"' in types for extension in ('so', 'dylib', 'dll')), types
    assert types.index('"rels"') < types.index('"psmdcp"') < types.index('"dll"') < types.index('"dylib"') < types.index('"so"')
    # A single fragment's NuGet package and jar come out unchanged.
    single = directory / 'single'
    assert assemble(single, fragments[0]).returncode == 0
    for name in (f'DucktapeLaya.{VERSION}.nupkg', f'{java_package.ARTIFACT}-{VERSION}.jar'):
        assert (single / name).read_bytes() == (fragments[0] / name).read_bytes(), name

    refused(directory / 'twice', [fragments[0], fragments[0]], 'Each platform may be given once')
    other = directory / 'other-commit'
    other.mkdir()
    refused(directory / 'commit', [fragments[0], fragment(other, 'windows-x64', commit='d' * 40)], 'differ in packaging_source_commit')
    foreign = directory / 'foreign'
    foreign.mkdir()
    refused(directory / 'foreign-out', [fragments[0], fragment(foreign, 'windows-x64', foreign='linux-x64')], 'native files for its own platform only')
    shared = directory / 'shared'
    shared.mkdir()
    refused(directory / 'shared-out', [fragments[0], fragment(shared, 'windows-x64', shared=b'other')], 'differs from linux-x64')
    tampered = directory / 'tampered'
    shutil.copytree(fragments[3], tampered)
    with zipfile.ZipFile(tampered / f'{java_package.ARTIFACT}-{VERSION}.jar', 'a') as archive:
        archive.writestr('extra', b'x')
    refused(directory / 'tampered-out', [fragments[0], tampered], 'size/hash mismatch')
    for kind in ('npm', 'java', 'nuget', 'wheel'):
        wrong = directory / f'payload-{kind}'
        shutil.copytree(fragments[3], wrong)
        record = json.loads((wrong / 'inventory.json').read_text())
        entry = next(iter(record['payload'][kind]))
        record['payload'][kind][entry] = '0' * 64
        (wrong / 'inventory.json').write_text(json.dumps(record))
        refused(directory / f'payload-{kind}-out', [fragments[0], wrong], 'differ from its inventory payload')
    refused(first, [fragments[0]], 'exists')
    # The manifest the packager writes has real line breaks (a literal backslash-r-backslash-n once made it unreadable
    # to java.util.jar), and where a JDK is present Java itself reads the version and module name from it.
    manifest = java_package.manifest_bytes(VERSION)
    assert b'\\' not in manifest and manifest.endswith(b'\r\n\r\n') and manifest.count(b'\r\n') == 5, manifest
    if shutil.which('java'):
        jar = directory / 'manifest.jar'
        write_zip(jar, {'META-INF/MANIFEST.MF': (manifest, False)}, EPOCH, java_package.LEAD)
        (directory / 'ManifestProbe.java').write_text(
            'public class ManifestProbe { public static void main(String[] a) throws Exception {\n'
            '  try (var jar = new java.util.jar.JarFile(a[0])) { var m = jar.getManifest().getMainAttributes();\n'
            '    System.out.print(m.getValue("Implementation-Version") + " " + m.getValue("Automatic-Module-Name")); } } }\n')
        seen = subprocess.check_output(['java', str(directory / 'ManifestProbe.java'), str(jar)], text=True)
        assert seen == f'{VERSION} {java_package.MODULE}', seen
print(f'Package assembly: {len(TARGETS)} platforms merged deterministically; mismatched fragments refused')
