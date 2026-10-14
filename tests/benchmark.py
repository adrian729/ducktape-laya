#!/usr/bin/env python3
"""Small real-model native baseline, not a throughput or task-quality claim."""
import argparse
import ctypes as c
import json
import os
import pathlib
import platform
import resource
import statistics
import sys
import time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
from release_support import ROOT, binary_info, git, read_json, sha256, verify_model

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('model', type=pathlib.Path)
p.add_argument('--build', type=pathlib.Path, default=ROOT / 'build')
p.add_argument('--request', type=pathlib.Path, default=ROOT / 'tests/fixtures/primitive.json')
p.add_argument('--samples', type=int, default=5)
p.add_argument('--threads', type=int, default=2)
p.add_argument('--report', type=pathlib.Path, required=True)
a = p.parse_args()
if not 1 <= a.samples <= 100 or not 1 <= a.threads <= 256: p.error('samples 1..100; threads 1..256')
if a.report.exists(): p.error('Report exists; choose a new path')
model, build = a.model.resolve(), a.build.resolve()
manifest = read_json(ROOT / 'models/manifest.json')
verify_model(model, manifest)
library = c.CDLL(str(build / 'libducktape_laya.so'))
class Options(c.Structure):
    _fields_ = [(name, c.c_uint32) for name in ['struct_size', 'abi_version', 'threads', 'reserved']]
library.dtl_default_options.restype = Options
library.dtl_open.argtypes = [c.c_char_p, c.POINTER(Options), c.POINTER(c.c_void_p), c.POINTER(c.c_void_p)]
library.dtl_open.restype = c.c_int32
library.dtl_execute.argtypes = [c.c_void_p, c.c_char_p, c.c_void_p, c.POINTER(c.c_void_p), c.POINTER(c.c_void_p)]
library.dtl_execute.restype = c.c_int32
library.dtl_close.argtypes = [c.c_void_p]
library.dtl_close.restype = None
library.dtl_free.argtypes = [c.c_void_p]
library.dtl_free.restype = None
request = a.request.read_bytes()
engine, error = c.c_void_p(), c.c_void_p()
options = library.dtl_default_options()
options.threads = a.threads
report = {'schema_version': 1, 'source_commit': git(ROOT, 'rev-parse', 'HEAD'),
          'source_tracked_dirty': bool(git(ROOT, 'diff', 'HEAD', '--name-only')),
          'library_sha256': sha256(build / 'libducktape_laya.so'), 'diagnostics': binary_info(build),
          'model_sha256': manifest['sha256'], 'request_sha256': sha256(a.request),
          'threads': a.threads, 'system': platform.platform(), 'logical_cpus': os.cpu_count(),
          'cpu_model': next((line.split(':', 1)[1].strip() for line in pathlib.Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('model name')), 'unknown'),
          'load_before': os.getloadavg(),
          'notes': ['Same process, sequential requests, warm OS file cache after hash verification.',
                    'Host is not isolated; timings are a retained baseline, not a service-level guarantee.',
                    'Timed execute includes C ABI JSON parsing/serialization; Python result decoding is excluded.']}
started = time.perf_counter()
status = library.dtl_open(os.fsencode(model), c.byref(options), c.byref(engine), c.byref(error))
report['open_seconds'] = time.perf_counter() - started
try:
    if status: raise RuntimeError(c.string_at(error).decode() if error else str(status))
    def execute():
        result, err = c.c_void_p(), c.c_void_p()
        started = time.perf_counter()
        status = library.dtl_execute(engine, request, None, c.byref(result), c.byref(err))
        elapsed = time.perf_counter() - started
        try:
            if status: raise RuntimeError(c.string_at(err).decode() if err else str(status))
            return elapsed, c.string_at(result)
        finally:
            library.dtl_free(result); library.dtl_free(err)
    report['first_request_seconds'], baseline = execute()
    samples = []
    for _ in range(a.samples):
        elapsed, output = execute()
        if output != baseline: raise AssertionError('Repeated output changed')
        samples.append(elapsed)
    report['warm_seconds'] = samples
    report['warm_median_seconds'] = statistics.median(samples)
    report['sequential_requests_per_second'] = len(samples) / sum(samples)
    report['warm_min_seconds'], report['warm_max_seconds'] = min(samples), max(samples)
    decoded = json.loads(baseline)
    report['usage'] = [item['usage'] for item in decoded] if isinstance(decoded, list) else decoded['usage']
    report['status'] = 'passed'
finally:
    library.dtl_free(error)
    started = time.perf_counter()
    library.dtl_close(engine)
    report['close_seconds'] = time.perf_counter() - started
report['peak_rss_kib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
report['load_after'] = os.getloadavg()
a.report.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({key: report[key] for key in ['status', 'open_seconds', 'first_request_seconds', 'warm_median_seconds', 'peak_rss_kib']}))
