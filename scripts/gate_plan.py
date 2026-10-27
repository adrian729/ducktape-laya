"""What a release qualification can know about its gates before any long step runs (scripts/check-candidate.py --plan).

Pure functions over the candidate's record and sources, so the checks run in seconds and are unit-tested without
evidence. The gate logic itself stays in check-candidate.py: the evidence keys the gates read are found by parsing it,
never listed a second time.
"""
import ast

# Calls whose first argument names an evidence key (check-candidate.py's helpers and Context's methods).
KEY_CALLS = {'load', 'long_gate', 'path', 'ci_json', 'ci_file', 'qualify_file'}


def _literals(node):
    """String constants of a literal (a tuple's first element when its items are tuples), else []."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.Tuple, ast.List)):
        found = []
        for item in node.elts:
            found += _literals(item.elts[0] if isinstance(item, (ast.Tuple, ast.List)) and item.elts else item)
        return found
    return []


def gate_sources(source):
    """(evidence keys the gates read, names passed to tested_source) found in a checker's source text.

    A key is a string literal passed to load/long_gate/ctx.path/ci_json/..., indexing or .get() of `paths`, or a name
    bound by a `for` over literals that is then passed to one of those. Keys built with f-strings (a target's
    `{platform}:native`) come from the CI download and are not found here."""
    keys, tested = set(), set()

    def add(arg, into, env):
        into.update(_literals(arg))
        if isinstance(arg, ast.Name):
            into.update(env.get(arg.id, []))

    def visit(node, env):
        if isinstance(node, ast.For):
            target = node.target.elts[0] if isinstance(node.target, ast.Tuple) else node.target
            if isinstance(target, ast.Name) and _literals(node.iter):
                env = {**env, target.id: _literals(node.iter)}  # the variable means these only inside this loop
        elif isinstance(node, ast.Call) and node.args:
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ''
            if name in KEY_CALLS:
                add(node.args[0], keys, env)
            elif name == 'tested_source' and len(node.args) > 1:
                add(node.args[1], tested, env)
            elif name == 'get' and getattr(node.func.value, 'id', '') == 'paths':
                add(node.args[0], keys, env)
        elif isinstance(node, ast.Subscript) and getattr(node.value, 'id', '') == 'paths':
            add(node.slice, keys, env)
        for child in ast.iter_child_nodes(node):
            visit(child, env)

    visit(ast.parse(source), {})
    return keys, tested


# Keys fetch-ci-evidence.py --qualify adds from the CI qualify run once the host gates are done (its QUALIFY_ARTIFACTS).
QUALIFY_KEYS = ('sanitizers', 'fuzz', 'upgrade')


def manual_keys(record):
    """Keys added after the host gates: the qualify run's (RELEASING.md step 7) and the maintainer's (steps 8 and 9)."""
    return set(QUALIFY_KEYS) | {'security_review'} | {f'{item["platform"]}:drift' for item in record['targets'] if item['tier'] != 1}


def plan_problems(record, pinned_runtime, sources, harness, planned_keys, supplied_keys, runtime=None, upgrade_report=None,
                  upgrade_sha256=None, primary_sha256=None):
    """Every inconsistency between the gates and the plan that is knowable before running anything.

    sources: the checker's source texts; harness: its HARNESS table; planned_keys: the evidence keys the host plan will
    write; supplied_keys: the keys already in evidence.json (the CI download); runtime: the commit the CI qualify run
    rehearses; upgrade_report: that run's report when already downloaded; upgrade_sha256/primary_sha256: the
    model-upgrade candidate model's and the primary model's manifest hashes."""
    problems = []
    used, tested = set(), set()
    for source in sources:
        keys, names = gate_sources(source)
        used |= keys
        tested |= names
    missing_harness = sorted(tested - set(harness))
    if missing_harness:
        problems.append(f'tested_source names {", ".join(missing_harness)} have no HARNESS entry (the reuse rules would raise KeyError at the end)')
    available = set(planned_keys) | set(supplied_keys) | manual_keys(record)
    unproduced = sorted(used - available)
    if unproduced:
        problems.append('gates read evidence that nothing will produce: ' + ', '.join(unproduced) +
                        ' (not in the host plan, the CI download or the hand-added keys)')
    # The runtime axis (upgrade_rollback: candidate_runtime != baseline_runtime) and the model axis.
    if not runtime:
        problems.append('no rehearsal runtime: the upgrade_rollback gate needs a runtime other than the pin')
    elif runtime == pinned_runtime:
        problems.append(f'the rehearsal runtime {runtime[:12]} is the pin: upgrade_rollback requires a runtime axis '
                        '(the qualify run must be dispatched with -f runtime=COMMIT past the pin)')
    if upgrade_report is not None and runtime:
        if upgrade_report.get('candidate_runtime') != runtime:
            problems.append(f'the downloaded upgrade report rehearsed {str(upgrade_report.get("candidate_runtime"))[:12]}, not {runtime[:12]}')
        if upgrade_report.get('baseline_runtime') != pinned_runtime:
            problems.append('the downloaded upgrade report compares against another baseline than the pin')
        if upgrade_report.get('candidate_runtime') == upgrade_report.get('baseline_runtime'):
            problems.append('the downloaded upgrade report rehearsed the pin against itself')
    if upgrade_sha256 is not None and upgrade_sha256 == primary_sha256:
        problems.append('the model-upgrade candidate model is the primary model: model_upgrade requires a model axis')
    return problems
