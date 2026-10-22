"""Build the Maven artifacts around an installed native library (build tooling only).

ducktape-laya-VERSION.jar bundles the shared library at native/<platform>/<file>
(native/linux-x64/libducktape_laya.so, native/windows-x64/ducktape_laya.dll, ...),
which Native.java extracts when no library is configured; one runner's jar holds
its own platform and scripts/assemble-packages.py merges them into one jar; the sources and javadoc jars
and the POM are what Maven Central requires next to it. Signing is a publication
step."""
import pathlib
import subprocess
import tempfile
import xml.etree.ElementTree as ElementTree
from artifact_support import write_zip
from release_support import ROOT

GROUP, ARTIFACT, MODULE = 'io.github.adrian729', 'ducktape-laya', 'io.github.adrian729.laya'
POM = '''<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>{group}</groupId>
  <artifactId>{artifact}</artifactId>
  <version>{version}</version>
  <packaging>jar</packaging>
  <name>ducktape-laya</name>
  <description>Local Laya GGUF decisions (choice, noul, score) for Java 22+ through the bundled ducktape-laya native library</description>
  <url>https://github.com/adrian729/ducktape-laya</url>
  <licenses>
    <license>
      <name>MIT</name>
      <url>https://opensource.org/license/mit</url>
    </license>
  </licenses>
  <developers>
    <developer>
      <id>adrian729</id>
      <name>Adrián Sánchez Albanell</name>
      <url>https://github.com/adrian729</url>
    </developer>
  </developers>
  <scm>
    <connection>scm:git:https://github.com/adrian729/ducktape-laya.git</connection>
    <developerConnection>scm:git:ssh://git@github.com/adrian729/ducktape-laya.git</developerConnection>
    <url>https://github.com/adrian729/ducktape-laya</url>
  </scm>
</project>
'''

# Elements Maven Central rejects a POM without (central.sonatype.org/publish/requirements).
CENTRAL_REQUIRED = ['groupId', 'artifactId', 'version', 'name', 'description', 'url', 'licenses/license/name',
                    'developers/developer/name', 'scm/url', 'scm/connection']


def pom(version):
    """The POM text, checked against Central's required elements."""
    text = POM.format(group=GROUP, artifact=ARTIFACT, version=version)
    root, ns = ElementTree.fromstring(text.encode('utf-8')), {'m': 'http://maven.apache.org/POM/4.0.0'}
    missing = [path for path in CENTRAL_REQUIRED
               if not (root.findtext('/'.join(f'm:{part}' for part in path.split('/')), namespaces=ns) or '').strip()]
    if missing: raise ValueError(f'POM lacks elements Maven Central requires: {", ".join(missing)}')
    return text


# javadoc writes text with the platform's line separator; every platform's javadoc jar must be the same.
TEXT = {'.html', '.css', '.js', '.svg', '.txt', '.md', ''}


def lf(path):
    data = path.read_bytes()
    return data.replace(b'\r\n', b'\n') if path.suffix in TEXT else data


# The manifest must lead the jar for JarInputStream.
LEAD = ('META-INF/MANIFEST.MF',)


def native_entry(target):
    return f'native/{target.name}/{target.library}'


def build(out, version, library, epoch, target):
    """Write the jar, sources jar, javadoc jar and POM into out; library is the real shared library file."""
    out = pathlib.Path(out)
    source = ROOT / 'bindings/java/src/main/java'
    sources = sorted(source.rglob('*.java'))
    legal = {f'META-INF/{path.relative_to(ROOT).as_posix()}': (path.read_bytes(), False)
             for path in [ROOT / 'LICENSE', ROOT / 'THIRD_PARTY_NOTICES.md', *sorted((ROOT / 'licenses').iterdir())]}
    manifest = (f'Manifest-Version: 1.0\\r\\nAutomatic-Module-Name: {MODULE}\\r\\nImplementation-Title: {ARTIFACT}\\r\\n'
                f'Implementation-Version: {version}\\r\\n\\r\\n').encode()
    lead = LEAD
    with tempfile.TemporaryDirectory(prefix='ducktape-java-') as directory:
        directory = pathlib.Path(directory)
        classes, docs = directory / 'classes', directory / 'docs'
        subprocess.run(['javac', '--release', '22', '-Xlint:all,-restricted,-serial,-missing-explicit-ctor,-module',
                        '-d', str(classes), *map(str, sources)], check=True, capture_output=True)
        subprocess.run(['javadoc', '--release', '22', '-quiet', '-notimestamp', '-Xdoclint:none', '-d', str(docs),
                        *map(str, sources)], check=True, capture_output=True)
        files = {path.relative_to(classes).as_posix(): (path.read_bytes(), False) for path in sorted(classes.rglob('*.class'))}
        files[native_entry(target)] = (pathlib.Path(library).read_bytes(), True)
        write_zip(out / f'{ARTIFACT}-{version}.jar', {**files, **legal, lead[0]: (manifest, False)}, epoch, lead)
        write_zip(out / f'{ARTIFACT}-{version}-sources.jar',
                  {**{path.relative_to(source).as_posix(): (path.read_bytes(), False) for path in sources}, **legal,
                   lead[0]: (manifest, False)}, epoch, lead)
        write_zip(out / f'{ARTIFACT}-{version}-javadoc.jar',
                  {**{path.relative_to(docs).as_posix(): (lf(path), False) for path in sorted(docs.rglob('*')) if path.is_file()},
                   lead[0]: (manifest, False)}, epoch, lead)
    (out / f'{ARTIFACT}-{version}.pom').write_text(pom(version), encoding='utf-8')
