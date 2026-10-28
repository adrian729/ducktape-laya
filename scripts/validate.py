#!/usr/bin/env python3
"""Run local native validation and retain explicit passed/failed/not_run gates.

Without --model this is a model-free smoke check, never release approval.
No model downloads, expected-output changes, publication or pin promotion.

Checks (--checks, or the flags that add them; the list is generated from CHECK_HELP):
@CHECKS@

--full runs every check of FULL_CHECKS and FULL_POLICY_CHECKS (with --upstream-reference) and writes
selection.profile host-full: the release host's evidence. It refuses (exit 1, full_refusal.missing) when any of
them neither passed nor is a listed platform skip, and refuses before configuring without --model or an additional
model of the release record. Every other run writes profile generic (the default) or selected; neither is evidence.

Phases: --phase build configures and builds and keeps BUILD/build-phase/ (report.json, configure.log,
build.log); --phase test --from BUILD runs --checks on that build without building (the library, addon and
CMakeCache.txt must be the build phase's); --phase all (the default) does both. The report is always written; the
exit status is non-zero when any selected check fails.

Runs on every release target (Linux x86-64 and arm64, Windows x64). A check that does not apply on a platform is
never left out silently: the report lists it under `skipped` with the reason, and every check not selected with
'not selected'. On Windows run it from a Visual Studio x64 developer environment and pass the clang-cl build
through --cmake-arg.
"""
import argparse
import concurrent.futures
import copy
import json
import os
import pathlib
import platform as platform_module
import re
import shutil
import subprocess
import sys
import textwrap
import threading
import time
import xml.etree.ElementTree as ElementTree

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from release_support import (CCACHE_ENVIRONMENT, EXE, LIBRARY, ROOT, binary_info, build_environment, ccache_config,  # noqa: E402
                             check_record, default_jobs, find_manifest, generator_arguments, git, host_target, read_json,
                             require_mapped_source_paths, require_runtime_identity, require_runtime_sources, sha256, source_files,
                             verify_model)

BINDINGS = ('rust', 'node', 'python', 'java', 'dotnet', 'go')
# A failed binding suite fails binding_conformance.
BINDING_SUITES = {'rust', 'node', 'python', 'java-compile', 'java-test-compile', 'java', 'dotnet', 'go'}
# Every run label --full must pass (or skip with a reason from FULL_ALLOWED_SKIPS), and every policy check
# (scripts/check-policy.py --all). Hard-coded here and in scripts/release_targets.py (HOST_FULL_RUNS, POLICY_LABELS),
# independent of scripts/ci_paths.py (M1); tests/release-targets.py requires the lists to agree.
FULL_CHECKS = ('native', 'examples-smoke', 'abi', 'check-binary', 'rust', 'node', 'python', 'java', 'dotnet', 'go', 'bindings',
               'original-reference', 'upstream-reference', 'package', 'package-smoke')
FULL_POLICY_CHECKS = ('result-comparison', 'qualification-policy', 'docs-links', 'docs-links-self-test', 'release-notes', 'ci-changes',
                      'abi-layouts', 'binding-abi', 'typescript-consumers', 'packaging-identities', 'package-assembly', 'fetch-ci-evidence',
                      'release-targets', 'release-assets', 'browser-backend-identities', 'browser-lifecycle', 'browser-binding',
                      'model-download', 'runtime-sources', 'merge-validation', 'validate', 'check-sanitizers', 'verify', 'qualify-host',
                      'ci-contracts')
# The platform allow-list: {label: (sys.platform values, reason)}; policy labels are checked as policy:<label>.
FULL_ALLOWED_SKIPS = {'examples-smoke': (('win32',), 'compiles the examples with a POSIX driver'),
                      'policy:release-targets': (('win32',), None), 'policy:release-assets': (('win32',), None)}
PLATFORM_POLICY = ('packaging-identities', 'browser-backend-identities', 'abi-layouts', 'typescript-consumers')
# Every accepted --checks label and what it runs; the --help text is generated from this table.
CHECK_HELP = {
    'generic': "the default: ctest -L '^tier-generic$' (with --model also '^cat-real_model$'), examples-smoke, the ABI check and scripts/check-binary.py",
    'ctest': 'every registered native test (ctest --no-tests=error, the JUnit names compared with the registrations), examples-smoke, ABI, '
             'check-binary; with --model the native_contracts gate',
    'references': "the original Laya reference and, with the reference probe (--upstream-reference), upstream; the additional models' "
                  'reference and identity runs (--extra-model)',
    'parity:smoke': 'tests/parity.py --profile smoke (--parity smoke)',
    'parity:smoke-a': 'the first half of the smoke matrix (the Windows real-model shards)',
    'parity:smoke-b': 'the second half of the smoke matrix',
    'parity:full': 'tests/parity.py --profile full, every binding: the binding_conformance gate (--parity full)',
    'parity:full-a': 'the first half of the full matrix (CI shards; scripts/merge-validation.py joins the halves)',
    'parity:full-b': 'the second half of the full matrix',
    'bindings': 'the binding suites (--bindings LIST|all), concurrently',
    'bindings:a': 'the suites rust, node and python (the Windows real-model shards)',
    'bindings:b': 'the suites java, dotnet and go (the slow-starting ones)',
    'packages': 'scripts/package-native.py and tests/package-smoke.py (--packages)',
    'policy': 'scripts/check-policy.py --all (--policy all)',
    'policy-platform': 'scripts/check-policy.py --platform-only (--policy platform)',
}
CHECKS = tuple(CHECK_HELP)


def checks_help():
    width = max(map(len, CHECK_HELP)) + 2
    return '\n'.join(textwrap.fill(text, 118, initial_indent='  ' + label.ljust(width), subsequent_indent=' ' * (2 + width))
                     for label, text in CHECK_HELP.items())


FULL_SELECTION = ('ctest', 'references', 'parity:full', 'bindings', 'packages', 'policy')
PARITY = {'parity:smoke': ('bindings-smoke', 'smoke', 'all', None), 'parity:full': ('bindings', 'full', 'all', 'binding_conformance'),
          'parity:full-a': ('bindings-a', 'full', 'a', None), 'parity:full-b': ('bindings-b', 'full', 'b', None),
          'parity:smoke-a': ('bindings-smoke-a', 'smoke', 'a', None), 'parity:smoke-b': ('bindings-smoke-b', 'smoke', 'b', None)}
# The binding suites of each half, as tests/parity.py splits the consumers (the slow-starting Java, .NET and Go in b).
BINDING_HALVES = {'bindings:a': ('rust', 'node', 'python'), 'bindings:b': ('java', 'dotnet', 'go')}
NEEDS_TESTS = ('ctest', 'generic', 'references', 'parity:smoke', 'parity:smoke-a', 'parity:smoke-b', 'parity:full', 'parity:full-a',
               'parity:full-b')
NEEDS_MODEL = ('references', 'parity:smoke', 'parity:smoke-a', 'parity:smoke-b', 'parity:full', 'parity:full-a', 'parity:full-b')
# What --full without a model cannot run.
REAL_MODEL_LABELS = ('native', 'bindings', 'original-reference', 'upstream-reference')
# Native tests every target registers (also in scripts/release_targets.py); the fault-injection tests are Linux-only.
MODEL_FREE_TESTS = {'abi-errors', 'feature-errors', 'control-errors', 'decision-rules', 'language-routing', 'tokenizer-partition',
                    'tokenizer-scaling', 'request-parse-scaling'}
REAL_MODEL_TESTS = {'real-model', 'real-features', 'real-control', 'real-control-tokenize', 'real-tokenizer', 'real-decision-head'}
LINUX_REAL_MODEL_TESTS = ('real-faults', 'real-c-faults', 'real-runtime-faults', 'real-api-faults')
ADDITIONAL_MODEL_GATES = ('tokenizer', 'decision_head', 'control_tokenize', 'original_reference', 'upstream_reference')
NOT_SELECTED = 'not selected'
BUILD_PHASE = 'build-phase'
# Build-option cache entries the report records.
RECORDED_OPTIONS = ('CMAKE_C_COMPILER:', 'CMAKE_CXX_COMPILER:', 'CMAKE_C_COMPILER_LAUNCHER:', 'CMAKE_CXX_COMPILER_LAUNCHER:', 'CMAKE_C_FLAGS',
                    'CMAKE_CXX_FLAGS', 'CMAKE_BUILD_TYPE:', 'CMAKE_GENERATOR:', 'GGML_', 'DUCKTAPE_DEV_BUILD:', 'DUCKTAPE_DEV_PCH:',
                    'DUCKTAPE_REFERENCE_PROBE_LAUNCHER:', 'DUCKTAPE_BUILD_REFERENCE_PROBE:', 'DUCKTAPE_TEST_MODEL:')


class Refused(Exception):
    """A selected check failed or a precondition does not hold; the report records it."""


class System:
    """Everything validate.py does outside its own process. tests/validate.py substitutes a fake."""
    windows = sys.platform == 'win32'
    linux = sys.platform.startswith('linux')
    machine = platform_module.machine()

    def run(self, command, log, env, timeout=3600):
        with open(log, 'w') as stream:
            try:
                return subprocess.run(command, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout).returncode
            except subprocess.TimeoutExpired:
                return 124

    def output(self, command, env):
        return subprocess.check_output(command, env=env, text=True)

    def which(self, name):
        return shutil.which(name)

    def verify_model(self, path, manifest):
        verify_model(path, manifest)

    def find_manifest(self, path, record):
        return find_manifest(path, record)

    def binary_info(self, build):
        return binary_info(build)

    def require_build(self, build, runtime, addon=True):
        """The library was linked from this tree's runtime sources, and no unmapped source path reached a binary."""
        require_runtime_sources(build, runtime)
        for binary in (LIBRARY, *(['ducktape_laya.node'] if addon else [])):
            require_mapped_source_paths(build / binary)

    def platform(self):
        try:
            return host_target().name
        except ValueError:
            return None

    def node_version(self):
        return subprocess.check_output(['node', '--version'], text=True).strip()

    def node_headers(self):
        node = shutil.which('node')
        return pathlib.Path(node).resolve().parents[1] / 'include/node' if node else None

    def toolchains(self, launchers):
        commands = {'rustc': ['rustc', '--version'], **{name: [name, '--version'] for name in launchers},
                    'go': ['go', 'version'], 'java': ['java', '--version'], 'dotnet': ['dotnet', '--version'], 'python': [sys.executable, '--version']}
        return {name: (subprocess.run(command, capture_output=True, text=True).stdout or '').strip().splitlines()[:1]
                if shutil.which(command[0]) else [] for name, command in commands.items()}

    def ccache_config(self, env):
        return ccache_config(env)

    def build_environment(self):
        return build_environment()

    def host(self):
        # Recorded as tests/installed-packages.py records it: check-candidate.py requires host-full reports from a local host.
        return {'github_actions': os.environ.get('GITHUB_ACTIONS') == 'true', 'hostname': platform_module.node(),
                'system': platform_module.system(), 'machine': platform_module.machine()}

    def source(self):
        return {'source_commit': git(ROOT, 'rev-parse', 'HEAD'), 'source_tracked_dirty': bool(git(ROOT, 'diff', 'HEAD', '--name-only')),
                'source_files_sha256': source_files(),
                'fixtures_sha256': {x.name: sha256(x) for x in sorted((ROOT / 'tests/fixtures').glob('*.json'))}}


def parser():
    p = argparse.ArgumentParser(description=__doc__.replace('@CHECKS@', checks_help()), formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--model', type=pathlib.Path)
    p.add_argument('--build', type=pathlib.Path, help='Build directory (default build-validation; with --phase test, --from)')
    p.add_argument('--output', type=pathlib.Path, required=True)
    p.add_argument('--jobs', type=int, help='Build jobs (default DUCKTAPE_JOBS, else min(CPUs, MemAvailable / 0.6 GB))')
    p.add_argument('--test-jobs', type=int, help='ctest -j (default 1 with --model off CI, as the real-model timing tests need '
                   'a quiet machine; else min(4, CPUs))')
    p.add_argument('--node-include', type=pathlib.Path)
    p.add_argument('--node-lib', type=pathlib.Path, help='node.lib of the Node distribution (Windows; scripts/fetch-node-sdk.py --lib)')
    p.add_argument('--cmake-arg', action='append', default=[], help='Extra configure argument, repeatable (Windows: -GNinja, '
                   '-DCMAKE_C_COMPILER=.../clang-cl.exe, -DCMAKE_CXX_COMPILER=..., -DDTL_PATCH_EXECUTABLE=...)')
    p.add_argument('--full', action='store_true', help='Every check, every policy check, the upstream reference and the packages: '
                   'selection.profile host-full (needs --model and every additional model)')
    p.add_argument('--checks', help='Comma-separated checks to run: ' + ', '.join(CHECKS))
    p.add_argument('--bindings', help='Binding suites to run: a comma-separated subset of ' + ','.join(BINDINGS) + ', or all')
    p.add_argument('--policy', choices=['all', 'platform', 'none'], default='none', help='Policy checks through scripts/check-policy.py')
    p.add_argument('--packages', action='store_true', help='Build the packages and load them outside the checkout')
    p.add_argument('--parity', choices=['smoke', 'full'], help='Cross-binding parity profile (tests/parity.py)')
    p.add_argument('--upstream-reference', action='store_true', help='Build the isolated test-only upstream processor and compare '
                   'every real-model fixture request (adds the references check)')
    p.add_argument('--extra-model', type=pathlib.Path, action='append', default=[], help='GGUF of an additional pinned model '
                   '(release/compatibility.json additional_models) to qualify against the original Laya and upstream; repeatable')
    p.add_argument('--library-only', action='store_true', help='Build the library and Node addon without the native tests (the addon only where a selected check needs it: not for binding suites other than node)')
    p.add_argument('--phase', choices=['all', 'build', 'test'], default='all')
    p.add_argument('--from', dest='from_build', type=pathlib.Path, help='--phase test: the build directory of a --phase build run')
    p.add_argument('--reproducibility', action='store_true', help='Also compile two independent source snapshots')
    # ggml's GGML_CCACHE compiles through any ccache or sccache on PATH with a global launch rule; it is always OFF here.
    p.add_argument('--compiler-launcher', choices=['none', 'ccache', 'sccache'], default='none',
                   help='Compile the product targets through this cache (CMAKE_<LANG>_COMPILER_LAUNCHER; default none). Recorded in '
                   'the report; the release host\'s --full evidence must be uncached')
    p.add_argument('--reference-probe-launcher', choices=['auto', 'none', 'ccache', 'sccache'], default='auto',
                   help='Compiler cache of the probe-only targets (DUCKTAPE_REFERENCE_PROBE_LAUNCHER; default: ccache or sccache when on PATH)')
    return p


def addon_needed(a, checks, bindings):
    """Whether the run builds the Node addon: always, except a library-only run of binding suites other than node's. Only the node
    suite loads the addon; the packages, the parity runs and the native checks of every other run need it (and its Node headers)."""
    return not (a.library_only and a.phase == 'all' and checks == ['bindings'] and 'node' not in bindings and not a.reproducibility)


def select(p, a, system):
    """The checks of this run and its selection.profile, from the flags."""
    if a.full:
        others = [flag for flag, given in (('--checks', a.checks), ('--bindings', a.bindings), ('--policy', a.policy != 'none'),
                                           ('--packages', a.packages), ('--parity', a.parity), ('--library-only', a.library_only),
                                           ('--phase test', a.phase == 'test')) if given]
        if others:
            p.error('--full selects every check itself; it does not take ' + ', '.join(others))
        if a.phase == 'build':
            # The build of a full run (the reference probe included) for CI shards: no checks, so no evidence profile.
            return {'profile': 'selected', 'checks': [], 'bindings': [], 'policy': 'none', 'packages': False, 'parity_profile': None,
                    'upstream_reference': True, 'addon': True}
        return {'profile': 'host-full', 'checks': list(FULL_SELECTION), 'bindings': list(BINDINGS), 'policy': 'all', 'packages': True,
                'parity_profile': 'full', 'upstream_reference': True,
                'addon': True}
    if a.checks is not None:
        if a.phase == 'build':
            p.error('--phase build runs no checks')
        checks = [name for name in dict.fromkeys(item.strip() for item in a.checks.split(',')) if name]
        unknown = [name for name in checks if name not in CHECKS]
        if unknown or not checks:
            p.error('--checks takes a comma-separated subset of ' + ', '.join(CHECKS) + (': unknown ' + ', '.join(unknown) if unknown else ''))
    elif a.phase == 'build':
        checks = []
    else:
        checks = [] if a.library_only else ['generic']
        if a.upstream_reference and a.model:
            checks.append('references')
        if a.parity:
            checks.append('parity:' + a.parity)
        if a.bindings is not None:
            checks.append('bindings')
        if a.packages:
            checks.append('packages')
        if a.policy != 'none':
            checks.append('policy' if a.policy == 'all' else 'policy-platform')
    parity = [name for name in checks if name.startswith('parity:')]
    # The two halves may share a run (one CI shard, as on linux-arm64): each writes its own files.
    if len(parity) > 1 and sorted(parity) != ['parity:full-a', 'parity:full-b']:
        p.error('one parity check per run, or the two halves parity:full-a and parity:full-b: ' + ', '.join(parity))
    if 'generic' in checks and 'ctest' in checks:
        p.error('generic runs a subset of ctest: select one')
    halves = [name for name in checks if name in BINDING_HALVES]
    if len(halves) + ('bindings' in checks) > 1:
        p.error('select bindings or one of its halves (bindings:a, bindings:b)')
    if halves and a.bindings is not None:
        p.error(f'{halves[0]} selects its own suites: no --bindings')
    if len({'policy', 'policy-platform'} & set(checks)) > 1:
        p.error('select policy or policy-platform')
    if a.library_only:
        needing = [name for name in checks if name in NEEDS_TESTS]
        if needing:
            p.error('--library-only builds no tests: ' + ', '.join(needing) + ' need them')
    if not a.model:
        needing = [name for name in checks if name in NEEDS_MODEL]
        if needing:
            p.error(', '.join(needing) + ' need --model')
    if a.phase != 'build' and not checks:
        p.error('nothing to run: select checks (--checks, --bindings, --packages, --policy, --parity) or --phase build')
    bindings = list(BINDINGS)
    if a.bindings not in (None, 'all'):
        bindings = [name for name in dict.fromkeys(a.bindings.split(',')) if name]
        if not bindings or set(bindings) - set(BINDINGS):
            p.error('--bindings takes a comma-separated subset of ' + ','.join(BINDINGS) + ', or all')
        bindings = [name for name in BINDINGS if name in bindings]
    if halves:
        bindings = [name for name in BINDINGS if name in BINDING_HALVES[halves[0]]]
    default = a.checks is None and checks == ['generic']
    policy = 'all' if 'policy' in checks else 'platform' if 'policy-platform' in checks else 'none'
    return {'profile': 'generic' if default else 'selected', 'checks': checks, 'bindings': bindings if 'bindings' in checks or halves else [],
            'policy': policy, 'packages': 'packages' in checks, 'parity_profile': PARITY[parity[0]][1] if parity else None,
            'upstream_reference': bool(a.upstream_reference), 'addon': addon_needed(a, checks, bindings)}


def select_tests(show, model):
    """(test names, label regex) of the generic selection, from `ctest --show-only=json-v1` output: tier-generic,
    and with a model the real-model category too. A build without tier labels (registered before dtl_add_test) is refused."""
    labels = {test['name']: next((prop['value'] for prop in test.get('properties', []) if prop.get('name') == 'LABELS'), [])
              for test in show.get('tests', [])}
    if not any(label.startswith('tier-') for values in labels.values() for label in values):
        raise Refused('the build registers no tier-* labels (CMakeLists.txt dtl_add_test); the generic selection needs them')
    regex = '^(tier-generic|cat-real_model)$' if model else '^tier-generic$'
    pattern = re.compile(regex)
    names = sorted(name for name, values in labels.items() if any(pattern.search(label) for label in values))
    if not names:
        raise Refused(f'no registered test matches {regex}')
    return names, regex


def junit_results(junit):
    """(test names, {skip key: reason}) of a CTest JUnit file. Tests CTest reports as skipped (SKIP_RETURN_CODE) and
    checks inside passing tests that print a 'SKIPPED:' line are skips, so no skipped check is left out silently. A check
    that does not apply to the platform prints 'NOT APPLICABLE:' instead and is not recorded: it is not a skip."""
    names, skipped = [], {}
    if not junit.is_file():
        return names, skipped
    for case in ElementTree.parse(junit).getroot().iter('testcase'):
        name, output = case.get('name'), case.findtext('system-out') or ''
        names.append(name)
        lines = [line.strip() for line in output.splitlines() if 'SKIPPED:' in line]
        if case.get('status') in ('notrun', 'disabled') or case.find('skipped') is not None:
            skipped[name] = 'reported skipped by the test: ' + ('; '.join(lines) or case.get('status', 'skipped'))
        elif lines:
            skipped[name + ' (partly)'] = '; '.join(lines)
    return sorted(names), skipped


def fold_policy(report, policy, directory, out):
    """Fold scripts/check-policy.py's policy.json (contract 16) into the report: its runs under their own labels (logs
    copied to OUT/<label>.log), its skips as policy:<label>. Returns the labels that failed."""
    if policy.get('schema_version') != 1 or not isinstance(policy.get('runs'), dict) or not isinstance(policy.get('skipped'), dict):
        raise Refused('policy.json: unsupported schema')
    if policy.get('source_commit') != report['source_commit']:
        raise Refused(f'policy.json: checked {policy.get("source_commit")}, not {report["source_commit"]}')
    failed = []
    for label, run in policy['runs'].items():
        if label in report['runs']:
            raise Refused(f'policy.json: {label} is already a run of this report')
        log = directory / run.get('log', label + '.log')
        if log.is_file():
            shutil.copyfile(log, out / f'{label}.log')
        report['runs'][label] = {'command': run['command'], 'exit_code': run['exit_code'], 'seconds': run['seconds'], 'log': f'{label}.log'}
        if run['exit_code'] != 0:
            failed.append(label)
    for label, reason in policy['skipped'].items():
        report['skipped'][f'policy:{label}'] = reason
    return failed


def full_missing(report, windows, record_models=()):
    """Every FULL_CHECKS run label, policy check and additional-model gate that neither passed nor is a listed platform skip."""
    runs, skipped = report['runs'], report['skipped']
    platform_name = 'win32' if windows else sys.platform

    def passed(label):
        # native: every registered test ran (the JUnit names), not only ctest's exit status.
        complete = label != 'native' or (report.get('ctest_ran') == report.get('ctest_registered') and report.get('ctest_ran'))
        return runs.get(label, {}).get('exit_code') == 0 and bool(complete)

    def allowed(key):
        rule = FULL_ALLOWED_SKIPS.get(key)
        return bool(rule) and platform_name in rule[0] and key in skipped and rule[1] in (None, skipped[key])
    missing = [label for label in FULL_CHECKS if not passed(label) and not allowed(label)]
    missing += [f'policy:{label}' for label in FULL_POLICY_CHECKS if not passed(label) and not allowed(f'policy:{label}')]
    for model_id in record_models:
        gates = report.get('additional_model_gates', {}).get(model_id, {})
        missing += [f'additional-model:{model_id}:{gate}' for gate in ADDITIONAL_MODEL_GATES if gates.get(gate) != 'passed']
    return missing


class Validation:
    def __init__(self, a, sel, system, record):
        self.a, self.sel, self.system, self.record = a, sel, system, record
        self.lock = threading.Lock()
        self.out = a.output.resolve()
        self.model = a.model.resolve() if a.model else None
        self.manifest = read_json(ROOT / record['models'][0])
        self.extra_models = [(path.resolve(), system.find_manifest(path, record)) for path in a.extra_model]
        self.build = (a.from_build if a.phase == 'test' else a.build or ROOT / 'build-validation').resolve()
        self.windows, self.linux = system.windows, system.linux
        self.jobs = a.jobs or default_jobs()
        self.test_jobs = a.test_jobs or (1 if self.model and os.environ.get('GITHUB_ACTIONS') != 'true' else min(4, os.cpu_count() or 1))
        self.launcher = '' if a.compiler_launcher == 'none' else a.compiler_launcher
        probe = a.reference_probe_launcher
        if probe == 'auto':
            probe = next((name for name in ('ccache', 'sccache') if system.which(name)), 'none')
        # The probe-only launcher exists only with the probe (CMake refuses it otherwise, cmake/launcher_guard.cmake).
        self.probe_launcher = '' if probe == 'none' or not sel['upstream_reference'] else probe
        build = self.build
        # LD_LIBRARY_PATH (Linux) and DYLD_LIBRARY_PATH (macOS) let the Rust and Go test programs, linked against
        # @rpath/libducktape_laya.1.dylib without a run path on macOS, find the library in the build directory.
        env = dict(os.environ, LD_LIBRARY_PATH=str(build), DYLD_LIBRARY_PATH=str(build), DUCKTAPE_LAYA_LIB_DIR=str(build),
                   PYTHONPATH=str(ROOT / 'bindings/python/src'),
                   DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_NOLOGO='1', GOTOOLCHAIN='local',
                   CGO_CFLAGS='-I' + str(ROOT / 'include'), CGO_LDFLAGS='-L' + str(build), **CCACHE_ENVIRONMENT)
        env.pop('LAYA_MODEL', None)
        if self.model:
            env['LAYA_MODEL'] = str(self.model)
        # Windows finds the DLL (for ctypes, Rust and Go test programs) on PATH.
        if self.windows:
            env['PATH'] = str(build) + os.pathsep + env.get('PATH', '')
            # vcvars64.bat sets Platform=x64, which MSBuild reads as the solution platform (.NET has Any CPU).
            for key in [key for key in env if key.upper() == 'PLATFORM']:
                env.pop(key)
        self.env = env
        source = system.source()
        self.report = {
            'schema_version': 1, **{key: source[key] for key in ('source_commit', 'source_tracked_dirty')},
            'runtime': record['runtime'], 'model': self.manifest if self.model else None,
            'additional_models': [m for _, m in self.extra_models], 'additional_model_gates': {},
            'mode': 'real_model' if self.model else 'model_free', 'release_ready': False, 'platform': system.platform(),
            'phase': a.phase, 'checks': list(sel['checks']),
            'selection': {'profile': sel['profile'], 'bindings': sel['bindings'], 'native_tests': bool({'ctest', 'generic'} & set(sel['checks'])),
                          'policy': sel['policy'], 'packages': sel['packages'], 'parity_profile': sel['parity_profile'],
                          'test_jobs': self.test_jobs, 'checks': list(sel['checks']), 'upstream_reference': sel['upstream_reference'],
                          'library_only': bool(a.library_only), 'node_addon': sel['addon']},
            'host': system.host(), 'skipped': {}, 'compiler_launcher': a.compiler_launcher,
            'reference_probe_launcher': self.probe_launcher or 'none',
            'build_environment': system.build_environment(), 'node_version': system.node_version() if sel['addon'] else None,
            'toolchains': system.toolchains([self.launcher] if self.launcher else []),
            'source_files_sha256': source['source_files_sha256'],
            'gates': {key: {'status': 'not_run'} for key in record['required_release_gates']}, 'runs': {},
            'fixtures_sha256': source['fixtures_sha256'],
            'notes': ['Passing these selected checks does not substitute for unrun release gates.',
                      'Historical evidence is not automatically accepted for a different source or binary.']}

    # Report bookkeeping (thread-safe: the binding suites run concurrently).
    def save(self):
        with self.lock:
            (self.out / 'report.json').write_text(json.dumps(self.report, indent=2) + '\n')

    def skip(self, label, reason):
        with self.lock:
            self.report['skipped'][label] = reason
        self.save()
        print(f'{label}: skipped ({reason})', flush=True)

    def run(self, label, command, gate=None, extra_env=None, check=True):
        started = time.monotonic()
        command = list(map(str, command))
        code = self.system.run(command, self.out / (label + '.log'), dict(self.env, **(extra_env or {})))
        with self.lock:
            self.report['runs'][label] = {'command': command, 'exit_code': code, 'seconds': round(time.monotonic() - started, 3),
                                          'log': label + '.log'}
            if gate:
                self.report['gates'][gate] = {'status': 'failed' if code else 'passed', 'run': label}
            elif code and label in BINDING_SUITES:
                self.report['gates']['binding_conformance'] = {'status': 'failed', 'run': label}
        self.save()
        print(f'{label}: {"FAILED" if code else "passed"}', flush=True)
        if code and check:
            raise Refused(f'{label} failed; see {self.out / (label + ".log")}')
        return code

    # Configure and build (phases all and build).
    def configure_and_build(self):
        a, build, record = self.a, self.build, self.record
        shutil.rmtree(build / BUILD_PHASE, ignore_errors=True)
        addon = self.sel['addon']
        headers = (a.node_include or self.system.node_headers()) if addon else None
        if addon and (not headers or not (headers / 'node_api.h').is_file()):
            raise Refused('Supply --node-include pointing to node_api.h')
        if addon and self.windows and not (a.node_lib and a.node_lib.is_file()):
            raise Refused('Supply --node-lib with node.lib on Windows')
        build.mkdir(parents=True, exist_ok=True)
        try:
            generator = generator_arguments(a.cmake_arg, build)
        except ValueError as error:
            raise Refused(str(error)) from None
        upstream = self.sel['upstream_reference']
        # CMake paths with forward slashes (identical on Linux; Windows paths otherwise carry escapes).
        self.run('configure', ['cmake', '-S', ROOT, '-B', build, *generator, '-DCMAKE_BUILD_TYPE=Release',
            # Validation is for the approved checkout, even if this cache previously built a candidate runtime.
            f'-DDUCKTAPE_LLAMA_SOURCE={(ROOT / record["runtime"]["path"]).as_posix()}', '-DDUCKTAPE_LLAMA_REVISION=',
            '-DDUCKTAPE_BUILD_FUZZERS=OFF', '-DDUCKTAPE_BUILD_TESTS=' + ('OFF' if a.library_only else 'ON'),
            '-DDUCKTAPE_BUILD_EXAMPLES=OFF', '-DDUCKTAPE_BUILD_NODE=' + ('ON' if addon else 'OFF'),
            '-DDUCKTAPE_BUILD_REFERENCE_PROBE=' + ('ON' if upstream else 'OFF'),
            *([f'-DNODE_INCLUDE_DIR={headers.as_posix()}'] if addon else []),
            f'-DDUCKTAPE_TEST_MODEL={self.model.as_posix() if self.model else ""}',
            *([f'-DNODE_LIB={a.node_lib.resolve().as_posix()}'] if addon and a.node_lib else []), *a.cmake_arg,
            # Last, so that nothing before overrides them (the last -D of a name wins): no dev options (they are never
            # evidence), the product launcher, the probe-only launcher, and never ggml's global launch rule.
            '-DDUCKTAPE_DEV_BUILD=OFF', '-DDUCKTAPE_DEV_PCH=OFF',
            *(f'-DCMAKE_{lang}_COMPILER_LAUNCHER={self.launcher}' for lang in ('C', 'CXX')),
            f'-DDUCKTAPE_REFERENCE_PROBE_LAUNCHER={self.probe_launcher}', '-DGGML_CCACHE=OFF'])
        # laya-stress is EXCLUDE_FROM_ALL; the build keeps it for the stress jobs (scripts/check-stress.py --prebuilt).
        self.run('build', ['cmake', '--build', build, '--parallel', self.jobs, *([] if a.library_only else ['--target', 'all', 'laya-stress'])])
        self.report['test_model_path'] = self.model.as_posix() if self.model else ''
        self.identity()
        cache = (build / 'CMakeCache.txt').read_text().splitlines()
        options = {line.split(':', 1)[0]: line.split('=', 1)[1] for line in cache
                   if '=' in line and ':' in line and line.startswith(RECORDED_OPTIONS)}
        self.report['build_options'] = options
        expected = {'CMAKE_C_COMPILER_LAUNCHER': self.launcher, 'CMAKE_CXX_COMPILER_LAUNCHER': self.launcher, 'GGML_CCACHE': 'OFF',
                    'DUCKTAPE_DEV_BUILD': 'OFF', 'DUCKTAPE_DEV_PCH': 'OFF'}
        if any(options.get(key) != value for key, value in expected.items()):
            raise Refused(f'The build directory is configured with {({k: options.get(k) for k in expected})}, not {expected}')
        guard = build / 'generated/launcher-guard.json'
        self.report['launcher_guard'] = read_json(guard) if guard.is_file() else None
        self.report['ccache_config'] = self.system.ccache_config(self.env)
        if self.report['ccache_config'] is not None and self.report['ccache_config'].get('base_dir', '') != '':
            raise Refused(f'ccache base_dir is {self.report["ccache_config"]["base_dir"]!r} in this environment; it must be empty')
        if not a.library_only:
            self.registration()

    def identity(self):
        build = self.build
        self.report['library_sha256'] = sha256(build / LIBRARY)
        # None when this run built no addon (selection.node_addon is false): the report then states there is none.
        self.report['addon_sha256'] = sha256(build / 'ducktape_laya.node') if self.sel['addon'] else None
        self.report['cmake_cache_sha256'] = sha256(build / 'CMakeCache.txt')
        self.report['diagnostics'] = self.system.binary_info(build)
        require_runtime_identity(self.report['diagnostics'], self.record)
        self.system.require_build(build, ROOT / self.record['runtime']['path'], self.sel['addon'])

    def registration(self):
        """Assert the required tests are registered; an empty green suite is not evidence."""
        show = json.loads(self.system.output(['ctest', '--test-dir', str(self.build), '--show-only=json-v1'], self.env))
        self.show = show
        self.report['ctest_registered'] = sorted(test['name'] for test in show['tests'])
        required = set(MODEL_FREE_TESTS)
        skipped = self.report['skipped']
        if self.model:
            required |= REAL_MODEL_TESTS
        # Fault injection uses Linux process controls (prctl, rlimits); CMake registers it on Linux only.
        if self.linux:
            required.add('ggml-faults')
        else:
            skipped['ggml-faults'] = 'fault injection uses Linux process controls (rlimits, fork); registered on Linux only'
        if self.model and self.linux:
            required |= set(LINUX_REAL_MODEL_TESTS)
        elif self.model:
            for name in LINUX_REAL_MODEL_TESTS:
                skipped[name] = 'fault injection uses Linux process controls (prctl, rlimits); registered on Linux only'
        # The emulated-CPU guard test exercises the x86-64 AVX2 guard under QEMU user mode (Linux only).
        x86 = self.system.machine in ('x86_64', 'AMD64')
        if self.model and self.linux and x86:
            required.add('cpu-guard')
        elif self.model and x86:
            skipped['cpu-guard'] = 'QEMU user-mode CPU emulation runs on Linux only'
        elif self.model:
            skipped['cpu-guard'] = 'the emulated-CPU guard checks the x86-64 build (AVX2 baseline); not registered on ' + self.system.machine
        missing = required - set(self.report['ctest_registered'])
        if missing:
            raise Refused('Required native tests are missing: ' + ', '.join(sorted(missing)))

    def snapshot(self):
        """BUILD/build-phase/: the build's report and logs, which --phase test and scripts/merge-validation.py read."""
        directory = self.build / BUILD_PHASE
        directory.mkdir(exist_ok=True)
        snapshot = copy.deepcopy(self.report)
        snapshot.update(phase='build', checks=[], selected_checks='passed')
        snapshot['selection'] = {**snapshot['selection'], 'checks': []}
        snapshot['runs'] = {label: run for label, run in snapshot['runs'].items() if label in ('configure', 'build')}
        (directory / 'report.json').write_text(json.dumps(snapshot, indent=2) + '\n')
        for name in ('configure.log', 'build.log'):
            shutil.copyfile(self.out / name, directory / name)

    # Phase test: reuse a build phase's directory, never building.
    def reuse_build(self):
        build = self.build
        path = build / BUILD_PHASE / 'report.json'
        if not path.is_file():
            raise Refused(f'{build} has no {BUILD_PHASE}/report.json (run --phase build first)')
        built = read_json(path)
        if built.get('phase') != 'build' or built.get('selected_checks') != 'passed':
            raise Refused(f'{path}: not a passed build phase')
        for key in ('source_commit', 'source_tracked_dirty', 'source_files_sha256', 'fixtures_sha256'):
            if built.get(key) != self.report[key]:
                raise Refused(f'{path}: {key} differs from this checkout\'s')
        # A test phase on a build without the addon cannot select the addon's checks (the binding job never splits phases).
        self.sel['addon'] = self.report['selection']['node_addon'] = built['selection'].get('node_addon', True)
        self.identity()
        for key in ('library_sha256', 'addon_sha256', 'cmake_cache_sha256'):
            if built.get(key) != self.report[key]:
                raise Refused(f'{key} of {build} differs from its build phase: the build directory changed since')
        model = self.model.as_posix() if self.model else ''
        if built.get('test_model_path') != model:
            raise Refused(f'the build registered its tests with the model {built.get("test_model_path")!r}, not {model!r}')
        if self.a.upstream_reference and not built['selection'].get('upstream_reference'):
            raise Refused('--upstream-reference: the build phase did not build the reference probe')
        for key in ('test_model_path', 'build_options', 'launcher_guard', 'ccache_config', 'compiler_launcher', 'reference_probe_launcher',
                    'build_environment'):
            self.report[key] = built.get(key)
        self.report['selection']['upstream_reference'] = self.sel['upstream_reference'] = bool(built['selection'].get('upstream_reference'))
        self.report['selection']['library_only'] = built['selection'].get('library_only', False)
        if not self.report['selection']['library_only']:
            self.registration()
            if self.report['ctest_registered'] != built.get('ctest_registered'):
                raise Refused('the registered tests differ from the build phase\'s')

    # Checks.
    def ctest(self, generic):
        build, out = self.build, self.out
        registered = self.report['ctest_registered']
        if generic:
            names, regex = select_tests(self.show, self.model)
        else:
            names, regex = registered, None
        self.report['ctest_selection'] = {'labels': regex, 'tests': names}
        junit = out / 'ctest-junit.xml'
        command = ['ctest', '--test-dir', build, '--output-on-failure', '--output-junit', junit, '--test-output-size-passed', 1 << 20,
                   '--no-tests=error', '-j', self.test_jobs, *(['-L', regex] if regex else [])]
        gate = 'native_contracts' if self.model and not generic else None
        try:
            self.run('native', command, gate)
        finally:
            ran, skipped = junit_results(junit)
            self.report['ctest_ran'] = ran
            self.report['skipped'].update(skipped)
            self.save()
        if ran != sorted(names):
            if gate:
                self.report['gates'][gate] = {'status': 'failed', 'run': 'native'}
            raise Refused(f'ctest ran {len(ran)} tests, not the {len(names)} selected: '
                          f'missing {sorted(set(names) - set(ran))[:6]}, unexpected {sorted(set(ran) - set(names))[:6]}')
        if self.windows:
            self.skip('examples-smoke', 'compiles the examples with a POSIX driver')
        else:
            self.run('examples-smoke', [sys.executable, ROOT / 'tests/examples-smoke.py', '--build-dir', build])
        self.run('abi', [sys.executable, ROOT / 'scripts/check-abi.py', build / LIBRARY], 'abi')
        self.run('check-binary', [sys.executable, ROOT / 'scripts/check-binary.py', build])

    def references(self):
        build, out, model = self.build, self.out, self.model
        self.run('original-reference', [sys.executable, ROOT / 'tests/original-reference.py', model, '--build-dir', build,
                                        '--report', out / 'original-reference.json'], 'original_reference')
        upstream = self.sel['upstream_reference']
        if upstream:
            self.run('upstream-reference', [sys.executable, ROOT / 'tests/compare-native-reference.py', model,
                                            '--build', build, '--report', out / 'upstream-reference.json'], 'upstream_reference')
        # The additional models must pass the same reference gates; check-candidate.py requires every model in
        # release/compatibility.json additional_models.
        def extra_gate(name, gate, label, command):
            self.report['additional_model_gates'].setdefault(name, {})[gate] = 'failed'
            self.run(label, command)
            self.report['additional_model_gates'][name][gate] = 'passed'
            self.save()
        for extra, extra_manifest in self.extra_models:
            name = extra_manifest['id']
            # The runtime patches' token and score identity holds for every model: the tokenizer golden entries
            # recorded for this vocabulary, and the selected decision head's scores.
            extra_gate(name, 'tokenizer', f'tokenizer-{name}',
                       [build / ('laya-tokenizer-tests' + EXE), ROOT / 'tests/fixtures/tokenizer-partition.txt', extra])
            extra_gate(name, 'decision_head', f'decision-head-{name}', [build / ('laya-decision-head-tests' + EXE), extra])
            # A deadline or cancellation stops tokenization of newline-separated lines (every newline is a special
            # token in the multilingual vocabulary); real-control-tokenize has only the primary.
            extra_gate(name, 'control_tokenize', f'control-tokenize-{name}',
                       [build / ('laya-control-tokenize-tests' + EXE), model, extra])
            extra_gate(name, 'original_reference', f'original-reference-{name}',
                       [sys.executable, ROOT / 'tests/original-reference.py', extra, '--build-dir', build,
                        '--cases', ROOT / extra_manifest['original_reference_cases'], '--report', out / f'original-reference-{name}.json'])
            if upstream:
                extra_gate(name, 'upstream_reference', f'upstream-reference-{name}',
                           [sys.executable, ROOT / 'tests/compare-native-reference.py', extra,
                            '--build', build, '--report', out / f'upstream-reference-{name}.json'])

    def parity(self, check):
        label, profile, half, gate = PARITY[check]
        # A half writes outputs-<half>.json and parity-timing-<half>.json (scripts/merge-validation.py joins them into
        # outputs.json); smoke and full write outputs.json and parity-timing.json.
        suffix = f'-{half}' if half != 'all' else ''
        self.run(label, [sys.executable, ROOT / 'tests/parity.py', self.model, '--build-dir', self.build, '--profile', profile,
                         '--bindings', half, '--report', self.out / f'outputs{suffix}.json',
                         '--timing', self.out / f'parity-timing{suffix}.json'], gate)

    def bindings(self):
        """The selected binding suites, concurrently; a failed suite fails binding_conformance (after all have run)."""
        java = ROOT / 'bindings/java'

        def java_windows():
            # bindings/java/build.sh's commands with Windows classpath separators (bash may be WSL's).
            lint = '-Xlint:all,-restricted,-serial,-missing-explicit-ctor,-module'
            sources = lambda part: sorted(str(path) for path in (java / 'src' / part).rglob('*.java'))
            classes, tests = java / 'target/classes', java / 'target/test-classes'
            shutil.rmtree(java / 'target', ignore_errors=True)
            for label, command in (('java-compile', ['javac', '--release', '22', lint, '-d', classes, *sources('main')]),
                                   ('java-test-compile', ['javac', '--release', '22', lint, '-cp', classes, '-d', tests, *sources('test')]),
                                   ('java', ['java', '--enable-native-access=ALL-UNNAMED', '-cp', os.pathsep.join(map(str, (classes, tests))),
                                             'io.github.adrian729.laya.EngineTest'])):
                if self.run(label, command, check=False):
                    return 1
            return 0
        suites = {'rust': lambda: self.run('rust', ['cargo', 'test', '--locked', '--offline', '--manifest-path', ROOT / 'bindings/rust/Cargo.toml'],
                                           check=False),
                  'node': lambda: self.run('node', ['node', '--test', ROOT / 'bindings/node/test.cjs'], check=False),
                  'python': lambda: self.run('python', [sys.executable, '-m', 'unittest', 'discover', '-s', ROOT / 'bindings/python/tests'],
                                             check=False),
                  'java': java_windows if self.windows else lambda: self.run('java', ['bash', java / 'build.sh'], check=False),
                  'dotnet': lambda: self.run('dotnet', ['dotnet', 'test', ROOT / 'bindings/dotnet/DucktapeLaya.slnx', '-m:1'], check=False),
                  'go': lambda: self.run('go', ['go', '-C', ROOT / 'bindings/go', 'test', '-count=1', './...'], check=False)}
        selected = self.sel['bindings']
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(selected)) as pool:
            futures = {name: pool.submit(suites[name]) for name in selected}
            failed = [name for name in selected if futures[name].result()]
        if failed:
            raise Refused('binding suites failed: ' + ', '.join(failed))

    def packages(self):
        self.run('package', [sys.executable, ROOT / 'scripts/package-native.py', '--build', self.build, '--output', self.out / 'packages'])
        self.run('package-smoke', [sys.executable, ROOT / 'tests/package-smoke.py', self.out / 'packages'])

    def policy(self, selection):
        directory = self.out / 'policy'
        command = [sys.executable, ROOT / 'scripts/check-policy.py', '--all' if selection == 'all' else '--platform-only',
                   '--output', directory, '--jobs', self.jobs, *(f'--cmake-arg={arg}' for arg in self.a.cmake_arg)]
        command = list(map(str, command))
        directory.mkdir()
        code = self.system.run(command, directory / 'check-policy.log', self.env)
        path = directory / 'policy.json'
        if not path.is_file():
            raise Refused(f'scripts/check-policy.py wrote no policy.json (exit {code}); see {directory / "check-policy.log"}')
        with self.lock:
            failed = fold_policy(self.report, read_json(path), directory, self.out)
        self.save()
        if failed or code:
            raise Refused('policy checks failed: ' + (', '.join(failed) or f'scripts/check-policy.py exit {code}'))

    def not_selected(self):
        """Every check not executed is listed: each FULL_CHECKS label, bindings, each policy check, policy."""
        reason = f'{NOT_SELECTED} ({self.report["selection"]["profile"]}: {",".join(self.sel["checks"]) or "build only"})'
        runs, skipped = self.report['runs'], self.report['skipped']
        for label in FULL_CHECKS:
            if label not in runs and label not in skipped:
                skipped[label] = reason
        if not any(label in runs for label in FULL_POLICY_CHECKS) and 'policy' not in skipped:
            skipped['policy'] = reason
        for label in FULL_POLICY_CHECKS:
            if label not in runs and f'policy:{label}' not in skipped:
                skipped[f'policy:{label}'] = reason

    def execute(self):
        phase, checks = self.a.phase, self.sel['checks']
        if phase in ('all', 'build'):
            self.configure_and_build()
            self.snapshot()
        else:
            self.reuse_build()
        for check in checks:
            if check in ('ctest', 'generic'):
                self.ctest(check == 'generic')
            elif check == 'references':
                self.references()
            elif check in PARITY:
                self.parity(check)
            elif check == 'bindings' or check in BINDING_HALVES:
                self.bindings()
            elif check == 'packages':
                self.packages()
            elif check in ('policy', 'policy-platform'):
                self.policy('all' if check == 'policy' else 'platform')
        if self.a.reproducibility:
            headers = self.a.node_include or self.system.node_headers()
            self.run('reproducibility', [sys.executable, ROOT / 'scripts/check-reproducibility.py', '--output', self.out / 'reproducibility',
                                         '--jobs', self.jobs, '--node-include', headers], 'reproducibility')


def main(argv=None, system=None):
    system = system or System()
    p = parser()
    a = p.parse_args(argv)
    if (a.jobs is not None and a.jobs < 1) or (a.test_jobs is not None and a.test_jobs < 1):
        p.error('--jobs and --test-jobs must be positive')
    if a.phase == 'test':
        if not a.from_build:
            p.error('--phase test needs --from BUILD (the build directory of a --phase build run)')
        if a.build and a.build.resolve() != a.from_build.resolve():
            p.error('--phase test builds nothing: --build, if given, must be the --from directory')
        # The shards of a CI build find the model where the build phase registered the tests with it.
        built = a.from_build / BUILD_PHASE / 'report.json'
        if not a.model and built.is_file() and read_json(built).get('test_model_path'):
            a.model = pathlib.Path(read_json(built)['test_model_path'])
    elif a.from_build:
        p.error('--from belongs to --phase test')
    if a.compiler_launcher != 'none' and not system.which(a.compiler_launcher):
        p.error(f'{a.compiler_launcher} is not on PATH')
    # The compiler cache and the dev options are this script's alone: a --cmake-arg cannot set them (the configure step
    # also passes its own values last, and the report's build_options record what the cache holds).
    overrides = [arg for arg in a.cmake_arg if re.search(r'CMAKE_\w*COMPILER_LAUNCHER|GGML_CCACHE|DUCKTAPE_DEV_|DUCKTAPE_REFERENCE_PROBE_LAUNCHER|^-C', arg)]
    if overrides:
        p.error('--cmake-arg cannot set the compiler launchers or the dev options (use --compiler-launcher): ' + ' '.join(overrides))
    if a.upstream_reference and not a.model:
        p.error('--upstream-reference requires --model')
    sel = select(p, a, system)
    record = check_record()
    manifest = read_json(ROOT / record['models'][0])
    extra_ids = [system.find_manifest(path, record)['id'] for path in a.extra_model]
    if manifest['id'] in extra_ids:
        p.error('--extra-model takes additional models; pass the primary with --model')
    a.output.resolve().mkdir(parents=True, exist_ok=False)
    validation = Validation(a, sel, system, record)
    report = validation.report
    record_models = [read_json(ROOT / name)['id'] for name in record.get('additional_models', [])] if a.full and a.phase == 'all' else []
    try:
        if a.full and a.phase == 'all' and (not a.model or set(record_models) - set(extra_ids)):
            # A forgotten model costs seconds, not a build.
            missing = ([] if a.model else list(REAL_MODEL_LABELS)) + [f'additional-model:{m}' for m in record_models if m not in extra_ids]
            report['full_refusal'] = {'missing': missing}
            raise Refused('--full needs --model and every additional model (--extra-model): missing ' + ', '.join(missing))
        if a.model:
            system.verify_model(validation.model, manifest)
        validation.save()
        validation.execute()
        report['selected_checks'] = 'passed'
    except Exception as error:  # every failure is recorded in the report, which is always written
        report['selected_checks'] = 'failed'
        report['error'] = f'{error}' if isinstance(error, Refused) else f'{type(error).__name__}: {error}'
    finally:
        validation.not_selected()
        if a.full and a.phase == 'all' and 'full_refusal' not in report:
            missing = full_missing(report, system.windows, record_models)
            if missing:
                report['full_refusal'] = {'missing': missing}
                report['selected_checks'] = 'failed'
                report.setdefault('error', '--full: checks neither passed nor skipped on this platform: ' + ', '.join(missing))
        report['release_ready'] = all(gate['status'] == 'passed' for gate in report['gates'].values())
        validation.save()
    print(json.dumps({'selected_checks': report['selected_checks'], 'profile': report['selection']['profile'],
                      'release_ready': report['release_ready'], 'report': str(validation.out / 'report.json')}))
    return 0 if report['selected_checks'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
