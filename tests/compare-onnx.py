"""Native ORT CPU probe on pretokenized rows; excludes tokenizer/JSON overhead.

This measures the native backend through Python, not a finished C/Rust SDK.
Requires numpy and onnxruntime. Uses the unmodified research q8 graphs (batch=1).
"""
import argparse
import json
import pathlib
import statistics
import time
import numpy as np
import onnxruntime as ort

p = argparse.ArgumentParser()
p.add_argument('model', type=pathlib.Path)
p.add_argument('inputs', type=pathlib.Path)
p.add_argument('--threads', type=int, default=2)
p.add_argument('--official', action='store_true')
p.add_argument('--batch-questions', action='store_true')
a = p.parse_args()
samples = json.loads(a.inputs.read_text())
opts = ort.SessionOptions()
opts.intra_op_num_threads = a.threads
opts.inter_op_num_threads = 1
start = time.perf_counter()
suffix = '' if a.official else '_q8'
enc = ort.InferenceSession(str(a.model / f'encoder{suffix}.onnx'), opts, providers=['CPUExecutionProvider'])
head = ort.InferenceSession(str(a.model / f'head{suffix}.onnx'), opts, providers=['CPUExecutionProvider'])
load_ms = (time.perf_counter() - start) * 1000

def run(sample):
    result = []
    groups = [sample['questions']] if a.batch_questions else [[q] for q in sample['questions']]
    for group in groups:
        length = max(len(q['ids']) for q in group)
        k = max(len(q['markers']) for q in group)
        ids = np.full((len(group), length), 50283, dtype=np.int64)
        mask = np.zeros_like(ids)
        pos = np.zeros((len(group), k), dtype=np.int64)
        marker_mask = np.zeros_like(pos, dtype=np.bool_)
        for i, q in enumerate(group):
            ids[i, :len(q['ids'])] = q['ids']
            mask[i, :len(q['ids'])] = 1
            pos[i, :len(q['markers'])] = q['markers']
            marker_mask[i, :len(q['markers'])] = True
        hidden = enc.run(None, {'input_ids': ids, 'attention_mask': mask})[0]
        qt = np.array([q['qtype'] for q in group], dtype=np.int64)
        logits, act = head.run(None, {'hidden_states' if a.official else 'hidden': hidden,
            'attention_mask': mask, 'marker_pos': pos, 'marker_mask': marker_mask,
            'qtype': qt[:, None] if a.official else qt})
        for i, q in enumerate(group):
            result.append({'id': q['id'], 'logits': logits[i, :len(q['markers'])].tolist(),
                'act': act[i].tolist()})
    return result

run(samples[0])
results = []
for sample in samples:
    start = time.perf_counter()
    logits = run(sample)
    results.append({'ms': (time.perf_counter() - start) * 1000, 'logits': logits})
hwm = None
if pathlib.Path('/proc/self/status').exists():
    for line in pathlib.Path('/proc/self/status').read_text().splitlines():
        if line.startswith('VmHWM:'):
            hwm = int(line.split()[1])
print(json.dumps({'runtime': ort.__version__, 'threads': a.threads, 'loadMs': load_ms,
    'batchQuestions': a.batch_questions, 'official': a.official,
    'linuxHighWaterKiB': hwm, 'medianMs': statistics.median(r['ms'] for r in results),
    'results': results}, indent=2))
