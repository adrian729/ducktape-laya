#!/usr/bin/env python3
"""Merge the shard reports of one CI build into one validation report (§3.4 of the build-time plan).

  merge-validation.py --profile ci-full|ci-compiler --output DIR --build BUILD_DIR SHARD_DIR...

BUILD_DIR is the build directory restored from the build job's tarball (its build-phase/ holds the build's
report and logs); each SHARD_DIR is a `validate.py --phase test --from BUILD_DIR --checks ...` output, with
cross-host/ in the one shard that writes that side output. The merge refuses (exit 1, no output) unless every
shard passed, all of them and the build agree on the source, fixtures, library, addon, CMake cache, platform and
registered tests, their checks are disjoint and together exactly PROFILE_CHECKS[profile] (scripts/release_targets.py;
bindings:a and bindings:b, both required, together cover bindings),
every run label of every check ran with exit 0 (or is its listed platform skip) and no shard ran a label its checks do
not own, the C results of the two parity halves are byte-identical, and cross-host/ comes from exactly one shard for a
linux-x64 ci-full merge (none otherwise) with the build's library in its bundle.

Gates are copied from the shard that owns their check, never recomputed: native_contracts and abi from ctest;
original_reference, upstream_reference and additional_model_gates from references; binding_conformance passes only if
both parity halves and all six binding suites exited 0 (its run names bindings-a and bindings-b). Every other gate stays
not_run. Outputs: report.json (phase merged, selection.profile, shards), outputs.json in the single-run key order,
parity-timing.json ({"halves": {"a", "b"}}), the reference reports, ctest-junit.xml, configure.log and build.log, one
<label>.log per run label, shard-<check>.log per check, packages/ and cross-host/ copied verbatim.
"""
import argparse
import copy
import hashlib
import json
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from release_targets import (ADDITIONAL_MODEL_RUNS, BINDING_HALVES, PLATFORM_SKIPS, POLICY_LABELS, PROFILE_CHECKS, SHARD_EXTRA_RUNS,  # noqa: E402
                             SHARD_RUNS, TOOLING_REASON, fold_binding_halves)

# tests/parity.py's consumer order: outputs.json keys follow it.
CONSUMERS = ('C', 'C++', 'Rust', 'Node', 'Python', 'Java', '.NET', 'Go')
BINDING_SUITES = SHARD_RUNS['bindings']
IDENTITY = ('source_commit', 'source_tracked_dirty', 'source_files_sha256', 'fixtures_sha256', 'library_sha256', 'addon_sha256',
            'cmake_cache_sha256', 'platform', 'ctest_registered', 'mode', 'model', 'test_model_path', 'runtime', 'build_options')
# Gates and report parts each shard check owns.
OWNED_GATES = {'ctest': ('native_contracts', 'abi'), 'references': ('original_reference', 'upstream_reference')}
OWNED_FILES = {'ctest': ('ctest-junit.xml',), 'references': ('original-reference.json', 'upstream-reference.json')}
OWNED_FIELDS = {'ctest': ('ctest_ran', 'ctest_selection'), 'references': ('additional_model_gates',)}
CROSS_HOST = 'cross-host'
NOT_SELECTED = 'not selected'


class Refused(Exception):
    pass


def need(condition, message):
    if not condition:
        raise Refused(message)


def read_json(path):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError) as error:
        raise Refused(f'{path}: {error}') from None


def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def slug(check):
    return check.replace(':', '-')


def allowed_runs(check):
    return set(SHARD_RUNS[check]) | set(SHARD_EXTRA_RUNS.get(check, ()))


def owns(check, label):
    if label in allowed_runs(check):
        return True
    return check == 'references' and label.startswith(ADDITIONAL_MODEL_RUNS)


def merge_outputs(a, b):
    """outputs.json of a full run from the two halves: the C results must be byte-identical, as must everything that is
    not a consumer result; the consumers are put back in the single-run order."""
    need(list(a) == list(b), f'parity halves: report keys differ ({list(a)} and {list(b)})')
    for key in a:
        if key not in ('fixtures', 'open_config'):
            need(a[key] == b[key], f'parity halves: {key} differs')
    need(list(a['fixtures']) == list(b['fixtures']), 'parity halves: the fixtures differ')
    merged = {}
    for key in a:
        if key == 'fixtures':
            merged[key] = {}
            for fixture in a['fixtures']:
                left, right = a['fixtures'][fixture], b['fixtures'][fixture]
                need('C' in left and 'C' in right and json.dumps(left['C'], sort_keys=True) == json.dumps(right['C'], sort_keys=True),
                     f'parity halves: the C results of {fixture} differ')
                overlap = (set(left) & set(right)) - {'C'}
                need(not overlap, f'parity halves: {fixture} ran {sorted(overlap)} in both halves')
                merged[key][fixture] = {consumer: (left.get(consumer) if consumer in left else right[consumer])
                                        for consumer in CONSUMERS if consumer in left or consumer in right}
                need(list(merged[key][fixture]) == list(CONSUMERS), f'parity halves: {fixture} misses {sorted(set(CONSUMERS) - set(merged[key][fixture]))}')
        elif key == 'open_config':
            left, right = a[key], b[key]
            need({k: v for k, v in left.items() if k != 'results'} == {k: v for k, v in right.items() if k != 'results'},
                 'parity halves: the open-config pass differs')
            need(json.dumps(left['results'].get('C'), sort_keys=True) == json.dumps(right['results'].get('C'), sort_keys=True) and 'C' in left['results'],
                 'parity halves: the C results of the open-config pass differ')
            results = {consumer: (left['results'][consumer] if consumer in left['results'] else right['results'][consumer])
                       for consumer in CONSUMERS if consumer in left['results'] or consumer in right['results']}
            need(list(results) == list(CONSUMERS), 'parity halves: the open-config pass misses consumers')
            merged[key] = {k: (results if k == 'results' else v) for k, v in left.items()}
        else:
            merged[key] = a[key]
    return merged


def merge(profile, build, shard_dirs, output):
    need(profile in PROFILE_CHECKS, f'unknown profile {profile}')
    built = read_json(build / 'build-phase' / 'report.json')
    need(built.get('phase') == 'build' and built.get('selected_checks') == 'passed' and 'error' not in built,
         f'{build}: not a passed build phase')
    shards = {}
    for directory in shard_dirs:
        name = directory.name
        need(name not in shards, f'two shards named {name}')
        report = read_json(directory / 'report.json')
        need(report.get('phase') == 'test', f'{name}: phase {report.get("phase")}, not a --phase test shard')
        need(report.get('selected_checks') == 'passed' and 'error' not in report,
             f'{name}: selected checks {report.get("selected_checks")}: {report.get("error", "")}')
        for key in IDENTITY:
            need(report.get(key) == built.get(key), f'{name}: {key} differs from the build\'s')
        need(isinstance(report.get('checks'), list) and report['checks'], f'{name}: no checks')
        shards[name] = (directory, report)
    need(shards, 'no shards')
    # The checks: disjoint, and together exactly the profile's.
    owner = {}
    for name, (_, report) in shards.items():
        for check in report['checks']:
            need(check not in owner, f'{check} ran in both {owner.get(check)} and {name}')
            owner[check] = name
    # The binding suites run whole (`bindings`) or in two halves, both required: together they cover `bindings`.
    need(not ('bindings' in owner and set(BINDING_HALVES) & set(owner)), 'bindings ran both whole and in halves')
    need(len(set(BINDING_HALVES) & set(owner)) != 1,
         f'only one binding half ran ({", ".join(sorted(set(BINDING_HALVES) & set(owner)))}): bindings:a and bindings:b are both required')
    covered = fold_binding_halves(owner)
    need(sorted(covered) == sorted(PROFILE_CHECKS[profile]), f'the shards ran {sorted(owner)}, not the {profile} checks {sorted(PROFILE_CHECKS[profile])}')
    platform = built.get('platform')
    # The runs: every label of every check with exit 0 (or its platform skip), each label owned by its shard's checks.
    runs = {label: built['runs'][label] for label in ('configure', 'build')}
    run_owner = {}
    for name, (_, report) in shards.items():
        for label, run in report['runs'].items():
            need(any(owns(check, label) for check in report['checks']), f'{name}: ran {label}, which none of its checks {report["checks"]} owns')
            need(run.get('exit_code') == 0, f'{name}: {label} exited {run.get("exit_code")}')
            need(label not in runs, f'{label} ran in two shards')
            runs[label] = run
            run_owner[label] = name
        for check in report['checks']:
            for label in SHARD_RUNS[check]:
                rule = PLATFORM_SKIPS.get(label)
                skipped = rule and platform in rule[0] and report['skipped'].get(label) == rule[1]
                need(label in report['runs'] or skipped, f'{name}: {check} did not run {label}')
    # Cross-host: a side output of exactly one shard for a linux-x64 ci-full merge, of none otherwise.
    with_cross = [name for name, (directory, _) in shards.items() if (directory / CROSS_HOST).exists()]
    if profile == 'ci-full' and platform == 'linux-x64':
        need(len(with_cross) == 1, f'linux-x64: cross-host/ must come from exactly one shard, not {with_cross or "none"}')
        cross = shards[with_cross[0]][0] / CROSS_HOST
        bundle = cross / 'bundle' / 'libducktape_laya.so'
        need(bundle.is_file() and (cross / 'record.json').is_file(), f'{with_cross[0]}: cross-host/ lacks bundle/libducktape_laya.so or record.json')
        need(sha256(bundle) == built['library_sha256'], f'{with_cross[0]}: the cross-host bundle does not hold the build\'s library')
    else:
        need(not with_cross, f'{platform} {profile}: cross-host/ is a side output of the linux-x64 candidate only ({with_cross})')
    # Parity: the halves joined.
    halves = {}
    for half in ('a', 'b'):
        directory, report = shards[owner[f'parity:full-{half}']]
        halves[half] = (read_json(directory / f'outputs-{half}.json'), read_json(directory / f'parity-timing-{half}.json'))
        need(halves[half][1].get('bindings') == half and halves[half][1].get('profile') == 'full', f'parity:full-{half}: its timing names '
             f'{halves[half][1].get("profile")}/{halves[half][1].get("bindings")}')
    outputs = merge_outputs(halves['a'][0], halves['b'][0])

    # The merged report: the build's, with each owned part copied from its shard.
    merged = copy.deepcopy(built)
    merged['gates'] = {gate: {'status': 'not_run'} for gate in built['gates']}
    for check, gates in OWNED_GATES.items():
        report = shards[owner[check]][1]
        for gate in gates:
            merged['gates'][gate] = copy.deepcopy(report['gates'][gate])
    for check, fields in OWNED_FIELDS.items():
        report = shards[owner[check]][1]
        for field in fields:
            merged[field] = copy.deepcopy(report.get(field))
    binding_checks = [c for c in ('bindings', *BINDING_HALVES) if c in owner]
    selected = {name for c in binding_checks for name in shards[owner[c]][1]['selection']['bindings']}
    need(selected == set(BINDING_SUITES), f'the binding shards selected {sorted(selected)}, not {sorted(BINDING_SUITES)}')
    binding_selection = [name for name in BINDING_SUITES if name in selected]
    passed = all(runs.get(label, {}).get('exit_code') == 0 for label in ('bindings-a', 'bindings-b', *BINDING_SUITES))
    merged['gates']['binding_conformance'] = {'status': 'passed' if passed else 'failed', 'run': ['bindings-a', 'bindings-b']}
    merged['runs'] = runs
    # Skips: the owner's reasons; the selection skips of checks another shard ran are dropped; policy checks of the
    # tooling job are listed with its reason.
    skipped = {}
    for _, report in [(None, built)] + [shards[name] for name in shards]:
        for key, reason in report['skipped'].items():
            if key not in skipped or (skipped[key].startswith(NOT_SELECTED) and not reason.startswith(NOT_SELECTED)):
                skipped[key] = reason
    skipped = {key: reason for key, reason in skipped.items() if key not in runs and key.removeprefix('policy:') not in runs}
    if 'bindings-a' in runs and 'bindings-b' in runs:
        skipped.pop('bindings', None)
    if any(label in runs for label in POLICY_LABELS):
        skipped.pop('policy', None)
    for key in list(skipped):
        if key.startswith('policy:') and key.split(':', 1)[1] not in runs:
            skipped[key] = TOOLING_REASON
    merged['skipped'] = skipped
    hosts = [built.get('host') or {}] + [report.get('host') or {} for _, report in shards.values()]
    merged['host'] = {**(built.get('host') or {}), 'github_actions': all(host.get('github_actions') is True for host in hosts)}
    ctest = shards[owner['ctest']][1]
    merged['selection'] = {**built['selection'], 'profile': profile, 'checks': list(PROFILE_CHECKS[profile]),
                           'bindings': binding_selection, 'native_tests': True,
                           'policy': 'platform' if 'policy-platform' in owner else 'none', 'packages': 'packages' in owner,
                           'parity_profile': 'full', 'test_jobs': ctest['selection']['test_jobs']}
    merged.update(phase='merged', checks=list(PROFILE_CHECKS[profile]), selected_checks='passed')
    merged.pop('error', None)
    merged['shards'] = {name: {'checks': report['checks'], 'report_sha256': sha256(directory / 'report.json'), 'host': report.get('host'),
                               'side_outputs': [CROSS_HOST] if name in with_cross else []}
                        for name, (directory, report) in shards.items()}
    merged['release_ready'] = all(gate['status'] == 'passed' for gate in merged['gates'].values())

    # Files.
    (output / 'report.json').write_text(json.dumps(merged, indent=2) + '\n')
    (output / 'outputs.json').write_text(json.dumps(outputs, indent=2) + '\n')
    (output / 'parity-timing.json').write_text(json.dumps({'halves': {half: halves[half][1] for half in ('a', 'b')}}, indent=2) + '\n')
    for name in ('configure.log', 'build.log'):
        shutil.copyfile(build / 'build-phase' / name, output / name)
    for label, name in run_owner.items():
        log = shards[name][0] / (label + '.log')
        need(log.is_file(), f'{name}: {label}.log missing')
        shutil.copyfile(log, output / (label + '.log'))
    for check, files in OWNED_FILES.items():
        directory = shards[owner[check]][0]
        for file in files:
            if (directory / file).is_file():
                shutil.copyfile(directory / file, output / file)
    references = shards[owner['references']][0]
    for path in sorted(references.glob('*-reference-*.json')):
        shutil.copyfile(path, output / path.name)
    if 'packages' in owner and (shards[owner['packages']][0] / 'packages').is_dir():
        shutil.copytree(shards[owner['packages']][0] / 'packages', output / 'packages', symlinks=True)
    if with_cross:
        shutil.copytree(shards[with_cross[0]][0] / CROSS_HOST, output / CROSS_HOST, symlinks=True)
    for check, name in owner.items():
        directory, report = shards[name]
        lines = [f'shard {name}: checks {", ".join(report["checks"])}; report.json sha256 {sha256(directory / "report.json")}',
                 f'host: {json.dumps(report.get("host"), sort_keys=True)}']
        lines += [f'{label}: exit {run["exit_code"]}, {run["seconds"]} s' for label, run in report['runs'].items()]
        (output / f'shard-{slug(check)}.log').write_text('\n'.join(lines) + '\n')
    return merged


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--profile', choices=sorted(PROFILE_CHECKS), required=True)
    p.add_argument('--output', type=pathlib.Path, required=True)
    p.add_argument('--build', type=pathlib.Path, required=True, help='The build directory (with build-phase/report.json)')
    p.add_argument('shards', type=pathlib.Path, nargs='+')
    a = p.parse_args(argv)
    output = a.output.resolve()
    if output.exists():
        p.error(f'{output} exists')
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = pathlib.Path(tempfile.mkdtemp(prefix=output.name + '.partial-', dir=output.parent))
    try:
        merged = merge(a.profile, a.build.resolve(), [shard.resolve() for shard in a.shards], staging)
    except Refused as error:
        shutil.rmtree(staging, ignore_errors=True)
        print(f'merge-validation: refused: {error}', file=sys.stderr)
        return 1
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    staging.rename(output)
    print(json.dumps({'profile': a.profile, 'shards': sorted(merged['shards']), 'gates': {k: v['status'] for k, v in merged['gates'].items()},
                      'report': str(output / 'report.json')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
