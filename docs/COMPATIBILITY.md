# Compatibility and versioning

What stays stable between versions, how breaking changes are versioned, and how to migrate. Platform support is in [PLATFORMS.md](PLATFORMS.md); the changes per version are in the [changelog](../CHANGELOG.md).

## Versions

| Version | C ABI | Linux library | Released |
| --- | --- | --- | --- |
| 0.1.0 | 1 | `libducktape_laya.so.0` | GitHub Releases, npm, crates.io |
| 0.2.0 | 2 | `libducktape_laya.so.1` | GitHub Releases, npm, crates.io, PyPI, NuGet, Maven Central, Go module proxy |

Released files are never replaced: a fix is a new version. Before 1.0, a breaking change bumps the minor version (0.1.0 to 0.2.0) and comes with migration notes below; after 1.0, the major version.

## What is a compatibility contract

- **The C ABI.** Every binding adapts it. Added functions and status codes keep the ABI version; any change to an existing layout, ownership rule or meaning needs a new ABI version and a new Linux library name (SONAME) with its own symbol version, so programs built against one ABI never load another by accident. On Windows the DLL name carries no ABI number; the bindings check `dtl_abi_version()` when they load it.
- **Behaviour.** Status numbers, result ordering, the meaning of answers and confidence fields, the default `reject` overflow policy, returning no partial result on failure, and the meaning of every JSON field.
- **Requests are strict.** Unknown fields are rejected, so a request that works keeps meaning the same thing. Runtime and model info may gain fields; ignore unknown fields there.
- **Results within one numerical profile.** The same request on the same build, model and settings gives the same result in every binding. Results on different CPUs, operating systems or the browser can differ slightly ([PLATFORMS.md](PLATFORMS.md#numerical-results-across-platforms)); that is measured for each release, not guaranteed.
- **Models.** Each supported model file is pinned by an immutable manifest; a model upgrade can change answers and is announced in the changelog. A new model capability needs explicit support in the library, not just a compatible file format.

## Known deviations

Agreement with the original Laya is checked per platform against the same limits (probabilities within 0.05). One answer on one platform has a documented, separately capped deviation, listed under the target's `known_deviations` in `release/compatibility.json` (read by `tests/original-reference.py`); every other answer there is still held to 0.05. A platform that is not a release target has none.

| Platform | Model | Answer | Drift | Cap | Cause |
| --- | --- | --- | --- | --- | --- |
| Windows x64 (clang-cl; released from the next version) | English Q8_0 | `json_escaping/spam` | 0.0541 (Linux x86-64: 0.0373) | 0.06 | The Windows C runtime's `erff` (GEGLU-erf) and `sinf`/`cosf` (RoPE cache) round some last bits differently from glibc. Upstream llama.cpp calls the platform math library on CPU, so this is upstream behaviour; the ggml kernels are the same as on Linux, and agreement with upstream llama.cpp is exact. Q8_0 activation quantization amplifies the difference on this answer. |

## Upgrading across the ABI break

ABI 1 and ABI 2 are different libraries (`libducktape_laya.so.0` and `libducktape_laya.so.1`), so both can be installed side by side, but an application built against one does not load the other. Upgrading means changing the code as below, rebuilding and relinking against the 0.2.0 headers and library. Rolling back means returning to the 0.1.0 build of the application with the retained 0.1.0 library; swapping only the library is not enough. `tests/rollback.py` rehearses exactly this: across an ABI break it builds each side's consumer from that side's own source commit. Within one ABI, a consumer built once must work with both libraries.

The npm packages bundle their own native library, so upgrading them is a dependency bump plus the code changes below. The Rust crates link the system library: upgrade the crate and the native library together. The Python, Go, Java and .NET bindings were not in 0.1.0.

## Migrating from 0.1.0 (ABI 1) to 0.2.0 (ABI 2)

### C ABI

- **One handle type.** `dtl_router`, `dtl_router_open`, `dtl_router_execute*` and `dtl_router_close` are gone: `dtl_open_router` returns a `dtl_engine`, and `dtl_execute`, `dtl_info` and `dtl_close` work on either kind.
- **One call function.** `dtl_decide`, `dtl_decide_observed`, `dtl_decide_with_options` and `dtl_execute_with_options` are gone. Call `dtl_execute(engine, request, options, &result, &error)`. The observer moved into `dtl_call_options` (`request_id`, `on_event`, `event_user_data`), so `dtl_observer` is gone too, and the cancellation user data is now `cancel_user_data`. Hosts that set a flag from another thread can pass the new `dtl_cancel_flag` as `should_cancel`, with a pointer to an aligned `uint32_t` as `cancel_user_data`.
- **No lenient decide.** The legacy primitive API ignored unknown top-level request fields; every request is now strict, so remove fields such as `metadata` before sending, or keep them outside the request. Unknown keys inside a question are rejected too.
- **Open config instead of `dtl_options`.** `dtl_open(path, config_json, ...)` and `dtl_open_router(router_json, config_json, ...)` take an optional JSON object (`threads`, `context_tokens`, `overflow`, `temperatures`); `dtl_options` and `dtl_default_options` are gone. A router route may carry its own `config`.
- **Library name.** The SONAME is now `libducktape_laya.so.1` and the exported symbols carry the version `DUCKTAPE_LAYA_2`; rebuild and relink. CMake consumers ask for `find_package(ducktape_laya 0.2 ...)`.
- **Results.** Every answer has `answer_confidence` (the probability of the reported answer) and `confidence` (`1 − H(p)/log k`). The old `choice`/`score` `confidence` formulas are replaced; `noul` answers gained both fields.
- **Diagnostics.** Model info reports `context_tokens`, `trained_context_tokens`, `temperatures` and `metadata_temperatures` instead of `max_tokens`; the runtime limit `tokens_per_question` became `default_context_tokens`.

### C++

- `ducktape::laya` and `ducktape::router` are replaced by one `ducktape::engine`: `ducktape::engine::open(path, config_json)` and `ducktape::engine::open_router(router_json, config_json)`, where `config_json` may be empty.
- `execute(request, observer, options)` and `decide` are replaced by `engine.execute(request, call_options)`. `ducktape::call_options` holds `request_id`, `timeout_ms`, `on_event` (a `std::function`), `should_cancel` and `cancel_user_data`.
- `ducktape::info(engine)` becomes `ducktape::info()` for the runtime and `engine.info()` for a model.

### Rust

- `Engine::open(path, threads)` and `Engine::open_router(json, threads)` become `Engine::open(path, &Config)` and `Engine::open_router(json, &Config)`. `Config::default().threads(n)` keeps the old thread setting; `context_tokens`, `overflow` and `temperature` are new.
- `decide`, `decide_with_options`, `execute_observed` and `execute_with_options` are replaced by `execute(json)` and `execute_with(json, options)`, which takes the options by value.
- `CallOptions` fields are private; set them with builders: `CallOptions::default().request_id(id).timeout_ms(ms).cancel_flag(&flag).on_event(&mut callback)`. The cancel flag is now an `AtomicU32` instead of an `AtomicBool`: store a nonzero value to cancel, or `status::DEADLINE_EXCEEDED` (6) to report a host deadline.
- Status constants are in `ducktape_laya::status` (`status::CANCELLED`, ...).
- `ducktape-laya-sys` follows the C ABI: `Router`, `Options`, `Observer`, `dtl_default_options` and the removed functions are gone; `dtl_open_router` and `dtl_cancel_flag` are new, and `CallOptions` has the ABI 2 layout.
- A program built with the 0.2 crates needs `libducktape_laya.so.1`; with only the 0.1.0 library installed it fails when the loader starts it, before any call.

### Node.js and browser

- `decide`, `decideStructured` and `decideBatch` are removed. `execute` takes one request (object or JSON string) or an array, with the same strict contract.
- `execute` options gain `requestId`. Open options gain `contextTokens`, `overflow` and `temperatures`; the browser's `openRouter` also takes `auto`, and each model asset may set its own `contextTokens`, `overflow` and `temperatures`.
- After an abort or deadline settles a call, its `onEvent` handler receives no more events.
- Strings that are not well-formed Unicode (lone surrogates) are rejected instead of being replaced.
- Both packages export `Status`. Argument errors carry `status` 1 (they keep their `TypeError`/`RangeError` types), and queue, closed and worker failures carry `code` (`ERR_LAYA_QUEUE_FULL`, `ERR_LAYA_CLOSED`, `ERR_LAYA_WORKER_EXITED`); match on these instead of message text.
- `close()` resolves even when the worker has already exited or failed.

### Behavior changes

These change results or accepted input without any code change:

- **Stored temperatures are bounded to [0.5, 5]**, as in the original Laya. The English model stores `choice.11` = 0.1006, so `choice` questions with 11 or more options now report much less extreme probabilities (a 0.24 top probability had been reported as 0.9999). Pass `temperatures` to choose other values; app-supplied values are used as given.
- **Structured values are serialized as the original Laya does** (Python `json.dumps`): floats keep full precision (`149.0` stays `149.0`), and `0`/`false` criterion descriptions are kept. Prompts for structured state, instructions or descriptions change, and so can token counts and probabilities.
- **`context_tokens` replaces the fixed 512-token limit.** The default is still 512; set it up to the model's trained length.
- **A `choice` with a single option** is accepted and answered without inference (probability 1, no input tokens), and so is a schema string `enum` with one value; 0.1.0 required at least two. `choice` criteria may also be a list of labels.
- **`"auto"` is reserved** and cannot be a route ID.
- **Rejecting over-long input is now the default policy**, not the only behavior: requests or engines may choose `truncate_end`, `truncate_start` or `scan`. Under the default, results are unchanged.
- **Text between mask tokens is tokenized separately**, as the models were trained. This fixes whitespace dropped before a mask token with the multilingual model's vocabulary.
- **New input limits** (status 1): unknown keys inside a question, `null` `score` levels, JSON objects with more than 1,024 members, runs of more than 4,096 consecutive ASCII spaces in a question or the state (lifted again after 0.2.0), and calls that would plan more than 4,096 forward passes. See [API.md](API.md#batches).
