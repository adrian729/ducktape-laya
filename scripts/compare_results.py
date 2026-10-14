"""Strict ordered JSON result comparison for maintainer conformance checks."""
import math


def compare_results(expected, actual, tolerance=0, path='$'):
    """Return (maximum numeric error, path); reject shape/type/value drift.

    Booleans and integer-valued contract fields are discrete. Float comparison
    permits JSON integer spellings but never NaN/infinity or boolean coercion.
    Dictionary insertion order is part of the observable result contract.
    """
    if isinstance(tolerance, bool) or not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError('Tolerance must be finite and nonnegative')
    maximum = (0, '')

    def require(condition, message):
        if not condition:
            raise AssertionError(message)

    def visit(x, y, at):
        nonlocal maximum
        if isinstance(x, dict):
            require(isinstance(y, dict) and list(x) == list(y), f'Key/order/type change: {at}')
            for key in x:
                visit(x[key], y[key], at + '.' + key)
        elif isinstance(x, list):
            require(isinstance(y, list) and len(x) == len(y), f'Array shape changed: {at}')
            for index, (left, right) in enumerate(zip(x, y)):
                visit(left, right, f'{at}[{index}]')
        elif type(x) in (int, float):
            require(type(y) in (int, float), f'Numeric type changed: {at}')
            require(math.isfinite(x) and math.isfinite(y), f'Nonfinite result: {at}')
            if type(x) is int:
                require(type(y) is int and x == y, f'Discrete value changed: {at}')
            delta = abs(x - y)
            if delta > maximum[0]:
                maximum = (delta, at)
        else:
            require(type(x) is type(y) and x == y, f'Value/type changed: {at}')

    visit(expected, actual, path)
    require(maximum[0] <= tolerance, f'Numeric gate failed: {maximum[0]} at {maximum[1]} exceeds {tolerance}')
    return maximum
