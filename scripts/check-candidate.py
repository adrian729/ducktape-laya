#!/usr/bin/env python3
"""Aggregate identity-bound evidence for one release candidate into gate results.

The evidence manifest maps evidence names to report paths produced by the
runners in docs/RELEASE_CHECKS.md. The candidate is the clean HEAD commit, the
packages from the native validation report, the release packages the CI
candidate run assembled (scripts/fetch-ci-evidence.py) and the browser package
inventory. The CI linux-x64 library and Node addon must be byte-identical to the
locally validated ones. Every release target in release/compatibility.json is
evaluated: the tier-1 target with the gates below, each tier-2 target with its
own gates from CI evidence (scripts/release_targets.py).
Each required gate passes only if its evidence exists, passed, and is bound to
that candidate (source commit, library, package, asset and model hashes). Hours-long
gates (soak, stress, fuzz, sanitizers, performance, upgrade rehearsals) may reuse
evidence from an ancestor commit when no library input and none of that gate's own
harness files changed since; the report lists every reuse.
Missing evidence is not_run; mismatched or failed evidence is failed.
`release_ready` is true only when every gate of every target passed. Publication gates
(signing, registry installation, retained rollback artifacts) are separate.
"""
import argparse
import fnmatch
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from release_support import ROOT, check_qualification, check_record, check_targets, git, read_json, security_review_problems, sha256, source_identity
from release_targets import (Context, Missing, Mismatch, COMPARISON_NOTES, compare_packages, evaluate_targets, need, require_complete_validation,
                             run_gate)

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('evidence', type=pathlib.Path, help='JSON object: evidence name -> report path (relative to the manifest)')
p.add_argument('--candidate', default='HEAD', help='Candidate commit (default HEAD); evaluated from its own tree')
p.add_argument('--report', type=pathlib.Path, required=True)
a = p.parse_args()
commit = git(ROOT, 'rev-parse', '--verify', a.candidate + '^{commit}')
# Release record, policy, notices and source identity come from the candidate's
# tree, so later tooling or documentation commits cannot change what is checked.
candidate_tree = pathlib.Path(tempfile.mkdtemp(prefix='ducktape-candidate-'))
with subprocess.Popen(['git', '-C', str(ROOT), 'archive', commit], stdout=subprocess.PIPE) as archive:
    with tarfile.open(fileobj=archive.stdout, mode='r|') as stream:
        stream.extractall(candidate_tree, filter='data')
record = read_json(candidate_tree / 'release/compatibility.json')
check_record()  # the checker checkout: runtime pin, patches and package versions
# The candidate's own record, policy and targets (an empty required_gates list, an unknown gate or a
# missing build workflow in a candidate whose checker checkout is newer must not pass unnoticed).
try:
    if record.get('schema_version') != 2: raise ValueError('Unsupported compatibility record schema')
    policy = check_qualification(record, candidate_tree)
    check_targets(record, candidate_tree)
except (ValueError, KeyError, TypeError, OSError) as error:
    raise SystemExit(f'check-candidate: the candidate {commit} has an invalid release record: {type(error).__name__}: {error}')
manifest_model = read_json(candidate_tree / record['models'][0])
base = a.evidence.resolve().parent
paths = {key: (base / value).resolve() for key, value in read_json(a.evidence).items()}
dirty = False  # Evidence must come from a clean candidate tree; checked per report.
source = source_identity(candidate_tree)['sha256']
ctx = Context(commit, source, record, policy, candidate_tree, paths)
load, same_source = ctx.load, ctx.same_source

# Inputs of the library under test, and of each reusable gate's harness.
LIBRARY_INPUTS = ['src/*', 'include/*', 'cmake/*', 'patches/*', 'CMakeLists.txt', 'release/compatibility.json',
                  'models/*', 'third_party/*', '.gitmodules']
NATIVE_TESTS = ['tests/*.cpp', 'tests/*.c', 'tests/consumers/*', 'tests/fixtures/*']
# Binding code and build manifests; package metadata alone does not change behaviour.
BINDING_CODE = ['bindings/*.cjs', 'bindings/*.mjs', 'bindings/*.cpp', 'bindings/*.rs', 'bindings/*.toml', 'bindings/*.lock',
                'bindings/*.py', 'bindings/*.go', 'bindings/*.mod', 'bindings/*.java', 'bindings/*.xml', 'bindings/*.sh',
                'bindings/*.cs', 'bindings/*.csproj', 'bindings/*.slnx']
# Helpers every runner imports.
COMMON_HARNESS = ['scripts/release_support.py', 'scripts/artifact_support.py', 'tests/reference_requests.py']
HARNESS = {
    # Package manifests too: they declare dependencies and install scripts.
    'security_review': BINDING_CODE + ['bindings/*.json', 'scripts/*', '.github/*'],
    'stress': ['tests/stress.cpp', 'scripts/check-stress.py'],
    'soak': ['tests/stress.cpp', 'scripts/check-stress.py'],
    # tests/fuzz-corpus.py seeds the corpus from the fixtures.
    'fuzz': ['tests/input-libfuzzer.cpp', 'tests/fuzz-corpus.py', 'scripts/check-fuzz.py', 'tests/fixtures/*'],
    # The native tests read the routing cases of the original reference.
    'sanitizers': NATIVE_TESTS + ['scripts/check-sanitizers.py', 'bindings/rust/*.rs', 'bindings/rust/*.toml', 'bindings/rust/*.lock',
                                  'tests/original-reference/routing/*'],
    'performance': ['tests/benchmark.py', 'scripts/performance-budgets.py', 'tests/fixtures/*'],
    'upgrade': NATIVE_TESTS + ['scripts/check-upgrade.py', 'scripts/compare_results.py', 'tests/parity.py'] + BINDING_CODE,
    'model_upgrade': NATIVE_TESTS + ['scripts/check-upgrade.py', 'scripts/compare_results.py', 'tests/parity.py'] + BINDING_CODE,
}
reused = {}
def tested_source(report, name):
    """Same commit, or an ancestor whose changes since touch no input of this gate."""
    produced = report.get('source_commit')
    need(report.get('source_tracked_dirty') is False, f'{name}: produced from a dirty tree')
    if produced == commit:
        return
    need(isinstance(produced, str) and subprocess.run(['git', '-C', str(ROOT), 'merge-base', '--is-ancestor', produced, commit],
         capture_output=True).returncode == 0, f'{name}: source {produced} is not the candidate or its ancestor')
    changed = git(ROOT, 'diff', '--name-only', '--no-renames', produced, commit).splitlines()
    touched = [path for path in changed if any(fnmatch.fnmatch(path, pattern) for pattern in LIBRARY_INPUTS + COMMON_HARNESS + HARNESS[name])]
    need(not touched, f'{name}: inputs changed since {produced[:12]}: {", ".join(touched[:6])}')
    reused[name] = produced

def native():
    report = load('native')
    same_source(report, 'native')
    require_complete_validation(report, 'native')
    need(report.get('mode') == 'real_model' and report.get('model', {}).get('sha256') == manifest_model['sha256'], 'native: not a real-model run of the pinned model')
    return report
def packages():
    report = native()
    directory = paths['native'].parent / 'packages'
    inventory = read_json(directory / 'inventory.json')
    need(inventory['packaging_source_commit'] == commit and not inventory['packaging_source_tracked_dirty'], 'packages: not built from candidate')
    need(inventory['build_source']['sha256'] == source, 'packages: built from different sources')
    for name, identity in inventory['artifacts'].items():
        need(sha256(directory / name) == identity['sha256'], f'packages: {name} differs from inventory')
    return report, directory, inventory
def browser_package():
    path = paths.get('browser_package')
    if not path or not path.is_file(): raise Missing('browser_package evidence missing')
    inventory = json.loads(path.read_text())
    need(inventory['packaging_source_commit'] == commit and not inventory['packaging_source_tracked_dirty'], 'browser package: not built from candidate')
    need(inventory['build_source']['sha256'] == source, 'browser package: assets built from different sources')
    for name, digest in inventory['artifacts'].items():
        need(sha256(path.parent / name) == digest, f'browser package: {name} differs from inventory')
    return inventory

def native_gate(gate):
    def check():
        report = native()
        need(report['gates'][gate]['status'] == 'passed', f'native: {gate} {report["gates"][gate]["status"]}')
        # Every additional pinned model is qualified like the primary one: the reference gates, and
        # within the native contracts its tokenizer golden entries, selected decision head and
        # tokenization stops (real-tokenizer, real-decision-head and real-control-tokenize run on the primary model).
        per_model = {'original_reference': [gate], 'upstream_reference': [gate],
                     'native_contracts': ['tokenizer', 'decision_head', 'control_tokenize']}
        for name in record.get('additional_models', []) if gate in per_model else []:
            model_id = read_json(candidate_tree / name)['id']
            for model_gate in per_model[gate]:
                status = report.get('additional_model_gates', {}).get(model_id, {}).get(model_gate, 'not_run')
                need(status == 'passed', f'native: {model_gate} for {model_id} {status}')
        if gate == 'original_reference':
            # Only the tier-1 target's documented deviations (none today) may exceed the limit.
            result = json.loads((paths['native'].parent / 'original-reference.json').read_text())
            allowed = {f'{d["case"]}/{d["question"]}' for d in ctx.targets['linux-x64']['known_deviations']}
            need(result['summary']['status'] == 'passed' and set(result['summary'].get('known_deviations_used', [])) <= allowed,
                 'native: original reference used an undocumented deviation')
        if gate == 'installed_packages':
            packages()
            # The shipped (CI-assembled) packages, installed here and on the CI linux-x64 runner.
            directory, _ = ctx.release_packages()
            for name in ('installed_release', 'linux-x64:installed'):
                installed = load(name) if name == 'installed_release' else ctx.ci_json(name)
                same_source(installed, name)
                host = installed.get('host') or {}
                if name == 'installed_release':
                    # The release host's own run: not the CI runner's report (a CI file, run on GitHub Actions) under another name.
                    need(paths[name].resolve() != ctx.path('linux-x64:installed').resolve() and
                         ctx.sha256(paths[name]) != ctx.sha256(ctx.path('linux-x64:installed')), f'{name}: is the CI linux-x64 report')
                    need(host.get('github_actions') is False and host.get('system') == 'Linux' and host.get('machine') in ('x86_64', 'AMD64'),
                         f'{name}: not run on a local linux-x64 host ({host or "no host recorded"})')
                    ci_host = ctx.ci_json('linux-x64:installed').get('host') or {}
                    need(host.get('hostname') and host.get('hostname') != ci_host.get('hostname'), f'{name}: ran on the CI host')
                else:
                    need(host.get('github_actions') is True, f'{name}: not a GitHub Actions run ({host or "no host recorded"})')
                need(installed['status'] == 'passed' and installed['platform'] == 'linux-x64', f'{name}: failed or not linux-x64')
                need(installed['artifact_inventory_sha256'] == sha256(directory / 'inventory.json'), f'{name}: not the release packages')
                need(installed['model_sha256'] == manifest_model['sha256'], f'{name}: model differs')
    return check

def sanitizers():
    report = load('sanitizers')
    tested_source(report, 'sanitizers')
    need(report['status'] == 'passed' and all(s['status'] == 'passed' for s in report['sanitizers'].values()), 'sanitizers: not all passed')
    need(report['model_sha256'] == manifest_model['sha256'], 'sanitizers: model differs')
    fuzz = load('fuzz')
    tested_source(fuzz, 'fuzz')
    need(fuzz['status'] == 'passed' and fuzz['qualification_budget_met'], 'fuzz: failed or below the qualification budget')
    # The budget of the candidate's own policy, not the one the (possibly reused) run was measured against.
    need(fuzz.get('requested_seconds', 0) >= policy['budgets']['fuzz_seconds_per_target'], 'fuzz: shorter than the candidate\'s fuzz budget')

def stress():
    library = native()['library_sha256']
    for name, kind in (('stress', 'routine_stress'), ('soak', 'candidate_soak')):
        report = load(name)
        tested_source(report, name)
        need(report['status'] == 'passed' and report['kind'] == kind and report['qualification_budget_met'], f'{name}: failed or below budget')
        budget = policy['budgets']['routine_stress_seconds' if name == 'stress' else 'candidate_soak_seconds']
        need(report.get('requested_seconds', 0) >= budget, f'{name}: shorter than the candidate\'s {budget} s budget')
        need(report['library_sha256'] == library, f'{name}: library is not the candidate library')
        need(report['model_sha256'] == manifest_model['sha256'], f'{name}: model differs')

def linux_matrix():
    # Userspaces and Electron run the shipped release packages.
    directory, _ = ctx.release_packages()
    userspaces = load('userspaces')
    need(userspaces['status'] == 'passed' and userspaces['artifact_inventory_sha256'] == sha256(directory / 'inventory.json'),
         'userspaces: failed or not the candidate packages')
    releases = {env['os_release'].get('VERSION_ID', '').strip('"') for env in userspaces['environments'].values() if env['status'] == 'passed'}
    required = {policy['platforms']['native']['minimum_userspace'], *policy['platforms']['native']['additional_userspaces']}
    need({f'ubuntu-{v}' for v in releases} >= required, f'userspaces: {sorted(releases)} do not cover {sorted(required)}')
    electron = load('electron')
    npm = f'ducktape-laya-{ctx.record["package_version"]}.tgz'
    need(electron['status'] == 'passed' and electron.get('platform') == 'linux-x64', 'electron: did not pass on linux-x64')
    need(electron['artifact'] == npm and electron['artifact_sha256'] == sha256(directory / npm), 'electron: not the candidate npm package')
    need({r['mode'] for r in electron['results']} == {'directory', 'asar'}, 'electron: directory and ASAR modes required')
    need({r['electron'] for r in electron['results']} <= set(policy['platforms']['native']['electron_versions']), 'electron: unlisted version')
    # Native validation reports from every compiler/Node combination (local or CI artifacts).
    compilers, nodes = set(), set()
    for item in load('native_matrix'):
        path = paths['native_matrix'].parent / item
        if not path.is_file(): raise Missing(f'native matrix report {item} missing')
        report = json.loads(path.read_text())
        same_source(report, f'native matrix {item}')
        require_complete_validation(report, f'native matrix {item}')
        need(all(report['gates'][g]['status'] == 'passed' for g in ('native_contracts', 'binding_conformance', 'installed_packages')),
             f'native matrix {item}: required gates did not pass')
        family, version = report['diagnostics']['compiler'].split()
        compilers.add({'GNU': 'gcc', 'Clang': 'clang'}.get(family, family.lower()) + '-' + version)
        nodes.add(report.get('node_version', '').lstrip('v'))
    need(compilers >= set(policy['platforms']['native']['compilers']), f'compilers: {sorted(compilers)} do not cover the matrix')
    need(nodes >= set(policy['platforms']['native']['node_versions']), f'node: {sorted(nodes)} do not cover the matrix')
    cross = load('cross_cpu')
    need(cross['exact'] and len({c['model name'] for c in cross['cpus']}) == 2, 'cross_cpu: outputs differ or CPUs not distinct')
    need(cross.get('bundle_sha256', {}).get('libducktape_laya.so') == native()['library_sha256'], 'cross_cpu: not the candidate library')

def browser_gate(backend):
    def check():
        package = browser_package()
        for page in ('browser', 'browser_isolated'):
            report = load(page)
            same_source(report, page)
            need(report['model_sha256'] == manifest_model['sha256'], f'{page}: model differs')
            need(report.get('package_dir'), f'{page}: did not test the extracted package')
            need(report['isolated'] == (page == 'browser_isolated') and not report['headless'] and not report['launch_flags'], f'{page}: page/launch settings differ')
            browsers = policy['platforms']['browser']['browser_versions']
            need(any(str(report.get('browser_version', '')).endswith(version.split('-', 1)[-1]) for version in browsers),
                 f'{page}: browser {report.get("browser_version")} is not one of {browsers}')
            if backend == 'webgpu':
                gpu = (report.get('environment') or {}).get('gpu') or {}
                hardware = f'{gpu.get("vendor")}-{gpu.get("architecture")}'
                need(gpu and not gpu.get('fallback') and hardware in policy['platforms']['browser']['webgpu_hardware'],
                     f'{page}: WebGPU ran on {hardware or "no adapter"}, not listed hardware')
            for name in ('laya.mjs', 'laya.wasm'):
                need(report['asset_hashes'][f'{backend}/{name}'] == package['assets'][f'{backend}/{name}'], f'{page}: {backend} assets are not the candidate package')
            gates = report['backends'][backend]
            required = ['execution', 'reference', 'controls', 'assets', 'upstream_numeric'] + (['device_loss', 'gpu_memory'] if backend == 'webgpu' else ['memory'])
            for name in required:
                need(gates.get(name, {}).get('status') == 'passed', f'{page}: {backend}/{name} {gates.get(name, {}).get("status", "missing")}')
    return check

def cross_profile_drift():
    report = native()
    outputs = sha256(paths['native'].parent / 'outputs.json')
    browser = load('browser')
    same_source(browser, 'browser')
    need(browser.get('native_report_sha256') == outputs, 'drift: browser compared against a different native report')
    for backend in ('wasm', 'webgpu'):
        need(browser['backends'][backend]['cross_profile_drift']['status'] == 'passed', f'drift: {backend} comparison failed')
    need(report['gates']['binding_conformance']['status'] == 'passed', 'drift: native conformance failed')

LICENSE_TEXTS = {'LICENSE', 'THIRD_PARTY_NOTICES.md', 'Apache-2.0.txt', 'llama.cpp-MIT.txt', 'nlohmann-json-MIT.txt'}
def license_inventory():
    _, directory, inventory = packages()
    package = browser_package()
    release, shipped = ctx.release_packages()
    for label, names, folder in (('native', inventory['artifacts'], directory), ('release', shipped['artifacts'], release),
                                 ('browser', package['artifacts'], paths['browser_package'].parent)):
        need('sbom.cdx.json' in names, f'{label}: SBOM missing from inventory')
        sbom = json.loads((folder / 'sbom.cdx.json').read_text())
        for component in sbom['components']:
            for prop in component['properties']:
                if prop['name'].startswith('ducktape:notice:'):
                    need(sha256(candidate_tree / prop['name'].split(':', 2)[2]) == prop['value'], f'{label}: SBOM notice hash is stale')
    with tarfile.open(directory / f'ducktape-laya-{inventory["package_version"]}-linux-x64.tar.gz') as archive:
        members = {pathlib.PurePosixPath(m).name for m in archive.getnames()}
    need(LICENSE_TEXTS <= members, 'native archive lacks license texts')
    # Every platform's shipped archive (tar.gz or zip) carries the license texts.
    for platform, section in shipped['fragments'].items():
        name = section['archive']
        if name.endswith('.zip'):
            with zipfile.ZipFile(release / name) as archive:
                members = {pathlib.PurePosixPath(m).name for m in archive.namelist()}
        else:
            with tarfile.open(release / name) as archive:
                members = {pathlib.PurePosixPath(m).name for m in archive.getnames()}
        need(LICENSE_TEXTS <= members, f'{name} lacks license texts')
    review = load('security_review')
    need(isinstance(review.get('model_licence'), str) and review['model_licence'].strip(), 'license review missing')

def reproducibility():
    report = load('reproducibility')
    same_source(report, 'reproducibility')
    need(report['status'] == 'passed' and not report['differences'], 'reproducibility: builds differ')
    validated = native()
    for label, files in report['builds'].items():
        for kind in ('library', 'addon'):
            need(any(entry.get('sha256') == validated[f'{kind}_sha256'] for entry in files.values()),
                 f'reproducibility: {label} build does not contain the candidate {kind}')

def artifact_integrity():
    report, _, inventory = packages()
    for run in ('packaging-identities', 'browser-backend-identities'):
        need(report['runs'][run]['exit_code'] == 0, f'native: {run} failed')
    need(inventory['build_identities']['ducktape_laya']['binary_sha256'] == report['library_sha256'], 'packages: library differs from validated library')
    # The shipped linux-x64 library and Node addon are the locally qualified bytes: CI must
    # reproduce this host's build exactly (a difference is not environmental; stop and report).
    addon = inventory['build_identities']['ducktape_laya_node']['binary_sha256']
    need(report.get('addon_sha256') == addon, 'packages: Node addon differs from validated addon')
    _, ci = ctx.target_native('linux-x64')
    need(ci['library_sha256'] == report['library_sha256'], 'CI linux-x64 library differs from the locally validated library')
    need(ci['addon_sha256'] == addon, 'CI linux-x64 Node addon differs from the locally validated addon')
    _, fragment = ctx.fragment('linux-x64')
    identities = fragment['build_identities']
    need(identities['ducktape_laya']['binary_sha256'] == report['library_sha256'] and identities['ducktape_laya_node']['binary_sha256'] == addon,
         'release packages: linux-x64 library or addon is not the locally validated one')
    # Everything else the linux-x64 packages carry (headers, CMake and pkg-config files, Python, Java and C# code,
    # crates, SBOM) must be what this host's own package-native run made, compared entry by entry (the containers'
    # compression differs between hosts, their entries do not).
    local_directory = paths['native'].parent / 'packages'
    fragment_directory, _ = ctx.fragment('linux-x64')
    differences = compare_packages(local_directory, fragment_directory, list(inventory['artifacts']), list(fragment['artifacts']))
    need(not differences, 'packages: CI linux-x64 fragment differs from this host\'s package run: ' +
         '; '.join(f'{name}: {detail}' for name, detail in differences[:6]))
    for platform in ctx.targets:
        ctx.release_payload(platform)
    browser_package()

def performance():
    # The primary model's benchmark is evidence "performance"; each additional model's
    # is "performance_<model id>", checked against the budget its manifest names.
    checks = [('performance', 'release/budgets/native-performance.json')]
    for name in record.get('additional_models', []):
        manifest = read_json(candidate_tree / name)
        checks.append((f'performance_{manifest["id"]}', manifest['performance_budgets']))
    for evidence, budgets in checks:
        report = load(evidence)
        tested_source(report, 'performance')
        need(report['library_sha256'] == native()['library_sha256'], f'{evidence}: not the candidate library')
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/performance-budgets.py'), 'check', str(paths[evidence]),
                                 '--budgets', str(candidate_tree / budgets)], capture_output=True, text=True)
        need(result.returncode == 0, f'{evidence}: ' + result.stdout.strip())

def security_review():
    review = load('security_review')
    problems = security_review_problems(review)
    need(not problems, 'security review: ' + '; '.join(problems))
    tested_source(review, 'security_review')
    need(review.get('runtime_commit') == record['runtime']['commit'], 'security review: different runtime pin')
    need(review.get('runtime_patches') == [patch['sha256'] for patch in record['runtime']['patches']], 'security review: different runtime patches')
    need(review.get('release_blocking_code_issues') == [], 'security review: unresolved release-blocking issues')

def upgrade_rollback():
    upgrade = load('upgrade')
    tested_source(upgrade, 'upgrade')
    need(upgrade['candidate_runtime'] != upgrade['baseline_runtime'] and upgrade['adapter_checks_passed'], 'upgrade: runtime axis not rehearsed')
    model = load('model_upgrade')
    tested_source(model, 'model_upgrade')
    need(model['candidate_model']['sha256'] != model['model']['sha256'], 'model upgrade: no model axis')
    need(all(model['gates'][g]['status'] == 'passed' for g in ('native_contracts', 'binding_conformance', 'abi')) and
         model.get('comparison', {}).get('status') in ('passed', 'failed'), 'model upgrade: candidate checks did not complete')
    rollback = load('rollback')
    need(rollback['status'] == 'passed' and rollback['candidate_inventory_sha256'] == sha256(ctx.release_packages()[0] / 'inventory.json'),
         'rollback: failed or not rehearsed with the release packages')
    version = lambda text: tuple(int(part) for part in text.split('.'))
    need(rollback.get('previous_inventory_sha256') != rollback['candidate_inventory_sha256'] and
         version(rollback.get('previous_version', '0.0.0')) < version(rollback.get('candidate_version', '0.0.0')),
         'rollback: the previous side is not an earlier release')
    need(subprocess.run(['git', '-C', str(ROOT), 'merge-base', '--is-ancestor', rollback.get('previous_source_commit', ''), commit],
                        capture_output=True).returncode == 0, 'rollback: the previous release is not an ancestor of the candidate')

GATES = {'native_contracts': native_gate('native_contracts'), 'binding_conformance': native_gate('binding_conformance'),
         'upstream_reference': native_gate('upstream_reference'), 'original_reference': native_gate('original_reference'),
         'abi': native_gate('abi'),
         'installed_packages': native_gate('installed_packages'), 'sanitizers': sanitizers, 'stress': stress,
         'linux_matrix': linux_matrix, 'browser_wasm': browser_gate('wasm'), 'browser_webgpu': browser_gate('webgpu'),
         'license_inventory': license_inventory, 'reproducibility': reproducibility, 'cross_profile_drift': cross_profile_drift,
         'artifact_integrity': artifact_integrity, 'performance': performance, 'security_review': security_review,
         'upgrade_rollback': upgrade_rollback}
def checker_tree_dirty():
    """Tracked changes anywhere, or any untracked file under scripts/ (ignored ones included, bytecode caches
    excepted): the checker imports its modules from there before anything else."""
    untracked = [name for name in git(ROOT, 'ls-files', '--others', '--', 'scripts').splitlines() if '__pycache__/' not in name]
    return bool(git(ROOT, 'diff', 'HEAD', '--name-only') or untracked)

if set(GATES) != set(record['required_release_gates']):
    raise SystemExit('Gate checker does not match release/compatibility.json required gates')
result = {'schema_version': 2, 'candidate_commit': commit, 'checker_commit': git(ROOT, 'rev-parse', 'HEAD'),
          'checker_tree_dirty': checker_tree_dirty(),
          'source_identity_sha256': source,
          'model_sha256': manifest_model['sha256'], 'evidence': {k: pathlib.PurePath(os.path.relpath(v, base)).as_posix() for k, v in paths.items()}, 'gates': {}}
# Release-level (tier-1) gates, then every target's.
for gate, check in GATES.items():
    result['gates'][gate] = run_gate(check)
result['targets'] = evaluate_targets(ctx, result['gates'])
# A dirty checker's verdict is not release evidence (scripts/release-gates.jq refuses it too).
result['release_ready'] = (not result['checker_tree_dirty'] and all(g['status'] == 'passed' for g in result['gates'].values()) and
                           all(g['status'] == 'passed' for t in result['targets'].values() for g in t['gates'].values()))
# The package inventories this report qualified; attest-release.yml checks the
# published native-inventory.json (the CI-assembled release packages) and
# npm-browser-inventory.json against them.
result['inventories_sha256'] = {}
for label, path in (('native', paths.get('release_packages')), ('browser', paths.get('browser_package'))):
    if path and path.is_file(): result['inventories_sha256'][label] = sha256(path)
# The CI candidate run the release packages come from; attest-release.yml requires the published
# native-inventory.json to be its packages-assembled inventory.
try:
    run = ctx.ci_run()
    result['ci_run'] = {key: run.get(key) for key in ('repository', 'workflow_path', 'run_id', 'run_attempt', 'head_sha', 'html_url')}
except (Missing, Mismatch, ValueError, OSError):
    pass
result['reused_evidence'] = reused
result['package_comparison'] = COMPARISON_NOTES
result['publication_gates'] = {gate: 'not_run' for gate in policy['publication_gates']}
a.report.write_text(json.dumps(result, indent=2) + '\n')
shutil.rmtree(candidate_tree, ignore_errors=True)
print(json.dumps({'release_ready': result['release_ready'], 'gates': {k: v['status'] for k, v in result['gates'].items()},
                  'targets': {p: {k: v['status'] for k, v in t['gates'].items()} for p, t in result['targets'].items() if t['tier'] != 1}},
                 indent=2))
raise SystemExit(0 if result['release_ready'] else 1)
