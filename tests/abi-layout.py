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
    # The linker Cargo is told to use on Windows (CI names MSVC's, which Git Bash's link.exe shadows on PATH).
    linker = os.environ.get('CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER') if sys.platform == 'win32' else None
    probes = {
        'C': words(os.environ.get('CC', 'cc')) + ['-std=c11', '-I', str(ROOT / 'include'), str(source)],
        'C++': words(os.environ.get('CXX', 'c++')) + ['-x', 'c++', '-std=c++17', '-I', str(ROOT / 'include'), str(source)],
        'Rust': words(os.environ.get('RUSTC', 'rustc')) + ['--edition=2021', *(['-C', f'linker={linker}'] if linker else []), str(rust_source)],
    }
    # The C++ wrapper refuses a library of another ABI: a stub library that reports ABI 3.
    stub = tmp / 'abi-check.cpp'
    stub.write_text('''#include "ducktape_laya.hpp"
#include <cstdio>
#include <cstdlib>
#include <cstring>
static uint32_t abi = 3;
extern "C" {
uint32_t dtl_abi_version(void) { return abi; }
dtl_status dtl_open(const char *, const char *, dtl_engine **engine, char **error) { *engine = nullptr; *error = nullptr; return DTL_MODEL_ERROR; }
dtl_status dtl_open_router(const char *, const char *, dtl_engine **engine, char **error) { *engine = nullptr; *error = nullptr; return DTL_MODEL_ERROR; }
dtl_status dtl_info(dtl_engine *, char **result, char **error) { *result = static_cast<char *>(std::malloc(3)); std::strcpy(*result, "{}"); *error = nullptr; return DTL_OK; }
dtl_call_options dtl_default_call_options(void) { dtl_call_options options{}; options.struct_size = sizeof options; return options; }
dtl_status dtl_execute(dtl_engine *, const char *, const dtl_call_options *, char **, char **) { return DTL_INVALID_ARGUMENT; }
void dtl_close(dtl_engine *) {}
void dtl_free(void *buffer) { std::free(buffer); }
}
template <class F> int status(F f) { try { f(); } catch (const ducktape::error &e) { return e.status; } return 0; }
int main() {
    int refused = status([] { ducktape::info(); }) == 7 && status([] { ducktape::engine::open("m.gguf"); }) == 7 &&
                  status([] { ducktape::engine::open_router("{}"); }) == 7;
    abi = 2;
    int accepted = status([] { ducktape::info(); }) == 0 && status([] { ducktape::engine::open("m.gguf"); }) == 2;
    std::printf("%d %d\\n", refused, accepted);
    return refused && accepted ? 0 : 1;
}
''')
    binary = tmp / ('abi-check' + ('.exe' if sys.platform == 'win32' else ''))
    subprocess.run(words(os.environ.get('CXX', 'c++')) + ['-std=c++17', '-DDUCKTAPE_LAYA_BUILD', '-I', str(ROOT / 'include'), str(stub), '-o', str(binary)],
                   check=True, capture_output=True, text=True)
    result = subprocess.run([binary], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f'ducktape_laya.hpp accepted a library of another ABI or refused ABI 2: {result.stdout}{result.stderr}')
    print('C++: the wrapper refuses a library of another ABI with status 7')
    for language, command in probes.items():
        binary = tmp / (language.replace('+', 'p') + ('.exe' if sys.platform == 'win32' else ''))
        subprocess.run(command + ['-o', str(binary)], check=True, capture_output=True, text=True)
        output = subprocess.check_output([binary], text=True)
        actual = {line.split()[0]: list(map(int, line.split()[1:])) for line in output.splitlines()}
        if actual != EXPECTED:
            raise RuntimeError(f'{language} layout differs from ABI 2: {actual}')
        print(f'{language}: frozen ABI 2 sizes, alignments and field offsets match')
