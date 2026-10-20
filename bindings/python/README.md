# Ducktape Laya for Python

Local Laya GGUF decisions (`choice`, `noul`, ordinal `score`) from Python, backed
by the shared `libducktape_laya.so`. Inference runs in-process: no server, network
or model download. Linux x86-64 only; see the repository README for CPU and glibc
requirements. Python 3.10+.

The package uses `ctypes`, so it needs no compiler. It loads the native library
bundled in the wheel, or the system's `libducktape_laya.so.1`. Setting
`DUCKTAPE_LAYA_LIB_DIR` selects a specific library instead; if that one cannot be
loaded, the first `Engine.open`, `Engine.open_router` or `runtime_info` call raises
`OSError` rather than falling back (loading is lazy, so import itself succeeds). Download the model separately and check its hash; see the repository README.

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

Requests are JSON values or JSON strings; a string is sent as is. `execute` accepts primitive and structured requests and lists of up to 32 requests under one strict contract. Optional per-request `max_tokens`, `temperatures`, `overflow`, `max_windows` and (with `"model": "auto"`) `lang` are plain request keys. Results are parsed JSON.

Open options are keyword-only and omitted when unset: `Engine.open(path, threads=0, context_tokens=None, temperatures=None, overflow=None)`. `context_tokens` is the prompt capacity (default 512, at most the model's trained length), `temperatures` maps calibration keys such as `"choice"` or `"noul.2"` to positive overrides, and `overflow` is the default policy for prompts over the limit (`"reject"` (default), `"truncate_end"`, `"truncate_start"` or `"scan"`; a request's own `overflow` wins). The binding
checks some values itself: `threads` (0..256) and `overflow` raise `ValueError`,
a non-integer `context_tokens` raises `TypeError`, and non-finite numbers (such as
a NaN temperature) or non-serializable configs raise `ValueError`/`TypeError` from
JSON encoding. Other invalid values (for example a non-positive `context_tokens`
or temperature, a NUL character or invalid Unicode in a path or config) raise `LayaError` with status 1. `Engine.open_router(config, ...)` takes the same options, applied to every route; a route may be `"path.gguf"` or `{"path": ..., "config": {...}}`, whose config overrides them key by key. Requests to a router name a route in `"model"`. With `"auto": {"english": "en", "default": "ml"}` in the router config, `"model": "auto"` chooses the route by the state's language (optionally given as `"lang"`); `"auto"` cannot be a route ID.

Errors: native failures and invalid request text raise `LayaError`, whose `status`
matches the C ABI (`Status`). Calls after `close()` raise `EngineClosedError`, a
`LayaError`. Arguments the binding checks itself (`threads`, `timeout`,
`request_id`) raise `ValueError` or `TypeError`, and a missing or incompatible
library raises `OSError`.

Calls block and release the GIL. An engine is safe to share between threads;
calls on one engine run one at a time, so open several engines for parallel
inference. Per call, `timeout` (seconds; `None` or 0 means no deadline) sets a
deadline (status 6),
`cancel` takes a single `CancelToken` whose `cancel()` stops the call from any thread
(status 5), and `on_event` receives start/end/error events on the thread running
the call. `execute_async` runs the call on a worker thread
(where `on_event` then runs), and cancelling the task cancels the native call.
`close()` waits for running calls; called from an `on_event` callback, it returns
at once and the model is released when the running call finishes. Engines not
closed are released when garbage collected, but close them for prompt release.
`Engine.open_router` opens a lazy registry of several local models; see
[the API guide](https://github.com/adrian729/ducktape-laya/blob/main/docs/API.md).

## From a checkout

The binding is not on PyPI yet. Build the native library first (see the repository README), then either point Python at the source and the built library:

```sh
export DUCKTAPE_LAYA_LIB_DIR=/path/to/ducktape-laya/build
export PYTHONPATH=/path/to/ducktape-laya/bindings/python/src
python3 -c 'import ducktape_laya; print(ducktape_laya.runtime_info()["abi_version"])'
```

or install the source directory into a virtual environment with `pip install /path/to/ducktape-laya/bindings/python` and keep `DUCKTAPE_LAYA_LIB_DIR` set when running. Either way the library comes from `DUCKTAPE_LAYA_LIB_DIR`: a wheel built from `pyproject.toml` alone does not contain it. `scripts/package-linux.py` builds the real wheel, which bundles the library and needs no environment variable.
