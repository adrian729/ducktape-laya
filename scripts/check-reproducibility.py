#!/usr/bin/env python3
"""Compare independent native compilations of one recorded working-tree snapshot.

No downloads, publication, source edits or model required. Retains both builds.
This tests identical toolchains/options, not reproducibility across compilers.
"""
import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import tarfile
from release_support import ROOT, check_record, git, sha256

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--output', type=pathlib.Path, required=True)
p.add_argument('--jobs', type=int, default=2)
p.add_argument('--node-include', type=pathlib.Path)
a = p.parse_args()
if a.jobs < 1: p.error('--jobs must be positive')
record = check_record()
out = a.output.resolve()
out.mkdir(parents=True, exist_ok=False)
node_include = a.node_include or pathlib.Path(shutil.which('node')).resolve().parents[1] / 'include/node'
if not (node_include / 'node_api.h').is_file(): p.error('Supply --node-include with node_api.h')
snapshot = out / 'snapshot'
snapshot.mkdir()
# Capture tracked files once; include staged new files and report any dirty state.
names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
manifest = {}
for name in sorted(filter(None, names)):
    source = ROOT / name
    if source.is_dir() or not source.exists(): continue
    target = snapshot / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target, follow_symlinks=False)
    manifest[name] = {'sha256': sha256(source), 'mode': source.lstat().st_mode,
                      'symlink': os.readlink(source) if source.is_symlink() else None}
runtime = snapshot / record['runtime']['path']
runtime.mkdir(parents=True)
archive = out / 'runtime.tar'
with archive.open('wb') as stream:
    subprocess.run(['git', '-C', str(ROOT / record['runtime']['path']), 'archive', record['runtime']['commit']], stdout=stream, check=True)
with tarfile.open(archive) as stream:
    stream.extractall(runtime, filter='data')
report = {'schema_version': 1, 'status': 'running', 'source_commit': git(ROOT, 'rev-parse', 'HEAD'),
          'source_tracked_dirty': bool(git(ROOT, 'diff', 'HEAD', '--name-only')),
          'snapshot_sha256': hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
          'runtime': record['runtime'], 'scope': 'installed native and Node files; same toolchain/options, different source/build/install roots',
          'source_files': manifest, 'builds': {}}
def save():
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
save()
try:
    for label in ['first', 'second']:
        source, build, install = (out / label / part for part in ['source', 'build', 'install'])
        shutil.copytree(snapshot, source, symlinks=True)
        commands = [
            ['cmake', '-S', str(source), '-B', str(build), '-DCMAKE_BUILD_TYPE=Release',
             '-DDUCKTAPE_BUILD_TESTS=OFF', '-DDUCKTAPE_BUILD_NODE=ON', f'-DNODE_INCLUDE_DIR={node_include}',
             f'-DDUCKTAPE_LLAMA_REVISION={record["runtime"]["commit"]}', '-DGGML_CCACHE=OFF'],
            ['cmake', '--build', str(build), '--parallel', str(a.jobs)],
            ['cmake', '--install', str(build), '--prefix', str(install)]]
        with (out / f'{label}.log').open('w') as log:
            for command in commands:
                subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
        header = (build / 'third_party/llama.cpp/ggml/src/ggml-version.h').read_text()
        if not re.search(r'#define GGML_COMMIT\s+"' + record['runtime']['commit'] + '"', header):
            raise RuntimeError('Generated GGML identity does not match the runtime pin')
        files = {}
        for path in sorted(install.rglob('*')):
            if path.is_symlink(): value = {'symlink': os.readlink(path)}
            elif path.is_file(): value = {'sha256': sha256(path), 'mode': path.stat().st_mode & 0o777}
            else: continue
            files[str(path.relative_to(install))] = value
        report['builds'][label] = files
        save()
    first, second = report['builds'].values()
    report['differences'] = [key for key in sorted(first.keys() | second.keys()) if first.get(key) != second.get(key)]
    report['status'] = 'failed' if report['differences'] else 'passed'
except Exception as error:
    report['status'] = 'failed'
    report['error'] = str(error)
    raise
finally:
    save()
print(json.dumps({'status': report['status'], 'differences': report['differences'], 'report': str(out / 'report.json')}))
raise SystemExit(report['status'] != 'passed')
