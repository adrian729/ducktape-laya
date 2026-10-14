"""Shared maintainer checks. Standard library only; never used by consumers."""
import hashlib
import json
import pathlib
import subprocess
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
    if read_json(root / 'bindings/node/package.json')['version'] != version:
        raise ValueError('npm package version mismatch')
    browser = root / 'bindings/browser/package.json'
    if browser.exists() and read_json(browser)['version'] != version:
        raise ValueError('Browser package version mismatch')
    for name in record['models']:
        model = read_json(root / name)
        if model.get('llama_cpp_commit', expected) != expected:
            raise ValueError(f'Model compatibility runtime mismatch: {name}')
        if len(model['sha256']) != 64 or len(model['revision']) != 40:
            raise ValueError(f'Model must identify immutable artifact: {name}')
    return record

def binary_info(build):
    """Inspect the built ABI without a model; detect stale/incompatible artifacts."""
    import ctypes
    library = ctypes.CDLL(str(pathlib.Path(build).resolve() / 'libducktape_laya.so'))
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
