"""Adapt the pinned research export's I/O names/rank to laya-ts; no weight changes.

Requires onnx in a developer environment. Input and output directories must differ.
This is a benchmark artifact adapter, not a shipped runtime or general converter.
"""
import argparse
import pathlib
import shutil
import onnx
from onnx import helper, TensorProto

p = argparse.ArgumentParser()
p.add_argument('source', type=pathlib.Path)
p.add_argument('dest', type=pathlib.Path)
a = p.parse_args()
assert a.source.resolve() != a.dest.resolve()
a.dest.mkdir(parents=True, exist_ok=True)
for kind in ('encoder', 'head'):
    model = onnx.load(a.source / f'{kind}_q8.onnx', load_external_data=False)
    if kind == 'head':
        for node in model.graph.node:
            for i, name in enumerate(node.input):
                if name == 'hidden':
                    node.input[i] = 'hidden_states'
                elif name == 'qtype':
                    node.input[i] = 'qtype_flat'
        for inp in model.graph.input:
            if inp.name == 'hidden':
                inp.name = 'hidden_states'
            elif inp.name == 'qtype':
                inp.type.tensor_type.shape.dim.add().dim_value = 1
        model.graph.initializer.append(helper.make_tensor('qtype_axes', TensorProto.INT64, [1], [1]))
        model.graph.node.insert(0, helper.make_node('Squeeze', ['qtype', 'qtype_axes'], ['qtype_flat']))
    onnx.save(model, a.dest / f'{kind}.onnx')
    target = a.dest / f'{kind}_q8.onnx.data'
    if target.is_symlink():
        target.unlink()
    if not target.exists():
        shutil.copyfile(a.source / target.name, target)
for name in ('tokenizer.json', 'rl_agent_config.json'):
    shutil.copyfile(a.source / name, a.dest / name)
