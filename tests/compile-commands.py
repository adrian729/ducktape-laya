#!/usr/bin/env python3
"""Compare the library's object-library compile commands with main's (F3).

compile-commands.py [--fuzz] MAIN.json NEW.json: NEW's src/json_io.cpp command must equal MAIN's
src/laya.cpp command, and NEW's src/language.cpp command MAIN's src/language.cpp command (the
library's copies), argument by argument and in order, after removing -o, -MF and -MT with their
values, the source path, -Dducktape_laya_EXPORTS (shared-library sources only) and
-Werror=date-time (added to every compile since). Paths are compared relative to each file's
source and build directories. --fuzz also requires -fsanitize=fuzzer-no-link in both NEW
commands. Exits 1 at the first difference.
"""
import json
import pathlib
import shlex
import sys

DROPPED = {'-Dducktape_laya_EXPORTS', '-Werror=date-time', '/clang:-Werror=date-time'}
LIBRARY_TARGETS = ('/ducktape_laya.dir/', '/dtl_json_io.dir/', '/dtl_language.dir/')

def command(path, source):
    found = []
    for entry in json.loads(pathlib.Path(path).read_text()):
        file = pathlib.Path(entry['file']).as_posix()
        if not file.endswith('/' + source):
            continue
        args = entry['arguments'] if 'arguments' in entry else shlex.split(entry['command'])
        output = entry.get('output') or (args[args.index('-o') + 1] if '-o' in args else '')
        if any(target in '/' + pathlib.PurePath(output).as_posix() for target in LIBRARY_TARGETS):
            found.append((args, file[:-len(source)].rstrip('/'), pathlib.Path(entry['directory']).as_posix()))
    if len(found) != 1:
        sys.exit(f'{path}: {len(found)} library compile commands for {source}, expected 1')
    args, root, build = found[0]
    kept, skip = [], False
    for arg in args:
        if skip or arg in DROPPED or arg == source or pathlib.PurePath(arg).as_posix().endswith('/' + source):
            skip = False
            continue
        skip = arg in ('-o', '-MF', '-MT')
        if not skip:
            kept.append(arg.replace(build, '<BUILD>').replace(root, '<SOURCE>'))
    return kept

def fuzz_instrumented(args):
    return any(arg.startswith('-fsanitize=') and 'fuzzer-no-link' in arg[len('-fsanitize='):].split(',') for arg in args)

args = sys.argv[1:]
fuzz = args[:1] == ['--fuzz']
if fuzz:
    args = args[1:]
if len(args) != 2:
    sys.exit(__doc__)
main, new = args
for old_source, new_source in [('src/laya.cpp', 'src/json_io.cpp'), ('src/language.cpp', 'src/language.cpp')]:
    before, after = command(main, old_source), command(new, new_source)
    if fuzz and not fuzz_instrumented(after):
        sys.exit(f'{new_source}: no -fsanitize=fuzzer-no-link in {new}')
    for index, (a, b) in enumerate(zip(before, after)):
        if a != b:
            sys.exit(f'{new_source}: argument {index} differs from main\'s {old_source}: {a!r} != {b!r}')
    if len(before) != len(after):
        extra = before[len(after)] if len(before) > len(after) else after[len(before)]
        sys.exit(f'{new_source}: {len(before)} arguments in main\'s {old_source}, {len(after)} here; first extra: {extra!r}')
    print(f'{new_source}: same compile command as main\'s {old_source} ({len(after)} arguments)')
