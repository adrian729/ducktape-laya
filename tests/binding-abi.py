#!/usr/bin/env python3
"""Check the static copies of the C ABI that bindings keep by hand.

The header (include/ducktape_laya.h) is the source. Each binding declares the eight status
codes and, where it lays out `dtl_call_options` itself (Python, Java, .NET), its field order and
widths. A parser that finds nothing, or anything but exactly the header's eight entries, fails,
so a declaration that moves out of reach of its regex cannot pass silently.

    python3 tests/binding-abi.py [--only LANG[,LANG...]]

LANG is one of: c, python, node, browser, java, dotnet, go, rust. This is a Linux policy check;
the compiled layout probes (C, C++, Rust) are `abi-layouts` (tests/abi-layout.py).
"""
import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
HEADER = ROOT / 'include/ducktape_laya.h'
BINDINGS = ROOT / 'bindings'
JAVA = BINDINGS / 'java/src/main/java/io/github/adrian729/laya'
WIDTHS = {'uint32_t': 4, 'uint64_t': 8}


def snake(name):
    """UPPER_SNAKE from PascalCase or camelCase; names already UPPER_SNAKE (or OK) stay."""
    if re.fullmatch(r'[A-Z][A-Z0-9_]*', name):
        return name
    return re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', name).upper()


def block(text, opener):
    """The text between the braces of the first `{` after the regex `opener`, or None."""
    m = re.search(opener, text)
    start = text.find('{', m.end()) if m else -1
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        depth += (text[i] == '{') - (text[i] == '}')
        if depth == 0:
            return text[start + 1:i]
    return None


def header_statuses():
    text = HEADER.read_text()
    entries = {m[1]: int(m[2]) for m in re.finditer(r'\bDTL_([A-Z_]+) = (\d+)', block(text, r'enum\s*(?=\{)') or '')}
    if len(entries) != 8:
        raise SystemExit(f'{HEADER.relative_to(ROOT)}: expected 8 status codes, found {len(entries)}')
    return entries


def header_call_options():
    """(field, width) in declaration order."""
    body = block(HEADER.read_text(), r'typedef struct dtl_call_options') or ''
    fields = []
    for m in re.finditer(r'^\s*([\w ]+?[\w*]) ?\*?\s*(\w+);', body, re.M):
        ctype, name = m[1].strip(), m[2]
        star = '*' in m[0] or ctype.endswith('callback') or ctype.endswith('_callback')
        fields.append((name, 8 if star or ctype.startswith('dtl_') else WIDTHS[ctype]))
    return fields


def read(path):
    return path.read_text(encoding='utf-8')


def statuses(path, pattern, scope=None, flags=re.M):
    """Entries (normalized name -> value) matched by `pattern` in `path` (or its `scope` block)."""
    if not path.is_file():
        return None, f'{path.relative_to(ROOT)}: missing'
    text = read(path)
    if scope:
        text = block(text, scope)
        if text is None:
            return None, f'{path.relative_to(ROOT)}: no status declaration found'
    found = [(snake(m[1]), int(m[2])) for m in re.finditer(pattern, text, flags)]
    return found, None


JS_ENTRIES = r'\b([A-Z][A-Z_]*): (\d+)\b'
STATUS = [  # language -> [(file, pattern, scope)]
    ('python', [(BINDINGS / 'python/src/ducktape_laya/_errors.py', r'^\s+([A-Z_]+) = (\d+)\s*$', 'class Status')]),
    ('node', [(BINDINGS / 'node/index.cjs', JS_ENTRIES, r'const Status = Object\.freeze\('),
              (BINDINGS / 'node/index.d.ts', JS_ENTRIES, r'declare const Status\b[^{]*(?=\{)')]),
    ('browser', [(BINDINGS / 'browser/index.mjs', JS_ENTRIES, r'const Status = Object\.freeze\('),
                 (BINDINGS / 'browser/protocol.d.ts', JS_ENTRIES, r'declare const Status\b[^{]*(?=\{)')]),
    ('java', [(JAVA / 'Status.java', r'\b([A-Z][A-Z_]*)\((\d+)\)', None)]),
    ('dotnet', [(BINDINGS / 'dotnet/DucktapeLaya/Status.cs', r'^\s*(\w+)\s*=\s*(\d+)\s*,?', r'enum Status\b')]),
    ('go', [(BINDINGS / 'go/laya.go', r'^\s*Status(\w+)\s+(?:Status\s+)?=\s*(\d+)\b', None)]),
    ('rust', [(BINDINGS / 'rust/ducktape-laya/src/lib.rs', r'^\s*(\w+)\s*=\s*(\d+)\s*,', r'enum Status\b')]),
]


def check_statuses(lang, sources, expected, problems):
    for path, pattern, scope in sources:
        rel = path.relative_to(ROOT)
        if scope is None:
            found, error = statuses(path, pattern)
        elif scope.startswith('class Status'):  # Python: an indented body, not braces
            if not path.is_file():
                found, error = None, f'{rel}: missing'
            else:
                m = re.search(r'^class Status\b[^\n]*:\n((?:[ \t]+[^\n]*\n|\n)+)', read(path), re.M)
                found, error = ([(snake(x[1]), int(x[2])) for x in re.finditer(pattern, m[1], re.M)], None) if m else (None, f'{rel}: no Status class')
        else:
            found, error = statuses(path, pattern, scope)
        if error:
            problems.append(f'{lang}: {error}')
        elif len(found) != 8 or dict(found) != expected:
            problems.append(f'{lang}: {rel} declares {dict(found)}; the header has {expected} (need exactly 8 entries)')


def java_layout():
    path = JAVA / 'Native.java'
    text = read(path) if path.is_file() else ''
    m = re.search(r'CALL_OPTIONS = MemoryLayout\.structLayout\(([^;]*)\);', text, re.S)
    if not m:
        return None, f'{path.relative_to(ROOT)}: no CALL_OPTIONS layout'
    widths = []
    for item in (x.strip() for x in m[1].split(',') if x.strip()):
        widths.append({'ValueLayout.JAVA_INT': 4, 'ValueLayout.JAVA_LONG': 8, 'PTR': 8}.get(item, item))
    names = re.search(r'dtl_call_options:([^*]*)\.', text)
    order = [x.strip() for x in names[1].split(',')] if names else []
    return list(zip(order, widths)) if len(order) == len(widths) else [(None, w) for w in widths], None


def python_layout():
    path = BINDINGS / 'python/src/ducktape_laya/_native.py'
    m = re.search(r'class CallOptions\(ctypes\.Structure\):\s*_fields_ = \[(.*?)\]\s*$', read(path), re.S | re.M) if path.is_file() else None
    if not m:
        return None, f'{path.relative_to(ROOT)}: no CallOptions._fields_'
    kinds = {'ctypes.c_uint32': 4, 'ctypes.c_uint64': 8, 'ctypes.c_void_p': 8, 'EVENT_CALLBACK': 8, 'CANCEL_CALLBACK': 8}
    fields = [(x[1], kinds.get(x[2])) for x in re.finditer(r"\('(\w+)',\s*([\w.]+)\)", m[1])]
    return fields, None


def dotnet_layout():
    path = BINDINGS / 'dotnet/DucktapeLaya/Native.cs'
    body = block(read(path), r'struct DtlCallOptions') if path.is_file() else None
    if body is None:
        return None, f'{path.relative_to(ROOT)}: no DtlCallOptions'
    kinds = {'uint': 4, 'ulong': 8, 'void*': 8}
    fields = []
    for m in re.finditer(r'public\s+([^;]+?)\s+([\w, ]+);', body):
        kind = 'void*' if '*' in m[1] else m[1]
        for name in (n.strip() for n in m[2].split(',')):
            fields.append((snake(name).lower(), kinds.get(kind, 8 if kind.startswith('delegate') else None)))
    return fields, None


LAYOUTS = {'python': python_layout, 'java': java_layout, 'dotnet': dotnet_layout}


def check_layout(lang, expected, problems):
    found, error = LAYOUTS[lang]()
    if error:
        problems.append(f'{lang}: {error}')
        return
    if len(found) != len(expected):
        problems.append(f'{lang}: call options have {len(found)} fields; the header has {len(expected)}')
        return
    for (name, width), (want_name, want_width) in zip(found, expected):
        if width != want_width or (name is not None and name != want_name):
            problems.append(f'{lang}: call options field {name or "?"} is {width} bytes; the header has {want_name} at {want_width}')


def main():
    languages = ['c'] + [lang for lang, _ in STATUS]
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--only', help='comma-separated languages: ' + ', '.join(languages))
    args = parser.parse_args()
    selected = args.only.split(',') if args.only else languages
    unknown = [lang for lang in selected if lang not in languages]
    if unknown:
        parser.error('unknown language: ' + ', '.join(unknown))
    expected = header_statuses()
    options = header_call_options()
    if [n for n, _ in options] != ['struct_size', 'reserved', 'request_id', 'timeout_ms', 'on_event', 'event_user_data',
                                   'should_cancel', 'cancel_user_data'] or [w for _, w in options] != [4, 4, 8, 8, 8, 8, 8, 8]:
        raise SystemExit(f'{HEADER.relative_to(ROOT)}: unexpected dtl_call_options {options}')
    problems = []
    for lang, sources in STATUS:
        if lang in selected:
            check_statuses(lang, sources, expected, problems)
    for lang in LAYOUTS:
        if lang in selected:
            check_layout(lang, options, problems)
    for problem in problems:
        print('binding-abi:', problem, file=sys.stderr)
    if problems:
        return 1
    print(f'binding-abi: {", ".join(selected)} match the header ({len(expected)} statuses, {len(options)} call option fields)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
