#!/usr/bin/env python3
"""The CI workflows against the data that drives them (offline, no model).

Checks that .github/workflows/native.yml and platforms.yml agree with scripts/ci_paths.py,
and that the check lists of the scripts agree with each other:

- every workflow job's `name:` is a JOBS key of that workflow, and every JOBS entry of the
  workflow is some workflow job's name in each of its profiles, exactly once per profile. A
  workflow job serves the profiles its `if:` mode condition selects
  (`needs.changes.outputs.mode == 'candidate'` or `!= 'candidate'`; none: every profile of its
  JOBS entry). A matrix job named from the matrix counts by its rendered names: the JOBS keys
  its name template matches, and for the shard jobs (`name: ${{ matrix.job }}`, env
  DTL_PLATFORM) the jobs of SHARDS[mode][platform] (packages-<platform> is a workflow job of
  its own);
- the `needs:` edges of each workflow job, its needed job ids mapped to the names they serve
  in the same profile, equal JOBS[name]['needs'][profile] (`changes`, which every job needs
  for its `if:`, is compared separately);
- every job other than `changes` needs `changes` and has the run-set condition
  `!cancelled() && !contains(needs.*.result, 'failure') && contains(fromJSON(needs.changes.outputs.run), '<name>')`
  (a matrix job: its matrix output `!= '[]'`);
- the `jobs` check lists of each candidate platform in SHARDS are disjoint and together equal
  release_targets.PROFILE_CHECKS[profile] (bindings:a and bindings:b together stand for bindings); side_outputs is {'native-linux-x64': ['cross-host']}
  for linux-x64 and empty everywhere else;
- set(validate.FULL_CHECKS) <= set(ci_paths.VALIDATE_CHECKS);
- validate.FULL_POLICY_CHECKS and release_targets.POLICY_LABELS name exactly the check-policy.py labels;
- every label of scripts/check-policy.py has POLICY_CHECKS inputs, and packaging-identities and
  browser-backend-identities keep their labels;
- compiler caches (hendrikmuhs/ccache-action, inline or through native.yml's &compiler-cache, whose
  key is env DTL_CACHE_KEY): every job that saves has its own key per variant, no key extends
  another (a restore prefix `KEY-` would match the other job's entries), the prefix
  scripts/ci-cache-prune.py groups a saved key under is that job's alone (it keeps the newest two
  per prefix and ref, so a shared prefix lets one job evict another's cache), and every
  restore-only key and DTL_CACHE_FALLBACK line names a key some job saves.

The workflows are read with PyYAML when it is installed, and otherwise with a reader for the
job-level keys these files use (name, needs, if, env, strategy.matrix.include); with PyYAML
both readings are compared, so the fallback stays faithful.

Usage: python3 tests/ci-contracts.py [--root DIR]
"""
import argparse
import importlib.util
import pathlib
import re
import subprocess
import sys

WORKFLOWS = ('native.yml', 'platforms.yml')
# The modes of `needs.changes.outputs.mode` and the JOBS profiles they run.
MODE_PROFILES = {'verify': {'push', 'real_model'}, 'candidate': {'candidate'}, 'qualify': {'qualify'},
                 'warm-cache': {'warm'}, 'experiment': {'experiment'}}
SHARD_MODES = ('real_model', 'candidate')
IF_PREFIX = "!cancelled() && !contains(needs.*.result, 'failure')"
MATRIX_OUTPUT = re.compile(r"^\$\{\{\s*fromJSON\(needs\.changes\.outputs\.([A-Za-z0-9_]+)\)\s*\}\}$")
MATRIX_EXPRESSION = re.compile(r'\$\{\{\s*matrix\.[A-Za-z0-9_]+\s*\}\}')
MODE_CONDITION = re.compile(r"needs\.changes\.outputs\.mode\s*(==|!=)\s*'([a-z-]+)'")


# ---------------------------------------------------------------- reading the workflows

def unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in '\'"':
        return value[1:-1].replace("''", "'") if value[0] == "'" else value[1:-1]
    return value


def strip_comment(line):
    """The line without a trailing comment (a '#' after whitespace, outside quotes)."""
    quote = None
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = None
        elif char in '\'"':
            quote = char
        elif char == '#' and (index == 0 or line[index - 1] in ' \t'):
            return line[:index].rstrip()
    return line.rstrip()


def flow_list(text):
    inner = text.strip()
    if not (inner.startswith('[') and inner.endswith(']')):
        raise ValueError(f'not a flow sequence: {text!r}')
    return [unquote(item) for item in inner[1:-1].split(',') if item.strip()]


def read_jobs_plain(text):
    """{job id: {'name', 'needs', 'if', 'env', 'include'}} from the job-level keys, without PyYAML.

    Covers the subset the workflows use: two-space indentation, `needs` as a scalar, a flow
    sequence (possibly over several lines) or a block sequence, `if` and `name` as one-line
    scalars, `env` as a block mapping of scalars (an anchor on it allowed), and
    `strategy.matrix.include` as a one-line scalar. Anything else at these keys is an error.
    """
    lines = text.splitlines()
    try:
        start = lines.index('jobs:')
    except ValueError:
        raise ValueError('no top-level jobs: key')
    jobs, current, index = {}, None, start + 1
    while index < len(lines):
        raw = lines[index]
        line = strip_comment(raw)
        index += 1
        if not line.strip():
            continue
        if not raw.startswith(' '):
            break  # the next top-level key
        job = re.fullmatch(r'  ([A-Za-z0-9_-]+):', line)
        if job:
            current = jobs.setdefault(job[1], {'name': None, 'needs': [], 'if': None, 'env': {}, 'include': None})
            continue
        key = re.fullmatch(r'    ([A-Za-z0-9_-]+):\s*(.*)', line)
        if not key or current is None:
            continue
        name, value = key[1], key[2]
        if name == 'name':
            current['name'] = unquote(value)
        elif name == 'if':
            if value in ('|', '>', '>-', '|-'):
                raise ValueError(f'block scalar if: is not supported by the plain reader (line {index})')
            current['if'] = unquote(value)
        elif name == 'needs':
            if value.startswith('['):
                while not value.rstrip().endswith(']'):
                    value += ' ' + strip_comment(lines[index]).strip()
                    index += 1
                current['needs'] = flow_list(value)
            elif value:
                current['needs'] = [unquote(value)]
            else:
                while index < len(lines) and re.fullmatch(r'      - .*', strip_comment(lines[index])):
                    current['needs'].append(unquote(strip_comment(lines[index]).strip()[2:]))
                    index += 1
        elif name == 'env':
            if value and not re.fullmatch(r'&[A-Za-z0-9_-]+', value):
                raise ValueError(f'env: {value!r} is not supported by the plain reader (line {index})')
            while index < len(lines):
                entry = strip_comment(lines[index])
                if not entry.strip():
                    index += 1
                    continue
                pair = re.fullmatch(r'      ([A-Za-z_][A-Za-z0-9_]*):\s*(.*)', entry)
                if not pair:
                    break
                if pair[2] in ('>', '>-'):
                    raise ValueError(f'folded env value is not supported by the plain reader (line {index + 1})')
                index += 1
                if pair[2] in ('|', '|-'):
                    # A literal block: the following lines indented further than the key, joined by newlines.
                    block = []
                    while index < len(lines) and (lines[index].startswith('        ') or not lines[index].strip()):
                        block.append(lines[index][8:].rstrip())
                        index += 1
                    while block and not block[-1]:
                        block.pop()
                    current['env'][pair[1]] = '\n'.join(block) + ('\n' if pair[2] == '|' else '')
                    continue
                current['env'][pair[1]] = unquote(pair[2])
        elif name == 'strategy':
            while index < len(lines) and (not lines[index].strip() or lines[index].startswith('      ')):
                include = re.fullmatch(r'\s+include:\s*(.+)', strip_comment(lines[index]))
                if include:
                    current['include'] = unquote(include[1])
                index += 1
    return jobs


def read_jobs_yaml(text, yaml):
    document = yaml.safe_load(text)
    jobs = {}
    for job_id, body in document['jobs'].items():
        needs = body.get('needs', [])
        matrix = (body.get('strategy') or {}).get('matrix') or {}
        include = matrix.get('include') if isinstance(matrix, dict) else None
        jobs[job_id] = {'name': body.get('name'), 'needs': [needs] if isinstance(needs, str) else list(needs),
                        'if': body.get('if'), 'env': {key: '' if value is None else str(value).lower() if isinstance(value, bool) else str(value)
                                                      for key, value in (body.get('env') or {}).items()},
                        'include': include if isinstance(include, str) else None}
    return jobs


def read_jobs(path, problems):
    text = path.read_text()
    plain = read_jobs_plain(text)
    try:
        import yaml
    except ImportError:
        return plain
    parsed = read_jobs_yaml(text, yaml)
    if parsed != plain:
        for job_id in sorted(set(parsed) | set(plain)):
            if parsed.get(job_id) != plain.get(job_id):
                problems.append(f'{path.name}: the plain reader reads job {job_id} as {plain.get(job_id)}, PyYAML as {parsed.get(job_id)}')
    return parsed


# ---------------------------------------------------------------- loading the data

def load_module(path, name):
    if not path.is_file():
        raise SystemExit(f'ci-contracts: {path} is missing')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except SystemExit as error:
        raise RuntimeError(f'importing {path} exited ({error}); it needs a main() guard') from None
    return module


def policy_labels(root):
    """The labels scripts/check-policy.py --list prints: the first word of each line that is a label."""
    result = subprocess.run([sys.executable, str(root / 'scripts/check-policy.py'), '--list'], capture_output=True, text=True, cwd=root)
    if result.returncode:
        raise RuntimeError(f'check-policy.py --list exited {result.returncode}: {result.stderr.strip()}')
    labels = []
    for line in result.stdout.splitlines():
        words = line.split()
        if words and re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', words[0]) and words[0] not in labels:
            labels.append(words[0])
    return labels


# ---------------------------------------------------------------- the checks

def template_pattern(template):
    """A regex for the names a job name with matrix expressions renders to."""
    parts = MATRIX_EXPRESSION.split(template)
    return re.compile('^' + '([a-z0-9]+(?:-[a-z0-9]+)*)'.join(re.escape(part) for part in parts) + '$')


def served(workflow, job_id, job, jobs_table, shards, problems):
    """{name: set of profiles} the workflow job serves."""
    template = job['name']
    if not template:
        problems.append(f'{workflow}: job {job_id} has no name:')
        return {}
    include = MATRIX_OUTPUT.match(job['include'] or '')
    names = {}
    if MATRIX_EXPRESSION.search(template):
        if not include:
            problems.append(f'{workflow}: job {job_id} is named from its matrix but its matrix is not a changes output')
            return {}
        if re.fullmatch(r'\$\{\{\s*matrix\.job\s*\}\}', template):
            platform = job['env'].get('DTL_PLATFORM')
            if not platform:
                problems.append(f'{workflow}: shard job {job_id} has no DTL_PLATFORM')
                return {}
            for mode in SHARD_MODES:
                for name in (shards.get(mode, {}).get(platform, {}).get('jobs') or {}):
                    if name != f'packages-{platform}':
                        names.setdefault(name, set()).add(mode)
        else:
            pattern = template_pattern(template)
            matched = [key for key, entry in jobs_table.items() if entry.get('workflow') == workflow and pattern.match(key)]
            if not matched:
                problems.append(f'{workflow}: no JOBS key matches the name {template!r} of job {job_id}')
            for name in matched:
                names[name] = set(jobs_table[name].get('profiles', []))
    else:
        names[template] = None
    result = {}
    conditions = MODE_CONDITION.findall(job['if'] or '')
    for name, shard_profiles in names.items():
        if name == 'changes':
            continue  # each workflow's own `changes` job decides what runs; it is no verification unit
        entry = jobs_table.get(name)
        if entry is None:
            problems.append(f'{workflow}: job {job_id} is named {name!r}, which is not a JOBS key')
            continue
        if entry.get('workflow') != workflow:
            problems.append(f'{workflow}: job {name} belongs to {entry.get("workflow")} in JOBS')
        profiles = set(entry.get('profiles', []))
        if shard_profiles is not None:
            missing = shard_profiles - profiles
            if missing:
                problems.append(f'{workflow}: {name} is a {"/".join(sorted(missing))} shard in SHARDS, not a profile of its JOBS entry')
            profiles &= shard_profiles
        for operator, mode in conditions:
            if mode not in MODE_PROFILES:
                problems.append(f'{workflow}: job {job_id} tests an unknown mode {mode!r}')
                continue
            profiles = profiles & MODE_PROFILES[mode] if operator == '==' else profiles - MODE_PROFILES[mode]
        result[name] = profiles
    return result


def check_workflow(root, workflow, jobs_table, shards, problems):
    jobs = read_jobs(root / '.github/workflows' / workflow, problems)
    serving = {job_id: served(workflow, job_id, job, jobs_table, shards, problems) for job_id, job in jobs.items()}

    # Every JOBS entry of the workflow, each of its profiles served exactly once.
    coverage = {}
    for job_id, names in serving.items():
        for name, profiles in names.items():
            for profile in profiles:
                coverage.setdefault((name, profile), []).append(job_id)
    for name, entry in jobs_table.items():
        if entry.get('workflow') != workflow or name == 'changes':
            continue
        for profile in entry.get('profiles', []):
            ids = coverage.get((name, profile), [])
            if len(ids) != 1:
                problems.append(f'{workflow}: {name} in profile {profile} is served by {len(ids)} workflow jobs {ids}, not one')

    # needs per profile.
    def names_of(job_id, profile):
        return {name for name, profiles in serving.get(job_id, {}).items() if profile in profiles}

    for job_id, job in jobs.items():
        if job_id == 'changes' or job['name'] == 'changes':
            continue
        unknown = [need for need in job['needs'] if need not in jobs]
        if unknown:
            problems.append(f'{workflow}: job {job_id} needs unknown jobs {unknown}')
        if 'changes' not in job['needs']:
            problems.append(f'{workflow}: job {job_id} does not need changes')
        for name, profiles in serving[job_id].items():
            for profile in sorted(profiles):
                actual = set().union(*(names_of(need, profile) for need in job['needs'] if need != 'changes')) if job['needs'] else set()
                expected = set(jobs_table[name].get('needs', {}).get(profile, [])) - {'changes'}
                if actual != expected:
                    problems.append(f'{workflow}: {name} ({profile}, job {job_id}) needs {sorted(actual)}, '
                                    f'JOBS says {sorted(expected)}')

        # The run-set condition of §3.1.
        condition = ' '.join((job['if'] or '').split())
        include = MATRIX_OUTPUT.match(job['include'] or '')
        if IF_PREFIX not in condition:
            problems.append(f'{workflow}: job {job_id} if: lacks {IF_PREFIX!r}')
        if include:
            if f"needs.changes.outputs.{include[1]} != '[]'" not in condition:
                problems.append(f"{workflow}: matrix job {job_id} if: lacks needs.changes.outputs.{include[1]} != '[]'")
        elif job['name'] and not MATRIX_EXPRESSION.search(job['name']):
            if f"contains(fromJSON(needs.changes.outputs.run), '{job['name']}')" not in condition:
                problems.append(f"{workflow}: job {job_id} if: lacks contains(fromJSON(needs.changes.outputs.run), '{job['name']}')")
    return len(jobs)


def check_shards(shards, profile_checks, problems):
    for mode in SHARD_MODES:
        for platform, entry in (shards.get(mode) or {}).items():
            side = entry.get('side_outputs') or {}
            expected_side = {'native-linux-x64': ['cross-host']} if (mode, platform) == ('candidate', 'linux-x64') else {}
            if side != expected_side:
                problems.append(f'SHARDS[{mode}][{platform}] side_outputs {side}, expected {expected_side}')
            checks = [check for job_checks in (entry.get('jobs') or {}).values() for check in job_checks]
            if 'cross-host' in checks:
                problems.append(f'SHARDS[{mode}][{platform}]: cross-host is a side output, not a check')
            if mode != 'candidate':
                continue
            profile = entry.get('profile')
            if profile not in profile_checks:
                problems.append(f'SHARDS[candidate][{platform}] profile {profile!r} is not in PROFILE_CHECKS')
                continue
            duplicates = sorted({check for check in checks if checks.count(check) > 1})
            if duplicates:
                problems.append(f'SHARDS[candidate][{platform}]: checks in more than one job: {duplicates}')
            # bindings:a + bindings:b (both, in different jobs or one) cover `bindings`; a lone half covers nothing.
            if {'bindings:a', 'bindings:b'} <= set(checks):
                checks = [check for check in checks if check not in ('bindings:a', 'bindings:b')] + ['bindings']
            if set(checks) != set(profile_checks[profile]):
                problems.append(f'SHARDS[candidate][{platform}] checks {sorted(set(checks))} are not PROFILE_CHECKS[{profile!r}] '
                                f'{sorted(profile_checks[profile])}')


# ---------------------------------------------------------------- compiler caches

CCACHE_ACTION = 'hendrikmuhs/ccache-action@'


def job_blocks(text):
    """{job id: the job's lines} of the top-level jobs: mapping."""
    lines, blocks, current = text.splitlines(), {}, None
    start = lines.index('jobs:')
    for line in lines[start + 1:]:
        if line and not line.startswith(' '):
            break
        job = re.fullmatch(r'  ([A-Za-z0-9_-]+):\s*', line)
        if job:
            current = blocks.setdefault(job[1], [])
        elif current is not None:
            current.append(line)
    return blocks


def cache_steps(path, jobs):
    """[(job id, variant, key prefix, saves)] of each compiler-cache step of a workflow."""
    text = path.read_text()
    steps = []
    for job_id, block in job_blocks(text).items():
        env = jobs.get(job_id, {}).get('env', {})
        joined = '\n'.join(block)
        if re.search(r'^\s+- [&*]compiler-cache\s*$', joined, re.M):
            variant = 'sccache' if 'sccache' in env.get('DTL_LAUNCHER', '') else 'ccache'
            steps.append((job_id, variant, env.get('DTL_CACHE_KEY'), env.get('DTL_CACHE_SAVE') == 'true'))
            continue
        for at, line in enumerate(block):
            if CCACHE_ACTION not in line:
                continue
            indent = len(line) - len(line.lstrip(' - '))
            body = []
            for following in block[at + 1:]:
                if following.strip() and len(following) - len(following.lstrip()) <= indent - 2:
                    break
                body.append(following)
            fields = dict(re.findall(r'^[ \t]+([a-z-]+):[ \t]*(.*)$', '\n'.join(body), re.M))
            key = re.sub(r'-\$\{\{\s*hashFiles\(.*$', '', fields.get('key', '')).strip()
            variant = unquote(fields.get('variant', 'ccache'))
            steps.append((job_id, variant, key or None, unquote(fields.get('save', 'true')) != 'false'))
    return steps


def check_caches(root, read, problems):
    """The compiler-cache keys of both workflows against each other and scripts/ci-cache-prune.py's grouping."""
    native = (root / '.github/workflows/native.yml').read_text()
    if "key: ${{ env.DTL_CACHE_KEY }}-" not in native or '${{ env.DTL_CACHE_FALLBACK }}' not in native:
        problems.append('native.yml: &compiler-cache no longer keys on env.DTL_CACHE_KEY and restores env.DTL_CACHE_FALLBACK')
    prune = load_module(root / 'scripts/ci-cache-prune.py', 'ci_cache_prune')
    steps, fallbacks = [], []
    for workflow in WORKFLOWS:
        jobs = read[workflow]
        for job_id, variant, key, saves in cache_steps(root / '.github/workflows' / workflow, jobs):
            if not key:
                problems.append(f'{workflow} {job_id}: a compiler-cache step without a key')
                continue
            steps.append((f'{workflow} {job_id}', variant, key, saves))
            fallback = jobs[job_id]['env'].get('DTL_CACHE_FALLBACK', '')
            fallbacks += [(f'{workflow} {job_id}', variant, line.strip()) for line in fallback.splitlines() if line.strip()]
    savers = {}
    for job, variant, key, saves in steps:
        if saves:
            savers.setdefault((variant, key), []).append(job)
    for (variant, key), jobs in sorted(savers.items()):
        if len(jobs) > 1:
            problems.append(f'{variant} cache key {key} is saved by more than one job: {", ".join(jobs)}')
        saved = f'{variant}-{key}-{"0" * 64}-2026-10-25T05:17:46.069Z'
        if prune.prefix(saved) != f'{variant}-{key}':
            problems.append(f'ci-cache-prune.py groups {saved} under {prune.prefix(saved)}, not {variant}-{key}')
        for (other_variant, other), other_jobs in savers.items():
            if other_variant == variant and other != key and f'{other}-'.startswith(f'{key}-'):
                problems.append(f'{variant} cache key {other} ({", ".join(other_jobs)}) extends {key} ({", ".join(jobs)}): '
                                f'restoring {key} would match its entries')
    for job, variant, key, saves in steps:
        if not saves and (variant, key) not in savers:
            problems.append(f'{job}: restores {variant} key {key}, which no job saves')
    for job, variant, key in fallbacks:
        if (variant, key) not in savers:
            problems.append(f'{job}: DTL_CACHE_FALLBACK {key} is a {variant} key no job saves')
    return len(savers)


def check_shard_artifacts(root, shards, problems):
    """Each platform's evidence job downloads exactly its own shards: its pattern matches none of another platform's."""
    import fnmatch
    text = (root / '.github/workflows/native.yml').read_text()
    names = re.findall(r'^[ \t]+name: (shard[^ \n]*\$\{\{ env\.DTL_PLATFORM \}\}[^ \n]*)$', text, re.M)
    patterns = re.findall(r'^[ \t]+pattern: (shard[^ \n]*\$\{\{ env\.DTL_PLATFORM \}\}[^ \n]*)$', text, re.M)
    if not names or len(patterns) != 1:
        problems.append(f'native.yml: expected shard uploads and one shard download pattern, found {len(names)} and {len(patterns)}')
        return
    platforms = sorted({platform for mode in shards.values() for platform in mode})
    labels = ['ctest', 'packages', 'parity-full-a']
    for platform in platforms:
        pattern = patterns[0].replace('${{ env.DTL_PLATFORM }}', platform)
        for other in platforms:
            for name in names:
                artifact = re.sub(r'\$\{\{ matrix\.label \}\}', labels[0], name.replace('${{ env.DTL_PLATFORM }}', other))
                if fnmatch.fnmatchcase(artifact, pattern) != (other == platform):
                    problems.append(f'native.yml: the {platform} shard pattern {pattern} '
                                    f'{"matches" if other != platform else "misses"} {other}\'s shard {artifact}')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--root', type=pathlib.Path, default=pathlib.Path(__file__).resolve().parents[1], help='Repository root')
    a = p.parse_args(argv)
    root = a.root.resolve()
    sys.path.insert(0, str(root / 'scripts'))
    problems = []
    ci_paths = load_module(root / 'scripts/ci_paths.py', 'ci_paths')
    release_targets = load_module(root / 'scripts/release_targets.py', 'release_targets')
    validate = load_module(root / 'scripts/validate.py', 'validate_under_test')
    jobs_table, shards = ci_paths.JOBS, ci_paths.SHARDS

    count = sum(check_workflow(root, workflow, jobs_table, shards, problems) for workflow in WORKFLOWS)
    cache_keys = check_caches(root, {workflow: read_jobs(root / '.github/workflows' / workflow, problems) for workflow in WORKFLOWS}, problems)
    workflows = {entry.get('workflow') for entry in jobs_table.values()}
    if not workflows <= set(WORKFLOWS):
        problems.append(f'JOBS names workflows {sorted(workflows - set(WORKFLOWS))} that do not exist')
    check_shards(shards, release_targets.PROFILE_CHECKS, problems)
    check_shard_artifacts(root, shards, problems)

    missing = sorted(set(validate.FULL_CHECKS) - set(ci_paths.VALIDATE_CHECKS))
    if missing:
        problems.append(f'validate.FULL_CHECKS not classified in ci_paths.VALIDATE_CHECKS: {missing}')

    labels = policy_labels(root)
    for label in ('packaging-identities', 'browser-backend-identities'):
        if label not in labels:
            problems.append(f'check-policy.py has no {label} label')
    # --full and the tier-1 checker require every policy check: their hard-coded lists (M1) must name
    # exactly check-policy.py's labels, or a new check would escape the host-full evidence.
    for name, listed in (('validate.FULL_POLICY_CHECKS', validate.FULL_POLICY_CHECKS),
                         ('release_targets.POLICY_LABELS', release_targets.POLICY_LABELS)):
        if set(listed) != set(labels):
            problems.append(f'{name} differs from the check-policy.py labels: missing {sorted(set(labels) - set(listed))}, '
                            f'extra {sorted(set(listed) - set(labels))}')
    unclassified = sorted(label for label in labels if not ci_paths.POLICY_CHECKS.get(label))
    if unclassified:
        problems.append(f'check-policy.py labels without POLICY_CHECKS inputs: {unclassified}')

    for problem in problems:
        print('ci-contracts: ' + problem, file=sys.stderr)
    if problems:
        return 1
    print(f'ci-contracts: {count} workflow jobs and {len(jobs_table)} JOBS entries agree; SHARDS, FULL_CHECKS and '
          f'{len(labels)} policy labels agree; {cache_keys} saved cache keys are distinct')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
