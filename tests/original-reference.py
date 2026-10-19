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
# A decision may flip only where the original's top two probabilities are this
# close: quantization can legitimately reorder a near tie.
NEAR_TIE = 0.05
# Case name -> why the core refuses it today. Remove an entry when its feature lands.
UNSUPPORTED = {
    'choice_criteria_list': 'plain-list choice criteria',
    'single_option': 'single-option choice',
    'long_state': 'state longer than the context (overflow policies)',
    'many_options': 'option text over the head budget (overflow policies)',
    'shortlist_many_options': 'embedding shortlist (not planned)',
}

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('model', type=pathlib.Path)
parser.add_argument('--build-dir', type=pathlib.Path, default=ROOT / 'build')
parser.add_argument('--cases', type=pathlib.Path, default=ROOT / 'tests/original-reference/english')
parser.add_argument('--report', type=pathlib.Path)
args = parser.parse_args()
build = args.build_dir.resolve()
consumer = build / 'laya-conformance-c'
env = dict(os.environ, LD_LIBRARY_PATH=str(build) + ':' + os.environ.get('LD_LIBRARY_PATH', ''))


def run(request):
    output = subprocess.run([str(consumer), str(args.model.resolve())], input=json.dumps(request, ensure_ascii=False).encode(),
                            capture_output=True, env=env, timeout=900)
    if output.returncode:
        return None, output.stderr.decode(errors='replace').strip()
    return json.loads(output.stdout), None


def compare(expected, got):
    """Return (max |dp|, decision changed, near tie) for one answer."""
    if expected['type'] == 'noul':
        drift = abs(got['noul'] - expected['noul'])
        changed = (got['noul'] >= 0.5) != (expected['noul'] >= 0.5)
        near = abs(expected['noul'] - 0.5) * 2 < NEAR_TIE
        return drift, changed, near
    probs, ours = expected['probabilities'], got['probabilities']
    drift = max(abs(ours[label] - p) for label, p in probs.items())
    top = sorted(probs.values(), reverse=True)
    near = len(top) > 1 and top[0] - top[1] < NEAR_TIE
    best = lambda p: max(p, key=p.get)
    return drift, best(ours) != best(probs), near


failures, answers = [], []
report: dict = {'cases': {}, 'model': str(args.model), 'thresholds': {
    'max_probability_drift': MAX_PROBABILITY_DRIFT, 'near_tie': NEAR_TIE}}
for path in sorted(args.cases.glob('case_*.json')):
    case = json.loads(path.read_text())
    name = case['name']
    got, error = run({'state': case['state'], 'questions': case['questions']})
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
    if got['usage']['input_tokens'] != expected_tokens:
        failures.append(f'{name}: input_tokens {got["usage"]["input_tokens"]} != {expected_tokens}')
    entry['input_tokens'] = {'ours': got['usage']['input_tokens'], 'original': expected_tokens}
    for qid, expected in case['result']['answers'].items():
        drift, changed, near = compare(expected, got['answers'][qid])
        answers.append(drift)
        entry['answers'][qid] = {'type': expected['type'], 'max_probability_drift': round(drift, 6),
                                 'decision_changed': changed, 'near_tie': near}
        if drift > MAX_PROBABILITY_DRIFT:
            failures.append(f'{name}/{qid}: probability drift {drift:.4f} > {MAX_PROBABILITY_DRIFT}')
        if changed and not near:
            failures.append(f'{name}/{qid}: decision differs from the original')
    entry['status'] = 'passed' if not any(f.startswith(name + '/') or f.startswith(name + ':') for f in failures) else 'failed'
    report['cases'][name] = entry
    print(f'{name}: {entry["status"]}, max drift {max(a["max_probability_drift"] for a in entry["answers"].values()):.4f}')
report['summary'] = {'answers': len(answers), 'max_probability_drift': max(answers, default=0),
                     'mean_probability_drift': sum(answers) / len(answers) if answers else 0,
                     'status': 'failed' if failures else 'passed', 'failures': failures}
if args.report:
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
print(json.dumps(report['summary'], indent=2))
sys.exit(1 if failures else 0)
