# Ducktape Laya for Python

Local Laya GGUF decisions (`choice`, `noul`, ordinal `score`) from Python, backed
by the shared `libducktape_laya.so`. Inference runs in-process: no server, network
or model download. Linux x86-64 only; see the repository README for CPU and glibc
requirements. Python 3.10+.

The package uses `ctypes`, so it needs no compiler. It loads the native library
from, in order: `DUCKTAPE_LAYA_LIB_DIR`, a copy bundled in the wheel, then the
system loader path (`libducktape_laya.so.0`). Download the model separately and
check its hash; see the repository README.

```python
import ducktape_laya

with ducktape_laya.Engine.open("/path/to/Laya-Q8_0.gguf") as engine:
    result = engine.execute({
        "state": "Please refund the duplicate charge.",
        "questions": {
            "refund": {"type": "noul", "instructions": "Does the customer request a refund?"},
        },
    })
    print(result["answers"]["refund"]["noul"])
```

Requests are JSON values or JSON strings; pass a string when literal key order
matters. `execute` accepts primitive and structured requests and lists of up to 32
requests; `decide` keeps the legacy primitive semantics. Results are parsed JSON.
Errors raise `LayaError` with a `status` matching the C ABI (`Status`).

Calls block and release the GIL. An engine is safe to share between threads;
calls on one engine run one at a time, so open several engines for parallel
inference. `execute_async` and `decide_async` run the call on a worker thread,
and cancelling the task cancels the native call. Per call, `timeout` (seconds)
sets a deadline (status 6), `cancel` takes a `threading.Event` (status 5), and
`on_event` receives start/end/error events on the calling thread. `close()`
waits for running calls. `Engine.open_router` opens a lazy registry of several
local models; see [the API guide](https://github.com/adrian729/ducktape-laya/blob/main/docs/API.md).
