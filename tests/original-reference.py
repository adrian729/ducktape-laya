#!/usr/bin/env python3
"""Compare the core's results with the original Laya implementation's.

Runs each vendored case (tests/original-reference/<model>/case_*.json) through
the C conformance consumer and checks decisions, probability drift and token
counts against the original's recorded result. Cases for features the core does
not support yet must still be refused; when one starts passing, move it into the
supported set.
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
MAX_PROBABILITY_DRIFT = 0.05
# Where the original's top two probabilities are this close, the model is
# undecided and quantization noise can reorder them: such a decision may differ,
# but its probabilities are still held to the drift threshold.
NEAR_TIE = 0.05
# Case name -> why the core refuses it today. Remove an entry when its feature lands.
# The original clamps over-long input by default; here that is an explicit policy.
TRUNCATED = {'long_state', 'many_options'}
UNSUPPORTED = {
    'shortlist_many_options': 'embedding shortlist (not planned)',
}
# (case set, case, question) -> documented deviation of one answer on this platform, with
# its own cap; every other answer stays at MAX_PROBABILITY_DRIFT. The deviations are the
# release target's known_deviations in release/compatibility.json (docs/COMPATIBILITY.md,
# "Known deviations"); a platform that is not a release target has none.
sys.path.insert(0, str(ROOT / 'scripts'))
from release_support import RELEASE_PLATFORMS, host_target, read_json, record_target  # noqa: E402
try:
    PLATFORM = host_target().name
except ValueError:
    PLATFORM = None
KNOWN_DEVIATIONS = {} if PLATFORM not in RELEASE_PLATFORMS else {
    (item['cases'], item['case'], item['question']): item
    for item in record_target(read_json(ROOT / 'release/compatibility.json'), PLATFORM).get('known_deviations', [])}

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('model', type=pathlib.Path)
parser.add_argument('--build-dir', type=pathlib.Path, default=ROOT / 'build')
parser.add_argument('--cases', type=pathlib.Path, default=ROOT / 'tests/original-reference/english')
parser.add_argument('--report', type=pathlib.Path)
args = parser.parse_args()
build = args.build_dir.resolve()
consumer = build / ('laya-conformance-c' + ('.exe' if sys.platform == 'win32' else ''))
env = dict(os.environ, LD_LIBRARY_PATH=str(build) + ':' + os.environ.get('LD_LIBRARY_PATH', ''),
           DYLD_LIBRARY_PATH=str(build) + ':' + os.environ.get('DYLD_LIBRARY_PATH', ''),
           PATH=str(build) + os.pathsep + os.environ.get('PATH', ''))
# The original runs each checkpoint at the sequence length it was trained with.
config = json.dumps({'threads': 4, 'context_tokens': json.loads((args.cases / 'meta.json').read_text())['max_len']})


def run(request):
    output = subprocess.run([str(consumer), str(args.model.resolve()), config], input=json.dumps(request, ensure_ascii=False).encode(),
                            capture_output=True, env=env, timeout=900)
    if output.returncode:
        return None, output.stderr.decode(errors='replace').strip()
    return json.loads(output.stdout), None


def compare(expected, got):
    """Return (max drift over probabilities and confidence fields, decision changed, near tie).

    `answer_confidence` (max p) is compared for every type. The entropy `confidence`
    is compared for choice and score; the original's noul `confidence` is max(p),
    which is our `answer_confidence`."""
    drift = abs(got['answer_confidence'] - expected['answer_confidence'])
    if expected['type'] == 'noul':
        drift = max(drift, abs(got['noul'] - expected['noul']), abs(got['answer_confidence'] - expected['confidence']))
        changed = (got['noul'] >= 0.5) != (expected['noul'] >= 0.5)
        near = abs(expected['noul'] - 0.5) * 2 < NEAR_TIE
        return drift, changed, near
    probs, ours = expected['probabilities'], got['probabilities']
    drift = max(drift, abs(got['confidence'] - expected['confidence']),
                *(abs(ours[label] - p) for label, p in probs.items()))
    top = sorted(probs.values(), reverse=True)
    near = len(top) > 1 and top[0] - top[1] < NEAR_TIE
    best = lambda p: max(p, key=p.get)
    return drift, best(ours) != best(probs), near


failures, answers = [], []
report: dict = {'cases': {}, 'model': str(args.model), 'config': json.loads(config), 'thresholds': {
    'max_probability_drift': MAX_PROBABILITY_DRIFT, 'near_tie': NEAR_TIE,
    'known_deviations': {'/'.join(key[1:]): value for key, value in KNOWN_DEVIATIONS.items()
                         if key[0] == args.cases.resolve().name}}, 'platform': PLATFORM}
for path in sorted(args.cases.glob('case_*.json')):
    case = json.loads(path.read_text())
    name = case['name']
    request = {'state': case['state'], 'questions': case['questions']}
    if name in TRUNCATED:
        request['overflow'] = 'truncate_end'
    got, error = run(request)
    if name in UNSUPPORTED:
        report['cases'][name] = {'status': 'unsupported', 'reason': UNSUPPORTED[name], 'error': error}
        if got is not None:
            failures.append(f'{name}: now supported; remove it from UNSUPPORTED')
        print(f'{name}: unsupported ({UNSUPPORTED[name]})')
        continue
    if error:
        failures.append(f'{name}: {error}')
        report['cases'][name] = {'status': 'failed', 'error': error}
        continue
    entry = {'answers': {}}
    expected_tokens = case['result']['usage']['input_tokens']
    # Single-option choices are answered without inference, so they use no tokens.
    if all(q.get('type') == 'choice' and len(q.get('criteria') or ()) == 1 for q in case['questions'].values()):
        expected_tokens = 0
    if got['usage']['input_tokens'] != expected_tokens:
        failures.append(f'{name}: input_tokens {got["usage"]["input_tokens"]} != {expected_tokens}')
    entry['input_tokens'] = {'ours': got['usage']['input_tokens'], 'original': expected_tokens}
    if name in TRUNCATED:
        for field in ('state_tokens', 'state_tokens_dropped'):
            if got['usage'][field] != case['result']['usage'][field]:
                failures.append(f'{name}: {field} {got["usage"][field]} != {case["result"]["usage"][field]}')
    for qid, expected in case['result']['answers'].items():
        drift, changed, near = compare(expected, got['answers'][qid])
        answers.append(drift)
        entry['answers'][qid] = {'type': expected['type'], 'max_probability_drift': round(drift, 6),
                                 'decision_changed': changed, 'near_tie': near}
        known = KNOWN_DEVIATIONS.get((args.cases.resolve().name, name, qid))
        limit = known['max_probability_drift'] if known else MAX_PROBABILITY_DRIFT
        if known and drift > MAX_PROBABILITY_DRIFT:
            entry['answers'][qid]['known_deviation'] = known
            report.setdefault('known_deviations_used', []).append(f'{name}/{qid}')
        if drift > limit:
            failures.append(f'{name}/{qid}: probability drift {drift:.4f} > {limit}')
        if changed and not near:
            failures.append(f'{name}/{qid}: decision differs from the original')
    entry['status'] = 'passed' if not any(f.startswith(name + '/') or f.startswith(name + ':') for f in failures) else 'failed'
    report['cases'][name] = entry
    print(f'{name}: {entry["status"]}, max drift {max(a["max_probability_drift"] for a in entry["answers"].values()):.4f}')
report['summary'] = {'answers': len(answers), 'max_probability_drift': max(answers, default=0),
                     'mean_probability_drift': sum(answers) / len(answers) if answers else 0,
                     'status': 'failed' if failures else 'passed', 'failures': failures,
                     'known_deviations_used': report.get('known_deviations_used', [])}
if args.report:
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
print(json.dumps(report['summary'], indent=2))
sys.exit(1 if failures else 0)
