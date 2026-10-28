#!/usr/bin/env python3
"""Measure questions on your own labelled examples before relying on them.

    python3 scripts/evaluate-questions.py MODEL EXAMPLES.jsonl QUESTIONS.json [options]

It needs the Python package (`pip install ducktape-laya`, or a checkout's binding on
PYTHONPATH with DUCKTAPE_LAYA_LIBRARY naming the built library) and nothing else.

EXAMPLES.jsonl has one example per line: ``{"state": ..., "label": ...}``. The
labels decide the evaluation:

- ``true``/``false``: a yes/no task. Each question gives the probability of
  ``true``: a ``noul`` its ``noul``, a ``choice`` the probability of the option
  named by ``"positive"`` in its definition.
- option keys (strings): a ``choice`` task; each question must be a ``choice``
  with those keys.
- numbers: an ordinal task (``score`` against the level index, or any question
  type against a number).

QUESTIONS.json maps names to questions in the request format, so several
phrasings can be compared on the same examples. A ``choice`` used for a yes/no
task also carries ``"positive": OPTION``; it is removed before the request.

Every result is set against the baselines an application would otherwise use:
the length of the state, the most common label, and a rule you supply as
``"rule": true/false`` on each example (for example a regular expression's
verdict). Thresholds are chosen on one half of the examples and measured on the
other (2-fold, stratified, seeded), so the precision and recall reported are
what a threshold picked on data like yours would give, not the best case.

Controls (``--controls``):

- ``order``: asks each ``choice`` again with its options in reverse order and
  reports how often the answer flips. The model has a position bias; average
  the two orders if the flip rate matters to you.
- ``shuffle``: asks each question again with the state's words shuffled. A
  result that survives shuffling comes from which words appear, not from what
  the text says.
"""
import argparse
import json
import math
import random
import sys
import time
import zlib



def _text(state):
    return state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)


def _shuffled(state, seed):
    words = _text(state).split()
    random.Random(seed).shuffle(words)
    return ' '.join(words)


def _reversed_choice(question):
    result = dict(question)
    criteria = question['criteria']
    result['criteria'] = (dict(reversed(list(criteria.items()))) if isinstance(criteria, dict)
                          else list(reversed(criteria)))
    return result


def auc(scores, labels):
    """Probability that a random positive outranks a random negative (ties count half); None without both."""
    pairs = sorted(zip(scores, labels))
    positives = sum(1 for _, y in pairs if y)
    negatives = len(pairs) - positives
    if not positives or not negatives:
        return None
    rank_sum, i = 0.0, 0
    while i < len(pairs):
        j = i
        while j < len(pairs) and pairs[j][0] == pairs[i][0]:
            j += 1
        mid_rank = (i + j + 1) / 2
        rank_sum += mid_rank * sum(1 for _, y in pairs[i:j] if y)
        i = j
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def stratified_auc(scores, labels, lengths, groups=4):
    """Mean AUC within length quartiles: the signal left once length is held roughly fixed."""
    order = sorted(range(len(scores)), key=lambda i: lengths[i])
    values = []
    for g in range(groups):
        part = order[g * len(order) // groups:(g + 1) * len(order) // groups]
        value = auc([scores[i] for i in part], [labels[i] for i in part])
        if value is not None:
            values.append(value)
    return sum(values) / len(values) if values else None


def pearson(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((y - mb) ** 2 for y in b))
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb) if sa and sb else None


def _ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j < len(order) and values[order[j]] == values[order[i]]:
            j += 1
        for k in order[i:j]:
            ranks[k] = (i + j - 1) / 2
        i = j
    return ranks


def spearman(a, b):
    return pearson(_ranks(a), _ranks(b))


def _counts(scores, labels, threshold):
    tp = sum(1 for s, y in zip(scores, labels) if s >= threshold and y)
    fp = sum(1 for s, y in zip(scores, labels) if s >= threshold and not y)
    return tp, fp


def _pick_threshold(scores, labels, precision):
    """Lowest threshold whose precision on these examples reaches `precision`; None if none does."""
    for t in sorted(set(scores)):
        tp, fp = _counts(scores, labels, t)
        if tp and tp / (tp + fp) >= precision:
            return t
    return None


def _best_f1_threshold(scores, labels):
    positives = sum(labels)
    best = (-1.0, None)
    for t in sorted(set(scores)):
        tp, fp = _counts(scores, labels, t)
        f1 = 2 * tp / (tp + fp + positives) if tp else 0.0
        best = max(best, (f1, t), key=lambda x: x[0])
    return best[1]


def folds(labels, seed=0):
    """Two stratified halves of the example indices."""
    rng = random.Random(seed)
    halves = ([], [])
    for value in (True, False):
        members = [i for i, y in enumerate(labels) if y == value]
        rng.shuffle(members)
        for k, i in enumerate(members):
            halves[k % 2].append(i)
    return halves


def held_out(scores, labels, pick, seed=0):
    """Picks a threshold on each half and applies it to the other; pooled precision, recall and both thresholds."""
    a, b = folds(labels, seed)
    tp = fp = 0
    thresholds = []
    for fit, test in ((a, b), (b, a)):
        t = pick([scores[i] for i in fit], [labels[i] for i in fit])
        thresholds.append(t)
        if t is None:
            continue
        x, y = _counts([scores[i] for i in test], [labels[i] for i in test], t)
        tp, fp = tp + x, fp + y
    positives = sum(labels)
    return {'precision': tp / (tp + fp) if tp + fp else None,
            'recall': tp / positives if positives else None, 'thresholds': thresholds}


def _with_rule(scores, rules):
    """The rule OR the score: a rule's positives score above every probability."""
    return [2.0 if r else s for s, r in zip(scores, rules)]


def binary_report(scores, labels, lengths, precision, rules=None):
    log_lengths = [math.log(max(n, 1)) for n in lengths]
    report = {
        'auc': auc(scores, labels),
        'auc_within_length_quartiles': stratified_auc(scores, labels, lengths),
        'correlation_with_log_length': pearson(scores, log_lengths),
        'at_precision': held_out(scores, labels, lambda s, y: _pick_threshold(s, y, precision)),
        'at_best_f1': held_out(scores, labels, _best_f1_threshold),
        'at_0.5': dict(zip(('precision', 'recall'), _fixed(scores, labels, 0.5))),
    }
    if rules is not None:
        combined = _with_rule(scores, rules)
        report['rule_or_score_at_precision'] = held_out(combined, labels, lambda s, y: _pick_threshold(s, y, precision))
    return report


def _fixed(scores, labels, threshold):
    tp, fp = _counts(scores, labels, threshold)
    positives = sum(labels)
    return (tp / (tp + fp) if tp + fp else None, tp / positives if positives else None)


def baseline_report(labels, lengths, precision, rules=None):
    by_length = [float(n) for n in lengths]
    report = {'positives': sum(labels), 'examples': len(labels),
              'length': {'auc': auc(by_length, labels),
                         'at_precision': held_out(by_length, labels, lambda s, y: _pick_threshold(s, y, precision)),
                         'at_best_f1': held_out(by_length, labels, _best_f1_threshold)}}
    if rules is not None:
        report['rule'] = dict(zip(('precision', 'recall'), _fixed([float(r) for r in rules], labels, 1.0)))
    return report


def _probability(question, answer, positive):
    if question['type'] == 'noul':
        return answer['noul']
    if question['type'] == 'choice':
        return answer['probabilities'][positive]
    return answer['score']


def _load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _examples(path):
    rows = []
    with open(path, encoding='utf-8') as f:
        for n, line in enumerate(f, 1):
            if line.strip():
                row = json.loads(line)
                if 'state' not in row or 'label' not in row:
                    raise SystemExit(f'{path}:{n}: each example needs "state" and "label"')
                rows.append(row)
    if not rows:
        raise SystemExit(f'{path}: no examples')
    return rows


def _mode(labels):
    if all(isinstance(y, bool) for y in labels):
        return 'binary'
    if all(isinstance(y, str) for y in labels):
        return 'choice'
    if all(isinstance(y, (int, float)) and not isinstance(y, bool) for y in labels):
        return 'ordinal'
    raise SystemExit('labels must be all booleans, all option keys (strings) or all numbers')


def _check_questions(questions, mode, labels):
    for name, q in questions.items():
        if mode == 'binary' and q.get('type') == 'choice' and q.get('positive') not in (q.get('criteria') or {}):
            raise SystemExit(f'question {name}: a choice for a yes/no task needs "positive" naming one of its options')
        if mode == 'binary' and q.get('type') == 'score':
            raise SystemExit(f'question {name}: use noul or choice for a yes/no task')
        if mode == 'choice':
            keys = q.get('criteria') if q.get('type') == 'choice' else None
            if not isinstance(keys, dict) or not set(labels) <= set(keys):
                raise SystemExit(f'question {name}: a choice task needs a choice whose option keys include every label')


def _run(engine, questions, states, progress):
    """Asks every question about every state; returns {name: [answer, ...]} and the seconds per example."""
    answers = {name: [] for name in questions}
    seconds = []
    for n, state in enumerate(states, 1):
        start = time.perf_counter()
        result = engine.execute({'state': state, 'questions': questions})
        seconds.append(time.perf_counter() - start)
        for name in questions:
            answers[name].append(result['answers'][name])
        if progress and (n % 10 == 0 or n == len(states)):
            print(f'  {n}/{len(states)} ({sum(seconds) / n:.2f} s per example)', file=sys.stderr)
    return answers, seconds


def evaluate(engine, examples, questions, *, controls=(), precision=0.9, progress=False):
    """Returns the evaluation as a JSON-ready dict; `engine` is an open Engine."""
    labels = [row['label'] for row in examples]
    mode = _mode(labels)
    questions = {name: dict(q) for name, q in questions.items()}
    _check_questions(questions, mode, labels)
    states = [row['state'] for row in examples]
    lengths = [len(_text(s)) for s in states]
    rules = [bool(row['rule']) for row in examples] if all('rule' in row for row in examples) else None
    positive = {name: q.pop('positive', None) for name, q in questions.items()}
    asked, seconds = _run(engine, questions, states, progress)
    report = {'mode': mode, 'examples': len(examples), 'seconds_per_example': sum(seconds) / len(seconds),
              'questions': {}}
    if mode == 'binary':
        report['baselines'] = baseline_report(labels, lengths, precision, rules)
        report['target_precision'] = precision
    elif mode == 'choice':
        majority = max(set(labels), key=labels.count)
        report['baselines'] = {'majority_label': majority, 'majority_accuracy': labels.count(majority) / len(labels)}
    else:
        report['baselines'] = {'length_spearman': spearman([float(n) for n in lengths], labels)}
    scores = {}
    for name, q in questions.items():
        entry = {}
        if mode == 'binary':
            scores[name] = [_probability(q, a, positive[name]) for a in asked[name]]
            entry.update(binary_report(scores[name], labels, lengths, precision, rules))
        elif mode == 'choice':
            picks = [a['choice'] for a in asked[name]]
            entry['accuracy'] = sum(p == y for p, y in zip(picks, labels)) / len(labels)
            entry['predicted'] = {k: picks.count(k) for k in q['criteria']}
            entry['confusion'] = {y: {k: sum(1 for p, t in zip(picks, labels) if t == y and p == k) for k in q['criteria']}
                                  for y in sorted(set(labels))}
            scores[name] = picks
        else:
            scores[name] = [_probability(q, a, positive[name]) if q['type'] != 'choice'
                            else max(a['probabilities'].values()) for a in asked[name]]
            entry['spearman'] = spearman(scores[name], labels)
            entry['spearman_with_length'] = spearman(scores[name], [float(n) for n in lengths])
            entry['range'] = [min(scores[name]), max(scores[name])]
        report['questions'][name] = entry
    if 'order' in controls:
        flipped = {name: _reversed_choice(q) for name, q in questions.items() if q['type'] == 'choice'}
        if flipped:
            again, _ = _run(engine, flipped, states, progress)
            for name in flipped:
                first = [a['choice'] for a in asked[name]]
                second = [a['choice'] for a in again[name]]
                control = {'flip_rate': sum(a != b for a, b in zip(first, second)) / len(first)}
                if mode == 'binary':
                    other = [_probability(questions[name], a, positive[name]) for a in again[name]]
                    control['mean_abs_difference'] = sum(abs(a - b) for a, b in zip(scores[name], other)) / len(other)
                    control['auc_reversed'] = auc(other, labels)
                report['questions'][name]['order_control'] = control
    if 'shuffle' in controls and mode == 'binary':
        shuffled = [_shuffled(s, zlib.crc32(_text(s).encode())) for s in states]
        again, _ = _run(engine, questions, shuffled, progress)
        for name, q in questions.items():
            other = [_probability(q, a, positive[name]) for a in again[name]]
            report['questions'][name]['shuffle_control'] = {'auc_shuffled_words': auc(other, labels)}
    report['scores'] = scores
    return report


def _f(value, digits=2):
    return '-' if value is None else f'{value:.{digits}f}'


def summary(report):
    lines = [f"{report['examples']} examples, {report['seconds_per_example']:.2f} s per example for all questions"]
    base = report['baselines']
    if report['mode'] == 'binary':
        p = report['target_precision']
        length = base['length']
        lines.append(f"positives {base['positives']}; length alone: AUC {_f(length['auc'])}, "
                     f"recall at precision >= {p} (held out) {_f(length['at_precision']['recall'])}")
        if 'rule' in base:
            lines.append(f"rule: precision {_f(base['rule']['precision'])}, recall {_f(base['rule']['recall'])}")
        lines.append(f"{'question':24} {'AUC':>5} {'AUC|len':>7} {'r(len)':>6}  {'held out at precision ' + str(p):>28}  "
                     f"{'best F1 (held out)':>18}  {'at 0.5':>11}")
        for name, e in report['questions'].items():
            at, f1, half = e['at_precision'], e['at_best_f1'], e['at_0.5']
            lines.append(f"{name:24} {_f(e['auc']):>5} {_f(e['auc_within_length_quartiles']):>7} "
                         f"{_f(e['correlation_with_log_length']):>6}  "
                         f"{'P ' + _f(at['precision']) + ' R ' + _f(at['recall']):>28}  "
                         f"{'P ' + _f(f1['precision']) + ' R ' + _f(f1['recall']):>18}  "
                         f"{'P ' + _f(half['precision']) + ' R ' + _f(half['recall']):>11}")
            if 'rule_or_score_at_precision' in e:
                r = e['rule_or_score_at_precision']
                lines.append(f"{'':24} rule OR score, held out at precision {p}: P {_f(r['precision'])} R {_f(r['recall'])}")
            if 'order_control' in e:
                c = e['order_control']
                lines.append(f"{'':24} options reversed: AUC {_f(c.get('auc_reversed'))}, answer flips "
                             f"{_f(c['flip_rate'])}, mean |difference| {_f(c.get('mean_abs_difference'))}")
            if 'shuffle_control' in e:
                lines.append(f"{'':24} words shuffled: AUC {_f(e['shuffle_control']['auc_shuffled_words'])} "
                             '(the closer to the AUC above, the more the answer comes from which words appear)')
    elif report['mode'] == 'choice':
        lines.append(f"always {base['majority_label']!r}: accuracy {_f(base['majority_accuracy'])}")
        for name, e in report['questions'].items():
            lines.append(f"{name:24} accuracy {_f(e['accuracy'])}  predicted {e['predicted']}")
            for label, row in e['confusion'].items():
                lines.append(f"{'':24}   true {label!r}: {row}")
            if 'order_control' in e:
                lines.append(f"{'':24} options reversed: answer flips {_f(e['order_control']['flip_rate'])}")
    else:
        lines.append(f"length alone: Spearman {_f(base['length_spearman'])}")
        for name, e in report['questions'].items():
            lines.append(f"{name:24} Spearman {_f(e['spearman'])}, with length {_f(e['spearman_with_length'])}, "
                         f"range {_f(e['range'][0])}-{_f(e['range'][1])}")
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('model', help='the GGUF model file')
    parser.add_argument('examples', help='JSONL file of {"state": ..., "label": ..., "rule": optional bool}')
    parser.add_argument('questions', help='JSON file mapping names to questions')
    parser.add_argument('--precision', type=float, default=0.9, help='target precision for yes/no tasks (default 0.9)')
    parser.add_argument('--controls', default='', help='comma-separated: order, shuffle')
    parser.add_argument('--threads', type=int, default=None)
    parser.add_argument('--context-tokens', type=int, default=None)
    parser.add_argument('--overflow', default=None)
    parser.add_argument('--json', dest='json_path', help='write the full report, with every score, to this file')
    args = parser.parse_args(argv)
    controls = {c for c in args.controls.split(',') if c}
    if controls - {'order', 'shuffle'}:
        parser.error(f'unknown control: {", ".join(sorted(controls - {"order", "shuffle"}))}')
    examples, questions = _examples(args.examples), _load(args.questions)
    from ducktape_laya import Engine
    with Engine.open(args.model, threads=args.threads, context_tokens=args.context_tokens, overflow=args.overflow) as engine:
        report = evaluate(engine, examples, questions, controls=controls, precision=args.precision, progress=True)
    if args.json_path:
        with open(args.json_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=1)
    print(summary(report))


if __name__ == '__main__':
    main()
