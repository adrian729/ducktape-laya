#!/usr/bin/env python3
"""Run the release host's qualification gates in order and write their evidence keys.

The long instrumented gates (sanitizers, the one-hour fuzz, the runtime-upgrade rehearsal) run in
CI (`native.yml` mode=qualify); this script runs what must run on this host (docs/RELEASING.md,
step 2), in this order:

1. the gcc evidence build and `validate.py --full` (`--test-jobs 1`), nothing else on the machine;
2. performance (`tests/benchmark.py` per model, checked against its budget) on the idle machine;
3. the soak (`check-stress.py` at the candidate soak budget), pinned with taskset to all threads
   of the SOAK_CORES highest-numbered physical cores; meanwhile, on the other physical cores (never
   a soak core's SMT sibling), one after another: (a) `check-reproducibility.py` (tier 1: two uncached
   builds that must contain step 1's library and addon); (b) `model_upgrade` alone, since its
   real-model ctest has the CPU-time ratio tests; (c) the browser builds, package and both page
   runs, `installed_release`, `userspaces`, `rollback` and the cross-CPU compare;
4. waits for the soak, writes native-matrix.json (the list native_matrix names) and merges the
   evidence keys into evidence.json (next to
   `fetch-ci-evidence.py --merge`'s keys): native, native_matrix (this host's report and the CI
   clang report), reproducibility, performance, performance_<model id>, soak, stress (the soak
   report), model_upgrade, browser_package, browser, browser_isolated, installed_release,
   userspaces, rollback, cross_cpu.

Steps 1 and 2 never share the machine. The candidate's own reuse rules (check-candidate.py
LIBRARY_INPUTS, COMMON_HARNESS, HARNESS) decide which hours-long evidence of --reuse EVIDENCE
can stand instead of a new run. --print-cpus prints the soak and other CPU sets; --dry-run prints
the steps, CPU sets and evidence keys without running anything.
"""
import argparse
import ast
import fnmatch
import glob
import json
import os
import pathlib
import shlex
import subprocess
import sys
import tarfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = sys.executable
REUSABLE = {'soak': ('soak', 'stress'), 'model-upgrade': ('model_upgrade',), 'performance': ('performance',)}


# ------------------------------------------------------------------------------------------------
# CPU topology
# ------------------------------------------------------------------------------------------------
def parse_cpu_list(text):
    cpus = set()
    for part in text.strip().split(','):
        if not part:
            continue
        low, _, high = part.partition('-')
        cpus.update(range(int(low), int(high or low) + 1))
    return cpus


def format_cpu_list(cpus):
    cpus, parts = sorted(cpus), []
    for cpu in cpus:
        if parts and cpu == parts[-1][1] + 1:
            parts[-1][1] = cpu
        else:
            parts.append([cpu, cpu])
    return ','.join(f'{low}' if low == high else f'{low}-{high}' for low, high in parts)


# The soak's physical cores. A stress round runs one engine at {"threads":2}, and every fifth round a
# second engine beside it: two cores give each of the first engine's two threads a core of its own and
# all four threads a CPU, where one core put the two on SMT siblings (V1 H10: 12.3 s per round; on
# the two cores' threads 13.1 s, on one thread of each of two cores 11.1 s, against 13.4-13.9 s on one
# core) and oversubscribed its two CPUs during the second engine's rounds. The other two cores still
# finish their gates (about 1.5 h) well inside the soak.
SOAK_CORES = 2


def cpu_sets(sys_root='/sys', soak_cores=SOAK_CORES):
    """(soak CPUs, other CPUs): the threads of the highest-numbered physical core(s), and every other online CPU."""
    base = pathlib.Path(sys_root) / 'devices/system/cpu'
    online = parse_cpu_list((base / 'online').read_text())
    cores = set()
    for cpu in online:
        siblings = parse_cpu_list((base / f'cpu{cpu}/topology/thread_siblings_list').read_text()) & online
        cores.add(tuple(sorted(siblings | {cpu})))
    cores = sorted(cores, key=min)  # a core is numbered by its first thread, whether or not a sibling is offline
    if len(cores) <= soak_cores:
        raise SystemExit(f'qualify-host: {len(cores)} physical cores; the soak needs {soak_cores} and the other gates at least one more')
    soak = set().union(*map(set, cores[-soak_cores:]))
    return soak, online - soak


# ------------------------------------------------------------------------------------------------
# The candidate's reuse rules
# ------------------------------------------------------------------------------------------------
RULE_NAMES = ('LIBRARY_INPUTS', 'NATIVE_TESTS', 'BINDING_CODE', 'COMMON_HARNESS', 'HARNESS')


def reuse_rules(source):
    """LIBRARY_INPUTS, COMMON_HARNESS and HARNESS of check-candidate.py, evaluated without running the script."""
    namespace = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in RULE_NAMES:
            namespace[node.targets[0].id] = eval(compile(ast.Expression(node.value), 'check-candidate.py', 'eval'),
                                                 {'__builtins__': {}}, dict(namespace))
    missing = [name for name in ('LIBRARY_INPUTS', 'COMMON_HARNESS', 'HARNESS') if name not in namespace]
    if missing:
        raise SystemExit('qualify-host: check-candidate.py has no ' + ', '.join(missing))
    return namespace


def git(*args):
    return subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, text=True)


def reusable(report_path, gate, candidate, rules):
    """Why a previous report can stand for this gate (None when it cannot), as check-candidate.py's tested_source decides."""
    try:
        report = json.loads(pathlib.Path(report_path).read_text())
    except (OSError, ValueError):
        return None
    produced = report.get('source_commit')
    if report.get('source_tracked_dirty') is not False or report.get('status', 'passed') != 'passed' or not isinstance(produced, str):
        return None
    if produced == candidate:
        return f'produced at the candidate {candidate[:12]}'
    if git('merge-base', '--is-ancestor', produced, candidate).returncode:
        return None
    diff = git('diff', '--name-only', '--no-renames', produced, candidate)
    if diff.returncode:
        return None
    patterns = rules['LIBRARY_INPUTS'] + rules['COMMON_HARNESS'] + rules['HARNESS'].get(gate, [])
    touched = [path for path in diff.stdout.splitlines() if any(fnmatch.fnmatch(path, pattern) for pattern in patterns)]
    return None if touched else f'from {produced[:12]}: no input of {gate} changed since'


# ------------------------------------------------------------------------------------------------
# Steps
# ------------------------------------------------------------------------------------------------
class Step:
    def __init__(self, name, phase, command=None, *, cpus='all', env=None, outputs=(), evidence=None, action=None, background=False,
                 capture=None, judge=None):
        self.name, self.phase, self.command, self.cpus, self.env = name, phase, command, cpus, env or {}
        self.outputs, self.evidence, self.action, self.background, self.capture = list(outputs), evidence or {}, action, background, capture
        # judge(exit status) -> passed; by default a step passes when its command exits 0.
        self.judge = judge or (lambda code: code == 0)


def model_upgrade_completed(report_path):
    """check-upgrade.py exits 1 whenever the candidate model's results differ from the baseline's, which a model
    upgrade expects (the drift needs reference evaluation). The step passes, as check-candidate.py's model_upgrade
    rule does, when the candidate checks completed: no failure, the native, binding and ABI gates passed and the
    comparison ran."""
    try:
        report = json.loads(pathlib.Path(report_path).read_text())
    except (OSError, ValueError):
        return False
    return ('failure' not in report and
            all(report.get('gates', {}).get(gate, {}).get('status') == 'passed' for gate in ('native_contracts', 'binding_conformance', 'abi')) and
            report.get('comparison', {}).get('status') in ('passed', 'failed'))


def model_ids(record):
    """[(manifest, budgets path)] of the primary model and of each additional model with performance budgets."""
    manifests = [json.loads((ROOT / path).read_text()) for path in record['models'] + record.get('additional_models', [])]
    result = [(manifests[0], 'release/budgets/native-performance.json')]
    result += [(m, m['performance_budgets']) for m in manifests[1:] if m.get('performance_budgets')]
    return result


def extract_package(package_dir, target):
    """The browser npm package's `package/` contents, as an application deploys them (no links, no absolute or .. paths)."""
    tarballs = sorted(pathlib.Path(package_dir).glob('*.tgz'))
    if len(tarballs) != 1:
        raise RuntimeError(f'expected one npm tarball in {package_dir}, found {len(tarballs)}')
    target = pathlib.Path(target)
    target.mkdir(parents=True, exist_ok=False)
    with tarfile.open(tarballs[0]) as archive:
        for member in archive.getmembers():
            name = pathlib.PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or not (member.isfile() or member.isdir()):
                raise RuntimeError(f'unsafe member {member.name} in {tarballs[0]}')
            if name.parts[:1] != ('package',) or len(name.parts) == 1:
                continue
            destination = target.joinpath(*name.parts[1:])
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.extractfile(member).read())
            destination.chmod(member.mode & 0o755 | 0o644)


def plan(a, record, policy, out, ci, reused):
    """The ordered steps (phase 1, 2, 3 soak, 3a-3c on the other cores) with their evidence keys (paths relative to out)."""
    rel = lambda path: os.path.relpath(path, out)  # noqa: E731
    model, extra = a.model, a.extra_model
    build = ROOT / 'build-validation'
    steps = [Step('native', 1, [PY, 'scripts/validate.py', '--full', '--model', model, *sum((['--extra-model', m] for m in extra), []),
                                '--upstream-reference', '--test-jobs', '1', '--output', out / 'native'],
                  env={'CC': 'gcc', 'CXX': 'g++'}, outputs=[out / 'native'],
                  evidence={'native': rel(out / 'native/report.json')})]
    if 'performance' not in reused:
        for manifest, budgets in model_ids(record):
            key = 'performance' if budgets == 'release/budgets/native-performance.json' else f'performance_{manifest["id"]}'
            gguf = model if key == 'performance' else next((m for m in extra if pathlib.Path(m).name == manifest['filename']),
                                                          f'<{manifest["filename"]}>')
            report = out / 'performance' / f'{manifest["id"]}.json'
            steps.append(Step(f'performance-{manifest["id"]}', 2, [PY, 'tests/benchmark.py', gguf, '--build', build, '--report', report],
                              outputs=[report], evidence={key: rel(report)}))
            steps.append(Step(f'performance-budget-{manifest["id"]}', 2, [PY, 'scripts/performance-budgets.py', 'check', report,
                                                                          '--budgets', budgets]))
    budgets = policy['budgets']
    if 'soak' not in reused:
        soak = [PY, 'scripts/check-stress.py', model, '--build', build, '--seconds', budgets['candidate_soak_seconds']]
        if 'candidate_soak_rounds' in budgets:
            soak += ['--min-rounds', budgets['candidate_soak_rounds']]
        steps.append(Step('soak', 3, soak + ['--output', out / 'soak'], cpus='soak', background=True, outputs=[out / 'soak'],
                          evidence={'soak': rel(out / 'soak/report.json'), 'stress': rel(out / 'soak/report.json')}))
    steps.append(Step('reproducibility', '3a', [PY, 'scripts/check-reproducibility.py', '--output', out / 'reproducibility'],
                      cpus='other', outputs=[out / 'reproducibility'], evidence={'reproducibility': rel(out / 'reproducibility/report.json')}))
    if 'model-upgrade' not in reused:
        steps.append(Step('model-upgrade', '3b', [PY, 'scripts/check-upgrade.py', '--runtime', record['runtime']['commit'], '--model', model,
                                                  '--candidate-model', a.upgrade_model, '--candidate-manifest', a.upgrade_manifest,
                                                  '--compiler-launcher', 'ccache', '--output', out / 'model-upgrade'],
                          cpus='other', outputs=[out / 'model-upgrade'], evidence={'model_upgrade': rel(out / 'model-upgrade/report.json')},
                          judge=lambda code: model_upgrade_completed(out / 'model-upgrade/report.json')))
    emsdk = a.emsdk or '<EMSDK>'
    for backend in ('wasm', 'webgpu'):
        steps.append(Step(f'browser-build-{backend}', '3c', [PY, 'scripts/build-browser.py', '--emsdk', emsdk, '--backend', backend,
                                                             '--reference', '--build', ROOT / f'build-browser-{backend}'], cpus='other'))
    steps.append(Step('browser-build-wasm-constrained', '3c', [PY, 'scripts/build-browser.py', '--emsdk', emsdk, '--backend', 'wasm',
                                                               '--maximum-memory', '469762048', '--build',
                                                               ROOT / 'build-browser-wasm-constrained'], cpus='other'))
    steps.append(Step('browser-package', '3c', [PY, 'scripts/package-browser.py', '--wasm', ROOT / 'build-browser-wasm', '--webgpu',
                                                ROOT / 'build-browser-webgpu', '--output', out / 'browser-package'], cpus='other',
                      outputs=[out / 'browser-package'], evidence={'browser_package': rel(out / 'browser-package/inventory.json')}))
    extracted = out / 'browser-package/extracted'
    steps.append(Step('browser-package-extract', '3c', action=lambda: extract_package(out / 'browser-package', extracted),
                      command=['(extract the npm package into)', extracted], cpus='other'))
    steps.append(Step('browser', '3c', ['node', 'tests/browser-automation/run.mjs', '--model', model, '--native', out / 'native/outputs.json',
                                        '--package-dir', extracted, '--constrained-wasm', ROOT / 'build-browser-wasm-constrained',
                                        '--output', out / 'browser'], cpus='other', outputs=[out / 'browser'],
                      evidence={'browser': rel(out / 'browser/report.json')}))
    steps.append(Step('browser-isolated', '3c', ['node', 'tests/browser-automation/run.mjs', '--model', model, '--package-dir', extracted,
                                                 '--isolated', '--output', out / 'browser-isolated'], cpus='other',
                      outputs=[out / 'browser-isolated'], evidence={'browser_isolated': rel(out / 'browser-isolated/report.json')}))
    packages = ci / 'packages-assembled'
    steps.append(Step('installed-release', '3c', [PY, 'tests/installed-packages.py', packages, model, '--platform', 'linux-x64',
                                                  '--report', out / 'installed-release.json'], cpus='other',
                      outputs=[out / 'installed-release.json'], evidence={'installed_release': rel(out / 'installed-release.json')}))
    rootfs = a.rootfs or ['<ROOTFS>']
    steps.append(Step('userspaces', '3c', [PY, 'tests/linux-userspace.py', packages, model, *rootfs, '--report', out / 'userspaces.json'],
                      cpus='other', outputs=[out / 'userspaces.json'], evidence={'userspaces': rel(out / 'userspaces.json')}))
    steps.append(Step('rollback', '3c', [PY, 'tests/rollback.py', a.lkg_packages or '<LKG_PACKAGES>', packages, model,
                                         '--report', out / 'rollback.json'], cpus='other', outputs=[out / 'rollback.json'],
                      evidence={'rollback': rel(out / 'rollback.json')}))
    gcc_ci = next(iter(sorted(glob.glob(str(ci / 'native-x64-gcc-node-*')))), str(ci / 'native-x64-gcc-node-<VERSION>'))
    steps.append(Step('cross-cpu-record', '3c', [PY, 'tests/cross-host.py', 'record', '--bundle', pathlib.Path(gcc_ci) / 'cross-host/bundle',
                                                 '--model', model, '--report', out / 'cross-host-local.json'], cpus='other',
                      outputs=[out / 'cross-host-local.json']))
    steps.append(Step('cross-cpu', '3c', [PY, 'tests/cross-host.py', 'compare', pathlib.Path(gcc_ci) / 'cross-host/record.json',
                                          out / 'cross-host-local.json'], cpus='other', capture=out / 'cross-cpu.json',
                      outputs=[out / 'cross-cpu.json'], evidence={'cross_cpu': rel(out / 'cross-cpu.json')}))
    # native_matrix is a JSON list of report paths relative to the list file (check-candidate.py's linux_matrix).
    clang_ci = next(iter(sorted(glob.glob(str(ci / 'native-x64-clang-node-*')))), str(ci / 'native-x64-clang-node-<VERSION>'))
    matrix = out / 'native-matrix.json'
    reports = [rel(out / 'native/report.json'), rel(pathlib.Path(clang_ci) / 'report.json')]
    steps.append(Step('evidence', 4, ['(write the native matrix)', matrix, '=', *reports], outputs=[matrix],
                      action=lambda: matrix.write_text(json.dumps(reports, indent=2) + '\n'), evidence={'native_matrix': rel(matrix)}))
    return steps


def discover_model(manifest):
    """--model's default: $DUCKTAPE_TEST_MODEL, else the pinned file in the Hugging Face cache."""
    if os.environ.get('DUCKTAPE_TEST_MODEL'):
        return pathlib.Path(os.environ['DUCKTAPE_TEST_MODEL'])
    hub = pathlib.Path(os.environ.get('HF_HUB_CACHE') or pathlib.Path(os.environ.get('HF_HOME', pathlib.Path.home() / '.cache/huggingface')) / 'hub')
    path = hub / f'models--{manifest["repository"].replace("/", "--")}/snapshots/{manifest["revision"]}/{manifest["filename"]}'
    return path if path.is_file() else None


def default_extra_models(record):
    """The additional models in build-models/ of this checkout or of the main worktree."""
    common = git('rev-parse', '--path-format=absolute', '--git-common-dir').stdout.strip()
    bases = [ROOT / 'build-models'] + ([pathlib.Path(common).parent / 'build-models'] if common else [])
    found = []
    for path in record.get('additional_models', []):
        name = json.loads((ROOT / path).read_text())['filename']
        found.append(next((base / name for base in bases if (base / name).is_file()), None))
    return found


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--candidate', help='The candidate commit (it must be the clean checkout\'s HEAD)')
    p.add_argument('--model', type=pathlib.Path, help='Primary model (default $DUCKTAPE_TEST_MODEL or the pinned Hugging Face cache path)')
    p.add_argument('--extra-model', type=pathlib.Path, action='append', default=[], help='Additional model, repeatable '
                   '(default: release/compatibility.json additional_models in build-models/)')
    p.add_argument('--output', type=pathlib.Path, default=ROOT / 'build-candidate', help='Candidate directory (default build-candidate)')
    p.add_argument('--evidence', type=pathlib.Path, help='evidence.json to merge the keys into (default OUTPUT/evidence.json)')
    p.add_argument('--ci-dir', type=pathlib.Path, help='fetch-ci-evidence.py output (default OUTPUT/ci)')
    p.add_argument('--emsdk', type=pathlib.Path, help='The pinned emsdk (release/browser-toolchain.json)')
    p.add_argument('--lkg-packages', type=pathlib.Path, help='Last-known-good packages (scripts/release-assets.py retain)')
    p.add_argument('--rootfs', type=pathlib.Path, action='append', help='Ubuntu root filesystem for userspaces, repeatable '
                   '(default build-platforms/*/rootfs)')
    p.add_argument('--upgrade-model', type=pathlib.Path, help='model_upgrade candidate model (default: the typed-decisions model)')
    p.add_argument('--upgrade-manifest', type=pathlib.Path, default=ROOT / 'models/laya-typed-decisions.json')
    p.add_argument('--reuse', type=pathlib.Path, help='A previous evidence.json whose hours-long reports may stand (candidate reuse rules)')
    p.add_argument('--soak-cores', type=int, default=SOAK_CORES, help=f'Physical cores the soak is pinned to (default {SOAK_CORES})')
    p.add_argument('--print-cpus', action='store_true', help='Print SOAK_CPUS and OTHER_CPUS and exit')
    p.add_argument('--dry-run', action='store_true', help='Print the steps, CPU sets and evidence keys; run nothing')
    p.add_argument('--sys-root', default='/sys', help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    if a.soak_cores < 1:
        p.error('--soak-cores must be positive')
    soak_cpus, other_cpus = cpu_sets(a.sys_root, a.soak_cores)
    if a.print_cpus:
        print(f'SOAK_CPUS={format_cpu_list(soak_cpus)}')
        print(f'OTHER_CPUS={format_cpu_list(other_cpus)}')
        return 0
    if not a.candidate:
        p.error('--candidate is required')
    head = git('rev-parse', 'HEAD').stdout.strip()
    candidate = git('rev-parse', '--verify', '--end-of-options', f'{a.candidate}^{{commit}}').stdout.strip()
    if not candidate:
        p.error(f'--candidate {a.candidate} is not a commit')
    problems = []
    if candidate != head:
        problems.append(f'the candidate {candidate[:12]} is not this checkout\'s HEAD {head[:12]}')
    if git('status', '--porcelain', '--untracked-files=no').stdout.strip():
        problems.append('the checkout has tracked changes')
    record = json.loads(git('show', f'{candidate}:release/compatibility.json').stdout or (ROOT / 'release/compatibility.json').read_text())
    policy = json.loads(git('show', f'{candidate}:release/qualification.json').stdout or (ROOT / 'release/qualification.json').read_text())
    rules = reuse_rules(git('show', f'{candidate}:scripts/check-candidate.py').stdout or (ROOT / 'scripts/check-candidate.py').read_text())
    manifest = json.loads((ROOT / record['models'][0]).read_text())
    a.model = a.model or discover_model(manifest)
    if not a.extra_model:
        a.extra_model = [m for m in default_extra_models(record) if m] if not a.dry_run else \
            [m or pathlib.Path(f'<{json.loads((ROOT / path).read_text())["filename"]}>')
             for m, path in zip(default_extra_models(record), record.get('additional_models', []))]
    if not a.upgrade_model:
        upgrade_name = json.loads(pathlib.Path(a.upgrade_manifest).read_text())['filename']
        a.upgrade_model = next((m for m in a.extra_model if pathlib.Path(m).name == upgrade_name), None)
    if not a.rootfs:
        a.rootfs = [pathlib.Path(path) for path in sorted(glob.glob(str(ROOT / 'build-platforms/*/rootfs')))]
    out = a.output.resolve()
    ci = (a.ci_dir or out / 'ci').resolve()
    evidence_path = (a.evidence or out / 'evidence.json').resolve()
    reused, reuse_keys = {}, {}
    if a.reuse:
        previous = json.loads(a.reuse.read_text())
        for step, keys in REUSABLE.items():
            if step == 'performance':
                keys = tuple(key for key in previous if key == 'performance' or key.startswith('performance_'))
            gate = {'soak': 'soak', 'model-upgrade': 'model_upgrade', 'performance': 'performance'}[step]
            reasons = [reusable(a.reuse.parent / previous[key], gate if key != 'stress' else 'stress', candidate, rules)
                       if key in previous else None for key in keys]
            if keys and all(reasons):
                reused[step] = reasons[0]
                reuse_keys.update({key: os.path.relpath((a.reuse.parent / previous[key]).resolve(), evidence_path.parent) for key in keys})
    if not a.model and not a.dry_run:
        problems.append('no model: pass --model or set DUCKTAPE_TEST_MODEL')
    a.model = a.model or pathlib.Path('<MODEL>')
    a.upgrade_model = a.upgrade_model or pathlib.Path('<UPGRADE_MODEL>')
    steps = plan(a, record, policy, out, ci, reused)
    if not a.dry_run:
        if len(a.extra_model) != len(record.get('additional_models', [])):
            problems.append('missing additional models: pass --extra-model for each of release/compatibility.json additional_models')
        for name, value in (('--emsdk', a.emsdk), ('--lkg-packages', a.lkg_packages)):
            if not value:
                problems.append(f'{name} is required')
        if not a.rootfs:
            problems.append('no userspace root filesystems: pass --rootfs (tests/download-userspaces.py --output build-platforms)')
        if not (ci / 'packages-assembled').is_dir():
            problems.append(f'no CI evidence in {ci}: run fetch-ci-evidence.py --candidate {candidate[:12]} first')
        existing = [str(path) for step in steps for path in step.outputs if pathlib.Path(path).exists()]
        if existing:
            problems.append('outputs exist (move them away): ' + ', '.join(existing))
    cpus = {'soak': format_cpu_list(soak_cpus), 'other': format_cpu_list(other_cpus if 'soak' not in reused else soak_cpus | other_cpus),
            'all': None}
    evidence = dict(reuse_keys)
    for step in steps:
        evidence.update(step.evidence)
    evidence = {key: (os.path.relpath(out / value, evidence_path.parent) if isinstance(value, str) and key not in reuse_keys
                      else [os.path.relpath(out / v, evidence_path.parent) for v in value] if isinstance(value, list) else value)
                for key, value in evidence.items()}
    print(f'qualify-host: candidate {candidate}; soak CPUs {cpus["soak"]}, other CPUs {cpus["other"]}')
    for step, reason in reused.items():
        print(f'  reused {step}: {reason}')
    for step in steps:
        where = {'soak': f'taskset -c {cpus["soak"]}', 'other': f'taskset -c {cpus["other"]}', 'all': 'alone'}[step.cpus]
        if step.command:
            env = ' '.join(f'{k}={v}' for k, v in step.env.items())
            command = shlex.join('python3' if part == PY else str(part) for part in step.command)
            print(f'  step {step.phase} {step.name} [{where}{", background" if step.background else ""}]: {env + " " if env else ""}{command}')
        else:
            print(f'  step {step.phase} {step.name}: (no command)')
    print(f'  then merge the evidence keys into {evidence_path}')
    print('  evidence keys: ' + ', '.join(evidence))
    if problems:
        for problem in problems:
            print(f'qualify-host: {problem}', file=sys.stderr)
        if not a.dry_run:
            return 2
    if a.dry_run:
        return 0
    keys = {step.name: [key for key in step.evidence if key not in reuse_keys] for step in steps}
    return run(steps, cpus, out, evidence, evidence_path, keys)


def run(steps, cpus, out, evidence, evidence_path, keys=None):
    """Run the steps; the evidence keys of a step that did not run (the evidence build failed) are not written."""
    logs = out / 'qualify-logs'
    logs.mkdir(parents=True, exist_ok=True)
    failed, background, started = [], [], time.monotonic()

    def launch(step):
        command = [str(part) for part in step.command]
        if step.cpus != 'all':
            command = ['taskset', '-c', cpus[step.cpus]] + command
        env = dict(os.environ, **step.env)
        log = (logs / f'{step.name}.log').open('w')
        stdout = (open(step.capture, 'w') if step.capture else log)
        print(f'qualify-host: {step.name} started ({time.monotonic() - started:.0f} s)', flush=True)
        return subprocess.Popen(command, cwd=ROOT, env=env, stdout=stdout, stderr=log), log, stdout

    not_run = []
    for step in steps:
        if step.phase == 4:
            continue  # after the soak
        if failed[:1] == ['native']:
            print(f'qualify-host: {step.name} not run: the evidence build failed', flush=True)
            not_run.append(step.name)
            continue
        if step.action:
            try:
                step.action()
            except (OSError, RuntimeError, tarfile.TarError) as error:
                print(f'qualify-host: {step.name} FAILED: {error}', flush=True)
                failed.append(step.name)
            continue
        process, log, stdout = launch(step)
        if step.background:
            background.append((step, process, log, stdout))
            continue
        code = process.wait()
        log.close()
        if stdout is not log:
            stdout.close()
        passed = step.judge(code)
        print(f'qualify-host: {step.name} {"passed" if passed else "FAILED"} ({time.monotonic() - started:.0f} s)', flush=True)
        if not passed:
            failed.append(step.name)
    for step, process, log, stdout in background:
        code = process.wait()
        log.close()
        passed = step.judge(code)
        print(f'qualify-host: {step.name} {"passed" if passed else "FAILED"} ({time.monotonic() - started:.0f} s)', flush=True)
        if not passed:
            failed.append(step.name)
    for step in steps:
        if step.phase != 4:
            continue
        if 'native' in not_run or failed[:1] == ['native']:
            not_run.append(step.name)
            continue
        try:
            step.action()
        except OSError as error:
            print(f'qualify-host: {step.name} FAILED: {error}', flush=True)
            failed.append(step.name)
    merged = {}
    if evidence_path.is_file():
        merged = json.loads(evidence_path.read_text())
    skipped = {key for name in not_run for key in (keys or {}).get(name, [])}
    merged.update({key: value for key, value in evidence.items() if key not in skipped})
    evidence_path.write_text(json.dumps(merged, indent=2) + '\n')
    print(f'qualify-host: wrote {len(evidence) - len(skipped & set(evidence))} keys to {evidence_path}' + (f'; failed: {", ".join(failed)}' if failed else ''))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
