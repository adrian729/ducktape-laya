#!/usr/bin/env python3
"""Compare browser output against an explicitly supplied native parity run."""
import argparse
import json
import pathlib
p=argparse.ArgumentParser()
p.add_argument('report',type=pathlib.Path)
p.add_argument('--native', type=pathlib.Path, required=True,
               help='Native report produced by tests/parity.py --report with the same model and fixtures')
a=p.parse_args()
root=pathlib.Path(__file__).resolve().parents[1]
report=json.loads(a.report.read_text())
native=json.loads(a.native.read_text())
reference={name: outputs['C'] for name, outputs in native['fixtures'].items()}
tolerance=json.loads((root/'release/browser-toolchain.json').read_text())['native_wasm_absolute_tolerance']
assert report['status']=='passed',report
assert report['ticks']>0, 'No main-thread heartbeat during worker execution'
assert [event['event'] for event in report['events']]==['start','end']*3
maximum=(0,'')
def compare(a,b,path):
    global maximum
    if isinstance(a,dict):
        assert list(a)==list(b),f'Key/order change: {path}'
        for k in a: compare(a[k],b[k],path+'.'+k)
    elif isinstance(a,list):
        assert len(a)==len(b),path
        for i,(x,y) in enumerate(zip(a,b)): compare(x,y,f'{path}[{i}]')
    elif isinstance(a,(int,float)) and not isinstance(a,bool):
        delta=abs(a-b)
        if delta>maximum[0]: maximum=(delta,path)
    else: assert a==b,(path,a,b)
assert set(reference) == set(report['results']), 'Fixture set changed'
for name in reference: compare(reference[name], report['results'][name], '$.'+name)
print(f'Maximum absolute difference: {maximum[0]:.12g} at {maximum[1]}; tolerance {tolerance}')
assert maximum[0]<=tolerance, 'Browser/native numeric gate failed'
