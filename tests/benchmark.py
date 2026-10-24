#!/usr/bin/env python3
"""Real-model native performance and resource baseline for one numerical profile.

Measures model open/close, first and warm latency percentiles for small,
structured, near-limit and batch requests, two concurrent engines, router cold
loads/evictions, cancellation and deadline response, RSS, descriptors and
threads. Every repeated output must match the first. One run is one sample;
budgets come from several independent runs (scripts/performance-budgets.py).
This is not a throughput promise or task-quality claim.
"""
import argparse
import hashlib
import ctypes as c
import json
import os
import pathlib
import platform
import resource
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
from release_support import LIBRARY, ROOT, binary_info, check_record, find_manifest, git, read_json, sha256

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('model', type=pathlib.Path)
p.add_argument('--build', type=pathlib.Path, default=ROOT / 'build')
p.add_argument('--samples', type=int, default=20, help='Warm samples per single-request workload')
p.add_argument('--threads', type=int, default=4)
p.add_argument('--report', type=pathlib.Path, required=True)
a = p.parse_args()
if not 3 <= a.samples <= 1000 or not 1 <= a.threads <= 256: p.error('samples 3..1000; threads 1..256')
if a.report.exists(): p.error('Report exists; choose a new path')
model, build = a.model.resolve(), a.build.resolve()
manifest = find_manifest(model, check_record())  # the primary or an additional pinned model
library = c.CDLL(str(build / LIBRARY))
CANCEL = c.CFUNCTYPE(c.c_uint32, c.c_void_p)
EVENT = c.CFUNCTYPE(None, c.c_char_p, c.c_void_p)
class CallOptions(c.Structure):
    _fields_ = [('struct_size', c.c_uint32), ('reserved', c.c_uint32), ('request_id', c.c_uint64),
                ('timeout_ms', c.c_uint64), ('on_event', EVENT), ('event_user_data', c.c_void_p),
                ('should_cancel', CANCEL), ('cancel_user_data', c.c_void_p)]
library.dtl_default_call_options.restype = CallOptions
out = [c.POINTER(c.c_void_p), c.POINTER(c.c_void_p)]
for name, args in {'dtl_open': [c.c_char_p, c.c_char_p, *out],
                   'dtl_execute': [c.c_void_p, c.c_char_p, c.POINTER(CallOptions), *out],
                   'dtl_open_router': [c.c_char_p, c.c_char_p, *out]}.items():
    getattr(library, name).argtypes = args
    getattr(library, name).restype = c.c_int32
for name in ['dtl_close', 'dtl_free']:
    getattr(library, name).argtypes = [c.c_void_p]
    getattr(library, name).restype = None
config_json = json.dumps({"threads": a.threads}).encode()

def fixture(name):
    return json.loads((ROOT / 'tests/fixtures' / name).read_text())
coverage = fixture('coverage.json')
WORKLOADS = {
    'small': fixture('primitive.json'),
    'structured': fixture('structured.json'),
    'near_limit': max(coverage, key=lambda r: len(json.dumps(r))),
    'batch': fixture('batch.json'),
}
def questions(request):
    items = request if isinstance(request, list) else [request]
    return sum(len(r.get('questions') or r['schema']['properties']) for r in items)

def check(status, error):
    if status: raise RuntimeError(c.string_at(error).decode() if error.value else f'status {status}')

def call(function, handle, request, options=None):
    result, error = c.c_void_p(), c.c_void_p()
    started = time.perf_counter()
    status = function(handle, request, options, c.byref(result), c.byref(error))
    elapsed = time.perf_counter() - started
    try:
        return status, elapsed, c.string_at(result) if result.value else None, c.string_at(error).decode() if error.value else ''
    finally:
        library.dtl_free(result); library.dtl_free(error)

def open_engine():
    engine, error = c.c_void_p(), c.c_void_p()
    started = time.perf_counter()
    status = library.dtl_open(os.fsencode(model), config_json, c.byref(engine), c.byref(error))
    elapsed = time.perf_counter() - started
    try: check(status, error)
    finally: library.dtl_free(error)
    return engine, elapsed

def rank(values, percentile):
    ordered = sorted(values)
    return ordered[max(0, -(-len(ordered) * percentile // 100) - 1)]

def summary(samples, count):
    return {'samples': len(samples), 'p50_seconds': statistics.median(samples), 'p95_seconds': rank(samples, 95),
            'p99_seconds': rank(samples, 99), 'max_seconds': max(samples), 'min_seconds': min(samples),
            'questions_per_second': count * len(samples) / sum(samples)}

MACOS = sys.platform == 'darwin'

def ps(*args):
    return subprocess.run(['ps', *args, '-p', str(os.getpid())], check=True, capture_output=True, text=True).stdout

def rss_kib():
    if MACOS: return int(ps('-o', 'rss=').split()[0])
    for line in pathlib.Path('/proc/self/status').read_text().splitlines():
        if line.startswith('VmRSS:'): return int(line.split()[1])

def resources():
    if MACOS:  # /dev/fd lists this process's descriptors; `ps -M` prints one row per thread after a header
        return {'rss_kib': rss_kib(), 'descriptors': len(os.listdir('/dev/fd')), 'threads': len(ps('-M').splitlines()) - 1}
    return {'rss_kib': rss_kib(), 'descriptors': len(os.listdir('/proc/self/fd')),
            'threads': len(os.listdir('/proc/self/task'))}

def cpu_model():
    if MACOS:
        return subprocess.run(['sysctl', '-n', 'machdep.cpu.brand_string'], capture_output=True, text=True).stdout.strip() or 'unknown'
    return next((line.split(':', 1)[1].strip() for line in pathlib.Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('model name')), 'unknown')

report = {'schema_version': 2, 'source_commit': git(ROOT, 'rev-parse', 'HEAD'),
          'source_tracked_dirty': bool(git(ROOT, 'diff', 'HEAD', '--name-only')),
          'library_sha256': sha256(build / LIBRARY), 'diagnostics': binary_info(build),
          'model_sha256': manifest['sha256'],
          'workloads': {name: {'questions': questions(r), 'request_sha256': hashlib.sha256(json.dumps(r).encode()).hexdigest()}
                        for name, r in WORKLOADS.items()},
          'threads': a.threads, 'samples': a.samples, 'system': platform.platform(), 'logical_cpus': os.cpu_count(),
          'cpu_model': cpu_model(),
          'load_before': os.getloadavg(),
          'notes': ['Fresh process; warm OS file cache after hash verification; sequential calls unless stated.',
                    'Host is not isolated; timings are a retained sample, not a service-level guarantee.',
                    'Latency includes C ABI JSON parsing/serialization; Python result decoding is excluded.']}
baseline = resources()
report['resources_before_open'] = baseline
engine, report['open_seconds'] = open_engine()
report['resources_after_open'] = resources()
measured = {}
try:
    for name, request in WORKLOADS.items():
        body = json.dumps(request).encode()
        status, first, expected, error = call(library.dtl_execute, engine, body)
        if status: raise RuntimeError(f'{name}: {error}')
        samples = []
        for _ in range(a.samples if name in ('small', 'structured') else max(3, a.samples // 2)):
            status, elapsed, output, error = call(library.dtl_execute, engine, body)
            if status or output != expected: raise AssertionError(f'{name}: repeated output changed or failed: {error}')
            samples.append(elapsed)
        measured[name] = {'first_seconds': first, **summary(samples, questions(request))}
    report['latency'] = measured

    # Cancellation and deadline response on the near-limit request.
    body = json.dumps(WORKLOADS['near_limit']).encode()
    delays, overshoots = [], []
    for _ in range(5):
        trigger = {'at': time.perf_counter() + 0.05, 'seen': None}
        def should_cancel(_):
            now = time.perf_counter()
            if now >= trigger['at']:
                trigger['seen'] = trigger['seen'] or now
                return 5
            return 0
        callback = CANCEL(should_cancel)
        control = library.dtl_default_call_options()
        control.should_cancel, control.cancel_user_data = callback, None
        status, elapsed, _, error = call(library.dtl_execute, engine, body, c.byref(control))
        if status != 5: raise AssertionError(f'cancellation returned {status}: {error}')
        delays.append(time.perf_counter() - trigger['at'])
        control = library.dtl_default_call_options()
        control.timeout_ms = 50
        status, elapsed, _, error = call(library.dtl_execute, engine, body, c.byref(control))
        if status != 6: raise AssertionError(f'deadline returned {status}: {error}')
        overshoots.append(elapsed - 0.05)
    report['cancellation'] = {'requested_after_seconds': 0.05, 'response_max_seconds': max(delays),
                              'response_median_seconds': statistics.median(delays),
                              'deadline_overshoot_max_seconds': max(overshoots)}
    expected = call(library.dtl_execute, engine, body)[2]
    if call(library.dtl_execute, engine, body)[2] != expected: raise AssertionError('result changed after cancellation')
    report['resources_steady'] = resources()
finally:
    started = time.perf_counter()
    library.dtl_close(engine)
    report['close_seconds'] = time.perf_counter() - started
report['resources_after_close'] = resources()

# Two engines in concurrent threads, each running the small request.
engines = [open_engine()[0] for _ in range(2)]
try:
    body = json.dumps(WORKLOADS['small']).encode()
    expected = call(library.dtl_execute, engines[0], body)[2]
    per_engine = max(3, a.samples // 2)
    latencies, failures = [], []
    def worker(engine):
        for _ in range(per_engine):
            status, elapsed, output, error = call(library.dtl_execute, engine, body)
            if status or output != expected: failures.append(error or 'output changed')
            latencies.append(elapsed)
    threads = [threading.Thread(target=worker, args=(engine,)) for engine in engines]
    started = time.perf_counter()
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    wall = time.perf_counter() - started
    if failures: raise AssertionError(f'concurrent engines: {failures[0]}')
    report['two_engines'] = {**summary(latencies, questions(WORKLOADS['small'])),
                             'aggregate_questions_per_second': 2 * per_engine * questions(WORKLOADS['small']) / wall,
                             'rss_kib': rss_kib()}
finally:
    for engine in engines: library.dtl_close(engine)

# Router over two hard links of the model with one loaded model: every switch is
# an eviction and cold load. Links live beside the build so they share its filesystem.
links = pathlib.Path(tempfile.mkdtemp(prefix='benchmark-router-', dir=build))
try:
    for name in 'ab':
        try: os.link(model, links / f'{name}.gguf')
        except OSError: shutil.copyfile(model, links / f'{name}.gguf')
    router_config = json.dumps({'models': {name: str(links / f'{name}.gguf') for name in 'ab'}, 'max_loaded': 1}).encode()
    router, error = c.c_void_p(), c.c_void_p()
    check(library.dtl_open_router(router_config, config_json, c.byref(router), c.byref(error)), error)
    library.dtl_free(error)
    try:
        cold, warm = [], []
        for name in 'ababab':
            body = json.dumps({**WORKLOADS['small'], 'model': name}).encode()
            status, elapsed, _, error = call(library.dtl_execute, router, body)
            if status: raise RuntimeError(error)
            cold.append(elapsed)
            status, elapsed, _, error = call(library.dtl_execute, router, body)
            if status: raise RuntimeError(error)
            warm.append(elapsed)
        report['router'] = {'cold_load_call_median_seconds': statistics.median(cold), 'cold_load_call_max_seconds': max(cold),
                            'warm_call_median_seconds': statistics.median(warm), 'switches': len(cold)}
    finally:
        library.dtl_close(router)
finally:
    shutil.rmtree(links, ignore_errors=True)
report['resources_final'] = resources()
# ru_maxrss is KiB on Linux and bytes on macOS
report['peak_rss_kib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // (1024 if MACOS else 1)
report['leaks'] = {key: report['resources_final'][key] - baseline[key] for key in ('descriptors', 'threads')}
report['load_after'] = os.getloadavg()
# macOS: the libdispatch pool behind Accelerate grows and shrinks on its own (observed 1-2 extra workers after the
# warm-up), so thread counts there are recorded but not gated; descriptors still are.
if MACOS:
    report['skipped_checks'] = {'thread_leak': 'libdispatch/Accelerate worker threads vary on macOS; leaks.threads is the raw difference'}
    report['status'] = 'passed' if report['leaks']['descriptors'] == 0 else 'failed'
else:
    report['status'] = 'passed' if not any(report['leaks'].values()) else 'failed'
a.report.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({'status': report['status'], 'open_seconds': report['open_seconds'],
                  **{name: round(m['p50_seconds'], 4) for name, m in measured.items()}, 'peak_rss_kib': report['peak_rss_kib']}))
raise SystemExit(report['status'] != 'passed')
