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
