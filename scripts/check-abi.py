#!/usr/bin/env python3
"""Require exported functions to match the public C header exactly.

Reads the ELF dynamic symbol table (nm -D), the PE export table (Windows DLL,
read directly) or the Mach-O external symbols (nm -gU)."""
import argparse
import pathlib
import re
import subprocess
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from release_support import pe_info  # noqa: E402
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('library', type=pathlib.Path)
a = p.parse_args()
root = pathlib.Path(__file__).resolve().parents[1]
header = (root / 'include/ducktape_laya.h').read_text()
expected = set(re.findall(r'DTL_API\s+\w+\s+(dtl_\w+)\s*\(', header))
head = a.library.read_bytes()[:4]
if head == b'\x7fELF':
    output = subprocess.check_output(['nm', '-D', '--defined-only', str(a.library)], text=True)
    actual = set(re.findall(r'\b(dtl_\w+)(?:@@\S+)?$', output, re.M))
    other = [line for line in output.splitlines() if not re.search(r'\b(?:dtl_\w+|DUCKTAPE_LAYA_\S+)(?:@@\S+)?$', line)]
elif head[:2] == b'MZ':
    exports = pe_info(a.library)['exports']
    actual = {name for name in exports if re.fullmatch(r'dtl_\w+', name)}
    other = [name for name in exports if name not in actual]
else:
    output = subprocess.check_output(['nm', '-gU', str(a.library)], text=True)
    names = [line.split()[-1] for line in output.splitlines() if line.strip()]
    actual = {name[1:] for name in names if re.fullmatch(r'_dtl_\w+', name)}
    other = [name for name in names if not re.fullmatch(r'_dtl_\w+', name)]
assert expected == actual, {'missing': sorted(expected-actual), 'unexpected': sorted(actual-expected)}
assert not other, 'Unexpected public symbols: ' + repr(other)
print(f'ABI exports match public header: {len(actual)} functions')
