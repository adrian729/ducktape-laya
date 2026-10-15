"""Ordered JSON conformance checks and explicit cross-profile drift measurement."""
import math


def _walk(expected, actual, path, decision_paths):
    report = {'maximum_absolute_difference': 0, 'path': '',
              'numeric_differences': 0, 'decision_differences': []}

    def require(condition, message):
        if not condition:
            raise AssertionError(message)

    def visit(x, y, at, components):
        if isinstance(x, dict):
            require(isinstance(y, dict) and list(x) == list(y), f'Key/order/type change: {at}')
            for key in x:
                visit(x[key], y[key], at + '.' + key, components + (key,))
        elif isinstance(x, list):
            require(isinstance(y, list) and len(x) == len(y), f'Array shape changed: {at}')
            for index, (left, right) in enumerate(zip(x, y)):
                visit(left, right, f'{at}[{index}]', components + (index,))
        elif components in decision_paths:
            require(type(x) in (str, bool, int) and type(x) is type(y), f'Decision type changed: {at}')
            if x != y:
                report['decision_differences'].append({'path': at, 'expected': x, 'actual': y})
        elif type(x) in (int, float):
            require(type(y) in (int, float), f'Numeric type changed: {at}')
            require(math.isfinite(x) and math.isfinite(y), f'Nonfinite result: {at}')
            if type(x) is int:
                require(type(y) is int and x == y, f'Discrete value changed: {at}')
            delta = abs(x - y)
            require(math.isfinite(delta), f'Nonfinite difference: {at}')
            if delta:
                report['numeric_differences'] += 1
            if delta > report['maximum_absolute_difference']:
                report['maximum_absolute_difference'], report['path'] = delta, at
        else:
            require(type(x) is type(y) and x == y, f'Value/type changed: {at}')

    visit(expected, actual, path, ())
    return report


def js_object_order(value):
    """Return value with every object in ECMAScript own-property order.

    JavaScript enumerates canonical array-index keys ascending, then other
    string keys in insertion order. Parsed JS results follow this order.
    """
    if isinstance(value, list):
        return [js_object_order(item) for item in value]
    if not isinstance(value, dict):
        return value
    def index(key):
        return key.isdigit() and str(int(key)) == key and int(key) < 2**32 - 1
    keys = sorted((k for k in value if index(k)), key=int) + [k for k in value if not index(k)]
    return {key: js_object_order(value[key]) for key in keys}


def compare_results(expected, actual, tolerance=0, path='$'):
    """Return (maximum numeric error, path); reject shape/type/value drift.

    Booleans and integer-valued contract fields are discrete. Float comparison
    permits JSON integer spellings but never NaN/infinity or boolean coercion.
    Dictionary insertion order is part of the observable result contract.
    """
    if isinstance(tolerance, bool) or not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError('Tolerance must be finite and nonnegative')
    report = _walk(expected, actual, path, frozenset())
    maximum = report['maximum_absolute_difference'], report['path']
    if maximum[0] > tolerance:
        raise AssertionError(f'Numeric gate failed: {maximum[0]} at {maximum[1]} exceeds {tolerance}')
    return maximum


def measure_profile_drift(expected, actual, decision_paths=(), path='$'):
    """Measure drift without claiming numerical equivalence.

    Only explicitly listed scalar decision paths may change discrete values;
    each path is a tuple of dictionary keys / array indices from the root.
    Metadata, ordering, shapes, other discrete fields and finite values remain
    strict. This must never replace same-backend upstream conformance checks.
    """
    return _walk(expected, actual, path, frozenset(decision_paths))
