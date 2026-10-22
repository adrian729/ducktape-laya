"""Build the Python wheel around an installed native library (build tooling only)."""
import base64
import hashlib
import pathlib
from artifact_support import write_zip
from release_support import ROOT

CLASSIFIERS = {'linux': 'Operating System :: POSIX :: Linux', 'darwin': 'Operating System :: MacOS',
               'win32': 'Operating System :: Microsoft :: Windows'}


def tag(target):
    """The wheel tag: manylinux_2_38 (the library's glibc floor), macosx_14_0 or win_amd64."""
    return f'py3-none-{target.wheel}'


def build(out, version, library, epoch, target):
    """Write ducktape_laya-VERSION-TAG.whl into out; library is the real shared library file.

    The pure-Python files are the same for every target; _native/ holds the
    target's library under the name _native.py loads."""
    source = ROOT / 'bindings/python'
    dist = f'ducktape_laya-{version}.dist-info'
    files = {f'ducktape_laya/{path.name}': (path.read_bytes(), False)
             for path in sorted((source / 'src/ducktape_laya').glob('*.py'))}
    files[f'ducktape_laya/_native/{target.library}'] = (pathlib.Path(library).read_bytes(), True)
    licenses = [ROOT / 'LICENSE', ROOT / 'THIRD_PARTY_NOTICES.md', *sorted((ROOT / 'licenses').iterdir())]
    for path in licenses:
        name = path.relative_to(ROOT).as_posix()
        files[f'{dist}/licenses/{name}'] = (path.read_bytes(), False)
    metadata = [
        'Metadata-Version: 2.4', 'Name: ducktape-laya', f'Version: {version}',
        'Summary: Local Laya GGUF decisions (choice, noul, score) through the ducktape-laya native library',
        'Project-URL: Source, https://github.com/adrian729/ducktape-laya', 'License-Expression: MIT',
        *[f'License-File: {path.relative_to(ROOT).as_posix()}' for path in licenses],
        f'Classifier: {CLASSIFIERS[target.os]}', 'Classifier: Programming Language :: Python :: 3',
        'Requires-Python: >=3.10', 'Description-Content-Type: text/markdown']
    files[f'{dist}/METADATA'] = (('\n'.join(metadata) + '\n\n' + (source / 'README.md').read_text()).encode(), False)
    files[f'{dist}/WHEEL'] = (f'Wheel-Version: 1.0\nGenerator: ducktape-laya\nRoot-Is-Purelib: false\nTag: {tag(target)}\n'.encode(), False)
    record = []
    for name, (data, _) in sorted(files.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        record.append(f'{name},sha256={digest},{len(data)}')
    record.append(f'{dist}/RECORD,,')
    files[f'{dist}/RECORD'] = (('\n'.join(record) + '\n').encode(), False)
    wheel = pathlib.Path(out) / f'ducktape_laya-{version}-{tag(target)}.whl'
    write_zip(wheel, files, epoch)
    return wheel
