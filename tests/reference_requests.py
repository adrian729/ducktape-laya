"""Upstream-reference requests for every conformance fixture request, in literal key order.

Upstream processes primitive questions only. Structured requests are projected to
the primitive questions the shared core derives from their schema; agreement then
checks both that derivation and inference. Keys: fixture stem, plus the batch index.
"""
import json
import pathlib

FIXTURES = pathlib.Path(__file__).resolve().parent / 'fixtures'


def reference_key(stem, index=None):
    return stem if index is None else f'{stem}{index}'


def primitive_request(request):
    if 'schema' not in request:
        return request
    questions = {}
    for name, prop in request['schema']['properties'].items():
        question = {'instructions': prop.get('description', name)}
        if prop['type'] == 'string':
            question.update(type='choice', criteria={value: value for value in prop['enum']})
        elif prop['type'] == 'boolean':
            question['type'] = 'noul'
        else:
            question.update(type='score', criteria=[str(v) for v in range(prop['minimum'], prop['maximum'] + 1)])
        questions[name] = question
    return {'state': request['state'], 'questions': questions}


def as_text(value):
    """The core serializes structured values the way the original Laya does
    (json.dumps with default separators) before rendering; give upstream the
    same text so both sides render identical prompts."""
    return value if value is None or isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def trained_text(request):
    questions = {}
    for name, question in request['questions'].items():
        question = dict(question, instructions=as_text(question['instructions']))
        criteria = question.get('criteria')
        if isinstance(criteria, dict):
            question['criteria'] = {key: as_text(value) for key, value in criteria.items()}
        elif isinstance(criteria, list):
            question['criteria'] = [as_text(value) for value in criteria]
        questions[name] = question
    return dict(request, state=as_text(request['state']), questions=questions)


def reference_requests(fixtures=FIXTURES):
    """Return {key: (fixture name, primitive request)} with insertion order preserved."""
    requests = {}
    for fixture in sorted(pathlib.Path(fixtures).glob('*.json')):
        body = json.loads(fixture.read_text())
        items = enumerate(body) if isinstance(body, list) else [(None, body)]
        for index, request in items:
            requests[reference_key(fixture.stem, index)] = (fixture.name, trained_text(primitive_request(request)))
    return requests


def _temperature(table, kind, n):
    bucket = '2' if n <= 2 else '3_5' if n <= 5 else '6_10' if n <= 10 else '11'
    return table.get(f'{kind}.{bucket}', table.get(kind, 1.0))


def _retempered(answer, applied, stored):
    """One answer re-expressed at the temperature the model file stores.

    The core bounds fitted temperatures to [0.5, 5]; upstream applies them as
    stored. Probabilities are softmax(scores / T), so scaling log p by
    T_applied / T_stored gives upstream's distribution up to rounding."""
    import math
    kind = answer['type']
    values = [1 - answer['noul'], answer['noul']] if kind == 'noul' else list(answer['probabilities'].values())
    ratio = _temperature(applied, kind, len(values)) / _temperature(stored, kind, len(values))
    if ratio == 1:
        return answer
    logits = [math.log(v) * ratio if v > 0 else -math.inf for v in values]
    top = max(logits)
    weights = [math.exp(v - top) for v in logits]
    values = [w / sum(weights) for w in weights]
    answer = dict(answer)
    if kind == 'noul':
        answer['noul'] = values[1]
    else:
        answer['probabilities'] = dict(zip(answer['probabilities'], values))
        if kind == 'score':
            answer['score'] = sum(i * v for i, v in enumerate(values))
    return answer


def comparable_answers(ours, theirs, where, model=None):
    """Answers with the confidence fields set aside, for comparison with upstream.

    The core reports answer_confidence (max p) and confidence (1 - H(p)/log k) for
    every type; the upstream decision processing has an older `confidence` and no
    answer_confidence. Check ours against their definitions, then compare every
    other field. `model` is the engine's info()["model"]: where the core bounded a
    stored temperature, our probabilities are compared at the stored one."""
    import math
    mine, reference = {}, {}
    for question, answer in ours.items():
        probabilities = list(answer['probabilities'].values()) if 'probabilities' in answer else [answer['noul'], 1 - answer['noul']]
        entropy = -sum(p * math.log(p) for p in probabilities if p > 0)
        concentration = 1.0 if len(probabilities) < 2 else max(0.0, min(1.0, 1 - entropy / math.log(len(probabilities))))
        if abs(answer['answer_confidence'] - max(probabilities)) > 1e-9 or abs(answer['confidence'] - concentration) > 1e-9:
            raise AssertionError(f'{where}.{question}: confidence fields do not match their definitions')
        if model is not None:
            answer = _retempered(answer, model['temperatures'], model['metadata_temperatures'])
        mine[question] = {k: v for k, v in answer.items() if k not in ('answer_confidence', 'confidence')}
        reference[question] = {k: v for k, v in theirs[question].items() if k not in ('answer_confidence', 'confidence')}
    return mine, reference
