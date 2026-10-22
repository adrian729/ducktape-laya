#!/usr/bin/env python3
import re
from release_support import ROOT, check_record
record = check_record()
# The ABI version, SONAME, symbol version node and header must move together
# (ABI 1 shipped libducktape_laya.so.0).
abi = record['abi_version']
header = int(re.search(r'#define DTL_ABI_VERSION (\d+)u', (ROOT / 'include/ducktape_laya.h').read_text())[1])
soversion = int(re.search(r'SOVERSION (\d+)', (ROOT / 'CMakeLists.txt').read_text())[1])
node = re.findall(r'DUCKTAPE_LAYA_(\d+)', (ROOT / 'src/exports.map').read_text())
if header != abi or soversion != abi - 1 or node != [str(abi)]:
    raise SystemExit(f'ABI identity disagrees: record {abi}, header {header}, SOVERSION {soversion}, exports.map {node}')
for path in ['scripts/package-native.py', 'scripts/setup.py', 'tests/cross-host.py', 'bindings/python/src/ducktape_laya/_native.py']:
    names = set(re.findall(r'libducktape_laya\.so\.(\d+)', (ROOT / path).read_text()))
    if names - {str(soversion)}:
        raise SystemExit(f'{path} names libducktape_laya.so.{sorted(names)}, not .so.{soversion}')
print(f'Compatibility record consistent: {record["package_version"]}, ABI {abi} (.so.{soversion}), runtime {record["runtime"]["commit"]}')
