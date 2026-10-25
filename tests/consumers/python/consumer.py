# Public-API conformance consumer: request JSON on stdin, result JSON on stdout.
# Optional second argument: open config JSON (default {"threads":4}).
# Usage: consumer.py model.gguf [config.json] [--requests FILE... [--fresh-engine]] < request.json
# With --requests: one engine for all files (one per file with --fresh-engine), one result line per file.
import json
import sys

import ducktape_laya

args = sys.argv[1:]
at = 2 if len(args) > 1 and not args[1].startswith('--') else 1
config = json.loads(args[1]) if at == 2 else {'threads': 4}
files, fresh, usage = None, False, not args
while at < len(args):
    if args[at] == '--fresh-engine':
        fresh = True
    elif args[at] == '--requests' and files is None:
        files = []
        while at + 1 < len(args) and not args[at + 1].startswith('--'):
            at += 1
            files.append(args[at])
        usage = usage or not files
    else:
        usage = True
    at += 1
if usage or (fresh and files is None):
    sys.exit('Usage: consumer.py model.gguf [config.json] [--requests FILE... [--fresh-engine]] < request.json')


def show(result):
    print(json.dumps(result, ensure_ascii=False, separators=(',', ':')), flush=True)


if files is None:
    with ducktape_laya.Engine.open(args[0], **config) as engine:
        show(engine.execute(sys.stdin.read()))
elif fresh:
    for name in files:
        with open(name, encoding='utf-8') as file:
            request = file.read()
        with ducktape_laya.Engine.open(args[0], **config) as engine:
            show(engine.execute(request))
else:
    engine = None
    try:
        for name in files:
            with open(name, encoding='utf-8') as file:
                request = file.read()
            if engine is None:
                engine = ducktape_laya.Engine.open(args[0], **config)
            show(engine.execute(request))
    finally:
        if engine is not None:
            engine.close()
