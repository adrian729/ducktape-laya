#!/usr/bin/env python3
"""Reconfiguring an unchanged build recompiles nothing (model-free).

The configure step writes the patched runtime copies (runtime-patched/) and
ggml-version.h; cmake/generated_files.cmake replaces them only when their content
changes. This test reconfigures a completed build, checks that those files and
every object file keep their timestamps and that a second build leaves the
library as it was. With --edit-copy it also edits a patched copy: the next
configure restores it, the build recompiles that copy alone, and packaging's
runtime source record accepts the result (release_support.require_runtime_sources).

Without --build it configures and builds the library in a temporary directory
(Ninja when available, tests off), which takes a full library build. With
--build it reuses a completed build directory, for example validate.py's
build-validation after the run.
"""
import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
from release_support import LIBRARY, ROOT, check_record, require_runtime_sources, sha256

OBJECT_SUFFIXES = ('.o', '.obj')


def times(paths):
    return {path: path.stat().st_mtime_ns for path in paths}


def objects(build):
    return [path for path in build.rglob('*') if path.suffix in OBJECT_SUFFIXES and path.is_file()]


def generated(build):
    return [path for path in (build / 'runtime-patched').rglob('*') if path.is_file()] + \
        [build / 'third_party/llama.cpp/ggml/src/ggml-version.h']


def changed(before, after):
    return sorted(str(path) for path in before.keys() | after.keys() if before.get(path) != after.get(path))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--build', type=pathlib.Path, help='A completed build directory to reconfigure (default: a fresh temporary build)')
    p.add_argument('--target', action='append', help='Build target, repeatable (default: ducktape_laya for a fresh build, all with --build)')
    p.add_argument('--jobs', type=int, default=2)
    p.add_argument('--edit-copy', action='store_true', help='Also edit a patched copy and check that only it is recompiled')
    a = p.parse_args()
    record = check_record()
    runtime = ROOT / record['runtime']['path']
    temporary = None
    if a.build:
        build = a.build.resolve()
        if not (build / 'CMakeCache.txt').is_file():
            p.error(f'{build} is not a configured build directory')
        targets = a.target or []
    else:
        temporary = tempfile.TemporaryDirectory(prefix='ducktape-reconfigure-')
        build = pathlib.Path(temporary.name) / 'build'
        targets = a.target or ['ducktape_laya']
        generator = ['-G', 'Ninja'] if shutil.which('ninja') else []
        cmake('-S', ROOT, '-B', build, *generator, '-DCMAKE_BUILD_TYPE=Release', '-DDUCKTAPE_BUILD_TESTS=OFF',
              '-DDUCKTAPE_BUILD_NODE=OFF', '-DGGML_CCACHE=OFF')
    try:
        compile_targets(build, targets, a.jobs)
        library = build / LIBRARY
        require_runtime_header(build, record)
        before_objects, before_generated = times(objects(build)), times(generated(build))
        before_library = (sha256(library), library.stat().st_mtime_ns)
        if not before_objects:
            raise SystemExit(f'reconfigure: no object files under {build}')
        cmake('-S', ROOT, '-B', build)
        require_runtime_header(build, record)
        difference = changed(before_generated, times(generated(build)))
        if difference:
            raise SystemExit('reconfigure: the configure step rewrote unchanged generated sources: ' + ', '.join(difference))
        compile_targets(build, targets, a.jobs)
        difference = changed(before_objects, times(objects(build)))
        if difference:
            raise SystemExit(f'reconfigure: an unchanged reconfigure recompiled {len(difference)} objects: ' + ', '.join(difference[:16]))
        if (sha256(library), library.stat().st_mtime_ns) != before_library:
            raise SystemExit('reconfigure: an unchanged reconfigure relinked the library')
        print(f'reconfigure: unchanged reconfigure kept {len(before_generated)} generated sources and recompiled none of {len(before_objects)} objects')
        if a.edit_copy:
            edit_copy(build, targets, a.jobs, runtime)
    finally:
        if temporary:
            temporary.cleanup()


def require_runtime_header(build, record):
    """ggml-version.h names the pinned runtime commit (upstream's configure writes its own first)."""
    header = (build / 'third_party/llama.cpp/ggml/src/ggml-version.h').read_text()
    if f'#define GGML_COMMIT "{record["runtime"]["commit"]}"' not in header:
        raise SystemExit('reconfigure: ggml-version.h does not name the pinned runtime commit')


def edit_copy(build, targets, jobs, runtime):
    copy = build / 'runtime-patched/ggml/src/ggml.c'
    original = copy.read_bytes()
    before = times(objects(build))
    copy.write_bytes(original + b'\n/* edited */\n')
    cmake('-S', ROOT, '-B', build)
    if copy.read_bytes() != original:
        raise SystemExit('reconfigure: the configure step did not restore an edited patched copy')
    compile_targets(build, targets, jobs)
    recompiled = changed(before, times(objects(build)))
    if len(recompiled) != 1 or not recompiled[0].endswith(('ggml.c.o', 'ggml.c.obj')):
        raise SystemExit('reconfigure: restoring one patched copy recompiled ' + (', '.join(recompiled) or 'nothing'))
    require_runtime_sources(build, runtime)
    print('reconfigure: an edited patched copy was restored, recompiled alone and accepted by the runtime source record')


def compile_targets(build, targets, jobs):
    cmake('--build', build, '--parallel', jobs, *(['--target', *targets] if targets else []))


def cmake(*args):
    result = subprocess.run(['cmake', *map(str, args)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode:
        sys.stdout.write(result.stdout)
        raise SystemExit(f'reconfigure: cmake {" ".join(map(str, args))} failed')


if __name__ == '__main__':
    main()
