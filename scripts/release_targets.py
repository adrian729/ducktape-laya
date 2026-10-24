"""Release-target and release-package checks for scripts/check-candidate.py.

The candidate run (native.yml dispatched with candidate=true on the candidate
commit) builds every target's fragment, assembles the release packages and
tests them on every target's runner; scripts/fetch-ci-evidence.py downloads it
and records every file with its sha256 in ci-run.json. These functions check
that evidence, offline: every CI-derived file must be listed in ci-run.json with
its current sha256, the run must be a successful workflow_dispatch of the
target's build workflow on the candidate commit, and every report must come
from that commit's clean tree and name the validated library, addon and pinned
model. Tier-2 evidence is never reused from another commit.

Missing evidence raises Missing (gate not_run); anything else that does not
hold raises Mismatch (gate failed).
"""
import hashlib
import json
import pathlib
import re
import tarfile
import zipfile

from artifact_support import PAYLOAD_PREFIXES, read_tgz, read_zip, verify_inventory
from release_support import sha256, target as package_target


class Missing(Exception):
    pass


class Mismatch(Exception):
    pass


def need(condition, message):
    if not condition:
        raise Mismatch(message)


def require_complete_validation(report, name):
    """A validate.py report that left suites out, or was compiled through a cache, is not release evidence."""
    left_out = sorted(SELECTABLE_SUITES & set(report.get('skipped', {})))
    need(not left_out, f'{name}: left out {", ".join(left_out)} (validate.py --bindings or --skip-native-tests)')
    # Reports from before validate.py recorded the launcher were built without one.
    launcher = report.get('compiler_launcher', 'none')
    need(launcher == 'none', f'{name}: compiled through the compiler launcher {launcher}, not without one')


# Native tests every target registers; real-faults and real-c-faults are Linux-only (CMakeLists.txt).
MODEL_FREE_TESTS = {'abi-errors', 'feature-errors', 'control-errors', 'decision-rules', 'language-routing', 'tokenizer-partition'}
REAL_MODEL_TESTS = {'real-model', 'real-features', 'real-control', 'real-tokenizer'}
LINUX_TESTS = {'real-faults', 'real-c-faults', 'real-runtime-faults', 'ggml-faults'}
# Suites scripts/validate.py leaves out with --bindings or --skip-native-tests (listed under skipped).
SELECTABLE_SUITES = {'native', 'rust', 'node', 'python', 'java', 'dotnet', 'go', 'bindings', 'installed'}
# The run that may produce candidate evidence.
CI_EVENT, CI_CONCLUSION = 'workflow_dispatch', 'success'
# The only repository whose runs are candidate evidence (scripts/fetch-ci-evidence.py has the same constant).
RELEASE_REPOSITORY = 'adrian729/ducktape-laya'


# What cannot be compared byte for byte between this host and the CI runner, and what is compared instead
# (measured on a real candidate run: every other entry of every package, class files and DLLs included, was equal).
# Written to the gates.json report as package_comparison, so nothing is skipped silently.
COMPARISON_NOTES = {
    'ducktape_laya-targets.cmake': 'the CMake version that generated the install export writes its newest policy into '
                                   'cmake_policy(VERSION 2.8.3...X); that number is compared as <cmake>, every other byte is compared',
    'nuget.psmdcp': 'NuGet records the .NET runtime that ran dotnet pack in lastModifiedBy (";.NET 10.0" here, ";.NET 8.0" in CI); '
                    'that version is compared as <runtime>, every other byte is compared',
    '*-javadoc.jar': 'javadoc output depends on the exact JDK (CI pins Temurin 25.0.4; this host may differ), so the HTML and assets are not '
                     'compared: the manifest and the page names of the API module (io.github.adrian729.laya/...) are. The sources jar the '
                     'javadoc is generated from is compared entry by entry, as are the class files in the jar'}
JAVADOC_API = 'io.github.adrian729.laya/'


def normalized(entry, data):
    """An entry's bytes with the host-toolchain stamp of COMPARISON_NOTES replaced; everything else is untouched."""
    if entry.endswith('/ducktape_laya-targets.cmake'):
        return re.sub(rb'cmake_policy\(VERSION 2\.8\.3\.\.\.[0-9.]+\)', b'cmake_policy(VERSION 2.8.3...<cmake>)', data)
    if entry.endswith('/nuget.psmdcp'):
        return re.sub(rb'(<lastModifiedBy>[^<]*?);\.NET [0-9.]+', rb'\1;.NET <runtime>', data)
    return data


def package_entries(path):
    """{entry name: description} of one package file, independent of the container's compression and metadata.

    tar and zip entries are [sha256, executable] or ['link', target]; a file that is no archive is its
    sha256. Two hosts that deflate the same entries differently have equal descriptions. normalized()
    entries are described without their toolchain stamp; a javadoc jar is described by its manifest and
    its API page names (COMPARISON_NOTES)."""
    path = pathlib.Path(path)
    digest = lambda entry, data: hashlib.sha256(normalized(entry, data)).hexdigest()
    if path.name.endswith(('.tar.gz', '.tgz', '.crate')):
        entries = {}
        with tarfile.open(path, 'r:gz') as archive:
            for member in archive.getmembers():
                if member.isdir():
                    continue
                if member.issym() or member.islnk():
                    entries[member.name] = ['link', member.linkname]
                elif member.isfile():
                    entries[member.name] = [digest(member.name, archive.extractfile(member).read()), bool(member.mode & 0o111)]
                else:
                    raise ValueError(f'{path.name}: unexpected entry {member.name}')
        return entries
    if path.name.endswith(('.zip', '.jar', '.nupkg', '.whl')):
        with zipfile.ZipFile(path) as archive:
            entries = {info.filename: [digest(info.filename, archive.read(info)), bool((info.external_attr >> 16) & 0o111)]
                       for info in archive.infolist() if not info.is_dir()}
        if path.name.endswith('-javadoc.jar'):
            entries = {name: (value if name == 'META-INF/MANIFEST.MF' else 'api page') for name, value in entries.items()
                       if name == 'META-INF/MANIFEST.MF' or name.startswith(JAVADOC_API)}
        return entries
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compare_packages(local, ci, local_names, ci_names):
    """Differences between this host's package set and the CI fragment's: [(file, what differs)], by entry."""
    differences = []
    if sorted(local_names) != sorted(ci_names):
        differences.append(('(files)', f'only here {sorted(set(local_names) - set(ci_names))}, only in CI {sorted(set(ci_names) - set(local_names))}'))
    for name in sorted(set(local_names) & set(ci_names)):
        mine, theirs = package_entries(pathlib.Path(local) / name), package_entries(pathlib.Path(ci) / name)
        if mine == theirs:
            continue
        if isinstance(mine, dict) and isinstance(theirs, dict):
            changed = sorted(entry for entry in mine.keys() & theirs.keys() if mine[entry] != theirs[entry])
            detail = {'only here': sorted(mine.keys() - theirs.keys())[:4], 'only in CI': sorted(theirs.keys() - mine.keys())[:4], 'differ': changed[:4]}
            differences.append((name, {key: value for key, value in detail.items() if value}))
        else:
            differences.append((name, 'contents differ'))
    return differences


class Context:
    """The candidate (commit, source identity, record and policy from its own tree) and the evidence map."""

    def __init__(self, commit, source, record, policy, tree, paths):
        self.commit, self.source, self.record, self.policy, self.tree, self.paths = commit, source, record, policy, tree, paths
        self.primary = json.loads((tree / record['models'][0]).read_text())
        self.targets = {item['platform']: item for item in record['targets']}
        self._hashes, self._release = {}, None

    def sha256(self, path):
        """sha256 of an evidence file; evidence does not change while the checker runs."""
        path = pathlib.Path(path).resolve()
        if path not in self._hashes:
            self._hashes[path] = sha256(path)
        return self._hashes[path]

    def path(self, name):
        path = self.paths.get(name)
        if not path or not path.is_file():
            raise Missing(f'{name} evidence missing')
        return path

    def load(self, name):
        return json.loads(self.path(name).read_text())

    def same_source(self, report, name, key='source_commit', dirty_key='source_tracked_dirty'):
        need(report.get(key) == self.commit, f'{name}: source {report.get(key)} is not candidate {self.commit}')
        need(report.get(dirty_key) is False, f'{name}: produced from a dirty tree')

    # The CI candidate run and the files downloaded from it.
    def ci_run(self):
        run = self.load('ci_run')
        need(run.get('repository') == RELEASE_REPOSITORY, f'ci run: repository {run.get("repository")}, not {RELEASE_REPOSITORY}')
        need(run.get('head_sha') == self.commit, f'ci run: {run.get("run_id")} built {run.get("head_sha")}, not the candidate')
        need(run.get('event') == CI_EVENT and run.get('conclusion') == CI_CONCLUSION,
             f'ci run: {run.get("event")}/{run.get("conclusion")}, not a successful dispatch')
        workflows = {item['build']['built_by'] for item in self.record['targets']}
        need(len(workflows) == 1 and run.get('workflow_path') in workflows, f'ci run: workflow {run.get("workflow_path")} is not {sorted(workflows)}')
        need(isinstance(run.get('files'), dict) and run['files'], 'ci run: no file list')
        return run

    def ci_file(self, name, path=None):
        """A CI-derived file: listed in ci-run.json with its current sha256. Returns its path."""
        run = self.ci_run()
        path = (path or self.path(name)).resolve()
        if not path.is_file():
            raise Missing(f'{name}: {path.name} missing')
        base = self.path('ci_run').resolve().parent
        need(path.is_relative_to(base), f'{name}: not part of the downloaded CI run')
        listed = run['files'].get(path.relative_to(base).as_posix())
        need(listed is not None, f'{name}: {path.relative_to(base).as_posix()} is not a file of the CI run')
        need(listed == self.sha256(path), f'{name}: {path.relative_to(base).as_posix()} changed after download')
        return path

    def ci_json(self, name, path=None):
        return json.loads(self.ci_file(name, path).read_text())

    # The release packages: the CI-assembled multi-platform package directory.
    def release_packages(self):
        if self._release is None:
            self._release = self._release_packages()
        return self._release

    def _release_packages(self):
        path = self.ci_file('release_packages')
        directory, inventory = path.parent, json.loads(path.read_text())
        need(inventory.get('kind') == 'multi-platform', 'release packages: not a multi-platform assembly')
        need(inventory.get('packaging_source_commit') == self.commit and inventory.get('packaging_source_tracked_dirty') is False,
             'release packages: not packaged from the clean candidate')
        need(inventory.get('build_source', {}).get('sha256') == self.source, 'release packages: built from different sources')
        need(inventory.get('package_version') == self.record['package_version'], 'release packages: different package version')
        try:
            verify_inventory(directory)
        except (ValueError, OSError, KeyError) as error:
            raise Mismatch(f'release packages: {error}') from None
        need(inventory.get('platforms') == sorted(self.targets), f'release packages: platforms {inventory.get("platforms")} '
             f'are not the release targets {sorted(self.targets)}')
        need(sorted(inventory.get('fragments', {})) == sorted(self.targets), 'release packages: fragments differ from the targets')
        for platform, section in inventory['fragments'].items():
            need(section.get('packaging_source_commit') == self.commit and section.get('packaging_source_tracked_dirty') is False,
                 f'release packages: {platform} fragment not packaged from the clean candidate')
            need(section.get('archive') in inventory['artifacts'] and section.get('wheel') in inventory['artifacts'],
                 f'release packages: {platform} archive or wheel missing')
        return directory, inventory

    def fragment(self, platform):
        """The target's CI fragment (package-native.py output), bound to the release packages."""
        path = self.ci_file(f'{platform}:fragment')
        fragment = json.loads(path.read_text())
        need(fragment.get('platform') == platform, f'{platform} fragment: built for {fragment.get("platform")}')
        need(fragment.get('packaging_source_commit') == self.commit and fragment.get('packaging_source_tracked_dirty') is False,
             f'{platform} fragment: not packaged from the clean candidate')
        need(fragment.get('build_source', {}).get('sha256') == self.source, f'{platform} fragment: built from different sources')
        _, inventory = self.release_packages()
        need(inventory['fragments'][platform]['inventory_sha256'] == sha256(path),
             f'{platform} fragment: not the fragment the release packages were assembled from')
        return path.parent, fragment

    def release_payload(self, platform):
        """The release packages carry exactly this target's fragment's native files, and none for other platforms."""
        directory, inventory = self.release_packages()
        fragment_directory, fragment = self.fragment(platform)
        target, version = package_target(platform), inventory['package_version']
        section = inventory['fragments'][platform]
        need(section['payload'] == fragment['payload'], f'{platform}: release inventory payload differs from the fragment')
        for name in (section['archive'], section['wheel']):
            need(fragment['artifacts'].get(name, {}).get('sha256') == inventory['artifacts'][name]['sha256'],
                 f'{platform}: {name} is not the fragment\'s file')
        contents = {'npm': read_tgz(directory / f'ducktape-laya-{version}.tgz'),
                    'java': read_zip(directory / f'ducktape-laya-{version}.jar'),
                    'nuget': read_zip(directory / f'DucktapeLaya.{version}.nupkg'),
                    'wheel': read_zip(directory / section['wheel'])}
        for kind, entries in contents.items():
            prefix = PAYLOAD_PREFIXES[kind](target)
            own = {name: hashlib.sha256(data).hexdigest() for name, (data, _) in entries.items() if name.startswith(prefix)}
            need(own == fragment['payload'][kind], f'{platform}: {kind} native files differ from the fragment payload')
        # Native directories exist only for release targets, with exactly their payloads.
        roots = {'npm': 'package/native/', 'java': 'native/', 'nuget': 'runtimes/'}
        for kind, root in roots.items():
            natives = {name for name in contents[kind] if name.startswith(root)}
            expected = set().union(*(inventory['fragments'][p]['payload'][kind] for p in inventory['platforms']))
            need(natives == expected, f'{kind}: native files outside the release targets\' payloads: {sorted(natives ^ expected)[:4]}')

    # Per-target CI validation report.
    def target_native(self, platform):
        item = self.targets[platform]
        path = self.ci_file(f'{platform}:native')
        report = json.loads(path.read_text())
        self.same_source(report, f'{platform} validation')
        require_complete_validation(report, f'{platform} validation')
        need(report.get('mode') == 'real_model' and report.get('model', {}).get('sha256') == self.primary['sha256'],
             f'{platform} validation: not a real-model run of the pinned model')
        need(report.get('platform') == platform, f'{platform} validation: ran on {report.get("platform")}')
        diagnostics = report.get('diagnostics', {})
        need(diagnostics.get('numerical_profile', {}).get('id') == item['numerical_profile']['runtime_id'],
             f'{platform} validation: profile {diagnostics.get("numerical_profile", {}).get("id")} is not the record\'s')
        need(diagnostics.get('required_cpu_features') == item['cpu_baseline']['required_cpu_features'],
             f'{platform} validation: CPU features differ from the record')
        need(diagnostics.get('llama_cpp_commit') == self.record['runtime']['commit'] and
             diagnostics.get('runtime_patches') == self.record['runtime']['patches'] and
             diagnostics.get('package_version') == self.record['package_version'] and
             diagnostics.get('abi_version') == self.record['abi_version'], f'{platform} validation: runtime identity differs from the record')
        need(str(report.get('node_version', '')).lstrip('v') == item['build']['node_headers'],
             f'{platform} validation: Node {report.get("node_version")}, not {item["build"]["node_headers"]}')
        need(isinstance(report.get('library_sha256'), str) and isinstance(report.get('addon_sha256'), str),
             f'{platform} validation: library or addon identity missing')
        return path.parent, report

    def native_gate(self, platform, gate):
        directory, report = self.target_native(platform)
        status = report.get('gates', {}).get(gate, {}).get('status', 'missing')
        need(status == 'passed', f'{platform} validation: {gate} {status}')
        return directory, report


def target_gates(ctx, platform):
    """{gate: check()} for one tier-2 target."""
    item = ctx.targets[platform]
    linux = item['os'] == 'linux'

    def native_contracts():
        _, report = ctx.native_gate(platform, 'native_contracts')
        registered = set(report.get('ctest_registered', []))
        required = MODEL_FREE_TESTS | REAL_MODEL_TESTS | (LINUX_TESTS if linux else set())
        need(required <= registered, f'{platform}: native tests not registered: {sorted(required - registered)}')
        for name in LINUX_TESTS - registered:
            need(name in report.get('skipped', {}), f'{platform}: {name} neither ran nor was reported as skipped')

    def additional_models(report, gate):
        for name in item['models'][1:]:
            model_id = json.loads((ctx.tree / name).read_text())['id']
            status = report.get('additional_model_gates', {}).get(model_id, {}).get(gate, 'not_run')
            need(status == 'passed', f'{platform}: {gate} for {model_id} {status}')

    def upstream_reference():
        _, report = ctx.native_gate(platform, 'upstream_reference')
        additional_models(report, 'upstream_reference')

    def original_reference():
        directory, report = ctx.native_gate(platform, 'original_reference')
        additional_models(report, 'original_reference')
        result = ctx.ci_json(f'{platform}:native', directory / 'original-reference.json')
        need(result.get('summary', {}).get('status') == 'passed', f'{platform}: original reference did not pass')
        allowed = {f'{d["case"]}/{d["question"]}': d for d in item['known_deviations'] if d['cases'] == 'english'}
        used = result['summary'].get('known_deviations_used', [])
        need(set(used) <= set(allowed), f'{platform}: undocumented deviations {sorted(set(used) - set(allowed))}')
        for name in used:
            case, question = name.split('/')
            drift = result['cases'][case]['answers'][question]['max_probability_drift']
            need(drift <= allowed[name]['max_probability_drift'], f'{platform}: {name} drift {drift} above its cap')

    def installed_packages():
        ctx.native_gate(platform, 'installed_packages')
        directory, inventory = ctx.release_packages()
        installed = ctx.ci_json(f'{platform}:installed')
        ctx.same_source(installed, f'{platform} installed packages')
        need(installed.get('status') == 'passed' and installed.get('platform') == platform,
             f'{platform}: installed release packages {installed.get("status")} on {installed.get("platform")}')
        need(installed.get('artifact_inventory_sha256') == sha256(directory / 'inventory.json'),
             f'{platform}: installed packages are not the release packages')
        need(installed.get('model_sha256') == ctx.primary['sha256'], f'{platform}: installed packages ran another model')
        need(len(set(installed.get('consumers', {}).values())) == 1, f'{platform}: installed consumers disagree')
        if item['electron']:
            electron = ctx.ci_json(f'{platform}:electron')
            npm = f'ducktape-laya-{inventory["package_version"]}.tgz'
            need(electron.get('status') == 'passed' and electron.get('platform') == platform, f'{platform}: Electron did not pass here')
            need(electron.get('artifact') == npm and electron.get('artifact_sha256') == sha256(directory / npm),
                 f'{platform}: Electron did not test the release npm package')
            need({r.get('mode') for r in electron.get('results', [])} == {'directory', 'asar'}, f'{platform}: Electron directory and ASAR modes required')
            versions = ctx.policy['platforms']['targets'][platform]['electron_versions']
            need({r.get('electron') for r in electron['results']} <= set(versions), f'{platform}: unlisted Electron version')

    def artifact_integrity():
        _, report = ctx.target_native(platform)
        _, fragment = ctx.fragment(platform)
        identities = fragment.get('build_identities', {})
        need(identities.get('ducktape_laya', {}).get('binary_sha256') == report['library_sha256'],
             f'{platform}: packaged library is not the validated library')
        need(identities.get('ducktape_laya_node', {}).get('binary_sha256') == report['addon_sha256'],
             f'{platform}: packaged Node addon is not the validated addon')
        ctx.release_payload(platform)
        for run in ('packaging-identities', 'browser-backend-identities', 'package', 'package-smoke'):
            need(report.get('runs', {}).get(run, {}).get('exit_code') == 0, f'{platform} validation: {run} did not pass')

    def reproducibility():
        _, native = ctx.target_native(platform)
        report = ctx.ci_json(f'{platform}:reproducibility')
        ctx.same_source(report, f'{platform} reproducibility')
        need(report.get('status') == 'passed' and report.get('differences') == [], f'{platform}: reproduced builds differ')
        need(report.get('target_platform') == platform, f'{platform}: reproducibility ran on {report.get("target_platform")}')
        # Windows DLLs link the image's MSVC and SDK libraries: reproduce on the validated build's runner image.
        need(report.get('build_environment') == native.get('build_environment'),
             f'{platform}: reproduced with toolchain environment {report.get("build_environment")}, '
             f'not the validated build\'s {native.get("build_environment")}')
        builds = report.get('builds', {})
        need(len(builds) == 2, f'{platform}: reproducibility needs two builds')
        for label, files in builds.items():
            need(any(entry.get('sha256') == native['library_sha256'] for entry in files.values()),
                 f'{platform}: reproduced {label} build does not contain the validated library')

    def short_stress():
        _, native = ctx.target_native(platform)
        report = ctx.ci_json(f'{platform}:stress')
        ctx.same_source(report, f'{platform} stress')
        need(report.get('status') == 'passed' and report.get('kind') == 'short_stress' and report.get('qualification_budget_met') is True,
             f'{platform}: short stress failed or below budget')
        need(report.get('requested_seconds', 0) >= ctx.policy['budgets']['short_stress_seconds'], f'{platform}: stress shorter than the budget')
        need(report.get('target_platform') == platform, f'{platform}: stress ran on {report.get("target_platform")}')
        need(report.get('library_sha256') == native['library_sha256'], f'{platform}: stress did not run the validated library')
        need(report.get('model_sha256') == ctx.primary['sha256'], f'{platform}: stress ran another model')
        need(report.get('post_run_identity_verified') is True, f'{platform}: stress identities not verified after the run')

    def cross_profile_drift():
        directory, _ = ctx.target_native(platform)
        outputs = ctx.ci_file(f'{platform}:native', directory / 'outputs.json')
        reference = ctx.path('native').parent / 'outputs.json'
        if not reference.is_file():
            raise Missing('native outputs.json missing')
        drift = ctx.load(f'{platform}:drift')
        need(drift.get('comparison') == 'native_cross_profile_drift' and drift.get('status') == 'passed',
             f'{platform}: drift comparison failed')
        need(drift.get('reference_platform') == 'linux-x64' and drift.get('target_platform') == platform,
             f'{platform}: drift compares {drift.get("reference_platform")} with {drift.get("target_platform")}')
        need(drift.get('reference_report_sha256') == sha256(reference), f'{platform}: drift reference is not the validated linux-x64 outputs')
        need(drift.get('target_report_sha256') == sha256(outputs), f'{platform}: drift target is not this target\'s CI outputs')
        need(drift.get('model_sha256') == ctx.primary['sha256'], f'{platform}: drift measured with another model')
        need(drift.get('decision_differences') == [], f'{platform}: changed decisions {drift.get("decision_differences")}')

    gates = {'native_contracts': native_contracts, 'binding_conformance': lambda: ctx.native_gate(platform, 'binding_conformance'),
             'upstream_reference': upstream_reference, 'original_reference': original_reference,
             'abi': lambda: ctx.native_gate(platform, 'abi'), 'installed_packages': installed_packages,
             'artifact_integrity': artifact_integrity, 'reproducibility': reproducibility, 'short_stress': short_stress,
             'cross_profile_drift': cross_profile_drift}
    if set(gates) != set(ctx.policy['target_gates']):
        raise SystemExit('Target gate checker does not match release/qualification.json target_gates')
    return {name: gates[name] for name in item['required_gates']}


def run_gate(check):
    try:
        check()
        return {'status': 'passed'}
    except Missing as error:
        return {'status': 'not_run', 'detail': str(error)}
    except (Mismatch, KeyError, ValueError, OSError, TypeError) as error:
        return {'status': 'failed', 'detail': f'{type(error).__name__}: {error}'}


def evaluate_targets(ctx, tier1_gates):
    """{platform: {tier, gates[, ci_run]}}; the tier-1 target repeats the release-level gates."""
    result = {}
    for platform, item in ctx.targets.items():
        if item['tier'] == 1:
            result[platform] = {'tier': 1, 'gates': {gate: tier1_gates[gate] for gate in item['required_gates']}}
            continue
        result[platform] = {'tier': item['tier'], 'gates': {gate: run_gate(check) for gate, check in target_gates(ctx, platform).items()}}
    try:
        run = ctx.load('ci_run')
        for section in result.values():
            if section['tier'] != 1:
                section['ci_run'] = {key: run.get(key) for key in ('run_id', 'run_attempt', 'html_url')}
    except (Missing, ValueError, OSError):
        pass
    return result
