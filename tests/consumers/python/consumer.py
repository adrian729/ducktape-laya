# Public-API conformance consumer: request JSON on stdin, result JSON on stdout.
import json
import sys

import ducktape_laya

with ducktape_laya.Engine.open(sys.argv[1], threads=4) as engine:
    print(json.dumps(engine.execute(sys.stdin.read()), ensure_ascii=False, separators=(',', ':')))
