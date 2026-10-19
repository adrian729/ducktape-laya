# Ducktape Laya for Python

Local Laya GGUF decisions (`choice`, `noul`, ordinal `score`) from Python, backed
by the shared `libducktape_laya.so`. Inference runs in-process: no server, network
or model download. Linux x86-64 only; see the repository README for CPU and glibc
requirements. Python 3.10+.

The package uses `ctypes`, so it needs no compiler. It loads the native library
bundled in the wheel, or the system's `libducktape_laya.so.0`. Setting
`DUCKTAPE_LAYA_LIB_DIR` selects a specific library instead; if that one cannot be
loaded, import fails rather than falling back. Download the model separately and
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

Requests are JSON values or JSON strings; a string is sent as is. `execute`
accepts primitive and structured requests and lists of up to 32 requests; `decide`
keeps the legacy primitive semantics (on a router it is strict, like `execute`).
Results are parsed JSON.

Errors: native failures and invalid request text raise `LayaError`, whose `status`
matches the C ABI (`Status`). Calls after `close()` raise `EngineClosedError`, a
`LayaError`. Arguments the binding checks itself (`threads`, `timeout`,
`request_id`) raise `ValueError` or `TypeError`, and a missing or incompatible
library raises `OSError`.

Calls block and release the GIL. An engine is safe to share between threads;
calls on one engine run one at a time, so open several engines for parallel
inference. Per call, `timeout` (seconds, or `None`) sets a deadline (status 6),
`cancel` takes a `CancelToken` whose `cancel()` stops the call from any thread
(status 5), and `on_event` receives start/end/error events on the thread running
the call. `execute_async` and `decide_async` run the call on a worker thread
(where `on_event` then runs), and cancelling the task cancels the native call.
`close()` waits for running calls; called from an `on_event` callback, it returns
at once and the model is released when the running call finishes. Engines not
closed are released when garbage collected, but close them for prompt release.
`Engine.open_router` opens a lazy registry of several local models; see
[the API guide](https://github.com/adrian729/ducktape-laya/blob/main/docs/API.md).
