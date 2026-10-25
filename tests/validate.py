#!/usr/bin/env python3
"""Model-free checks of scripts/validate.py, offline, with a fake system (no build, no ctest, no model).

FakeSystem stands in for every subprocess and binary inspection validate.py makes: configure writes a
CMakeCache.txt from the -D arguments, the build writes stand-in library and addon files, ctest writes a JUnit file
for the tests its -L regex selects from a recorded `ctest --show-only=json-v1` registration (the S batch's
dtl_add_test labels), parity, the reference comparisons, the packages and scripts/check-policy.py write their
reports. tests/merge-validation.py runs its shards through it too.

Covered: the generic selection from tier and category labels (and the refusal of a build without tier labels);
check selection and its refusals; the --full refusal before configuring (full_refusal.missing) and after a failed
check; a complete --full run whose report scripts/release_targets.require_complete_validation accepts as host-full;
policy.json folded into runs and policy:<label> skips; every check not run listed as not selected; the report fields
(phase, selection, host, build_options with the dev options, launcher guard, ccache configuration); the build phase
snapshot and the --phase test identity checks."""
import contextlib
import copy
import io
import importlib.util
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('validate_script', ROOT / 'scripts/validate.py')
validate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validate)
from release_support import LIBRARY, read_json  # noqa: E402
from release_targets import HOST_FULL_RUNS, Mismatch, POLICY_LABELS, require_complete_validation  # noqa: E402

RECORD = read_json(ROOT / 'release/compatibility.json')
MANIFESTS = {name: read_json(ROOT / name) for name in RECORD['models'] + RECORD.get('additional_models', [])}
COMMIT = 'a' * 40


def registration(linux=True, model=True, tiers=True, x86=True):
    """`ctest --show-only=json-v1` of a build: the tests and labels CMakeLists.txt registers with dtl_add_test."""
    tests = [('abi-errors', 'generic', 'native'), ('feature-errors', 'generic', 'native'), ('control-errors', 'generic', 'native'),
             ('decision-rules', 'generic', 'native'), ('language-routing', 'generic', 'native'), ('tokenizer-partition', 'generic', 'native'),
             ('stress-rule', 'generic', 'native')]
    if linux:
        tests.append(('ggml-faults', 'generic', 'faults'))
    if model:
        tests += [(name, 'specialized', 'real_model') for name in ('real-model', 'real-features', 'real-control', 'real-control-tokenize',
                                                                    'real-tokenizer', 'real-decision-head')]
        if linux:
            tests += [(name, 'specialized', 'faults') for name in ('real-faults', 'real-runtime-faults', 'real-c-faults', 'real-api-faults')]
            if x86:
                tests.append(('cpu-guard', 'specialized', 'real_model'))
    return {'kind': 'ctestInfo', 'version': {'major': 1, 'minor': 0},
            'tests': [{'name': name, 'command': [name], 'properties': [{'name': 'LABELS', 'value': [f'tier-{tier}', f'cat-{category}']}
                                                                       if tiers else {'name': 'TIMEOUT', 'value': 1500}]}
                      for name, tier, category in tests]}


def fixture_outputs(consumers, fixtures=('primitive.json', 'structured.json')):
    """tests/parity.py's report for CONSUMERS (every consumer agrees, as a passing run's do)."""
    result = lambda name: {'answers': {'department': {'type': 'choice', 'choice': 'billing', 'probabilities': {'billing': 0.9, 'sales': 0.1}}},
                           'fixture': name}
    return {'fixtures': {name: {consumer: result(name) for consumer in consumers} for name in fixtures},
            'model_sha256': 'm' * 64, 'fixture_hashes': {name: 'f' * 64 for name in fixtures}, 'runtime': {'llama_cpp_commit': 'r' * 40},
            'library_sha256': 'l' * 64, 'source_identity': {'sha256': 's' * 64}, 'settings': {'threads': 4},
            'open_config': {'config': {'threads': 2}, 'fixture': 'primitive.json', 'fixture_hash': 'f' * 64,
                            'results': {consumer: result('open') for consumer in consumers}}}


HALVES = {'a': ('C', 'C++', 'Rust', 'Node', 'Python'), 'b': ('C', 'Java', '.NET', 'Go'),
          'all': ('C', 'C++', 'Rust', 'Node', 'Python', 'Java', '.NET', 'Go')}


def argument(command, name):
    return command[command.index(name) + 1]


class FakeSystem(validate.System):
    """validate.System without processes: deterministic stand-ins for every command validate.py runs."""
    windows, linux, machine = False, True, 'x86_64'

    def __init__(self, directory, ci=False, library=b'library bytes', fail=(), drop_junit=(), launchers=('ccache',), tiers=True,
                 platform='linux-x64', compiler='GNU 13.3.0'):
        self.directory = pathlib.Path(directory)
        self.target, self.compiler = platform, compiler
        self.machine = 'aarch64' if platform.endswith('arm64') else 'x86_64'
        self.ci, self.library, self.fail, self.drop_junit, self.launchers, self.tiers = ci, library, dict.fromkeys(fail, 1), set(drop_junit), launchers, tiers
        self.commands = []
        headers = self.directory / 'node/include/node'
        headers.mkdir(parents=True, exist_ok=True)
        (headers / 'node_api.h').write_text('')
        self.headers = headers

    def show(self, build):
        cache = dict(line.split('=', 1) for line in (build / 'CMakeCache.txt').read_text().splitlines() if '=' in line)
        return registration(self.linux, bool(cache.get('DUCKTAPE_TEST_MODEL:UNINITIALIZED')), self.tiers, self.machine in ('x86_64', 'AMD64'))

    def run(self, command, log, env, timeout=3600):
        self.commands.append(command)
        label = pathlib.Path(log).stem
        pathlib.Path(log).write_text(f'{label}: {" ".join(command)}\n')
        name = pathlib.Path(command[1]).name if len(command) > 1 else ''
        if command[0] == 'cmake' and '-S' in command:
            build = pathlib.Path(argument(command, '-B'))
            values = {}
            for arg in command:
                if arg.startswith('-D'):
                    key, _, value = arg[2:].partition('=')
                    values[key] = value
            values.setdefault('CMAKE_GENERATOR', 'Ninja')
            lines = [f'{key}:UNINITIALIZED={value}' for key, value in values.items()]
            lines += ['CMAKE_C_COMPILER:FILEPATH=/usr/bin/gcc', 'CMAKE_CXX_COMPILER:FILEPATH=/usr/bin/g++']
            (build / 'CMakeCache.txt').write_text('\n'.join(lines) + '\n')
            (build / 'generated').mkdir(exist_ok=True)
            probe = values.get('DUCKTAPE_REFERENCE_PROBE_LAUNCHER', '')
            (build / 'generated/launcher-guard.json').write_text(json.dumps(
                {'product_targets': ['dtl_json_io', 'dtl_language', 'ducktape_laya', 'ducktape_laya_node', 'ggml', 'llama'],
                 'probe_targets': {'laya-reference': probe} if probe else {}, 'rule_launch': ''}))
        elif command[0] == 'cmake' and '--build' in command:
            build = pathlib.Path(argument(command, '--build'))
            (build / LIBRARY).write_bytes(self.library)
            (build / 'ducktape_laya.node').write_bytes(self.library + b' addon')
        elif command[0] == 'ctest':
            build = pathlib.Path(argument(command, '--test-dir'))
            show = self.show(build)
            names = sorted(test['name'] for test in show['tests'])
            if '-L' in command:
                names, _ = validate.select_tests(show, 'cat-real_model' in argument(command, '-L'))
            names = [name for name in names if name not in self.drop_junit]
            junit = pathlib.Path(argument(command, '--output-junit'))
            junit.write_text('<testsuite>' + ''.join(f'<testcase name="{n}" status="run"><system-out>ok</system-out></testcase>' for n in names)
                             + '</testsuite>')
        elif name == 'parity.py':
            half = argument(command, '--bindings')
            pathlib.Path(argument(command, '--report')).write_text(json.dumps(fixture_outputs(HALVES[half]), indent=2) + '\n')
            pathlib.Path(argument(command, '--timing')).write_text(json.dumps(
                {'schema_version': 1, 'profile': argument(command, '--profile'), 'bindings': half, 'consumers': list(HALVES[half]),
                 'status': 'passed', 'launches': []}))
        elif name in ('original-reference.py', 'compare-native-reference.py'):
            pathlib.Path(argument(command, '--report')).write_text(json.dumps({'summary': {'status': 'passed', 'known_deviations_used': []},
                                                                               'model': pathlib.Path(command[2]).name}))
        elif name == 'package-native.py':
            output = pathlib.Path(argument(command, '--output'))
            output.mkdir(parents=True)
            (output / 'inventory.json').write_text('{}')
        elif name == 'check-policy.py':
            output = pathlib.Path(argument(command, '--output'))
            labels = list(validate.FULL_POLICY_CHECKS) if '--all' in command else list(validate.PLATFORM_POLICY)
            policy = {'schema_version': 1, 'source_commit': COMMIT, 'source_tracked_dirty': False,
                      'selection': 'all' if '--all' in command else 'platform', 'runs': {}, 'skipped': {}}
            for item in validate.FULL_POLICY_CHECKS:
                if item in labels:
                    (output / f'{item}.log').write_text(f'{item} ok\n')
                    policy['runs'][item] = {'command': [sys.executable, f'tests/{item}.py'], 'exit_code': self.fail.get(item, 0), 'seconds': 1.0,
                                            'log': f'{item}.log'}
                else:
                    policy['skipped'][item] = 'not selected (--platform-only)'
            (output / 'policy.json').write_text(json.dumps(policy))
            return 1 if any(run['exit_code'] for run in policy['runs'].values()) else 0
        return self.fail.get(label, 0)

    def output(self, command, env):
        self.commands.append(command)
        return json.dumps(self.show(pathlib.Path(argument(command, '--test-dir'))))

    def which(self, name):
        return f'/usr/bin/{name}' if name in self.launchers else None

    def verify_model(self, path, manifest):
        pass

    def find_manifest(self, path, record):
        return next(manifest for name, manifest in MANIFESTS.items() if manifest['id'] in pathlib.Path(path).name)

    def binary_info(self, build):
        return {'package_version': RECORD['package_version'], 'abi_version': RECORD['abi_version'],
                'llama_cpp_commit': RECORD['runtime']['commit'], 'runtime_patches': RECORD['runtime']['patches'], 'compiler': self.compiler,
                'numerical_profile': {'id': 'native-cpu:x86_64'}}

    def require_build(self, build, runtime):
        pass

    def platform(self):
        return self.target

    def node_version(self):
        return 'v26.7.0'

    def node_headers(self):
        return self.headers

    def toolchains(self, launchers):
        return {}

    def ccache_config(self, env):
        return {'base_dir': env.get('CCACHE_BASEDIR', 'unset'), 'compiler_check': env.get('CCACHE_COMPILERCHECK'),
                'sloppiness': env.get('CCACHE_SLOPPINESS')}

    def build_environment(self):
        return {'ImageVersion': 'fake'}

    def host(self):
        return {'github_actions': self.ci, 'hostname': 'runner' if self.ci else 'release-host', 'system': 'Linux', 'machine': self.machine}

    def source(self):
        return {'source_commit': COMMIT, 'source_tracked_dirty': False, 'source_files_sha256': 'f' * 64, 'fixtures_sha256': {'primitive.json': 'p' * 64}}


def models(directory):
    """Stand-in model files: the primary and every additional model, named after their manifest ids."""
    directory = pathlib.Path(directory)
    paths = {}
    for name, manifest in MANIFESTS.items():
        path = directory / f'{manifest["id"]}.gguf'
        path.write_bytes(b'model')
        paths[name] = path
    return paths[RECORD['models'][0]], [paths[name] for name in RECORD.get('additional_models', [])]


def run(system, *argv):
    """validate.main with a fake system: (exit code, report)."""
    with contextlib.redirect_stdout(io.StringIO()):
        code = validate.main(list(map(str, argv)), system)
    output = pathlib.Path(argv[list(argv).index('--output') + 1])
    return code, read_json(output / 'report.json')


class Validate(unittest.TestCase):
    def setUp(self):
        self.directory = pathlib.Path(tempfile.mkdtemp(prefix='ducktape-validate-'))
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.model, self.extra = models(self.directory)
        self.build = self.directory / 'build'
        self.count = 0

    def output(self):
        self.count += 1
        return self.directory / f'out-{self.count}'

    def full(self, system, *extra):
        arguments = ['--model', self.model, *[item for path in self.extra for item in ('--extra-model', path)]]
        return run(system, '--full', *arguments, '--build', self.build, '--output', self.output(), '--cmake-arg=-GNinja', *extra)

    def test_generic_selection_from_labels(self):
        names, regex = validate.select_tests(registration(), True)
        self.assertEqual(regex, '^(tier-generic|cat-real_model)$')
        self.assertIn('real-model', names)
        self.assertIn('cpu-guard', names)
        self.assertNotIn('real-faults', names)  # cat-faults, tier-specialized
        self.assertIn('ggml-faults', names)  # tier-generic
        names, regex = validate.select_tests(registration(model=False), False)
        self.assertEqual(regex, '^tier-generic$')
        self.assertEqual(sorted(names), sorted(['abi-errors', 'feature-errors', 'control-errors', 'decision-rules', 'language-routing',
                                                'tokenizer-partition', 'stress-rule', 'ggml-faults']))
        # Labels are matched whole: a tier-generic-extra label is not tier-generic.
        show = registration(model=False)
        show['tests'][0]['properties'][0]['value'] = ['tier-generic-extra', 'cat-native']
        self.assertNotIn(show['tests'][0]['name'], validate.select_tests(show, False)[0])
        with self.assertRaisesRegex(validate.Refused, 'no tier-'):
            validate.select_tests(registration(tiers=False), True)

    def test_check_selection(self):
        system = FakeSystem(self.directory)
        refused = [['--checks', 'parity:full-a,parity:full-b', '--model', self.model],
                   ['--checks', 'ctest,generic', '--model', self.model],
                   ['--checks', 'policy,policy-platform'],
                   ['--checks', 'nonsense'],
                   ['--library-only', '--checks', 'ctest'],
                   ['--checks', 'references'],
                   ['--full', '--checks', 'ctest', '--model', self.model],
                   ['--full', '--phase', 'test', '--from', self.build, '--model', self.model],
                   ['--phase', 'build', '--checks', 'ctest'],
                   ['--phase', 'test', '--checks', 'ctest'],
                   ['--from', self.build, '--checks', 'ctest'],
                   ['--library-only'],
                   ['--cmake-arg=-DDUCKTAPE_DEV_BUILD=ON'],
                   ['--cmake-arg=-DCMAKE_CXX_COMPILER_LAUNCHER=ccache'],
                   ['--cmake-arg=-DGGML_CCACHE=ON']]
        for argv in refused:
            with self.subTest(argv=argv), self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                validate.main(list(map(str, argv)) + ['--output', str(self.output())], system)
        p = validate.parser()
        select = lambda *argv: validate.select(p, p.parse_args([*map(str, argv), '--output', 'x']), system)
        self.assertEqual(select()['profile'], 'generic')
        self.assertEqual(select()['checks'], ['generic'])
        selection = select('--model', self.model, '--parity', 'smoke', '--upstream-reference', '--bindings', 'go,rust', '--policy', 'platform')
        self.assertEqual(selection['profile'], 'selected')
        self.assertEqual(selection['checks'], ['generic', 'references', 'parity:smoke', 'bindings', 'policy-platform'])
        self.assertEqual(selection['bindings'], ['rust', 'go'])
        self.assertEqual(select('--library-only', '--bindings', 'all')['checks'], ['bindings'])
        self.assertEqual(select('--model', self.model, '--full')['checks'], list(validate.FULL_SELECTION))
        self.assertEqual(select('--model', self.model, '--full')['profile'], 'host-full')

    def test_full_refuses_before_configuring_without_models(self):
        system = FakeSystem(self.directory)
        code, report = run(system, '--full', '--build', self.build, '--output', self.output())
        self.assertEqual(code, 1)
        self.assertEqual(report['selected_checks'], 'failed')
        ids = [MANIFESTS[name]['id'] for name in RECORD.get('additional_models', [])]
        self.assertEqual(report['full_refusal']['missing'], list(validate.REAL_MODEL_LABELS) + [f'additional-model:{m}' for m in ids])
        self.assertEqual(system.commands, [])  # nothing configured or built
        # The primary alone is not enough either.
        system = FakeSystem(self.directory)
        code, report = run(system, '--full', '--model', self.model, '--build', self.build, '--output', self.output())
        self.assertEqual(code, 1)
        self.assertEqual(report['full_refusal']['missing'], [f'additional-model:{m}' for m in ids])
        self.assertEqual(system.commands, [])

    def test_full_run_is_host_full_evidence(self):
        system = FakeSystem(self.directory)
        code, report = self.full(system)
        self.assertEqual(code, 0, report.get('error'))
        self.assertNotIn('full_refusal', report)
        self.assertEqual((report['phase'], report['selection']['profile'], report['selection']['policy']), ('all', 'host-full', 'all'))
        self.assertEqual(report['host'], {'github_actions': False, 'hostname': 'release-host', 'system': 'Linux', 'machine': 'x86_64'})
        for label in (*HOST_FULL_RUNS, *POLICY_LABELS):
            self.assertEqual(report['runs'][label]['exit_code'], 0, label)
        self.assertTrue((self.directory / f'out-{self.count}' / 'docs-links.log').is_file())
        self.assertEqual(report['ctest_ran'], report['ctest_registered'])
        self.assertEqual(report['ctest_selection']['labels'], None)
        ctest = next(command for command in system.commands if command[0] == 'ctest' and '--output-junit' in command)
        self.assertIn('--no-tests=error', ctest)
        self.assertNotIn('-L', ctest)
        for gate in ('native_contracts', 'abi', 'binding_conformance', 'original_reference', 'upstream_reference'):
            self.assertEqual(report['gates'][gate]['status'], 'passed', gate)
        self.assertEqual(report['gates']['installed_packages'], {'status': 'not_run'})
        for model_id, gates in report['additional_model_gates'].items():
            self.assertEqual(set(gates.values()), {'passed'}, model_id)
        options = report['build_options']
        self.assertEqual({key: options[key] for key in ('DUCKTAPE_DEV_BUILD', 'DUCKTAPE_DEV_PCH', 'GGML_CCACHE', 'CMAKE_C_COMPILER_LAUNCHER',
                                                        'CMAKE_CXX_COMPILER_LAUNCHER', 'DUCKTAPE_REFERENCE_PROBE_LAUNCHER')},
                         {'DUCKTAPE_DEV_BUILD': 'OFF', 'DUCKTAPE_DEV_PCH': 'OFF', 'GGML_CCACHE': 'OFF', 'CMAKE_C_COMPILER_LAUNCHER': '',
                          'CMAKE_CXX_COMPILER_LAUNCHER': '', 'DUCKTAPE_REFERENCE_PROBE_LAUNCHER': 'ccache'})
        self.assertEqual((report['compiler_launcher'], report['reference_probe_launcher']), ('none', 'ccache'))
        self.assertEqual(report['ccache_config'], {'base_dir': '', 'compiler_check': 'content', 'sloppiness': ''})
        self.assertEqual(report['launcher_guard']['probe_targets'], {'laya-reference': 'ccache'})
        self.assertEqual(report['skipped'], {})
        # The checker takes it as the tier-1 native report.
        require_complete_validation(report, 'native', profiles={'host-full'}, cache_allowed=False)
        # The build phase snapshot, for --phase test and the merge.
        snapshot = read_json(self.build / 'build-phase/report.json')
        self.assertEqual((snapshot['phase'], snapshot['selected_checks'], sorted(snapshot['runs'])), ('build', 'passed', ['build', 'configure']))

    def test_full_refuses_failed_checks(self):
        cases = {'policy run': ({'fail': ['docs-links']}, 'policy:docs-links'),
                 'binding suite': ({'fail': ['go']}, 'go'),
                 'ctest subset': ({'drop_junit': ['real-features']}, 'native')}
        for label, (options, missing) in cases.items():
            with self.subTest(label):
                shutil.rmtree(self.build, ignore_errors=True)
                code, report = self.full(FakeSystem(self.directory, **options))
                self.assertEqual(code, 1)
                self.assertEqual(report['selected_checks'], 'failed')
                self.assertIn(missing, report['full_refusal']['missing'])
                with self.assertRaises(Mismatch):
                    require_complete_validation(report, 'native', profiles={'host-full'}, cache_allowed=False)
        self.assertIn('ctest ran', report['error'])

    def test_generic_run_lists_everything_not_selected(self):
        system = FakeSystem(self.directory)
        code, report = run(system, '--model', self.model, '--build', self.build, '--output', self.output(), '--cmake-arg=-GNinja')
        self.assertEqual(code, 0, report.get('error'))
        self.assertEqual((report['selection']['profile'], report['checks']), ('generic', ['generic']))
        self.assertEqual(report['ctest_selection']['labels'], '^(tier-generic|cat-real_model)$')
        self.assertEqual(report['ctest_ran'], report['ctest_selection']['tests'])
        for label in ('native', 'examples-smoke', 'abi', 'check-binary'):
            self.assertEqual(report['runs'][label]['exit_code'], 0)
        # No probe without --upstream-reference, so no probe-only launcher (CMake refuses one without the probe).
        configure = next(command for command in system.commands if command[:1] == ['cmake'] and '-S' in command)
        self.assertIn('-DDUCKTAPE_REFERENCE_PROBE_LAUNCHER=', configure)
        self.assertEqual(report['reference_probe_launcher'], 'none')
        # A generic run does not prove the native contracts.
        self.assertEqual(report['gates']['native_contracts'], {'status': 'not_run'})
        for key in ('rust', 'bindings', 'original-reference', 'package', 'policy', *[f'policy:{label}' for label in POLICY_LABELS]):
            self.assertTrue(report['skipped'][key].startswith('not selected'), key)
        with self.assertRaisesRegex(Mismatch, 'profile generic'):
            require_complete_validation(report, 'native', profiles={'host-full'}, cache_allowed=False)

    def test_policy_folding(self):
        out = self.directory / 'fold'
        policy_dir = out / 'policy'
        policy_dir.mkdir(parents=True)
        (policy_dir / 'abi-layouts.log').write_text('layouts\n')
        policy = {'schema_version': 1, 'source_commit': COMMIT, 'source_tracked_dirty': False, 'selection': 'platform',
                  'runs': {'abi-layouts': {'command': ['python3', 'tests/abi-layout.py'], 'exit_code': 0, 'seconds': 2.5, 'log': 'abi-layouts.log'},
                           'typescript-consumers': {'command': ['python3', 'tests/typescript/check.py'], 'exit_code': 1, 'seconds': 1.0,
                                                    'log': 'typescript-consumers.log'}},
                  'skipped': {'docs-links': 'not selected (--platform-only)'}}
        report = {'source_commit': COMMIT, 'runs': {}, 'skipped': {}}
        self.assertEqual(validate.fold_policy(report, policy, policy_dir, out), ['typescript-consumers'])
        self.assertEqual(report['runs']['abi-layouts'], {'command': ['python3', 'tests/abi-layout.py'], 'exit_code': 0, 'seconds': 2.5,
                                                         'log': 'abi-layouts.log'})
        self.assertEqual((out / 'abi-layouts.log').read_text(), 'layouts\n')
        self.assertEqual(report['skipped'], {'policy:docs-links': 'not selected (--platform-only)'})
        with self.assertRaisesRegex(validate.Refused, 'already a run'):
            validate.fold_policy(report, policy, policy_dir, out)
        with self.assertRaisesRegex(validate.Refused, 'not ' + COMMIT):
            validate.fold_policy({'source_commit': COMMIT, 'runs': {}, 'skipped': {}}, {**policy, 'source_commit': 'b' * 40}, policy_dir, out)
        with self.assertRaisesRegex(validate.Refused, 'schema'):
            validate.fold_policy({'source_commit': COMMIT, 'runs': {}, 'skipped': {}}, {**policy, 'schema_version': 2}, policy_dir, out)

    def test_phases(self):
        system = FakeSystem(self.directory, ci=True)
        code, built = run(system, '--phase', 'build', '--model', self.model, '--upstream-reference', '--compiler-launcher', 'ccache',
                          '--build', self.build, '--output', self.output(), '--cmake-arg=-GNinja')
        self.assertEqual(code, 0, built.get('error'))
        self.assertEqual((built['phase'], built['checks'], built['compiler_launcher']), ('build', [], 'ccache'))
        before = len(system.commands)
        test = lambda *extra, system=system: run(system, '--phase', 'test', '--from', self.build, '--model', self.model,
                                                 '--output', self.output(), '--test-jobs', '4', *extra)
        code, report = test('--checks', 'ctest,references')
        self.assertEqual(code, 0, report.get('error'))
        # A shard without --model finds it where the build phase registered the tests with it.
        code, shard = run(system, '--phase', 'test', '--from', self.build, '--output', self.output(), '--checks', 'parity:full-a')
        self.assertEqual(code, 0, shard.get('error'))
        self.assertEqual((shard['mode'], shard['runs']['bindings-a']['command'][2]), ('real_model', str(self.model)))
        # The build keeps laya-stress (EXCLUDE_FROM_ALL) for the stress jobs.
        self.assertEqual(built['runs']['build']['command'][-3:], ['--target', 'all', 'laya-stress'])
        self.assertEqual(report['phase'], 'test')
        self.assertEqual(report['runs']['upstream-reference']['exit_code'], 0)  # the build phase built the probe
        self.assertNotIn('configure', report['runs'])
        self.assertFalse(any(command[0] == 'cmake' for command in system.commands[before:]))
        self.assertEqual(report['library_sha256'], built['library_sha256'])
        self.assertEqual(report['build_options'], built['build_options'])
        # The build directory must be the build phase's.
        (self.build / LIBRARY).write_bytes(b'rebuilt')
        code, report = test('--checks', 'ctest')
        self.assertEqual(code, 1)
        self.assertIn('library_sha256', report['error'])
        (self.build / LIBRARY).write_bytes(system.library)
        code, report = run(system, '--phase', 'test', '--from', self.build, '--model', self.extra[0], '--output', self.output(), '--checks', 'ctest')
        self.assertIn('registered its tests with the model', report['error'])
        shutil.rmtree(self.build / 'build-phase')
        code, report = test('--checks', 'ctest')
        self.assertIn('run --phase build first', report['error'])
        # A candidate build phase with --full: the full build (reference probe), no checks, not evidence.
        code, full_build = run(system, '--full', '--phase', 'build', '--model', self.model, '--build', self.directory / 'build-full',
                               '--output', self.output(), '--cmake-arg=-GNinja')
        self.assertEqual(code, 0, full_build.get('error'))
        self.assertEqual((full_build['selection']['profile'], full_build['checks'], full_build['selection']['upstream_reference']),
                         ('selected', [], True))
        self.assertNotIn('full_refusal', full_build)


if __name__ == '__main__':
    unittest.main()
