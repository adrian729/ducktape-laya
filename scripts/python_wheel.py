"""Build the Python wheel around an installed native library (build tooling only)."""
import base64
import hashlib
import pathlib
from artifact_support import write_zip
from release_support import ROOT

from release_support import host_platform
TAG = f'py3-none-manylinux_2_38_{host_platform()[1]}'  # glibc 2.38 is the native library's floor


def build(out, version, library, epoch):
    """Write ducktape_laya-VERSION-TAG.whl into out; library is the real .so file."""
    source = ROOT / 'bindings/python'
    dist = f'ducktape_laya-{version}.dist-info'
    files = {f'ducktape_laya/{path.name}': (path.read_bytes(), False)
             for path in sorted((source / 'src/ducktape_laya').glob('*.py'))}
    files['ducktape_laya/_native/libducktape_laya.so'] = (pathlib.Path(library).read_bytes(), True)
    licenses = [ROOT / 'LICENSE', ROOT / 'THIRD_PARTY_NOTICES.md', *sorted((ROOT / 'licenses').iterdir())]
    for path in licenses:
        name = path.relative_to(ROOT).as_posix()
        files[f'{dist}/licenses/{name}'] = (path.read_bytes(), False)
    metadata = [
        'Metadata-Version: 2.4', 'Name: ducktape-laya', f'Version: {version}',
        'Summary: Local Laya GGUF decisions (choice, noul, score) through the ducktape-laya native library',
        'Project-URL: Source, https://github.com/adrian729/ducktape-laya', 'License-Expression: MIT',
        *[f'License-File: {path.relative_to(ROOT).as_posix()}' for path in licenses],
        'Classifier: Operating System :: POSIX :: Linux', 'Classifier: Programming Language :: Python :: 3',
        'Requires-Python: >=3.10', 'Description-Content-Type: text/markdown']
    files[f'{dist}/METADATA'] = (('\n'.join(metadata) + '\n\n' + (source / 'README.md').read_text()).encode(), False)
    files[f'{dist}/WHEEL'] = (f'Wheel-Version: 1.0\nGenerator: ducktape-laya\nRoot-Is-Purelib: false\nTag: {TAG}\n'.encode(), False)
    record = []
    for name, (data, _) in sorted(files.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        record.append(f'{name},sha256={digest},{len(data)}')
    record.append(f'{dist}/RECORD,,')
    files[f'{dist}/RECORD'] = (('\n'.join(record) + '\n').encode(), False)
    wheel = pathlib.Path(out) / f'ducktape_laya-{version}-{TAG}.whl'
    write_zip(wheel, files, epoch)
    return wheel
