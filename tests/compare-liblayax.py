"""Probe a separately obtained LibLayaX release through its public C API.

No dependency on LibLayaX is added to the product. Timings on a busy machine are
observations only. Run after the other benchmarks, not concurrently with them.
"""
import argparse
import ctypes as c
import json
import pathlib
import time

p = argparse.ArgumentParser()
p.add_argument('library', type=pathlib.Path)
p.add_argument('model', type=pathlib.Path)
p.add_argument('samples', type=pathlib.Path)
p.add_argument('--limit', type=int, default=2)
a = p.parse_args()
lib = c.CDLL(str(a.library.resolve()))
lib.laya_version.restype = c.c_char_p
lib.laya_last_error.restype = c.c_char_p
lib.laya_create.argtypes = [c.c_char_p, c.c_char_p]
lib.laya_create.restype = c.c_void_p
lib.laya_predict.argtypes = [c.c_void_p, c.c_char_p]
lib.laya_predict.restype = c.c_void_p
lib.laya_info.argtypes = [c.c_void_p]
lib.laya_info.restype = c.c_void_p
lib.laya_free_string.argtypes = [c.c_void_p]
lib.laya_free_string.restype = None
lib.laya_destroy.argtypes = [c.c_void_p]
lib.laya_destroy.restype = None

def take(ptr):
    if not ptr:
        raise RuntimeError(lib.laya_last_error().decode())
    try:
        out = json.loads(c.string_at(ptr))
        if 'error' in out:
            raise RuntimeError(out['error'])
        return out
    finally:
        lib.laya_free_string(ptr)

fixture = json.loads((pathlib.Path(__file__).resolve().parents[1] / 'tests/fixtures/primitive.json').read_text())
samples = json.loads(a.samples.read_text())['results'][:a.limit]
start = time.perf_counter()
agent = lib.laya_create(str(a.model.resolve()).encode(), b'{"backend":"cpu","threads":2}')
if not agent:
    raise RuntimeError(lib.laya_last_error().decode())
load_ms = (time.perf_counter() - start) * 1000
try:
    info = take(lib.laya_info(agent))
    requests = [{**fixture, 'state': sample['state']} for sample in samples]
    take(lib.laya_predict(agent, json.dumps(requests[0]).encode()))
    results = []
    for request in requests:
        start = time.perf_counter()
        result = take(lib.laya_predict(agent, json.dumps(request).encode()))
        results.append({'ms': (time.perf_counter() - start) * 1000, 'result': result})
    batch = take(lib.laya_predict(agent, json.dumps(requests).encode()))
    batch_equal = batch['results'] == [r['result']['results'][0] for r in results]
    hwm = None
    if pathlib.Path('/proc/self/status').exists():
        for line in pathlib.Path('/proc/self/status').read_text().splitlines():
            if line.startswith('VmHWM:'):
                hwm = int(line.split()[1])
    print(json.dumps({'version': lib.laya_version().decode(), 'loadMs': load_ms,
        'info': info, 'threads': 2, 'linuxHighWaterKiB': hwm,
        'batchEqualsSingle': batch_equal, 'results': results}, indent=2))
finally:
    lib.laya_destroy(agent)
