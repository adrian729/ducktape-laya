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
# One export list per object format: the header's DTL_API functions are exactly the ELF
# version script's globals and the Mach-O list (the Windows DLL is checked against it too).
declared = sorted(re.findall(r'DTL_API\s+\w+\s+(dtl_\w+)\s*\(', (ROOT / 'include/ducktape_laya.h').read_text()))
version_script = (ROOT / 'src/exports.map').read_text()
elf = sorted(re.findall(r'dtl_\w+', version_script.split('global:', 1)[1].split('local:', 1)[0]))
macho = sorted(line.strip().removeprefix('_') for line in (ROOT / 'src/exports.macos').read_text().splitlines() if line.strip())
if not declared or declared != elf or declared != macho:
    raise SystemExit(f'Export lists disagree: header {declared}, exports.map {elf}, exports.macos {macho}')
for path in ['scripts/package-native.py', 'scripts/setup.py', 'tests/cross-host.py', 'bindings/python/src/ducktape_laya/_native.py']:
    names = set(re.findall(r'libducktape_laya\.so\.(\d+)', (ROOT / path).read_text()))
    if names - {str(soversion)}:
        raise SystemExit(f'{path} names libducktape_laya.so.{sorted(names)}, not .so.{soversion}')
print(f'Compatibility record consistent: {record["package_version"]}, ABI {abi} (.so.{soversion}), runtime {record["runtime"]["commit"]}')
