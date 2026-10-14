#!/usr/bin/env python3
"""Require exported functions to match the public C header exactly."""
import argparse
import pathlib
import re
import subprocess
p = argparse.ArgumentParser()
p.add_argument('library', type=pathlib.Path)
a = p.parse_args()
root = pathlib.Path(__file__).resolve().parents[1]
header = (root / 'include/ducktape_laya.h').read_text()
expected = set(re.findall(r'DTL_API\s+\w+\s+(dtl_\w+)\s*\(', header))
output = subprocess.check_output(['nm', '-D', '--defined-only', str(a.library)], text=True)
actual = set(re.findall(r'\b(dtl_\w+)(?:@@\S+)?$', output, re.M))
assert expected == actual, {'missing': sorted(expected-actual), 'unexpected': sorted(actual-expected)}
other = [line for line in output.splitlines() if not re.search(r'\b(?:dtl_\w+|DUCKTAPE_LAYA_\S+)(?:@@\S+)?$', line)]
assert not other, 'Unexpected public symbols: ' + repr(other)
print(f'ABI exports match public header: {len(actual)} functions')
