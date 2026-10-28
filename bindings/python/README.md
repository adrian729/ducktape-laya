# Ducktape Laya for Python

Local [Laya](https://github.com/NandhaKishorM/laya) decisions from Python: ask named questions about a text and get probabilities. A `choice` question picks one of several options, a `noul` question gives the probability that a statement is true, and a `score` question picks a level on an ordered scale. Inference runs on the CPU inside your process, through the shared native library bundled in the wheel: no server, network access, compiler or implicit model download.

## Install

```sh
pip install ducktape-laya
```

Requirements: Python 3.10+. The wheels are `manylinux_2_38_x86_64`: Linux x86-64 with AVX, AVX2, FMA, F16C, BMI2 and SSE4.2 and glibc 2.38+ (Ubuntu 24.04+), and from 0.3.0 also `manylinux_2_38_aarch64` (Armv8.2-A with dot product and FP16) and `win_amd64` (needs the Microsoft Visual C++ Redistributable). Full matrix: [platforms](https://github.com/adrian729/ducktape-laya/blob/main/docs/PLATFORMS.md).

Download the model separately and check its hash ([models](https://github.com/adrian729/ducktape-laya/blob/main/docs/MODELS.md)).

## Usage

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

Requests are JSON values or JSON strings (a string is sent as is); results are parsed JSON. `execute` accepts primitive and structured requests and lists of up to 32 requests. Optional per-request `max_tokens`, `temperatures`, `overflow`, `max_windows` and (with `"model": "auto"`) `lang` are plain request keys. The request format is in the [API guide](https://github.com/adrian729/ducktape-laya/blob/main/docs/API.md).

## Configuration

Open options are keyword-only; `None` means unset (the library default) and any other value, 0 included, is sent as given and validated by the library, which rejects bad values with status 1: `Engine.open(path, threads=None, context_tokens=None, temperatures=None, overflow=None, max_windows_per_call=None)`. `path` is a `str`, `bytes` or `os.PathLike`; on POSIX the bytes reach the file system unchanged, so any file name works.

- `threads`: 0 (automatic: at most 4 and at most the CPUs the process may use) or 1–256.
- `context_tokens`: the prompt capacity, default 512, at most the model's maximum (`trained_context_tokens`, 8,192 for the shipped files).
- `temperatures`: calibration overrides such as `{"choice": 1.5, "noul.2": 0.8}`; positive and finite.
- `overflow`: the default policy for prompts over the limit: `"reject"` (default), `"truncate_end"`, `"truncate_start"` or `"scan"`; a request's own `overflow` wins.
- `max_windows_per_call`: the most forward passes one call may plan, 1–4,096, default 256 (engine-level only; a router's route cannot set it).

`Engine.open_router(config, ...)` opens a lazy registry of several local models with the same options applied to every route; a route may be `"path.gguf"` or `{"path": ..., "config": {...}}`, whose config overrides them key by key. Requests to a router name a route in `"model"`. With `"auto": {"english": "en", "default": "ml"}` in the router config, `"model": "auto"` chooses the route by the state's language (optionally given as `"lang"`); `"auto"` cannot be a route ID.

`ducktape_laya.runtime_info()` returns the runtime's identity, features and limits without loading a model; `engine.info()` adds the model's metadata.

## Calls, threads and cancellation

Calls block and release the GIL. An engine is safe to share between threads; calls on one engine run one at a time, so open several engines for parallel inference. Per call:

- `timeout` (seconds; `None` or 0 means none) sets a deadline (status 6).
- `cancel` takes a `CancellationToken`; its `cancel()` stops the call from any thread (status 5). A call whose token is already cancelled fails at once with status 5, with no events.
- `on_event` receives start/end/error events on the thread running the call.
- `request_id` is reported in events.

`execute_async` runs the call on a worker thread (where `on_event` then runs), and cancelling the task cancels the native call. Cancellation is cooperative and returns no partial result. `close()` waits for running calls; called from an `on_event` callback, it returns at once and the model is released when the running call finishes. Unclosed engines are released when garbage collected, but close them (or use `with`) for prompt release.

## Errors

- Native failures and invalid requests raise `LayaError`, whose `status` is a `ducktape_laya.Status` (the C ABI's codes).
- Calls after `close()` raise `EngineClosedError`, a `LayaError`.
- The binding checks only conversions, and raises `TypeError` or `ValueError` with `status` set to `INVALID_ARGUMENT` for them: a non-integer `threads`, `context_tokens` or `max_windows_per_call`, non-finite numbers or non-serializable configs, and bad `timeout` or `request_id` values. Every config value (ranges, `overflow` names, temperatures) goes to the library, which rejects bad ones with status 1; so does a NUL character or invalid Unicode in a path or config.
- When several checks fail, the first applies: closed engine, argument conversions, an already-cancelled token, then the native call. At open, arguments and config are checked before the library loads.
- A missing, unloadable or incompatible native library raises `LayaLoadError` (a `LayaError` with status 7, `UNSUPPORTED_PLATFORM`, that is also an `OSError`) on every `Engine.open`, `Engine.open_router` or `runtime_info` call (loading is lazy, so the import itself succeeds; a failed load is retried on the next call). The loader also refuses a library whose `package_version` differs from the package's. A bundled library that exists but cannot be loaded is reported as found, with the operating system's reason (on Linux typically a glibc older than 2.38, on Windows a missing Visual C++ Redistributable).

## Native library

The package uses `ctypes`. It loads the library bundled in the wheel (`ducktape_laya/_native/`), or else the system's `libducktape_laya.so.1` through the dynamic loader (on macOS the absolute path `/opt/homebrew/lib/libducktape_laya.1.dylib` or `libducktape_laya.dylib`, then the same names in `/usr/local/lib`, never a bare name; on Windows `ducktape_laya.dll` from the application directory, the system directories and `os.add_dll_directory()` directories only, never the current directory or `PATH`). Setting `DUCKTAPE_LAYA_LIBRARY` (the library file) or `DUCKTAPE_LAYA_LIB_DIR` (the directory that holds it) selects a specific library instead, `DUCKTAPE_LAYA_LIBRARY` first; both must be absolute paths, and if the chosen one cannot be loaded, the call raises `LayaLoadError` rather than falling back. On macOS the loader also asks `dladdr` which file provides the library and refuses one that `DYLD_LIBRARY_PATH` swapped in, with `LayaLoadError`. Wheels exist for Linux x86-64 and arm64, macOS arm64 and x86-64 and Windows x64; on other platforms build the library from source and set `DUCKTAPE_LAYA_LIB_DIR`.

## From a checkout

To use unreleased changes, build the native library ([building](https://github.com/adrian729/ducktape-laya/blob/main/docs/BUILDING.md)), then point Python at the source and the built library:

```sh
export DUCKTAPE_LAYA_LIB_DIR=/path/to/ducktape-laya/build
export PYTHONPATH=/path/to/ducktape-laya/bindings/python/src
python3 -c 'import ducktape_laya; print(ducktape_laya.runtime_info()["abi_version"])'
```

or `pip install /path/to/ducktape-laya/bindings/python` into a virtual environment and keep `DUCKTAPE_LAYA_LIB_DIR` set when running: a wheel built from `pyproject.toml` alone does not contain the library. The binding's tests run with `python3 -m unittest discover -s bindings/python/tests`, with `LAYA_MODEL` set for the real-model tests.
