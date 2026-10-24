"""Shared maintainer checks. Standard library only; never used by consumers."""
import hashlib
import math
import re
import json
import pathlib
import subprocess
import sys
import tomllib
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]

def read_json(path):
    return json.loads(pathlib.Path(path).read_text())

def sha256(path):
    with pathlib.Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def source_files():
    """Hash tracked working-file contents; deleted files map to null."""
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    return {name: sha256(ROOT / name) if (ROOT / name).is_file() else None
            for name in sorted(filter(None, names)) if not (ROOT / name).is_dir()}

# The shared library's file name in a build directory.
LIBRARY = {'darwin': 'libducktape_laya.dylib', 'win32': 'ducktape_laya.dll'}.get(sys.platform, 'libducktape_laya.so')
# Executable suffix for test programs in a build directory.
EXE = '.exe' if sys.platform == 'win32' else ''

# Every native package target. `name` is the release platform (native archive
# suffix, inventory `platform`, jar directory native/<name>/); the npm, wheel and
# NuGet names follow each ecosystem's own convention. `files` are the names the
# shared library is shipped under next to the Node addon (real bytes, no links).
class Target(dict):
    __getattr__ = dict.__getitem__

TARGETS = [Target(t) for t in [
    {'name': 'linux-x64', 'os': 'linux', 'machines': ('x86_64', 'AMD64'), 'npm': 'linux-x64',
     'wheel': 'manylinux_2_38_x86_64', 'nuget': 'linux-x64', 'library': 'libducktape_laya.so',
     'node_libraries': ['libducktape_laya.so.1'], 'archive': '.tar.gz'},
    {'name': 'linux-arm64', 'os': 'linux', 'machines': ('aarch64', 'arm64'), 'npm': 'linux-arm64',
     'wheel': 'manylinux_2_38_aarch64', 'nuget': 'linux-arm64', 'library': 'libducktape_laya.so',
     'node_libraries': ['libducktape_laya.so.1'], 'archive': '.tar.gz'},
    {'name': 'macos-arm64', 'os': 'darwin', 'machines': ('arm64',), 'npm': 'darwin-arm64',
     'wheel': 'macosx_14_0_arm64', 'nuget': 'osx-arm64', 'library': 'libducktape_laya.dylib',
     'node_libraries': ['libducktape_laya.1.dylib'], 'archive': '.tar.gz'},
    {'name': 'windows-x64', 'os': 'win32', 'machines': ('AMD64', 'x86_64'), 'npm': 'win32-x64',
     'wheel': 'win_amd64', 'nuget': 'win-x64', 'library': 'ducktape_laya.dll',
     'node_libraries': ['ducktape_laya.dll'], 'archive': '.zip'},
]]

# Toolchain environment a build ran in, beyond the compiler's own version: the GitHub runner
# image, and on Windows the MSVC toolset and Windows SDK whose libraries are linked into the DLL.
BUILD_ENVIRONMENT = ('ImageOS', 'ImageVersion', 'VCToolsVersion', 'WindowsSDKVersion', 'VSCMD_VER')

def build_environment():
    """The toolchain environment variables that are set (none on an ordinary Linux host)."""
    import os
    return {key: os.environ[key].strip().rstrip('\\') for key in BUILD_ENVIRONMENT if os.environ.get(key)}

def target(name):
    for item in TARGETS:
        if item.name == name: return item
    raise ValueError(f'Unknown package platform: {name}')

def host_target():
    """The package target of the machine packaging or testing runs on."""
    import platform
    system = 'linux' if sys.platform.startswith('linux') else sys.platform
    for item in TARGETS:
        if item.os == system and platform.machine() in item.machines: return item
    raise ValueError(f'No package target for {sys.platform}/{platform.machine()}; '
                     f'packages are built for {", ".join(t.name for t in TARGETS)}')

def verify_model(path, manifest):
    path = pathlib.Path(path)
    if path.stat().st_size != manifest['size_bytes'] or sha256(path) != manifest['sha256']:
        raise ValueError(f'Model does not match immutable manifest: {manifest["id"]}')

def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()

def check_record(root=ROOT):
    record = read_json(root / 'release/compatibility.json')
    if record['schema_version'] != 2:
        raise ValueError('Unsupported compatibility record schema')
    check_qualification(record, root)
    check_targets(record, root)
    runtime = root / record['runtime']['path']
    expected = record['runtime']['commit']
    if git(runtime, 'rev-parse', 'HEAD') != expected:
        raise ValueError('Runtime checkout does not match compatibility record')
    if git(runtime, 'status', '--porcelain', '--untracked-files=no'):
        raise ValueError('Runtime has unrecorded source modifications')
    entry = git(root, 'ls-files', '--stage', record['runtime']['path']).split()
    if len(entry) < 2 or entry[1] != expected:
        raise ValueError('Runtime gitlink does not match compatibility record')
    for patch in record['runtime']['patches']:
        if sha256(root / patch['path']) != patch['sha256']:
            raise ValueError('Runtime patch checksum mismatch: ' + patch['path'])
    version = record['package_version']
    for package in ['ducktape-laya', 'ducktape-laya-sys']:
        info = tomllib.loads((root / 'bindings/rust' / package / 'Cargo.toml').read_text())
        if info['package']['version'] != version:
            raise ValueError(f'Rust package version mismatch: {package}')
    msrv = read_json(root / 'release/qualification.json')['platforms']['native']['rust_msrv']
    for package in ['ducktape-laya', 'ducktape-laya-sys']:
        declared = tomllib.loads((root / 'bindings/rust' / package / 'Cargo.toml').read_text())['package'].get('rust-version')
        if not declared or not msrv.startswith(declared):
            raise ValueError(f'Rust rust-version {declared} does not match the qualified MSRV {msrv}: {package}')
    safe = tomllib.loads((root / 'bindings/rust/ducktape-laya/Cargo.toml').read_text())
    if safe['dependencies']['ducktape-laya-sys']['version'] != version:
        raise ValueError('Rust ducktape-laya-sys dependency version mismatch')
    for lock in ['bindings/rust/Cargo.lock', 'tests/consumers/rust/Cargo.lock']:
        locked = {item['name']: item['version'] for item in tomllib.loads((root / lock).read_text())['package']}
        if any(locked.get(name) != version for name in ('ducktape-laya', 'ducktape-laya-sys')):
            raise ValueError(f'Rust lock file version mismatch: {lock}')
    if read_json(root / 'bindings/node/package.json')['version'] != version:
        raise ValueError('npm package version mismatch')
    python = tomllib.loads((root / 'bindings/python/pyproject.toml').read_text())['project']['version']
    if python != version or f"__version__ = '{version}'" not in (root / 'bindings/python/src/ducktape_laya/__init__.py').read_text():
        raise ValueError('Python package version mismatch')
    pom = (root / 'bindings/java/pom.xml').read_text()
    if re.search(r'<artifactId>ducktape-laya</artifactId>\s*<version>([^<]+)</version>', pom)[1] != version:
        raise ValueError('Java package version mismatch')
    if f'<Version>{version}</Version>' not in (root / 'bindings/dotnet/DucktapeLaya/DucktapeLaya.csproj').read_text():
        raise ValueError('.NET package version mismatch')
    browser = root / 'bindings/browser/package.json'
    if browser.exists() and read_json(browser)['version'] != version:
        raise ValueError('Browser package version mismatch')
    for name in record['models']:
        model = read_json(root / name)
        if model.get('llama_cpp_commit', expected) != expected:
            raise ValueError(f'Model compatibility runtime mismatch: {name}')
        if len(model['sha256']) != 64 or len(model['revision']) != 40:
            raise ValueError(f'Model must identify immutable artifact: {name}')
    for name in record.get('additional_models', []):
        model = read_json(root / name)
        if model['conversion']['llama_cpp_commit'] != expected:
            raise ValueError(f'Model converter runtime mismatch: {name}')
        if not model.get('performance_budgets', '').startswith('release/budgets/'):
            raise ValueError(f'Model performance budgets not declared: {name}')
        if len(model['sha256']) != 64 or len(model['source']['revision']) != 40:
            raise ValueError(f'Model must identify immutable artifact: {name}')
    return record

# Release targets a record may name (release_support.TARGETS names that are qualified releases).
RELEASE_PLATFORMS = ('linux-x64', 'linux-arm64', 'windows-x64')

def record_target(record, platform):
    """The release target entry of record['targets'] for this package platform."""
    for item in record['targets']:
        if item['platform'] == platform: return item
    raise ValueError(f'{platform} is not a release target')

def check_targets(record, root=ROOT):
    """Release targets: one tier-1 linux-x64 with exactly the required release gates, tier-2
    targets with gates from the policy's target_gates, the primary model on every target."""
    policy = read_json(root / 'release/qualification.json')
    targets = record.get('targets')
    if not isinstance(targets, list) or not targets:
        raise ValueError('The record lists no release targets')
    names = [item.get('platform') for item in targets]
    if len(set(names)) != len(names) or not set(names) <= set(RELEASE_PLATFORMS):
        raise ValueError(f'Release targets must be distinct and among {RELEASE_PLATFORMS}: {names}')
    first = [item for item in targets if item.get('tier') == 1]
    if len(first) != 1 or first[0]['platform'] != 'linux-x64':
        raise ValueError('Exactly one tier-1 target, linux-x64, is required')
    models = set(record['models'] + record.get('additional_models', []))
    for item in targets:
        name, tier, gates = item['platform'], item.get('tier'), item.get('required_gates')
        if tier not in (1, 2) or type(tier) is not int:
            raise ValueError(f'{name}: tier must be 1 or 2')
        if not isinstance(gates, list) or not gates or len(gates) != len(set(gates)):
            raise ValueError(f'{name}: required gates must be a non-empty list without duplicates')
        if tier == 1 and set(gates) != set(record['required_release_gates']):
            raise ValueError(f'{name}: tier-1 gates differ from required_release_gates')
        if tier == 2 and not set(gates) <= set(policy['target_gates']):
            raise ValueError(f'{name}: unknown tier-2 gates {sorted(set(gates) - set(policy["target_gates"]))}')
        listed = item.get('models')
        if not isinstance(listed, list) or record['models'][0] not in listed or not set(listed) <= models:
            raise ValueError(f'{name}: models must include the primary model and only pinned models')
        if tier == 1 and set(listed) != models:
            raise ValueError(f'{name}: the tier-1 target qualifies every pinned model')
        if tier == 2 and name not in policy['platforms'].get('targets', {}):
            raise ValueError(f'{name}: no runner and versions in release/qualification.json platforms.targets')
        profile, baseline, build = item.get('numerical_profile', {}), item.get('cpu_baseline', {}), item.get('build', {})
        if not profile.get('runtime_id') or not profile.get('compiler') or not profile.get('math_library'):
            raise ValueError(f'{name}: incomplete numerical profile')
        features = baseline.get('required_cpu_features')
        if not isinstance(features, list) or not features or profile['runtime_id'] != 'native-cpu:' + '+'.join(features):
            raise ValueError(f'{name}: CPU baseline does not match the numerical profile')
        if not build.get('built_by') or not (root / build['built_by']).is_file() or not build.get('node_headers'):
            raise ValueError(f'{name}: build workflow or Node headers missing')
        if type(item.get('electron')) is not bool:
            raise ValueError(f'{name}: electron must be true or false')
        for deviation in item.get('known_deviations', []):
            cap = deviation.get('max_probability_drift')
            if (not all(isinstance(deviation.get(key), str) and deviation[key] for key in ('cases', 'case', 'question', 'reason'))
                    or type(cap) is not float or not math.isfinite(cap) or not 0.05 < cap < 0.1):
                raise ValueError(f'{name}: malformed known deviation {deviation}')
            document = deviation.get('documented_in', '').split('#')[0]
            if not document or not (root / document).is_file():
                raise ValueError(f'{name}: known deviation is not documented')
    return targets

def find_manifest(path, record, root=ROOT):
    """The pinned manifest (primary or additional) that a local model file matches."""
    for name in [*record['models'], *record.get('additional_models', [])]:
        manifest = read_json(root / name)
        try:
            verify_model(path, manifest)
            return manifest
        except ValueError:
            pass
    raise ValueError(f'{path} matches no pinned model manifest')

def check_qualification(record, root=ROOT):
    """Keep the executable required-gate list and product requirements in sync."""
    policy = read_json(root / 'release/qualification.json')
    if policy.get('schema_version') != 1 or policy.get('status') != 'qualification_target':
        raise ValueError('Unsupported qualification policy')
    required = record['required_release_gates']
    gates = policy['qualification_gates']
    if len(required) != len(set(required)) or set(required) != set(gates):
        raise ValueError('Required release gates differ from qualification policy')
    targets = policy.get('target_gates')
    if not isinstance(targets, dict) or not targets:
        raise ValueError('Missing target gate definitions')
    for name, gate in [*gates.items(), *targets.items()]:
        if not gate.get('acceptance') or not gate.get('implementation') or not gate.get('checks'):
            raise ValueError('Incomplete qualification gate: ' + name)
        for path in gate['implementation'] + gate['checks']:
            resolved = (root / path).resolve()
            if not resolved.is_relative_to(root.resolve()) or not resolved.exists():
                raise ValueError('Missing/outside qualification reference: ' + path)
    for key in ['routine_stress_seconds', 'candidate_soak_seconds', 'fuzz_seconds_per_target', 'baseline_runs', 'short_stress_seconds']:
        value = policy['budgets'].get(key)
        if type(value) is not int or value <= 0:
            raise ValueError('Invalid qualification budget: ' + key)
    if policy['budgets']['short_stress_seconds'] >= policy['budgets']['routine_stress_seconds']:
        raise ValueError('The short stress budget must be shorter than routine stress')
    if policy.get('numerical_contract', {}).get('policy') != 'distinct_profiles':
        raise ValueError('Unsupported numerical contract')
    binding = policy['budgets'].get('native_binding_absolute_tolerance')
    if type(binding) not in (int, float) or binding != 0:
        raise ValueError('Same-binary binding conformance must be exact')
    upstream = policy['budgets'].get('native_upstream_absolute_tolerance')
    if type(upstream) not in (int, float) or not math.isfinite(upstream) or upstream < 0:
        raise ValueError('Invalid upstream tolerance')
    if policy['budgets'].get('browser_absolute_tolerance_source') != 'release/browser-toolchain.json#upstream_absolute_tolerance':
        raise ValueError('Browser tolerance must apply to same-backend upstream comparison')
    return policy

def security_review_problems(review):
    """Shape problems of the hand-written security review evidence (docs/RELEASE_CHECKS.md, Security review)."""
    if not isinstance(review, dict):
        return ['not a JSON object']
    problems = []
    for key in ('source_commit', 'runtime_commit', 'model_licence'):
        if not isinstance(review.get(key), str) or not review[key].strip():
            problems.append(f'{key} must be a non-empty string')
    if review.get('source_tracked_dirty') is not False:
        problems.append('source_tracked_dirty must be false')
    patches = review.get('runtime_patches')
    if not isinstance(patches, list) or not all(isinstance(h, str) and len(h) == 64 for h in patches):
        problems.append('runtime_patches must be a list of SHA-256 hex digests')
    if not isinstance(review.get('release_blocking_code_issues'), list):
        problems.append('release_blocking_code_issues must be a list')
    return problems

INSTRUMENTATION_SYMBOL = re.compile(r'\b_?__(?:asan|ubsan|tsan|msan|sanitizer_cov|llvm_profile)_\w+')
# Sanitizer runtimes and debug C/C++ runtimes a release binary must not load.
DIAGNOSTIC_RUNTIME = re.compile(r'(?i)clang_rt\.|asan|ubsan|tsan|msan|^(?:vcruntime\d+d|msvcp\d+d|ucrtbased|concrt\d+d)\.dll$')

def require_uninstrumented_binary(path):
    """Reject diagnostic native runtimes before loading or packaging them."""
    head = pathlib.Path(path).read_bytes()[:4]
    if head == b'\x7fELF':
        symbols = subprocess.check_output(['readelf', '--dyn-syms', '--wide', str(path)], text=True)
        instrumented = INSTRUMENTATION_SYMBOL.search(symbols)
    elif head[:2] == b'MZ':
        info = pe_info(path)
        instrumented = any(DIAGNOSTIC_RUNTIME.search(name) for name in info['imports'] + info['delay_imports'])
    else:
        symbols = subprocess.check_output(['nm', '-a', str(path)], text=True)
        instrumented = (INSTRUMENTATION_SYMBOL.search(symbols) or
                        any(DIAGNOSTIC_RUNTIME.search(pathlib.PurePosixPath(name).name) for name in macho_info(path)['dylibs']))
    if instrumented:
        raise ValueError('Instrumented binary cannot be packaged: ' + str(path))

# A source path compiled into a binary (__FILE__ in assertions and log messages), as its strings hold
# it: a whole printable string, NUL-terminated, with a directory separator, ending in a C or C++ source or header suffix.
SOURCE_PATH = re.compile(rb'(?<![\x20-\x7e])([\x21-\x7e]*[/\\][\x21-\x7e]*\.(?:c|cc|cpp|cxx|h|hh|hpp|inc))(?=\x00)')

def require_mapped_source_paths(path, root=ROOT):
    """Reject a binary holding a source path the reproducible-path map did not map.

    With DUCKTAPE_REPRODUCIBLE_PATHS, -ffile-prefix-map maps the source, runtime and build directories
    to ./, ./third_party/llama.cpp and ./build, so every source path in a release binary starts (after
    a leading ./, which GCC keeps and Clang drops; Windows separators are \\) with a top-level entry of
    the checkout or with build. A compiler launcher that rewrites the paths it gives the compiler
    (ccache's base_dir makes them relative: ../../src/laya.cpp, or runtime-patched/... from the build
    directory) defeats the map and changes the binary's bytes; so does an absolute or unmapped path."""
    path = pathlib.Path(path)
    roots = {entry.name for entry in pathlib.Path(root).iterdir()} | {'build'}
    def mapped(found):
        parts = re.split(r'[/\\]', found)
        if parts[0] == '.': parts = parts[1:]
        return bool(parts) and parts[0] in roots
    unmapped = sorted({found for found in (raw.decode('utf-8', 'replace') for raw in SOURCE_PATH.findall(path.read_bytes()))
                       if not mapped(found)})
    if unmapped:
        raise ValueError(f'{path.name} holds source paths outside the reproducible-path map (a compiler cache with base_dir, '
                         'or an unmapped directory): ' + ', '.join(unmapped[:5]) + (f' and {len(unmapped) - 5} more' if len(unmapped) > 5 else ''))

def pe_info(path):
    """Exports, imported DLLs (direct and delay-loaded) and machine of a PE32+ image."""
    import struct
    data = pathlib.Path(path).read_bytes()
    try:
        if data[:2] != b'MZ': raise ValueError('no MZ header')
        pe = struct.unpack_from('<I', data, 0x3c)[0]
        if data[pe:pe + 4] != b'PE\0\0': raise ValueError('no PE signature')
        machine, sections, _, _, _, optional_size, _ = struct.unpack_from('<HHIIIHH', data, pe + 4)
        optional = pe + 24
        if struct.unpack_from('<H', data, optional)[0] != 0x20b: raise ValueError('not PE32+')
        characteristics = struct.unpack_from('<H', data, optional + 70)[0]
        count = struct.unpack_from('<I', data, optional + 108)[0]
        directories = [struct.unpack_from('<II', data, optional + 112 + 8 * i) for i in range(min(count, 16))]
        table = [struct.unpack_from('<8sIIII', data, optional + optional_size + 40 * i) for i in range(sections)]
        def offset(rva):
            for _, virtual_size, address, raw_size, raw in table:
                if address <= rva < address + max(virtual_size, raw_size): return rva - address + raw
            raise ValueError(f'RVA {rva:#x} outside every section')
        def string(rva):
            start = offset(rva)
            return data[start:data.index(b'\0', start)].decode('ascii')
        def descriptors(index, size, name_at):
            rva = directories[index][0] if index < len(directories) else 0
            names, position = [], offset(rva) if rva else None
            while position is not None:
                entry = data[position:position + size]
                if not any(entry): break
                names.append(string(struct.unpack_from('<I', entry, name_at)[0]))
                position += size
            return names
        exports = []
        if directories[0][0]:
            directory = offset(directories[0][0])
            number, _, names = struct.unpack_from('<III', data, directory + 24)
            exports = [string(struct.unpack_from('<I', data, offset(names) + 4 * i)[0]) for i in range(number)]
        return {'machine': machine, 'dll_characteristics': characteristics, 'exports': sorted(exports), 'imports': descriptors(1, 20, 12),
                'delay_imports': descriptors(13, 32, 4)}
    except (struct.error, IndexError, ValueError) as error:
        raise ValueError(f'{path}: not a readable PE32+ image ({error})') from None


def require_elf_hardening(path):
    """Full RELRO: a GNU_RELRO segment and BIND_NOW (flags NOW), so the GOT is read-only after load."""
    path = pathlib.Path(path)
    dynamic = subprocess.check_output(['readelf', '-dW', str(path)], text=True)
    segments = subprocess.check_output(['readelf', '-lW', str(path)], text=True)
    if 'GNU_RELRO' not in segments or not re.search(r'\(FLAGS\).*\bBIND_NOW\b|\(FLAGS_1\).*\bNOW\b', dynamic):
        raise SystemExit(f'{path.name} lacks full RELRO (GNU_RELRO and BIND_NOW)')


def require_guard_cf(path):
    """Control Flow Guard: IMAGE_DLLCHARACTERISTICS_GUARD_CF set in the PE header."""
    if not pe_info(path)['dll_characteristics'] & 0x4000:
        raise SystemExit(f'{pathlib.Path(path).name} is not linked with Control Flow Guard (GUARD_CF)')


# A64 instructions of -mbranch-protection=standard: PACIASP signs the return address, AUTIASP checks it.
PACIASP, AUTIASP = 0xd503233f, 0xd50323bf


def require_compiled_hardening(path, library):
    """Smoke checks of the compiler hardening (CMakeLists.txt) on a shipped ELF binary: the stack protector's failure
    handler is imported, a library calls fortified (_FORTIFY_SOURCE) functions, and on arm64 the code signs return
    addresses (PAC). BTI landing pads are compiled in too, but the binaries are not marked BTI-compatible: Ubuntu
    24.04's glibc start files crti.o and crtn.o carry no BTI property, so the linker leaves the GNU property note out."""
    import struct
    path = pathlib.Path(path)
    symbols = set(re.findall(r'\b(__\w+_chk(?:_fail)?)\b', subprocess.check_output(['readelf', '--dyn-syms', '--wide', str(path)], text=True)))
    if '__stack_chk_fail' not in symbols:
        raise SystemExit(f'{path.name} does not use the stack protector (no __stack_chk_fail)')
    if library and not symbols - {'__stack_chk_fail'}:
        raise SystemExit(f'{path.name} calls no fortified function (no __*_chk): built without _FORTIFY_SOURCE?')
    if re.search(r'Machine:\s+AArch64', subprocess.check_output(['readelf', '-hW', str(path)], text=True)):
        found = re.search(r'\] \.text\s+PROGBITS\s+[0-9a-f]+ ([0-9a-f]+) ([0-9a-f]+)', subprocess.check_output(['readelf', '-SW', str(path)], text=True))
        if not found:
            raise SystemExit(f'{path.name} has no .text section')
        offset, size = int(found[1], 16), int(found[2], 16) // 4 * 4
        with path.open('rb') as stream:
            stream.seek(offset)
            words = set(struct.unpack(f'<{size // 4}I', stream.read(size)))
        if PACIASP not in words or AUTIASP not in words:
            raise SystemExit(f'{path.name} does not sign return addresses (no PACIASP/AUTIASP): built without -mbranch-protection?')


def macho_info(path):
    """Linked dylibs, run-path entries and minimum macOS version of a 64-bit Mach-O image."""
    import struct
    data = pathlib.Path(path).read_bytes()
    try:
        magic, cpu, _, _, commands, _, _, _ = struct.unpack_from('<IiiIIIII', data, 0)
        if magic != 0xfeedfacf: raise ValueError('not a 64-bit little-endian Mach-O')
        dylibs, rpaths, minimum, position = [], [], None, 32
        for _ in range(commands):
            command, size = struct.unpack_from('<II', data, position)
            if command in (0xc, 0x18 | 0x80000000, 0x1f | 0x80000000):  # LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB
                start = position + struct.unpack_from('<I', data, position + 8)[0]
                dylibs.append(data[start:data.index(b'\0', start)].decode())
            elif command == 0x8000001c:  # LC_RPATH
                start = position + struct.unpack_from('<I', data, position + 8)[0]
                rpaths.append(data[start:data.index(b'\0', start)].decode())
            elif command == 0x32:  # LC_BUILD_VERSION
                packed = struct.unpack_from('<I', data, position + 12)[0]
                minimum = f'{packed >> 16}.{(packed >> 8) & 0xff}'
            position += size
        return {'cpu': cpu, 'dylibs': dylibs, 'rpaths': rpaths, 'minimum_os': minimum}
    except (struct.error, ValueError) as error:
        raise ValueError(f'{path}: not a readable Mach-O image ({error})') from None

def binary_info(build):
    """Inspect the built ABI without a model; detect stale/incompatible artifacts."""
    import ctypes
    library = ctypes.CDLL(str(pathlib.Path(build).resolve() / LIBRARY))
    library.dtl_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
    library.dtl_info.restype = ctypes.c_int32
    library.dtl_free.argtypes = [ctypes.c_void_p]
    library.dtl_free.restype = None
    result, error = ctypes.c_void_p(), ctypes.c_void_p()
    status = library.dtl_info(None, ctypes.byref(result), ctypes.byref(error))
    try:
        if status: raise ValueError('Built library diagnostics failed: ' + str(status))
        return json.loads(ctypes.string_at(result).decode())
    finally:
        library.dtl_free(result); library.dtl_free(error)

def source_identity(root=ROOT):
    """Use the same fingerprint definition as native and browser CMake builds."""
    with tempfile.TemporaryDirectory(prefix='ducktape-source-identity-') as directory:
        output = pathlib.Path(directory) / 'source.json'
        subprocess.run(['cmake', f'-DDTL_SOURCE_ROOT={root}', f'-DOUTPUT={output}',
                        '-P', str(root / 'cmake/source_identity.cmake')], check=True, capture_output=True)
        return read_json(output)

def require_build_source(identity, expected, label):
    if identity.get('source') != expected:
        raise ValueError(label + ': build sources differ from packaging sources; rebuild first')

def require_runtime_identity(diagnostics, record):
    expected = {'package_version': record['package_version'], 'abi_version': record['abi_version'],
                'llama_cpp_commit': record['runtime']['commit'], 'runtime_patches': record['runtime']['patches']}
    for key, value in expected.items():
        if diagnostics.get(key) != value:
            raise ValueError('Built library ' + key + ' differs from approved release record; rebuild first')

def native_build_identity(build, target, binary, expected):
    path = build / (target + '.build.json')
    if not path.is_file():
        raise ValueError(target + ': missing completed build identity; rebuild first')
    identity = read_json(path)
    require_build_source(identity, expected, target)
    if (identity.get('binary_name') != (build / binary).resolve().name or
        identity.get('binary_sha256') != sha256(build / binary)):
        raise ValueError(target + ': binary differs from completed build record; rebuild first')
    return identity

def runtime_sources(runtime, root=ROOT):
    """The manifest of runtime files a native build compiles (cmake/runtime_sources.cmake), as they are now."""
    with tempfile.TemporaryDirectory(prefix='ducktape-runtime-sources-') as directory:
        output = pathlib.Path(directory) / 'runtime-sources.json'
        subprocess.run(['cmake', f'-DRUNTIME={pathlib.Path(runtime).resolve().as_posix()}', f'-DOUTPUT={output}',
                        '-P', str(root / 'cmake/runtime_sources.cmake')], check=True, capture_output=True)
        return read_json(output)['upstream']

def _changed(recorded, current):
    names = sorted(name for name in recorded.keys() | current.keys() if recorded.get(name) != current.get(name))
    return ', '.join(names[:8]) + (f' and {len(names) - 8} more' if len(names) > 8 else '')

def require_runtime_sources(build, runtime, binary=LIBRARY, target='ducktape_laya', root=ROOT):
    """Reject a library linked from runtime sources other than the current tree's.

    The build records the runtime files it compiles when it configures (with the patched copies it
    made) and each time it links the library (with the library's hash). A library linked while the
    runtime tree, or a patched copy, was modified keeps that record after the tree is restored, so it
    cannot be packaged as the pinned runtime; rebuilding from the restored tree replaces it."""
    build = pathlib.Path(build)
    linked_path, configured_path = build / (target + '.runtime-sources.json'), build / 'generated/runtime-sources.configure.json'
    if not linked_path.is_file() or not configured_path.is_file():
        raise ValueError(target + ': missing runtime source record; rebuild first')
    linked, configured = read_json(linked_path), read_json(configured_path)
    if linked.get('binary_name') != (build / binary).resolve().name or linked.get('binary_sha256') != sha256(build / binary):
        raise ValueError(target + ': library differs from the one its runtime source record describes; rebuild first')
    current = runtime_sources(runtime, root)
    if linked.get('upstream') != current:
        raise ValueError(f'{target}: linked from runtime sources that differ from the current runtime tree '
                         f'({_changed(linked.get("upstream") or {}, current)}); rebuild from the pinned runtime')
    if linked.get('patched') != configured.get('patched'):
        raise ValueError(f'{target}: patched runtime copies changed after configure '
                         f'({_changed(linked.get("patched") or {}, configured.get("patched") or {})}); reconfigure and rebuild')
    originals = {name: (configured.get('upstream') or {}).get(name) for name in configured.get('patched') or {}}
    if originals != {name: current.get(name) for name in originals}:
        raise ValueError(f'{target}: runtime patched from sources that differ from the current runtime tree '
                         f'({_changed(originals, {name: current.get(name) for name in originals})}); reconfigure and rebuild')
    if linked.get('stale'):
        raise ValueError(f'{target}: linked while runtime files that had changed back with timestamps older than the previous link were '
                         f'not recompiled ({", ".join(linked["stale"][:8])}); touch them and rebuild, or build in a fresh directory')
    return {'upstream_files': len(current), 'patched_files': len(originals)}

def browser_build_identity(build, backend, record, toolchain, expected_source):
    """Reject swapped, stale or modified browser assets before packaging."""
    identity = read_json(build / 'laya.build.json')
    if identity.get('backend') != backend:
        raise ValueError(backend + ': built backend differs from package slot; rebuild with the correct backend')
    require_build_source(identity, expected_source, backend)
    if identity.get('maximum_memory') != 4294967296:
        raise ValueError(backend + ': not the product WebAssembly memory ceiling (test build); rebuild first')
    # scripts/build-browser.py builds the product at -O1 without GPU tracing.
    if identity.get('optimization') != '1' or identity.get('webgpu_debug') is not False:
        raise ValueError(backend + ': not a product build (optimization or GPU tracing differs); rebuild with scripts/build-browser.py')
    if (identity.get('package_version') != record['package_version'] or
        identity.get('llama_cpp_commit') != record['runtime']['commit'] or
        identity.get('runtime_patches') != record['runtime']['patches'] or
        toolchain['sdk_version'] not in identity.get('toolchain', '') or
        toolchain['emscripten_commit'] not in identity.get('toolchain', '')):
        raise ValueError(backend + ': built identity differs from release/toolchain record; rebuild first')
    for name in ['laya.mjs', 'laya.wasm']:
        if sha256(build / name) != identity.get('assets', {}).get(name):
            raise ValueError(backend + ': asset hash differs from completed build record')
    return identity
