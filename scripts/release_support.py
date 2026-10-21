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

# Package platform names for the machine packaging runs on: (directory, wheel arch).
PLATFORMS = {'x86_64': ('linux-x64', 'x86_64'), 'AMD64': ('linux-x64', 'x86_64'),
             'aarch64': ('linux-arm64', 'aarch64'), 'arm64': ('linux-arm64', 'aarch64')}

def host_platform():
    import platform
    if platform.machine() not in PLATFORMS:
        raise ValueError(f'Packages are built for Linux x86-64 and arm64, not {platform.machine()}')
    return PLATFORMS[platform.machine()]

def verify_model(path, manifest):
    path = pathlib.Path(path)
    if path.stat().st_size != manifest['size_bytes'] or sha256(path) != manifest['sha256']:
        raise ValueError(f'Model does not match immutable manifest: {manifest["id"]}')

def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()

def check_record(root=ROOT):
    record = read_json(root / 'release/compatibility.json')
    if record['schema_version'] != 1:
        raise ValueError('Unsupported compatibility record schema')
    check_qualification(record, root)
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
    for name, gate in gates.items():
        if not gate.get('acceptance') or not gate.get('implementation') or not gate.get('checks'):
            raise ValueError('Incomplete qualification gate: ' + name)
        for path in gate['implementation'] + gate['checks']:
            resolved = (root / path).resolve()
            if not resolved.is_relative_to(root.resolve()) or not resolved.exists():
                raise ValueError('Missing/outside qualification reference: ' + path)
    for key in ['routine_stress_seconds', 'candidate_soak_seconds', 'fuzz_seconds_per_target', 'baseline_runs']:
        value = policy['budgets'][key]
        if type(value) is not int or value <= 0:
            raise ValueError('Invalid qualification budget: ' + key)
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

def require_uninstrumented_binary(path):
    """Reject diagnostic native runtimes before loading or packaging them."""
    symbols = subprocess.check_output(['readelf', '--dyn-syms', '--wide', str(path)], text=True)
    if re.search(r'\b__(?:asan|ubsan|tsan|msan|sanitizer_cov|llvm_profile)_\w+', symbols):
        raise ValueError('Instrumented binary cannot be packaged: ' + str(path))

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
