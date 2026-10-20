# Public-API conformance consumer: request JSON on stdin, result JSON on stdout.
# Optional second argument: open config JSON (default {"threads":4}).
import json
import sys

import ducktape_laya

config = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {'threads': 4}
with ducktape_laya.Engine.open(sys.argv[1], **config) as engine:
    print(json.dumps(engine.execute(sys.stdin.read()), ensure_ascii=False, separators=(',', ':')))
