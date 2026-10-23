"""Synthetic release-candidate evidence for the checker and release-asset tests.

Builds, in a directory, what a release host has after scripts/fetch-ci-evidence.py
and its local runs: per-platform fragments shaped like scripts/package-native.py
output (small stand-in native files in real tar.gz, zip, jar, nupkg and wheel
containers), the release packages assembled from them by the real
scripts/assemble-packages.py, every CI report a candidate run keeps, ci-run.json
listing every downloaded file, and the local linux-x64 validation, drift and
browser evidence. Every report is bound to one commit, source identity, model
and library, so a checker passes on it until a test tampers with something."""
import gzip
import hashlib
import io
import json
import pathlib
import shutil
import subprocess
import sys
import tarfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from artifact_support import native_payload, set_npm_platforms, write_zip  # noqa: E402
from release_support import read_json, sha256, target  # noqa: E402
import java_package  # noqa: E402
import python_wheel  # noqa: E402

EPOCH = 1700000000
LICENSES = ['LICENSE', 'THIRD_PARTY_NOTICES.md', 'licenses/Apache-2.0.txt', 'licenses/llama.cpp-MIT.txt', 'licenses/nlohmann-json-MIT.txt']


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def tgz(path, entries):
    with open(path, 'wb') as stream, gzip.GzipFile(filename='', mode='wb', fileobj=stream, mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode='w') as archive:
            for name, data in entries.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(data))


def content_types(extension):
    return '\n'.join(['<?xml version="1.0" encoding="utf-8"?>',
                      '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
                      '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml" />',
                      '  <Default Extension="psmdcp" ContentType="application/vnd.openxmlformats-package.core-properties+xml" />',
                      f'  <Default Extension="{extension}" ContentType="application/octet" />',
                      '  <Override PartName="/LICENSE" ContentType="application/octet" />', '</Types>']).encode()


class World:
    def __init__(self, directory, commit, source, platforms=('linux-x64', 'linux-arm64', 'windows-x64')):
        self.directory, self.commit, self.source = pathlib.Path(directory), commit, source
        self.record = read_json(ROOT / 'release/compatibility.json')
        self.policy = read_json(ROOT / 'release/qualification.json')
        self.version = self.record['package_version']
        self.model = read_json(ROOT / self.record['models'][0])['sha256']
        self.targets = {item['platform']: item for item in self.record['targets']}
        self.platforms = list(platforms)
        self.ci = self.directory / 'ci'
        self.paths = {}
        for platform in self.platforms:
            self.fragment(platform)
        subprocess.run([sys.executable, str(ROOT / 'scripts/assemble-packages.py'), '--output', str(self.ci / 'packages-assembled'),
                        *[str(self.ci / f'packages-{p}') for p in self.platforms]], check=True, capture_output=True)
        self.release = self.ci / 'packages-assembled'
        for platform in self.platforms:
            self.reports(platform)
        self.local()
        self.rehash()

    # Native bytes of each platform.
    def library(self, platform):
        return f'{platform} library'.encode()

    def addon(self, platform):
        return f'{platform} addon'.encode()

    def fragment(self, platform):
        item = target(platform)
        out = self.ci / f'packages-{platform}'
        out.mkdir(parents=True)
        library, addon = self.library(platform), self.addon(platform)
        manifest = set_npm_platforms((ROOT / 'bindings/node/package.json').read_text(), [item]).encode()
        tgz(out / f'ducktape-laya-{self.version}.tgz',
            {'package/package.json': manifest, 'package/index.cjs': b'module.exports = 1;\n',
             f'package/native/{item.npm}/ducktape_laya.node': addon, **{f'package/native/{item.npm}/{name}': library for name in item.node_libraries}})
        write_zip(out / f'{java_package.ARTIFACT}-{self.version}.jar',
                  {'META-INF/MANIFEST.MF': (java_package.manifest_bytes(self.version), False), 'io/Laya.class': (b'class', False),
                   java_package.native_entry(item): (library, True)}, EPOCH, java_package.LEAD)
        nupkg = {'_rels/.rels': (b'rels', False), 'DucktapeLaya.nuspec': (b'nuspec', False), 'lib/net10.0/DucktapeLaya.dll': (b'managed', False),
                 f'runtimes/{item.nuget}/native/{item.library}': (library, False),
                 '[Content_Types].xml': (content_types(item.library.rsplit('.', 1)[1]), False),
                 'package/services/metadata/core-properties/nuget.psmdcp':
                     (b'<coreProperties>\n  <lastModifiedBy>NuGet.Packaging, Version=7.9.0.0;.NET 8.0</lastModifiedBy>\n</coreProperties>\n', False)}
        write_zip(out / f'DucktapeLaya.{self.version}.nupkg', nupkg, EPOCH, list(nupkg))
        prefix = f'ducktape-laya-{self.version}-{platform}'
        files = {f'{prefix}/lib/{item.library}': library, **{f'{prefix}/share/ducktape-laya/{name}': b'text' for name in LICENSES},
                 f'{prefix}/include/ducktape_laya.h': b'#pragma once\n', f'{prefix}/lib/cmake/ducktape_laya/ducktape_laya-config.cmake': b'# config\n',
                 f'{prefix}/lib/pkgconfig/ducktape-laya.pc': b'Libs: -lducktape_laya\n',
                 f'{prefix}/lib/cmake/ducktape_laya/ducktape_laya-targets.cmake': b'cmake_policy(PUSH)\ncmake_policy(VERSION 2.8.3...3.29)\n'}
        if item.archive == '.zip':
            write_zip(out / (prefix + '.zip'), {name: (data, False) for name, data in files.items()}, EPOCH)
        else:
            tgz(out / (prefix + '.tar.gz'), files)
        write_zip(out / f'ducktape_laya-{self.version}-{python_wheel.tag(item)}.whl',
                  {'ducktape_laya/__init__.py': (b'', False), f'ducktape_laya/_native/{item.library}': (library, True)}, EPOCH)
        for name in (f'ducktape-laya-sys-{self.version}.crate', f'ducktape-laya-{self.version}.crate'):
            tgz(out / name, {'crate/src/lib.rs': name.encode()})
        for name in (f'ducktape-laya-{self.version}-sources.jar', f'ducktape-laya-{self.version}-javadoc.jar'):
            pages = {'index.html': (b'jdk page', False), 'io.github.adrian729.laya/io/github/adrian729/laya/Laya.html': (b'jdk page', False)}
            write_zip(out / name, {'META-INF/MANIFEST.MF': (name.encode(), False), **(pages if name.endswith('javadoc.jar') else {})}, EPOCH)
        (out / f'ducktape-laya-{self.version}.pom').write_bytes(b'pom')
        (out / 'sbom.cdx.json').write_text(json.dumps({'bomFormat': 'CycloneDX', 'components': []}))
        inventory = {'schema_version': 2, 'package_version': self.version, 'packaging_source_commit': self.commit,
                     'packaging_source_tracked_dirty': False, 'runtime': self.record['runtime'], 'build_source': {'sha256': self.source},
                     'platform': platform, 'source_date_epoch': EPOCH,
                     'build_identities': {'ducktape_laya': {'binary_sha256': digest(library)}, 'ducktape_laya_node': {'binary_sha256': digest(addon)}},
                     'diagnostics': {}, 'toolchain': {}, 'cpu_build_options': {}, 'payload': native_payload(out, self.version, item),
                     'artifacts': {f.name: {'sha256': sha256(f), 'bytes': f.stat().st_size} for f in sorted(out.iterdir())}}
        write_json(out / 'inventory.json', inventory)

    def outputs(self, platform, probability=0.9):
        item = self.targets[platform]
        return {'model_sha256': self.model, 'fixture_hashes': {'primitive.json': 'f' * 64}, 'library_sha256': digest(self.library(platform)),
                'runtime': {'llama_cpp_commit': self.record['runtime']['commit'], 'runtime_patches': self.record['runtime']['patches'],
                            'package_version': self.version, 'abi_version': self.record['abi_version'],
                            'numerical_profile': {'id': item['numerical_profile']['runtime_id'], 'cross_profile_equivalence': False}},
                'fixtures': {'primitive.json': {'C': {'answers': {
                    'department': {'type': 'choice', 'choice': 'billing', 'probabilities': {'billing': probability, 'sales': 1 - probability}},
                    'refund': {'type': 'noul', 'noul': probability}}, 'usage': {'input_tokens': 12}}}}}

    def validation(self, platform):
        item = self.targets[platform]
        linux = item['os'] == 'linux'
        tests = ['abi-errors', 'feature-errors', 'control-errors', 'decision-rules', 'language-routing', 'tokenizer-partition',
                 'real-model', 'real-features', 'real-control', 'real-tokenizer']
        tests += ['real-faults', 'real-c-faults', 'real-runtime-faults', 'ggml-faults'] + (['cpu-guard'] if platform == 'linux-x64' else []) if linux else []
        skipped = {} if linux else {name: 'Linux only' for name in ('real-faults', 'real-c-faults', 'real-runtime-faults', 'ggml-faults', 'cpu-guard')}
        return {'schema_version': 1, 'source_commit': self.commit, 'source_tracked_dirty': False, 'mode': 'real_model',
                'model': {'sha256': self.model}, 'platform': platform, 'node_version': 'v' + item['build']['node_headers'],
                'library_sha256': digest(self.library(platform)), 'addon_sha256': digest(self.addon(platform)),
                'diagnostics': {'numerical_profile': {'id': item['numerical_profile']['runtime_id'], 'cross_profile_equivalence': False},
                                'required_cpu_features': item['cpu_baseline']['required_cpu_features'],
                                'llama_cpp_commit': self.record['runtime']['commit'], 'runtime_patches': self.record['runtime']['patches'],
                                'package_version': self.version, 'abi_version': self.record['abi_version'], 'compiler': 'GNU 13.3.0'},
                'gates': {gate: {'status': 'passed' if gate in ('native_contracts', 'binding_conformance', 'upstream_reference',
                                                              'original_reference', 'abi', 'installed_packages') else 'not_run'}
                          for gate in self.record['required_release_gates']},
                'ctest_registered': sorted(tests), 'skipped': skipped, 'additional_model_gates': {},
                'runs': {name: {'exit_code': 0} for name in ('packaging-identities', 'browser-backend-identities', 'package', 'package-smoke')}}

    def reports(self, platform):
        item = self.targets[platform]
        evidence = self.ci / f'evidence-{platform}'
        write_json(evidence / 'report.json', self.validation(platform))
        write_json(evidence / 'outputs.json', self.outputs(platform, 0.9 if platform == 'linux-x64' else 0.88))
        used = [f'{d["case"]}/{d["question"]}' for d in item['known_deviations']]
        write_json(evidence / 'original-reference.json',
                   {'summary': {'status': 'passed', 'known_deviations_used': used},
                    'cases': {'json_escaping': {'answers': {'spam': {'max_probability_drift': 0.0541 if used else 0.0373}}}}})
        installed = self.ci / f'installed-{platform}-real-model'
        write_json(installed / 'installed.json', {
            'schema_version': 1, 'status': 'passed', 'platform': platform, 'artifact_inventory_sha256': sha256(self.release / 'inventory.json'),
            'package_version': self.version, 'model_sha256': self.model, 'source_commit': self.commit, 'source_tracked_dirty': False,
            'host': {'system': {'linux': 'Linux', 'windows': 'Windows'}[item['os']], 'machine': 'x86_64', 'hostname': f'runner-{platform}',
                     'github_actions': True},
            'consumers': {name: 'c' * 64 for name in ('native', 'node', 'rust', 'python', 'java', 'dotnet', 'go')}})
        npm = f'ducktape-laya-{self.version}.tgz'
        versions = (self.policy['platforms']['targets'].get(platform) or self.policy['platforms']['native'])['electron_versions']
        write_json(installed / 'electron.json', {'status': 'passed', 'platform': platform, 'artifact': npm,
                                                 'artifact_sha256': sha256(self.release / npm),
                                                 'results': [{'mode': mode, 'electron': versions[0]} for mode in ('directory', 'asar')]})
        if item['tier'] == 1:
            return
        library = digest(self.library(platform))
        write_json(self.ci / f'stress-{platform}' / 'report.json', {
            'schema_version': 1, 'status': 'passed', 'source_commit': self.commit, 'source_tracked_dirty': False, 'kind': 'short_stress',
            'requested_seconds': self.policy['budgets']['short_stress_seconds'], 'required_seconds': self.policy['budgets']['short_stress_seconds'],
            'qualification_budget_met': True, 'target_platform': platform, 'library_sha256': library, 'model_sha256': self.model,
            'post_run_identity_verified': True})
        write_json(self.ci / f'reproducibility-{platform}' / 'report.json', {
            'schema_version': 1, 'status': 'passed', 'source_commit': self.commit, 'source_tracked_dirty': False, 'differences': [],
            'target_platform': platform, 'builds': {label: {f'lib/{target(platform).library}': {'sha256': library, 'mode': 420}}
                                                    for label in ('first', 'second')}})

    def local(self):
        """The release host's own runs: linux-x64 validation (with its packages), drift reports, browser package."""
        native = self.directory / 'native'
        report = self.validation('linux-x64')
        for name in self.record.get('additional_models', []):
            report['additional_model_gates'][read_json(ROOT / name)['id']] = {'original_reference': 'passed', 'upstream_reference': 'passed'}
        write_json(native / 'report.json', report)
        write_json(native / 'outputs.json', self.outputs('linux-x64'))
        write_json(native / 'original-reference.json', {'summary': {'status': 'passed', 'known_deviations_used': []}, 'cases': {}})
        shutil.copytree(self.ci / 'packages-linux-x64', native / 'packages')
        self.paths['native'] = native / 'report.json'
        for platform in self.platforms:
            if self.targets[platform]['tier'] == 1:
                continue
            write_json(self.directory / f'drift-{platform}.json', {
                'schema_version': 1, 'comparison': 'native_cross_profile_drift', 'status': 'passed', 'reference_platform': 'linux-x64',
                'target_platform': platform, 'reference_report_sha256': sha256(native / 'outputs.json'),
                'target_report_sha256': sha256(self.ci / f'evidence-{platform}' / 'outputs.json'), 'model_sha256': self.model,
                'maximum_absolute_difference': 0.02, 'decision_differences': []})
            self.paths[f'{platform}:drift'] = self.directory / f'drift-{platform}.json'
        browser = self.directory / 'browser-package'
        browser.mkdir()
        (browser / f'ducktape-laya-browser-{self.version}.tgz').write_bytes(b'browser package')
        (browser / 'sbom.cdx.json').write_text(json.dumps({'bomFormat': 'CycloneDX', 'components': []}))
        write_json(browser / 'inventory.json', {
            'schema_version': 2, 'packaging_source_commit': self.commit, 'packaging_source_tracked_dirty': False,
            'build_source': {'sha256': self.source}, 'assets': {},
            'artifacts': {path.name: sha256(path) for path in sorted(browser.iterdir()) if path.name != 'inventory.json'}})
        self.paths['browser_package'] = browser / 'inventory.json'
        native_policy = self.policy['platforms']['native']
        write_json(self.directory / 'userspaces.json', {
            'status': 'passed', 'artifact_inventory_sha256': sha256(self.release / 'inventory.json'),
            'environments': {name: {'status': 'passed', 'os_release': {'VERSION_ID': name.removeprefix('ubuntu-')}}
                             for name in (native_policy['minimum_userspace'], *native_policy['additional_userspaces'])}})
        self.paths['userspaces'] = self.directory / 'userspaces.json'
        write_json(self.directory / 'electron.json', json.loads((self.ci / 'installed-linux-x64-real-model/electron.json').read_text()))
        self.paths['electron'] = self.directory / 'electron.json'
        installed = self.directory / 'installed-release.json'
        value = json.loads((self.ci / 'installed-linux-x64-real-model/installed.json').read_text())
        value['host'] = {'system': 'Linux', 'machine': 'x86_64', 'hostname': 'release-host', 'github_actions': False}
        write_json(installed, value)
        self.paths['installed_release'] = installed

    def rehash(self, event='workflow_dispatch', head=None):
        """ci-run.json as scripts/fetch-ci-evidence.py writes it, listing the CI files as they are now."""
        files = {path.relative_to(self.ci).as_posix(): sha256(path) for path in sorted(self.ci.rglob('*'))
                 if path.is_file() and path.name not in ('ci-run.json', 'evidence.json')}
        write_json(self.ci / 'ci-run.json', {
            'schema_version': 1, 'repository': 'adrian729/ducktape-laya', 'workflow_path': '.github/workflows/native.yml', 'run_id': 7, 'run_attempt': 1,
            'head_sha': head or self.commit, 'event': event, 'status': 'completed', 'conclusion': 'success',
            'html_url': 'https://example.invalid/runs/7', 'candidate_commit': self.commit, 'artifacts': {}, 'files': files})
        sys.path.insert(0, str(ROOT / 'scripts'))
        import importlib.util
        spec = importlib.util.spec_from_file_location('fetch_ci_evidence', ROOT / 'scripts/fetch-ci-evidence.py')
        fetch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fetch)
        for key, relative in fetch.evidence_keys({'targets': [self.targets[p] for p in self.platforms]}).items():
            self.paths[key] = self.ci / relative

    def evidence_manifest(self, path):
        write_json(path, {key: str(value) for key, value in self.paths.items()})
        return path

    def edit(self, relative, change, rehash=True):
        """Change one CI JSON report; rehash keeps ci-run.json consistent (a consistently wrong download)."""
        path = self.ci / relative
        value = json.loads(path.read_text())
        change(value)
        write_json(path, value)
        if rehash:
            self.rehash()

    def repack_local(self, name, change):
        """Rewrite one file of this host's own linux-x64 package run (entries dict -> entries dict, or bytes -> bytes), keeping its inventory true."""
        from artifact_support import read_tgz, read_zip
        directory = self.directory / 'native' / 'packages'
        path = directory / name
        if name.endswith('.pom') or name.endswith('.json'):
            path.write_bytes(change(path.read_bytes()))
        elif name.endswith(('.tgz', '.tar.gz', '.crate')):
            tgz(path, change({key: data for key, (data, _) in read_tgz(path).items()}))
        else:
            entries = read_zip(path)
            write_zip(path, change(entries), EPOCH, [n for n in entries if n == 'META-INF/MANIFEST.MF'])
        inventory = json.loads((directory / 'inventory.json').read_text())
        inventory['artifacts'][name] = {'sha256': sha256(path), 'bytes': path.stat().st_size}
        write_json(directory / 'inventory.json', inventory)

    def repack_release(self, name, change):
        """Rewrite one release package (entries dict -> entries dict) and update the release inventory to match."""
        path = self.release / name
        from artifact_support import read_tgz, read_zip
        if name.endswith('.tgz'):
            entries = {key: data for key, (data, _) in read_tgz(path).items()}
            tgz(path, change(entries))
        else:
            entries = read_zip(path)
            write_zip(path, change(entries), EPOCH, [n for n in entries if n == 'META-INF/MANIFEST.MF'])
        inventory = json.loads((self.release / 'inventory.json').read_text())
        inventory['artifacts'][name] = {'sha256': sha256(path), 'bytes': path.stat().st_size}
        write_json(self.release / 'inventory.json', inventory)
        self.rehash()
