#!/usr/bin/env python3
"""Compile C, C++ and Rust layout probes against frozen Linux x86-64 ABI 2."""
import json
import os
import pathlib
import shlex
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIELDS = ['struct_size', 'reserved', 'request_id', 'timeout_ms', 'on_event', 'event_user_data', 'should_cancel', 'cancel_user_data']
STRUCTS = {'CallOptions': ('dtl_call_options', FIELDS)}
# Changing these requires an intentional ABI decision, not a regenerated golden.
EXPECTED = {'CallOptions': [56, 8, 0, 4, 8, 16, 24, 32, 40, 48]}

with tempfile.TemporaryDirectory(prefix='ducktape-abi-layout-') as directory:
    tmp = pathlib.Path(directory)
    c = ['#include "ducktape_laya.h"', '#include <stddef.h>', '#include <stdio.h>',
         '#ifdef __cplusplus', '#define ALIGNOF alignof', '#else', '#define ALIGNOF _Alignof', '#endif',
         'int main(void) { if (sizeof(void*) != 8) return 2;']
    rust = [f'#[allow(dead_code)] #[path={json.dumps(str(ROOT / "bindings/rust/ducktape-laya-sys/src/lib.rs"))}] mod sys;',
            'fn main() { assert_eq!(std::mem::size_of::<usize>(), 8);']
    for name, (ctype, fields) in STRUCTS.items():
        values = [f'sizeof({ctype})', f'ALIGNOF({ctype})'] + [f'offsetof({ctype}, {field})' for field in fields]
        c.append('printf("' + name + ' ' + ' '.join(['%zu'] * len(values)) + '\\n", ' + ', '.join(values) + ');')
        values = [f'std::mem::size_of::<sys::{name}>()', f'std::mem::align_of::<sys::{name}>()'] + [f'std::mem::offset_of!(sys::{name}, {field})' for field in fields]
        rust.append('println!("' + name + ' ' + ' '.join(['{}'] * len(values)) + '", ' + ', '.join(values) + ');')
    c.append('return 0; }'); rust.append('}')
    source = tmp / 'layout.c'; source.write_text('\n'.join(c))
    rust_source = tmp / 'layout.rs'; rust_source.write_text('\n'.join(rust))
    # A Windows compiler variable is one path (with spaces and backslashes), not a shell word list.
    words = (lambda value: [value]) if sys.platform == 'win32' else shlex.split
    probes = {
        'C': words(os.environ.get('CC', 'cc')) + ['-std=c11', '-I', str(ROOT / 'include'), str(source)],
        'C++': words(os.environ.get('CXX', 'c++')) + ['-x', 'c++', '-std=c++17', '-I', str(ROOT / 'include'), str(source)],
        'Rust': words(os.environ.get('RUSTC', 'rustc')) + ['--edition=2021', str(rust_source)],
    }
    for language, command in probes.items():
        binary = tmp / (language.replace('+', 'p') + ('.exe' if sys.platform == 'win32' else ''))
        subprocess.run(command + ['-o', str(binary)], check=True, capture_output=True, text=True)
        output = subprocess.check_output([binary], text=True)
        actual = {line.split()[0]: list(map(int, line.split()[1:])) for line in output.splitlines()}
        if actual != EXPECTED:
            raise RuntimeError(f'{language} layout differs from ABI 2: {actual}')
        print(f'{language}: frozen ABI 2 sizes, alignments and field offsets match')
