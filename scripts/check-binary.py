#!/usr/bin/env python3
"""Check the release constraints of a built native library and Node addon.

    check-binary.py BUILD                     the library and (when built) the addon of BUILD
    check-binary.py --library PATH [--addon PATH]

The constraints every shipped binary must meet, whatever produced it: no sanitizer or
profiling instrumentation (symbols or diagnostic runtimes), only source paths inside the
reproducible-path map; on Linux full RELRO (GNU_RELRO and BIND_NOW), the stack protector,
_FORTIFY_SOURCE on the library, return-address signing (PAC) on arm64, and the GLIBC/GLIBCXX
symbol versions within the floors of release/qualification.json and the wheel tag; on
Windows an x64 image, Control Flow Guard and exactly the exported C API of src/exports.macos;
on macOS a deployment target no newer than the wheel tag. scripts/package-native.py runs
these checks before packaging and scripts/validate.py runs them as `check-binary`.

Prints a JSON summary (`status`, `problems`, per-binary details, `required_runtime_symbol_versions`
on Linux, `minimum_macos` and `linked_libraries` on macOS, `linked_libraries` and
`delay_loaded_libraries` on Windows); exits 1 when any constraint fails.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from release_support import (ROOT, read_json, sha256, host_target, target as named_target, require_uninstrumented_binary,
                             require_mapped_source_paths, require_elf_hardening, require_origin_search_paths, require_compiled_hardening,
                             require_guard_cf, require_macho_cpu, require_macho_rpaths, require_macho_hardening, require_macho_signature,
                             require_macho_linkage,
                             pe_info, macho_info)

ADDON = 'ducktape_laya.node'


def _problem(check, *args):
    """The message of a failed check (the release_support checks raise ValueError or SystemExit), or None."""
    try:
        check(*args)
    except (ValueError, SystemExit) as error:
        return str(error) or type(error).__name__
    return None


def binary_format(path):
    head = pathlib.Path(path).read_bytes()[:4]
    if head == b'\x7fELF':
        return 'elf'
    if head[:2] == b'MZ':
        return 'pe'
    if head == b'\xcf\xfa\xed\xfe':
        return 'macho'
    return 'unknown'


def hardening_problems(path, library):
    """The hardening of one ELF binary (also applied by package-native.py to the stripped shipped files)."""
    return [problem for problem in (_problem(require_elf_hardening, path), _problem(require_origin_search_paths, path),
                                    _problem(require_compiled_hardening, path, library)) if problem]


def macho_problems(path, package_target, library):
    """The shipping checks of one Mach-O binary (also applied by package-native.py to the installed files): CPU type,
    run-path entries, compiler hardening, code signature, linked libraries, and for the library its exports."""
    extra = () if library else tuple('@rpath/' + name for name in package_target.node_libraries)
    problems = [problem for problem in (_problem(require_macho_cpu, path, package_target.name), _problem(require_macho_rpaths, path),
                                        _problem(require_macho_hardening, path, library), _problem(require_macho_signature, path, package_target.name),
                                        _problem(require_macho_linkage, path, extra)) if problem]
    if library:
        exports = [name.lstrip('_') for name in macho_info(path)['exports']]
        if exports != expected_exports():
            problems.append(f'{pathlib.Path(path).name} exports {exports}, expected exactly {expected_exports()}')
    return problems


def _version(text):
    return tuple(map(int, text.split('.')))


def _glibc_problems(library, package_target):
    """GLIBC/GLIBCXX/CXXABI symbol versions the library needs, against the declared floors and the wheel tag."""
    symbols = subprocess.check_output(['readelf', '--version-info', str(library)], text=True)
    requirements = sorted(set(re.findall(r'Name: ((?:GLIBC|GLIBCXX|CXXABI)_[0-9.]+)', symbols)))

    def newest(prefix):
        return max((_version(item[len(prefix):]) for item in requirements if item.startswith(prefix)), default=())
    platform = read_json(ROOT / 'release/qualification.json')['platforms']['native']
    wheel_glibc = tuple(map(int, re.search(r'manylinux_(\d+)_(\d+)', package_target.wheel).groups()))
    problems = []
    for prefix, limit in (('GLIBC_', wheel_glibc), ('GLIBC_', _version(platform['maximum_glibc'])),
                          ('GLIBCXX_', _version(platform['maximum_glibcxx']))):
        if newest(prefix) > limit:
            problems.append(f'Library needs {prefix}{".".join(map(str, newest(prefix)))}, above the declared {".".join(map(str, limit))}')
    return requirements, problems


def expected_exports():
    return sorted(line.strip().lstrip('_') for line in (ROOT / 'src/exports.macos').read_text().splitlines() if line.strip())


def check_binaries(library, addon=None, platform=None, root=ROOT):
    """Check LIBRARY (and ADDON when given) for PLATFORM (default: this host's package target). Returns the summary;
    `summary['status']` is 'passed' only when `summary['problems']` is empty. Never raises for a failed constraint."""
    package_target = named_target(platform) if platform else host_target()
    library = pathlib.Path(library)
    binaries = {'library': library} | ({'addon': pathlib.Path(addon)} if addon else {})
    problems, details, summary = [], {}, {'platform': package_target.name}
    for role, path in binaries.items():
        if not path.is_file():
            problems.append(f'{role}: {path} missing')
            continue
        kind = binary_format(path)
        details[role] = {'path': path.as_posix(), 'sha256': sha256(path), 'format': kind}
        own = [problem for problem in (_problem(require_uninstrumented_binary, path),
                                       _problem(require_mapped_source_paths, path, root)) if problem]
        expected = {'linux': 'elf', 'win32': 'pe', 'darwin': 'macho'}[package_target.os]
        if kind != expected:
            own.append(f'{path.name} is a {kind} binary, not the {expected} binary of {package_target.name}')
        elif kind == 'elf':
            own += hardening_problems(path, library=role == 'library')
        elif kind == 'pe':
            info = pe_info(path)
            if info['machine'] != 0x8664:
                own.append(f'{path.name} is not an x64 image')
            own += [problem for problem in (_problem(require_guard_cf, path),) if problem]
            if role == 'library' and info['exports'] != expected_exports():
                own.append(f'{path.name} exports {info["exports"]}, expected exactly {expected_exports()}')
            summary.setdefault('linked_libraries', {})[path.name] = sorted(info['imports'])
            summary.setdefault('delay_loaded_libraries', {})[path.name] = sorted(info['delay_imports'])
        elif kind == 'macho':
            info = macho_info(path)
            wheel_macos = tuple(map(int, re.search(r'macosx_(\d+)_(\d+)', package_target.wheel).groups()))
            if not info['minimum_os'] or _version(info['minimum_os']) > wheel_macos:
                own.append(f'{path.name} targets macOS {info["minimum_os"]}, above the wheel tag {package_target.wheel}')
            if role == 'library':
                summary['minimum_macos'] = info['minimum_os']
            summary.setdefault('linked_libraries', {})[path.name] = info['dylibs']
            own += macho_problems(path, package_target, library=role == 'library')
        details[role]['problems'] = own
        problems += own
    if package_target.os == 'linux' and 'library' in details and details['library']['format'] == 'elf':
        requirements, floor = _glibc_problems(library, package_target)
        summary['required_runtime_symbol_versions'] = requirements
        details['library']['problems'] += floor
        problems += floor
    return {'status': 'failed' if problems else 'passed', 'problems': problems, **summary, 'binaries': details}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('build', nargs='?', type=pathlib.Path, help='a build directory (its library, and its addon when built)')
    parser.add_argument('--library', type=pathlib.Path)
    parser.add_argument('--addon', type=pathlib.Path)
    parser.add_argument('--platform', help='the package target the binaries are for (default: this host\'s)')
    args = parser.parse_args(argv)
    if (args.build is None) == (args.library is None):
        parser.error('give a build directory or --library, not both')
    if args.build is not None and args.addon is not None:
        parser.error('--addon goes with --library; a build directory\'s addon is found in it')
    try:
        package_target = named_target(args.platform) if args.platform else host_target()
    except ValueError as error:
        parser.error(str(error))
    if args.build is not None:
        library = args.build / package_target.library
        addon = args.build / ADDON if (args.build / ADDON).is_file() else None
    else:
        library, addon = args.library, args.addon
    summary = check_binaries(library, addon, package_target.name)
    print(json.dumps(summary, indent=2))
    return 0 if summary['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
