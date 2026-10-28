#!/usr/bin/env python3
"""Real-model public-API conformance, independent of examples and demos.

Every binding must agree exactly with the C consumer. Reports capture outputs
for before/after adapter comparisons; they are not automatically accepted goldens.

Profiles: `full` (the default) runs every fixture through every consumer, then the
open-config pass (options.json with OPEN_CONFIG). `smoke` runs one request of each
shape (primitive, structured, batch, integer-keys) and the open-config pass through
every consumer, and the two long-state requests (coverage.json[5] and [6]) through C
and C++ only: the binding layers do not read the state length.

Halves (`--bindings a|b`) split the consumers across two jobs; both include C, whose
results the merge (scripts/merge-validation.py) requires to be byte-identical across
the halves. Each half writes the same report shape with its own consumers only.

Each consumer is launched once per open config with `--requests FILE...` (one engine,
one compact JSON result per line). `--one-process-per-request` reproduces the earlier
one-process-per-request runs (request on stdin), for the equality check of engine reuse;
`--fresh-engine` keeps one process but opens one engine per request. Timing per launch
goes to parity-timing.json next to the report, never into it."""
import argparse
from collections import OrderedDict
import math
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from compare_results import compare_results, js_object_order
from release_support import LIBRARY, binary_info, sha256, source_identity

ROOT = pathlib.Path(__file__).resolve().parents[1]
# Report key order (and the order consumers run in).
CONSUMERS = ('C', 'C++', 'Rust', 'Node', 'Python', 'Java', '.NET', 'Go')
# Both halves carry C (the reference every binding is compared with, and the merge's cross-half check).
# a: the consumers that need no separate build step (Rust compiles inside its first `cargo run`);
# b: the three that parity.py builds first (the Java jar, the .NET and Go programs), whose start-up is slowest.
HALVES = {'a': ('C', 'C++', 'Rust', 'Node', 'Python'), 'b': ('C', 'Java', '.NET', 'Go'), 'all': CONSUMERS}
# smoke: one request of each shape through every consumer.
SMOKE_FIXTURES = ('primitive.json', 'structured.json', 'batch.json', 'integer-keys.json')
# smoke: the long-state requests (512-token passes) through C and C++ only, as coverage.json[index].
SMOKE_LONG_STATE = ('coverage.json', (5, 6), ('C', 'C++'))
# Second pass: open config (context, overflow, calibration temperatures) through every binding.
OPEN_CONFIG = {'threads': 4, 'context_tokens': 256, 'overflow': 'truncate_end',
               'temperatures': {'choice': 1.3, 'noul.2': 1.2}, 'max_windows_per_call': 64}
OPEN_CONFIG_FIXTURE = ROOT / 'tests/fixtures/options.json'


def parse(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('model', type=pathlib.Path)
    parser.add_argument('--build-dir', type=pathlib.Path)
    parser.add_argument('--request', type=pathlib.Path, help='Use one fixture instead of the shared corpus (no open-config pass)')
    parser.add_argument('--report', type=pathlib.Path)
    parser.add_argument('--timing', type=pathlib.Path, help='Per-launch timing (default: parity-timing.json next to --report)')
    parser.add_argument('--profile', choices=('smoke', 'full'), default='full')
    parser.add_argument('--bindings', choices=sorted(HALVES), default='all', help='Consumers to run: half a, half b or all')
    parser.add_argument('--one-process-per-request', action='store_true',
                        help='Test only: one consumer process per request, the request on stdin (the earlier runs)')
    parser.add_argument('--fresh-engine', action='store_true', help='One engine per request inside each consumer process')
    parser.add_argument('--call-timeout', type=float, default=900,
                        help='Seconds per request; a hang guard, sized for loaded or slower hosts')
    args = parser.parse_args(argv)
    if not math.isfinite(args.call_timeout) or args.call_timeout <= 0:
        parser.error('--call-timeout must be finite and positive')
    if args.one_process_per_request and args.fresh_engine:
        parser.error('--fresh-engine applies to the --requests mode, not to --one-process-per-request')
    if args.timing is None and args.report is not None:
        args.timing = args.report.parent / 'parity-timing.json'
    return parser, args


def environment(build):
    env = dict(os.environ)
    if sys.platform == 'win32':
        # Windows finds DLLs on PATH; the Python consumer reads and writes UTF-8.
        env['PATH'] = str(build) + os.pathsep + env.get('PATH', '')
        env['PYTHONUTF8'] = '1'
    else:
        env['LD_LIBRARY_PATH'] = str(build) + ':' + env.get('LD_LIBRARY_PATH', '')
    env['DUCKTAPE_LAYA_LIB_DIR'] = str(build)
    env['PYTHONPATH'] = str(ROOT / 'bindings/python/src')
    env.update(DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_NOLOGO='1')
    env.update(CGO_CFLAGS='-I' + str(ROOT / 'include'), CGO_LDFLAGS='-L' + str(build), GOTOOLCHAIN='local')
    return env


def build_consumers(build, env, consumers):
    """Compiled consumers are built once, before any timed launch; only those of the selected consumers."""
    exe = '.exe' if sys.platform == 'win32' else ''
    if 'Java' in consumers:
        if sys.platform == 'win32':
            # build.sh --no-test without bash (the bash found first on Windows can be WSL's).
            java = ROOT / 'bindings/java/target'
            shutil.rmtree(java, ignore_errors=True)
            (java / 'classes').mkdir(parents=True)
            subprocess.run(['javac', '--release', '22', '-d', str(java / 'classes')] +
                           [str(path) for path in sorted((ROOT / 'bindings/java/src/main').rglob('*.java'))],
                           check=True, env=env, capture_output=True)
            subprocess.run(['jar', '--create', '--file', str(java / 'ducktape-laya.jar'), '-C', str(java / 'classes'), '.'],
                           check=True, env=env, capture_output=True)
        else:
            subprocess.run(['bash', str(ROOT / 'bindings/java/build.sh'), '--no-test'], check=True, env=env, capture_output=True)
    if 'Go' in consumers:
        subprocess.run(['go', 'build', '-o', str(build / ('conformance-go' + exe)), '.'], cwd=ROOT / 'tests/consumers/go',
                       check=True, env=env, capture_output=True)
    if '.NET' in consumers:
        subprocess.run(['dotnet', 'build', str(ROOT / 'tests/consumers/dotnet/Consumer.csproj'), '-c', 'Release', '-m:1',
                        '-o', str(build / 'conformance-dotnet')], check=True, env=env, capture_output=True)


def consumer_commands(build, model):
    exe = '.exe' if sys.platform == 'win32' else ''
    return {
        'C': [str(build / ('laya-conformance-c' + exe)), model],
        'C++': [str(build / ('laya-conformance-cpp' + exe)), model],
        'Rust': ['cargo', 'run', '--locked', '--offline', '--quiet', '--manifest-path',
                 str(ROOT / 'tests/consumers/rust/Cargo.toml'), '--target-dir',
                 str(build / 'conformance-rust'), '--', model],
        'Node': ['node', str(ROOT / 'tests/consumers/node.cjs'), model],
        'Python': [sys.executable, str(ROOT / 'tests/consumers/python/consumer.py'), model],
        'Java': ['java', '--enable-native-access=ALL-UNNAMED', '-cp', str(ROOT / 'bindings/java/target/ducktape-laya.jar'),
                 str(ROOT / 'tests/consumers/java/Consumer.java'), model],
        '.NET': ['dotnet', str(build / 'conformance-dotnet/Consumer.dll'), model],
        'Go': [str(build / ('conformance-go' + exe)), model],
    }


def plan(args, scratch, consumers):
    """[(fixture name, request file, consumers)] for the default-config pass, in report (name) order."""
    if args.request:
        return [(args.request.name, args.request, consumers)]
    fixtures = ROOT / 'tests/fixtures'
    if args.profile == 'full':
        return [(path.name, path, consumers) for path in sorted(fixtures.glob('*.json'))]
    entries = [(name, fixtures / name, consumers) for name in SMOKE_FIXTURES]
    name, indexes, only = SMOKE_LONG_STATE
    body = json.loads((fixtures / name).read_text(encoding='utf-8'))
    for index in indexes:
        path = scratch / f'{pathlib.Path(name).stem}-{index}.json'
        path.write_bytes(json.dumps(body[index], ensure_ascii=False).encode())
        entries.append((f'{name}[{index}]', path, tuple(c for c in consumers if c in only)))
    return sorted(entries, key=lambda entry: entry[0])


class Launcher:
    """Runs consumer processes and records each launch's wall time and result-line arrival offsets."""

    def __init__(self, env, call_timeout):
        self.env, self.call_timeout, self.launches = env, call_timeout, []

    def launch(self, command, label, requests, stdin=None):
        start = time.monotonic()
        process = subprocess.Popen(command, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env)
        errors, timed_out = [], threading.Event()

        def feed():
            try:
                process.stdin.write(stdin)
                process.stdin.close()
            except OSError:
                pass  # the consumer exited early; its exit status and stderr say why
        threads = [threading.Thread(target=lambda: errors.append(process.stderr.read()), daemon=True)]
        if stdin is not None:
            threads.append(threading.Thread(target=feed, daemon=True))
        for thread in threads:
            thread.start()

        def kill():
            timed_out.set()
            process.kill()
        timer = threading.Timer(self.call_timeout * max(1, len(requests)), kill)
        timer.start()
        lines, offsets = [], []
        try:
            for line in process.stdout:
                lines.append(line)
                offsets.append(round(time.monotonic() - start, 3))
            process.wait()
        finally:
            timer.cancel()
            for thread in threads:
                thread.join()
        wall = round(time.monotonic() - start, 3)
        self.launches.append({'launch': label, 'requests': list(requests), 'mode': 'stdin' if stdin is not None else 'requests',
                              'exit_code': process.returncode, 'timed_out': timed_out.is_set(), 'wall_seconds': wall,
                              'result_offsets_seconds': offsets})
        stderr = b''.join(errors).decode(errors='replace')
        if timed_out.is_set():
            raise RuntimeError(f'{label}: no result within {self.call_timeout * max(1, len(requests))} s')
        if process.returncode:
            raise RuntimeError(f'{label}: {stderr}')
        return lines


def run_pass(launcher, commands, entries, args, config, suffix=''):
    """Results {fixture name: {consumer: result}} of one open config, each consumer compared with C."""
    results = {name: {} for name, _, _ in entries}
    config_args = [] if config is None else [json.dumps(config, separators=(',', ':'))]
    for consumer in CONSUMERS:
        mine = [(name, path) for name, path, consumers in entries if consumer in consumers]
        if not mine:
            continue
        command = commands[consumer] + config_args
        if args.one_process_per_request:
            for name, path in mine:
                lines = launcher.launch(command, f'{name}{suffix}/{consumer}', [name], stdin=path.read_bytes())
                results[name][consumer] = json.loads(b''.join(lines), object_pairs_hook=OrderedDict)
        else:
            extra = ['--fresh-engine'] if args.fresh_engine else []
            label = f'{consumer}{suffix or ""}'
            lines = launcher.launch(command + ['--requests', *[str(path) for _, path in mine]] + extra, label,
                                    [name for name, _ in mine])
            lines = [line for line in lines if line.strip()]
            if len(lines) != len(mine):
                raise RuntimeError(f'{label}: {len(lines)} result lines for {len(mine)} requests')
            for (name, _), line in zip(mine, lines):
                results[name][consumer] = json.loads(line, object_pairs_hook=OrderedDict)
    for name, by_consumer in results.items():
        label = name + suffix
        for consumer in CONSUMERS:
            if consumer not in by_consumer:
                continue
            # Parsed JavaScript objects enumerate integer-like keys first; values stay exact.
            expected = js_object_order(by_consumer['C']) if consumer == 'Node' else by_consumer['C']
            compare_results(expected, by_consumer[consumer], path=f'{label}/{consumer}')
            print(f'{label}/{consumer}: exact agreement', flush=True)
    return results


def main(argv=None):
    parser, args = parse(argv)
    build = (args.build_dir or ROOT / 'build').resolve()
    model = str(args.model.resolve())
    consumers = HALVES[args.bindings]
    env = environment(build)
    started = time.monotonic()
    launcher = Launcher(env, args.call_timeout)
    timing = {'schema_version': 1, 'profile': args.profile, 'bindings': args.bindings, 'consumers': list(consumers),
              'mode': 'one-process-per-request' if args.one_process_per_request else 'requests', 'fresh_engine': args.fresh_engine,
              'status': 'failed', 'note': 'wall_seconds covers process start, model open and every request of the launch; '
              'result_offsets_seconds are the arrival times of the result lines from the launch start, so the first holds the '
              'start-up and model open with the first request and each later difference is one request\'s execute time',
              'launches': launcher.launches}
    try:
        with tempfile.TemporaryDirectory(prefix='ducktape-parity-') as scratch:
            entries = plan(args, pathlib.Path(scratch), consumers)
            if not entries:
                parser.error('No conformance fixtures found')
            build_started = time.monotonic()
            build_consumers(build, env, consumers)
            timing['consumer_build_seconds'] = round(time.monotonic() - build_started, 3)
            commands = consumer_commands(build, model)
            report = {'fixtures': {}, 'model_sha256': sha256(args.model),
                      'fixture_hashes': {name: sha256(path) for name, path, _ in entries},
                      'runtime': binary_info(build), 'library_sha256': sha256(build / LIBRARY),
                      'source_identity': source_identity(), 'settings': {'threads': 4}}
            results = run_pass(launcher, commands, entries, args, None)
            for name, _, _ in entries:
                report['fixtures'][name] = {consumer: results[name][consumer] for consumer in CONSUMERS if consumer in results[name]}
            if not args.request and OPEN_CONFIG_FIXTURE.is_file():
                name = OPEN_CONFIG_FIXTURE.name
                opened = run_pass(launcher, commands, [(name, OPEN_CONFIG_FIXTURE, consumers)], args, OPEN_CONFIG, '+open_config')
                report['open_config'] = {'config': OPEN_CONFIG, 'fixture': name, 'fixture_hash': sha256(OPEN_CONFIG_FIXTURE),
                                         'results': {consumer: opened[name][consumer] for consumer in CONSUMERS if consumer in opened[name]}}
        if args.report:
            args.report.write_text(json.dumps(report, indent=2) + '\n')
        timing['status'] = 'passed'
    finally:
        timing['total_seconds'] = round(time.monotonic() - started, 3)
        if args.timing:
            args.timing.parent.mkdir(parents=True, exist_ok=True)
            args.timing.write_text(json.dumps(timing, indent=2) + '\n')


if __name__ == '__main__':
    main()
