#!/usr/bin/env python3
"""Check the CPU guard on emulated CPUs that lack the build's required features.

Runs the C probe (and the Node binding) under QEMU user-mode CPU models. On a
CPU without a required feature, dtl_open must return DTL_UNSUPPORTED_PLATFORM
naming the first missing feature. QEMU executes AVX-family instructions even
when the emulated CPU does not advertise them, so the probe also runs with
execution logging: every translated block is decoded and must not contain an
instruction from a feature the emulated CPU lacks. A run on a CPU with every
feature is the positive control showing that the scan sees such instructions.
"""
import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
FMA = re.compile(r'^vf(n?)m(add|sub)')
F16C = {'vcvtph2ps', 'vcvtps2ph'}
BMI2 = {'shlx', 'shrx', 'sarx', 'rorx', 'mulx', 'pdep', 'pext', 'bzhi'}
AVX2 = re.compile(r'^(vpbroadcast|vperm2i128|vinserti128|vextracti128|vpermd|vpermq|vpermps|vpermpd|vpsllv|vpsrlv|vpsrav|vpgather|vgather|vpmaskmov|vpblendd|vbroadcasti128)')
# Emulated CPU -> (features it lacks among the build's requirements, first one the guard reports).
CASES = {'Nehalem': ({'avx', 'avx2', 'fma', 'f16c', 'bmi2'}, 'avx'),
         'SandyBridge': ({'avx2', 'fma', 'f16c', 'bmi2'}, 'avx2')}


def classify(mnemonic, operands):
    found = set()
    if mnemonic.startswith('v') and mnemonic not in ('verr', 'verw'):
        found.add('avx')
        if (mnemonic.startswith('vp') and '%ymm' in operands) or AVX2.match(mnemonic):
            found.add('avx2')
    if FMA.match(mnemonic): found.add('fma')
    if mnemonic in F16C: found.add('f16c')
    if mnemonic in BMI2: found.add('bmi2')
    if '%zmm' in operands: found.add('avx512')
    return found


def executed_features(log):
    """Decode QEMU -d in_asm blocks (raw OBJD-T bytes) and classify instructions."""
    blocks, address, data = [], None, ''
    for line in log.read_text().splitlines():
        match = re.match(r'^(0x[0-9a-f]+):', line)
        if match:
            if address is not None: blocks.append((address, bytes.fromhex(data)))
            address, data = int(match[1], 16), ''
        elif line.startswith('OBJD-T: '):
            data += line[8:].strip()
    if address is not None: blocks.append((address, bytes.fromhex(data)))
    found = {}
    with tempfile.TemporaryDirectory() as directory:
        for start in range(0, len(blocks), 2000):
            chunk = blocks[start:start + 2000]
            binary = pathlib.Path(directory) / 'blocks.bin'
            binary.write_bytes(b''.join(code for _, code in chunk))
            text = subprocess.run(['objdump', '-D', '-b', 'binary', '-m', 'i386:x86-64', str(binary)],
                                  capture_output=True, text=True, check=True).stdout
            offsets, base = [], 0
            for block_address, code in chunk:
                offsets.append((base, block_address)); base += len(code)
            for line in text.splitlines():
                parts = line.split('\t')
                if len(parts) < 3 or not parts[0].strip().endswith(':'): continue
                offset = int(parts[0].strip()[:-1], 16)
                fields = parts[2].split(None, 1)
                for feature in classify(fields[0], fields[1] if len(fields) > 1 else ''):
                    block_offset, block_address = max(o for o in offsets if o[0] <= offset)
                    found.setdefault(feature, hex(block_address + offset - block_offset))
    return len(blocks), found


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build', type=pathlib.Path, required=True, help='Build with laya-cpu-guard-probe and ducktape_laya.node')
    p.add_argument('model', type=pathlib.Path)
    p.add_argument('--report', type=pathlib.Path)
    p.add_argument('--skip-control', action='store_true', help='Skip the slow emulated full-feature open')
    a = p.parse_args()
    qemu, node_binary = shutil.which('qemu-x86_64'), shutil.which('node')
    if not qemu or not node_binary: p.error('qemu-x86_64 (QEMU user mode) and node are required')
    build, model = a.build.resolve(), a.model.resolve()
    probe = build / 'laya-cpu-guard-probe'
    env = dict(os.environ, LD_LIBRARY_PATH=str(build))
    report = {'schema_version': 1, 'qemu': subprocess.check_output([qemu, '--version'], text=True).splitlines()[0],
              'library': str(build / 'libducktape_laya.so'), 'cases': {}}
    failures = []
    with tempfile.TemporaryDirectory(prefix='ducktape-cpu-guard-') as temp:
        def emulate(cpu, trace):
            log = pathlib.Path(temp) / f'{cpu}.log'
            run = subprocess.run([qemu, '-cpu', cpu, *(['-d', 'in_asm', '-D', str(log)] if trace else []), str(probe), str(model)],
                                 env=env, capture_output=True, text=True, timeout=1800)
            if run.returncode: raise RuntimeError(f'{cpu}: probe exited {run.returncode}: {run.stderr.strip()}')
            return json.loads(run.stdout), run.stderr.strip(), log
        for cpu, (lacking, first) in CASES.items():
            result, error, log = emulate(cpu, True)
            blocks, found = executed_features(log)
            forbidden = {f: at for f, at in found.items() if f in lacking or f == 'avx512'}
            missing = [f for f in result['info'].get('missing_cpu_features', [])]
            case = {'status': result['status'], 'error': error, 'missing_cpu_features': missing,
                    'translated_blocks': blocks, 'executed_features': found, 'forbidden_executed': forbidden}
            report['cases'][cpu] = case
            if result['status'] != 7: failures.append(f'{cpu}: dtl_open returned {result["status"]}, expected 7')
            if f'feature: {first}' not in error: failures.append(f'{cpu}: error does not name {first}: {error!r}')
            if set(missing) != lacking: failures.append(f'{cpu}: dtl_info reports missing {missing}, expected {sorted(lacking)}')
            if forbidden: failures.append(f'{cpu}: executed instructions from unsupported features {forbidden}')
            addon = build / 'ducktape_laya.node'
            if not addon.is_file():
                case['node'] = {'status': 'not_built'}
                continue
            node = subprocess.run([qemu, '-cpu', cpu, node_binary, '-e',
                                   'require(process.argv[1]).Engine.open(process.argv[2])'
                                   '.then(() => process.exit(10), e => { console.log(JSON.stringify({status: e.status, message: e.message})); })',
                                   str(ROOT / 'bindings/node/index.cjs'), str(model)],
                                  env={**env, 'DUCKTAPE_LAYA_LIBRARY': str(addon.resolve())}, capture_output=True, text=True, timeout=1800)
            case['node'] = json.loads(node.stdout) if node.returncode == 0 and node.stdout.strip() else {'exit': node.returncode, 'stderr': node.stderr[-500:]}
            if case['node'].get('status') != 7: failures.append(f'{cpu}: Node open did not reject with status 7: {case["node"]}')
        if not a.skip_control:
            result, error, log = emulate('max', True)
            blocks, found = executed_features(log)
            report['cases']['max'] = {'status': result['status'], 'error': error, 'translated_blocks': blocks, 'executed_features': found}
            if result['status'] != 0: failures.append(f'max: dtl_open returned {result["status"]}: {error}')
            if not {'avx', 'avx2'} <= set(found): failures.append(f'max: scan did not observe AVX/AVX2 execution {sorted(found)}')
    report['status'] = 'failed' if failures else 'passed'
    report['failures'] = failures
    text = json.dumps(report, indent=2) + '\n'
    if a.report: a.report.write_text(text)
    print(text, end='')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
