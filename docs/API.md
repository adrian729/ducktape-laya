# Shared decision API

The request and result format, configuration and error behaviour shared by every binding. It describes `main`; the [changelog](../CHANGELOG.md#unreleased) lists what changed since the 0.3.0 release. Language-specific details (installation, types, threading) are in each package README.

## Terms

| Term | Meaning |
| --- | --- |
| Model | A Laya GGUF file ([models](MODELS.md)) |
| Engine | One open model, or a router (below); the handle every call goes through |
| Router | An engine that holds several models and picks one per request, by name or by language |
| State | What the questions are about: text or any JSON value |
| `choice` | A question that picks one of several labelled options |
| `noul` | A yes/no question: the answer is the probability that the statement is true |
| `score` | A question that picks a level on an ordered scale (for example low, medium, high) |
| Structured decision | A request given as a small JSON Schema instead of questions; the answers fill its properties |
| Calibration temperature | A per-question-type divisor applied to the model's scores before they become probabilities; stored in the model, and overridable |
| Context (`context_tokens`) | The longest prompt (state plus one question, in tokens) an engine reserves memory for |
| Overflow policy | What happens when a prompt is longer than its limit: reject (default), truncate or scan in windows |
| Status | The numeric result code every binding reports (0 success, 1 invalid request, ... 7 unsupported platform; [table](#ownership-admission-and-errors)) |

## Interfaces

All planning, validation, projection, inference and model caching run in C++. C++, Rust, Node/TypeScript, Python, Go, Java, .NET and browser bindings adapt the same C ABI. Native inference performs no downloads and uses no server. Browser asset fetching is explicitly controlled by the host; see [BROWSER.md](BROWSER.md).

The C ABI (version 2) has one handle type for a single model or a router, one call function and JSON for configuration and requests. All results/errors use `dtl_free`. No partial result is returned on failure. Requests are strict: unsupported fields are rejected.

| Interface | Open | Call | Router |
|---|---|---|---|
| C | `dtl_open(path, config_json, &engine, &error)` | `dtl_execute(engine, json, call_options, &result, &error)` | `dtl_open_router(router_json, config_json, &engine, &error)` |
| C++ | `ducktape::engine::open(path, config_json)` | `engine.execute(json, call_options)` | `ducktape::engine::open_router(router_json, config_json)` |
| Rust | `Engine::open(path)`; `Engine::open_with(path, &Config)` | `engine.execute(json)`; `execute_with(json, CallOptions)` | `Engine::open_router(src)`; `open_router_with(src, &Config)`, where `src` is router JSON or a `&RouterConfig` |
| Node/TS | `Engine.open(path, { config, maxPending })` | `execute(request, options)` | `Engine.openRouter(RouterConfig \| string, { config, maxPending })` |
| Browser | `Engine.open(model, { config, ... })`, `model` a URL or `{url, sha256?}` | Same as Node/TS | `Engine.openRouter({models, max_loaded?, auto?}, { config, ... })`; a route is a URL or `{url, sha256?, config?}` |
| Python | `Engine.open(path, threads=, context_tokens=, temperatures=, overflow=, max_windows_per_call=)` | `execute(request, on_event=, request_id=, timeout=, cancel=)`; `execute_async` | `Engine.open_router(config, ...)` with the same options |
| Go | `laya.Open(path, *Config)` | `Execute(json)`; `ExecuteContext(ctx, json, *CallOptions)` | `laya.OpenRouter(*RouterConfig, *Config)`; `laya.OpenRouterJSON(json, *Config)` |
| Java | `Engine.open(path, Config)`; `path` a `String` or `Path` | `execute(json)`; `execute(json, CallOptions)` | `Engine.openRouter(RouterConfig \| String, Config)` |
| .NET | `Engine.Open(path, Config)` | `Execute(json, options, token)`; `ExecuteAsync` | `Engine.OpenRouter(RouterConfig \| string, Config)` |

Every binding names the engine `Engine`, its settings `Config`, `CallOptions`, `RouterConfig` and `Status`. Configuration keys are the native snake_case keys wherever a binding takes a nested config (Node and the browser pass them verbatim in `config`; binding-only options such as `maxPending` are camelCase beside it). A router is `{models: {id: path | {path, config}}, max_loaded, auto}` in JSON and a `RouterConfig` value in the typed bindings. `url` and `sha256` stay browser-only: no native binding verifies a route's hash.

The bindings differ only where their language has a convention:

| Binding | Engine type | Results | Deadline | Cancellation | Events run on | Async | Error type | Closed engine |
|---|---|---|---|---|---|---|---|---|
| C++ | `ducktape::engine` | JSON string | `timeout_ms` | `should_cancel` callback | calling thread | — | `ducktape::error` | destructor |
| Rust | `Engine` | JSON string | `timeout_ms` | `CancellationToken` | calling thread | — | `Error` (`status: Status`) | ownership |
| Node/TS | `Engine` | parsed | `timeoutMs` | `AbortSignal` | host thread | promises | `LayaError` (`LayaLoadError`) | `EngineClosedError`, `code: 'ERR_LAYA_CLOSED'` |
| Browser | `Engine` | parsed | `timeoutMs` | `AbortSignal` | host thread | promises | `LayaError` (`LayaLoadError`) | `EngineClosedError`, `code: 'ERR_LAYA_CLOSED'` |
| Python | `Engine` | parsed | `timeout` (seconds) | `CancellationToken` | thread running the call | `execute_async` | `LayaError` (`LayaLoadError`) | `EngineClosedError` |
| Go | `*Engine` | JSON string | context deadline | context | calling goroutine | — | `*Error` (`Status`) | `*Error` wrapping the sentinel `ErrClosed` |
| Java | `Engine` | JSON string | `Duration` | `CancellationToken` | calling thread | — | `LayaException` (`LayaLoadException`, `LayaArgumentException`) | `EngineClosedException` |
| .NET | `Engine` | JSON string | `TimeSpan` | `CancellationToken` | calling thread (pool thread for `*Async`) | `ExecuteAsync` | `LayaException` (`LayaLoadException`, `LayaArgumentException`) | `EngineClosedException` (an `ObjectDisposedException`) |

Every binding reports native failures with the C status, typed as the binding's `Status` enum (a `StatusCode` number type in JavaScript), and maps cancellation to 5 and deadlines to 6 (.NET raises the standard `OperationCanceledException` when its token cancels). A call on a closed engine fails with status 1 and the binding's closed error (Python `EngineClosedError`, Java `EngineClosedException`, .NET `EngineClosedException`, Node and browser `EngineClosedError` with `code: 'ERR_LAYA_CLOSED'`, Go a fresh `*Error` wrapping the `ErrClosed` sentinel, so `errors.Is(err, ErrClosed)` matches and no error value is shared); a full Node or browser queue is also status 1 (`ERR_LAYA_QUEUE_FULL`). Arguments a binding checks itself use the language's standard argument errors, carrying status 1 (Python `TypeError`/`ValueError`, JavaScript `TypeError`/`RangeError`, Java and .NET `LayaArgumentException`). Bindings that convert strings reject text that is not well-formed Unicode (lone surrogates, invalid UTF-8) instead of replacing it.

**Validation.** The native library is the only validator of configuration values: a binding passes every value it is given and the core returns status 1. A binding checks only conversions (types, units, NUL bytes, Unicode, numbers JSON cannot carry, binding-only options). `null`/`None`/absent means the default and 0 is sent and rejected, with one exception: in Go a zero `Config` field means unset, because the zero value of a Go struct cannot tell the two apart. The browser checks `config` in the core over placeholder files before it downloads a model, and `threads` other than 1 is a status-1 `RangeError` there.

**Error order.** When several checks fail, the first applies: the engine is closed; argument conversions; an already-cancelled token, signal or context, which fails at once with status 5 (or 6 for an expired deadline) and makes no native call and emits no events; then, in Node and the browser, admission (a full queue); then the native call. At open, arguments and config are converted before the library loads. Node and the browser check a dead worker (`ERR_LAYA_WORKER_EXITED`) right after the closed check, before the arguments, because the worker cannot answer anything. .NET's `ExecuteAsync` with a cancelled token returns a task cancelled by `Task.Run`, so the order holds for the synchronous path only.

**Paths.** A model path reaches the core as bytes. Python (`str`, `bytes`, `os.PathLike`), Rust (`AsRef<Path>`), Go (a `string`) and Node (a string, `file:` URL, `Buffer` or `Uint8Array`) pass the bytes of a POSIX path unchanged, whatever their encoding. Java takes a `String` or a `Path` and uses `Path.toString()`, and .NET takes a `string`, so a POSIX path that is not valid UTF-8 cannot be passed from them. Router paths travel inside JSON and must be valid UTF-8 in every binding.

**Loading and overrides.** A native library (or Node addon, or browser runtime) that is missing, cannot be loaded or implements another ABI is status 7 in every binding that loads it at run time: Python raises `LayaLoadError` (a `LayaError` that is also an `OSError`), Node and the browser `LayaLoadError` (`code: 'ERR_LAYA_UNSUPPORTED'`), Java and .NET `LayaLoadException` (a `LayaException`) with the loader's error as its cause (`InnerException` in .NET). A load failure is not cached: the next call tries again. A library that is present but fails to load is reported as found, with the operating system's reason (typically a glibc older than 2.38 on Linux, or a missing Microsoft Visual C++ Redistributable on Windows), not as missing. The C++ wrapper, Rust, Go, Node, Python, Java, .NET and browser bindings check `dtl_abi_version()` and refuse any ABI but 2 (the C++ wrapper throws `ducktape::error` with status 7 from `open`, `open_router` and `info`). Rust, Go and the Node addon link against `libducktape_laya.so.1` and its `DUCKTAPE_LAYA_2` symbol versions, so with only an ABI 1 library (`libducktape_laya.so.0`) the program or addon fails to load before any call. The Node addon, Python, Java and .NET also refuse a library whose `package_version` differs from the package's own (status 7); Rust and Go cannot, since they link the library when the program is built.

Where the library is chosen at run time (Node, Python, Java, .NET), two environment variables override the bundled one, and both must be absolute paths (a relative one is a load error): `DUCKTAPE_LAYA_LIBRARY` names the library file (for Node, the addon file `ducktape_laya.node`) and `DUCKTAPE_LAYA_LIB_DIR` the directory that holds it (Node: the directory holding `ducktape_laya.node`). An override is authoritative: it is used or the load fails. Rust and Go choose the library when the program is built: the Rust `-sys` crate's build script reads `DUCKTAPE_LAYA_LIB_DIR` (a directory, at build time only; `DUCKTAPE_LAYA_LIBRARY` does not exist there) and Go uses `CGO_LDFLAGS`; at run time the system loader finds the library, so neither variable overrides it.

**Documented, not unified.** These differences are deliberate and follow each language:

| Binding | Inputs and results | Deadline unit | Events | Admission | Open-time cancellation | Safety net for an unclosed engine |
|---|---|---|---|---|---|---|
| Node/TS | request objects or JSON text; results parsed | `timeoutMs` | asynchronously on the host thread, before the promise settles | bounded FIFO queue (`maxPending`, default 16) | no | none: `close()` is required, the worker keeps the process alive |
| Browser | as Node | `timeoutMs` | as Node | as Node, plus browser limits ([BROWSER.md](BROWSER.md)) | yes (`signal` on open) | none |
| Python | request objects or JSON text; results parsed | `timeout` seconds | synchronously on the thread running the call | blocking (calls serialize on the handle) | no | `__del__` releases the engine |
| Rust | JSON text | `timeout_ms` | synchronously on the calling thread | blocking | no | `Drop` |
| Go | JSON text | context deadline | synchronously on the calling goroutine | blocking | no | finalizer |
| Java | JSON text | `Duration` | synchronously on the calling thread | blocking | no | `Cleaner` |
| .NET | JSON text | `TimeSpan` | synchronously on the calling thread (pool thread for `*Async`) | blocking | no | finalizer |

Closing differs by binding:

| Binding | Close while a call runs | Close from an event callback |
|---|---|---|
| C | Not allowed: `dtl_close` frees the handle at once | Not allowed |
| C++ | Not allowed: the destructor closes the handle at once | Not allowed |
| Rust | Prevented by ownership: dropping needs every borrow to end | Prevented by ownership |
| Node, browser | `close()` drains accepted calls; `close({ cancelPending: true })` cancels them first | Allowed; resolves after the running call |
| Python, Java, .NET, Go | Waits for running calls | Returns at once; the handle is released when the running call finishes (Go detects the callback's goroutine; there is no separate deferred close) |

In Python, Go, Java and .NET, a call made after closing has started fails at once with the binding's closed error, even while the close still waits for running calls.

## Engine configuration

Opening takes an optional JSON config; each binding exposes the same keys as native options. Unknown keys are rejected.

| Key | Meaning |
|---|---|
| `threads` | 0 (automatic: at most 4 and at most the usable CPUs, see [admission](#ownership-admission-and-errors)) or 1–256 CPU threads. Omit the key for the default; Go, whose zero value is "unset", omits 0 |
| `context_tokens` | The longest prompt the engine reserves memory for: 32 up to the model's maximum (`trained_context_tokens`, 8,192 for the shipped files). Default 512 (or that maximum if smaller). Compute follows each prompt's real length; only memory grows with this value. The original Laya runs the English checkpoint at 512 and the multilingual and typed-decisions checkpoints at 1,024, so longer prompts are untested for accuracy |
| `overflow` | Default policy for prompts over their limit; see [Long input](#long-input) |
| `max_windows_per_call` | The most forward passes one call may plan: 1–4,096, default 256 (twice the 128 questions of a call without `scan`). A question without `scan` is one pass and a single-option `choice` none. A call over it fails with status 1 before any inference; see [Long input](#long-input). Engine-wide: a router takes it only in its shared config, where it bounds the whole call across routes, and a route's own `config` that sets it is rejected |
| `temperatures` | Calibration overrides, keyed like the model's metadata: a type (`choice`, `score`, `noul`) or a type and option-count bucket (`choice.2`, `choice.3_5`, `choice.6_10`, `choice.11`, `score.3_5`, `noul.2`, ...). Values must be greater than 0 and finite as a 32-bit float (`1e-50` and `1e39` are rejected) |

A temperature is looked up most specific first: the bucket key, then the type key, then 1. An override replaces only the keys it names, so to change a two-option `choice` when the model defines `choice.2`, override `choice.2`; an override of plain `choice` applies only to buckets the model leaves undefined. Fit temperatures on your own data with the original Laya's calibration tools and pass them here.

Temperatures stored in the model are bounded to [0.5, 5], as the original Laya does: the shipped `choice.11` value, 0.1006, sharpens scores about tenfold and would report a 0.24 top probability as 0.9999. Temperatures you pass are used as given. `info()` reports the applied values as `temperatures` and the stored ones as `metadata_temperatures`.

A router's config applies to every route; a route given as `{"path": "...", "config": {...}}` overrides it key by key, except `max_windows_per_call`, which a route's config may not set. Routes share one loaded model when they have the same canonical path and the same configured `threads`, `context_tokens` and `temperatures` after that merge; each route still applies its own default `overflow`. The configured values are compared as given, not the effective ones: a route without `context_tokens` and a route with `context_tokens` 512 load the model separately even though both run at 512. `context_tokens` below 32 is rejected when the config is parsed; its upper bound, the model's maximum (`trained_context_tokens`, 8,192 for the shipped files), is checked when the model loads, which for a router route is the first request that uses it.

Each request may add `max_tokens` (a prompt limit up to the engine's `context_tokens`), `temperatures` (overrides for that request only, merged over the engine's), `overflow` and `max_windows` (see [Long input](#long-input)). A prompt over its limit is handled by the overflow policy; the default, `reject`, fails with status 1. `info()` on a single-model engine reports `context_tokens` and `trained_context_tokens`.

## Long input

A prompt longer than its limit (the request's `max_tokens`, at most the engine's `context_tokens`) is handled by the request's `overflow` policy. The engine's config can set a default `overflow`; a request's own value wins.

| `overflow` | What happens |
|---|---|
| `reject` (default) | The request fails with status 1. Nothing is decided from partial text |
| `truncate_end` | Keeps the beginning of the state and drops the rest |
| `truncate_start` | Keeps the end of the state and drops the beginning |
| `scan` | Reads the whole state in overlapping windows (each the room the question leaves, stepping by half a window) and combines them per question |

Under every policy except `reject`, option texts and instructions over the model's head budget are shortened exactly as llama.cpp's decision server does (the model was trained that way); `reject` refuses such questions instead. The question and options, after any shortening, must fit within the limit on their own; otherwise the request fails with status 1 under every policy. When the effective policy is not `reject` (the request's own `overflow`, or else the engine's or route's default), `usage` always reports `state_tokens`, `state_tokens_dropped` (the most any question dropped), `truncated_questions` (questions whose state was cut or whose instructions or options were shortened) and `windows` (the most state windows any question used: 1 when the state fitted, 0 when every question was a single-option `choice`). Under `reject` these fields are absent.

With `scan`, a `noul` answer is the maximum over windows ("does any part say this?"), or the minimum with `"aggregate": "all"` on the question ("does the whole text say this?"). `choice` and `score` answers come from the window with the highest `answer_confidence`. A scanned answer carries `window`: the deciding window's `index`, the window `count`, and its `token_start`/`token_end` in the tokenized state. Its probabilities are that window's, not a calibrated statement about the whole document. A state that fits is read once, exactly as without `scan`. `max_windows` (1–1024, default 32) caps the windows per question; a state needing more fails with status 1. Each window is one forward pass, and `input_tokens` counts every window. One call may plan at most the engine's `max_windows_per_call` forward passes (default 256, at most 4,096, `limits.windows_per_call`) in total over all its questions and requests, scanned or not; a call needing more fails with status 1 and a message naming `max_windows_per_call` before any inference on a single engine, and before the request that exceeds it on a router. Applications that accept untrusted input should lower it, since a pass near 512 tokens takes seconds.

## Primitive requests and results

Requests are strict: these are the only top-level keys, and any other key (including media `images` or `files`) is rejected.

| Request key | Meaning |
|---|---|
| `state` | Required, non-null text or JSON: what the questions are about |
| `questions` | 1–32 named questions; exactly one of `questions` or `schema` |
| `schema` | A [structured decision](#structured-decisions) schema instead of `questions` |
| `boolean_threshold` | With `schema` only: 0–1, default 0.5 |
| `model` | Routers only, and required there: a route ID or `"auto"` ([routing](#local-routing)) |
| `lang` | With `"model": "auto"` only: a language tag of 1–64 bytes that skips detection |
| `max_tokens` | Prompt limit for this request, at most the engine's `context_tokens` |
| `temperatures` | Calibration overrides for this request, merged over the engine's |
| `overflow` | `reject`, `truncate_end`, `truncate_start` or `scan` ([Long input](#long-input)) |
| `max_windows` | 1–1024, default 32: the most `scan` windows per question |

| Question key | Meaning |
|---|---|
| `type` | Required: `choice`, `noul` or `score` |
| `instructions` | Required, non-null text or JSON |
| `criteria` | Options or levels; see the table below |
| `aggregate` | `noul` only: `any` (default) or `all`, how `scan` combines windows |

Any other key in a question is rejected.

A primitive request contains non-null `state` and 1–32 named `questions`. Each question requires a supported `type` and non-null `instructions`; state, instructions and criterion descriptions may be text or structured JSON. Structured values enter the prompt as the original Laya writes them, the format the model was trained on: one-line JSON with `", "` and `": "` separators, non-ASCII text kept, and numbers at full precision (`149.0` stays `149.0`; integers outside the 64-bit range keep all their digits, as Python's exact integers do). An integer whose magnitude exceeds the largest double (about 1.797 × 10^308: some integers of 309 digits and all longer ones) is rejected with status 1, where Python would accept it. A structured state may serialize longer than its request text (`1e15` becomes `1000000000000000.0`); when the request runs the model, a state whose serialized text exceeds 2 MiB is rejected with status 1, as is a rendered question over 2 MiB (a question with a single option is answered without running it, and neither limit is checked). A criterion description that is `null` or `""` means no description; `0` and `false` are descriptions. A `score` answer's `legend` echoes the level descriptions as given; an integer outside the 64-bit range keeps all its digits there too, as a JSON number, in the bindings that return the result as text and in Python; Node and the browser parse results with `JSON.parse`, which rounds integers above 2^53 to the nearest double, so a legend there can lose digits.

| Type | Criteria | Answer |
| --- | --- | --- |
| `choice` | Object with 1–255 named options (label → description), or a list of 1–255 distinct labels without descriptions | Highest-probability `choice` and a `probabilities` object |
| `noul` | Omitted/null or an object containing only `false`/`true` descriptions | `noul`: probability of true |
| `score` | Array of 2–10 ordered level descriptions, none `null` | `score`: expected zero-based level index; `legend` and `probabilities` |

Every answer also has `answer_confidence` and `confidence`:

- `answer_confidence` is the probability of the reported answer: the winning option's or level's probability, and for `noul` the larger of `noul` and `1 − noul`. It is the calibrated quantity: once the temperatures are fitted on held-out data, answers reported at `answer_confidence` c are right about c of the time. Gate or abstain on this value.
- `confidence` is how concentrated the whole distribution is, `1 − H(p)/log k`, where H(p) = −Σ p log p is the entropy of the probabilities and k the number of options or levels (k = 2 for `noul`): 0 for a uniform distribution, 1 for certainty. It is not calibrated.

A `choice` with a single option is answered without running the model: that option, with probability, `confidence` and `answer_confidence` 1, using no input tokens.

Probabilities are a softmax of the model's scores divided by the calibration temperature for the question's type and option count (see [Engine configuration](#engine-configuration)). Both confidence fields are model-derived statistics, not application-specific decision thresholds. The definitions follow the original Laya, except that its `noul` `confidence` is max(p), which here is `answer_confidence`.

Results contain `answers` keyed by question ID and `usage` with `input_tokens` and `output_tokens: 0` (plus the overflow fields under [Long input](#long-input)). No chat text is generated. Input insertion order is preserved by the core and can affect inference; ties choose the first option. JavaScript object serialization reorders integer-like keys: pass a raw JSON string when literal ordering matters. Node and the browser serialize request objects with `JSON.stringify`, which also writes `149.0` as `149`, so a structured value's prompt text, and with it the results, can differ from the same request sent as a raw JSON string or from Python. They also reject `NaN` and `Infinity` in a request object with status 1, where `JSON.stringify` would write `null`. Bindings return identical results for the same request text. Node and browser results are parsed JavaScript objects, so their members follow ECMAScript property order (integer-like keys ascending, then insertion order); names and values match the core result, except that integers above 2^53 are rounded by `JSON.parse`. Literal mask text in values is replaced by a space before rendering, and the text between mask tokens is tokenized on its own, as the model was trained. Other special tokens written literally in text (for example `[SEP]` or `[CLS]`) are tokenized as those special tokens, as upstream llama.cpp and the original Laya's tokenizer do; a `[SEP]` in the last option's description therefore breaks the prompt layout and is rejected with status 1. Input longer than its limit follows the request's [overflow policy](#long-input).

### Ready-made requests

Ready-made requests (`examples/request.json`, a structured and a batch request, and presets for support triage, email triage, prompt-injection screening and moderation) are described in [examples/](../examples/README.md).

### Option order

Option order can influence a `choice`. To reduce that bias, ask the same question several times in one request with the options in rotated order (for example `intent_r0`, `intent_r1`, `intent_r2`), then average each label's probability across the copies. Each copy costs one more model pass.

## Batches

Pass a JSON array of 1–32 requests; results are an array in the same order. Primitive and structured requests can be mixed. Limits apply to the entire call: 1 MiB UTF-8, 128 questions total, 32 questions per request, depth 64, at most 1,024 members and no repeated key in any JSON object (also in open and router configs), and at most `max_windows_per_call` planned forward passes (default 256, at most 4,096); each prompt must fit its token limit (`max_tokens`, at most the engine's `context_tokens`, checked even when every question is a single-option `choice`) or follow its overflow policy. The model's instruction/option head budget still applies. Tokenization time is linear in the text's length. Every binding's `execute` accepts an array:

```js
const results = await engine.execute([requestA, requestB]);
```

On a single engine, every request is planned and tokenized before any inference. A router first validates every request and resolves every route (including `"model": "auto"` detection), then loads, tokenizes and executes request by request in input order. A later model/tokenization failure may follow earlier inference, but returns no partial results. Cache changes are not rolled back.

Questions run one at a time: one forward pass per question, or one per window under `scan`; a `choice` with a single option runs none. A request's state is tokenized once and reused by its questions. This is a bounded convenience/consistency API, not a simultaneous-inference optimization; [ROADMAP.md](ROADMAP.md#research-questions) records why questions are not batched into one pass. Accepted Node calls occupy one queue slot per whole batch; `close()` drains the batch.

## Structured decisions

Pass `state`, `schema`, and optionally `boolean_threshold` (default 0.5). Do not also pass `questions`. [The shared fixture](../tests/fixtures/structured.json) defines a representative request. Results include the original `answers` and `usage`, plus a `values` object.

The supported JSON Schema subset is deliberately small:

- Root `type: "object"` with 1–32 named `properties`.
- String properties require a unique string `enum` of 1–255 values; ordinary choice probabilities select the value with highest probability.
- Boolean properties use `noul`; projection is `probability >= boolean_threshold`. The threshold must be finite and between 0 and 1 inclusive.
- Integer properties require inclusive integer `minimum` and `maximum` within int32, spanning 2–10 levels. They use ordinal score questions with numeric level descriptions. Projection is **argmax level plus minimum**, not a rounded expected score. Raw `score` remains the expected zero-based level index.
- Ties choose the first declared enum/ordinal level.
- Property `description` supplies instructions; its name is the default.
- `required` is checked for existing, unique property names. Every declared property is populated, including optional properties. There is no automatic omission or confidence-based abstention.
- `additionalProperties`, when present, must be false. Title/description metadata is accepted as strings only. No additional values are generated.

Free text, nested objects, arrays, references, unions, nullable/constant values, other constraints and unknown schema/request keywords are rejected. Enum values are strings only in this version. The `answers` carry the same `answer_confidence` and `confidence` fields as primitive answers.

## Passive observers

One opted-in call emits `start` followed by either `end` or `error`. This applies to the whole batch, including validation failures reaching the native API. Events contain `event`, `request_id`, numeric `status`, and `elapsed_ms`. They contain no state, prompts, answers or paths. Duration includes validation, loading, lock waits and inference; native observer execution can affect elapsed time. There is no promise that observers provide precise inference benchmarks.

```js
await engine.execute(request, {
  onEvent(event) { console.log(event.event, event.request_id); }
});
```

Native callbacks run synchronously on the caller's thread, outside the engine and router locks. The observer fields of `dtl_call_options` (`request_id`, `on_event`, `event_user_data`) are borrowed until the call returns; each event string is borrowed only during the callback. Reentrant calls are allowed, but C/C++ callbacks must not destroy the active handle, and no callback may create circular waits. The other bindings defer a close requested from inside a callback until the running call returns. Keep callbacks short. Observer errors cannot change decision results: C++ exceptions are contained; C callbacks must never unwind; Rust catches unwinding panics (not process-aborting panics). If a Rust panic payload itself panics during destruction, that secondary payload is retained to prevent further destructor panics from escaping across the C ABI.

Node and the browser forward native events from the inference worker to the host thread. Request IDs are generated per instance unless `requestId` is given. A call's events reach its handler before its promise settles. If an abort or deadline settles the promise first, the handler is dropped and late events from that call are discarded. Handler promises are not awaited; throws/rejections are contained. Reentrant Node calls enter the ordinary bounded FIFO queue. Binding-level errors such as queue overflow, closed handles, NUL bytes or unserializable input occur before native execution and do not emit native events.

## Local routing

```js
const router = await Engine.openRouter({
  models: { english: '/app/resources/Laya-Q8_0.gguf' },
  max_loaded: 1
}, { config: { threads: 2 }, maxPending: 16 });
try {
  const result = await router.execute({ ...request, model: 'english' });
  console.log(result.model, result.answers);
} finally { await router.close(); }
```

The registry accepts 1–16 model IDs (1–128 bytes each) mapped to local regular files. Paths are canonicalized at open, so relative paths resolve then and symlink aliases share a cache entry. Hard links with different canonical paths are separate entries. Requests must specify a registered `model`; missing or unknown IDs fail with invalid argument. Single-model engines reject routing fields. Routed results include the chosen `model` ID. `auto` is reserved and cannot be a model ID; `"model": "auto"` chooses a route by language (below).

Loads are lazy and serialized. Before inference, each model must pass the core's Laya GGUF metadata, three-column output, template, marker, head-budget and calibration checks. This verifies format compatibility, **not model quality or artifact authenticity**. Applications should verify bundled weights against their manifest and keep resources immutable ([models](MODELS.md#verifying-a-model-you-ship)). The pinned English, multilingual and typed-decisions models are qualified here: each is checked against the original Laya's recorded results (identical token counts, probabilities within 0.05) and against upstream llama.cpp processing ([accuracy](PERFORMANCE.md#accuracy-against-the-original-laya)). Other models require their own tokenizer, calibration and decision tests before use. There is no fallback model or network download.

### Choosing the model by language

Routing by language is opt-in. Name the routes in `auto`, then send requests with `"model": "auto"`:

```json
{"models": {"en": "Laya-Q8_0.gguf", "ml": "laya-multilingual-f16.gguf"},
 "max_loaded": 2,
 "auto": {"english": "en", "default": "ml"}}
```

`max_loaded` 2 keeps both models loaded; with the default of 1, every switch of language reloads a model. The two files together are about 1.1 GB, so in the browser raise `maxModelBytes` above its 1 GiB default (see [BROWSER.md](BROWSER.md#open-options)).

Text that is confidently English goes to `english`; everything else goes to `default`, which should be the multilingual model since it also reads English. Only the string values of `state` are read (keys are usually English field names), down to nesting depth 6, joined with spaces, and only the first 4,000 characters (Unicode code points) of the result. Text is non-Latin when at least a fifth of its letters are non-Latin, or at least a tenth with 10 or more non-Latin letters. Otherwise it is English when it has at least four words, no other language leads (another language leads when it has a function word no other list has and at least 2 function-word hits, and at least 2 more than English), it has at least one English function word, and letters English does not use (é, ș, ł, ...) are rare. Short or undecided text goes to `default`. The word lists and thresholds come from the original Laya, and the detector agrees with all 180 of its recorded detection and routing cases (`tests/language.cpp`), but it is a heuristic: pass `lang` when the application knows the language. `"lang"` (`en`, `en-US`, `eng`, `English`, ...) skips detection; any other tag goes to `default`. `lang` is accepted only with `"model": "auto"`.

`usage.routing` reports why: `reason` is `lang` (with the request's `lang`), `english_text` (with `language: "en"`), `no_letters`, `non_latin_script` (with `script`), `too_short`, `other_language` (with `language`), `non_english_letters` (with `language` when another language's words were found) or `no_english_words`. Explicitly routed requests have no `routing`.

`max_loaded` defaults to 1, allows 1–4, and bounds the number of loaded models: one per canonical path and configured `threads`, `context_tokens` and `temperatures` (see [Engine configuration](#engine-configuration)). Least-recently-used models are unloaded **before** another is loaded at capacity. This avoids temporarily holding one extra model. A failed load can therefore evict a previously cached model. Failed loads are not cached. This is a model-count bound, not a byte-accurate RAM quota; size and runtime buffers vary with the artifact. Closing releases every cached model/context. Native close must never race an active call; see the closing table under [Interfaces](#interfaces) for what each binding does.

## Cancellation and deadlines

`dtl_execute` takes an optional borrowed `dtl_call_options`. Initialize it with `dtl_default_call_options()`. `timeout_ms` is zero (disabled) or 1..86400000. The native deadline begins during execution setup after the start observer, and includes validation, waiting for the handle lock and inference. Cancellation callbacks must be nonblocking and thread-safe, may run on backend threads, and must not reenter the active handle. Keep their user data alive until the call returns. Return zero to continue, `DTL_DEADLINE_EXCEEDED` for a host deadline, or another nonzero value to cancel. The first observed stop is latched even if a caller subsequently resets its flag. C++ callback exceptions count as cancellation.

Statuses: `DTL_CANCELLED` (5) and `DTL_DEADLINE_EXCEEDED` (6). Failure returns no partial result. Waiting calls check controls while acquiring the mutex; preparation and question boundaries check them too. CPU execution uses the upstream abort callback and resets it before releasing the inference lock. Cancellation is cooperative, not a thread kill or a hard wall-clock guarantee. Model loading and template execution may finish before the next safe check. Tokenization checks the controls while it splits text at special tokens and while it merges each word's tokens (runtime patch `tokenizer-stop-check.patch`): it counts its work over the whole call, also across the short pieces that special tokens split text into, and checks once per 1,024 units (words, merges, special-token passes and matches, and each KiB the special-token search scans), so a long tokenization stops shortly after a cancellation or deadline; the regular-expression pre-split of the text into words and each word's symbol setup are not checked and run to their end once started. Only the BPE tokenizer, which every supported Laya model uses, counts its merges; the SPM, WPM and UGM tokenizers are checked only while splitting at special tokens. Tokenization time grows linearly with the text, which the request limits above bound: on an 8-core x86-64 development machine that was running other work, each pass over 1 MiB of state took about 4.3 s for ordinary text and up to about 11 s for long words without spaces, the slowest input measured; the text was then tokenized twice, so a request paid twice that, and it is now tokenized once. Router cache mutations are not rolled back by a cancelled request.

C++ `execute` takes a `call_options` with `request_id`, `timeout_ms`, `should_cancel`, `cancel_user_data` and `on_event`. Rust `execute_with` takes `CallOptions` built with `request_id`, `timeout_ms`, `cancellation_token` (a borrowed `CancellationToken`) and `on_event`; the token only cancels, and `timeout_ms` is the deadline (the C API's `dtl_cancel_flag` still accepts 6 for a host deadline). Hosts whose own callbacks are costly on backend threads (interpreter locks, VM thread attachment) pass `dtl_cancel_flag` as `should_cancel` with a pointer to an aligned `uint32_t` they set; the Rust, Python, Java, .NET and Go bindings do this.

Node calls accept `{ signal, timeoutMs }`; an already-aborted signal fails at once with status 5. A `CancellationToken` (Python, Java, Rust) is one-shot: a cancelled token stays cancelled, so use a new one for later calls. Their deadline starts at enqueue and includes time waiting in the worker queue. Abort/deadline rejects the promise promptly and signals the same native control through shared memory. Rejection does not mean resources have already been freed. Cancelled entries still count against the queue bound until acknowledged, preventing cancellation from bypassing admission limits. `await close({ cancelPending: true })` cancels accepted work and waits for cleanup; ordinary `close()` still drains it. Repeated close calls share one promise. Never race native handle destruction with calls.

## Ownership, admission and errors

Native `threads` is 0 (automatic) or an integer in 1..256. Automatic is decided once, when the model loads, and uses at most 4 threads, and no more than the CPUs the process may run on: the hardware threads, the CPU affinity mask (on Linux that of the thread that loads the model, since Linux has no process-wide mask: the thread opening an engine, but for a router the thread running the first request on a route, because a route's model loads on its first use; ggml's worker threads inherit the affinity of the thread calling into the engine; on Windows the process's) and the smallest CPU quota of the process's cgroup v2 group and its ancestors (`cpu.max`, rounded up; Linux), at least 1. ggml's workers wait by spinning, so more threads than usable CPUs is slower. An explicit count is used as given. Node's `maxPending` defaults to 16 and accepts 1..1024; each accepted call or entire batch occupies one slot. Work is FIFO and excess work is rejected. `close()` stops admission and drains accepted calls. Worker failure rejects pending work; callers must open a new instance. Browser admission follows the same bounds, with platform-specific controls and initialization rules in [BROWSER.md](BROWSER.md).

C inputs are borrowed UTF-8 NUL-terminated strings. Initialize call options with `dtl_default_call_options()`; a `struct_size` other than this ABI's `sizeof(dtl_call_options)` or a nonzero `reserved` field is rejected with status 1. Output pointers are reset on entry. Successful result buffers and any non-null error buffers belong to the caller and must be released by `dtl_free`. Error messages are valid UTF-8 (invalid bytes from paths, metadata or request text are replaced with U+FFFD) of at most 1 KiB, longer ones cut at a character boundary and ended with `...`. An error message may be absent on allocation failure. Null close/free is allowed; closing a non-null handle invalidates it and must not race any active call.

| Status | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Invalid argument/request or exceeded input bounds |
| 2 | Model loading/compatibility failure |
| 3 | Inference failure |
| 4 | Out of memory |
| 5 | Cancelled |
| 6 | Deadline exceeded |
| 7 | Unsupported platform/required CPU features |

Allocation failure while parsing, planning, rendering or returning a request yields status 4 and leaves the engine usable. During model loading the runtime loader may report it as status 2. The library does not terminate the process on C++ allocation failure. Inside llama.cpp/ggml C code, failing to allocate model weights or compute buffers is reported as status 2 or 4. During inference, a failed CPU thread-pool or work-buffer allocation, and a CPU worker thread that the operating system refuses to create (for example at `RLIMIT_NPROC` or a container's pids limit), are reported as status 4; the engine stays usable. ggml aborts the process (`SIGABRT`) when one of its small bookkeeping allocations fails, such as a graph context or scheduler array; that is upstream design. On Linux with the default memory overcommit such allocations practically never fail, because the kernel's OOM killer stops a process under memory pressure first. In the browser an abort ends only that worker's WebAssembly instance (see BROWSER.md). Applications that must survive allocation failure inside ggml should run the engine in a separate process.

Every binding exposes native statuses through its error type, with a `Status` enum. JavaScript rejects with a `LayaError` carrying `status`: binding-level validation errors (including a `TypeError` for unknown router keys) carry status 1, and JavaScript lifecycle failures also carry `code`: `ERR_LAYA_QUEUE_FULL` and `ERR_LAYA_CLOSED` (an `EngineClosedError`) with status 1, `ERR_LAYA_WORKER_EXITED` with status 3; every status-7 error (a missing, unloadable or wrong-ABI addon or runtime, or a CPU without the required features) is a `LayaLoadError` with `ERR_LAYA_UNSUPPORTED`. A cancelled browser initialization is status 5, like a cancelled request. Error message wording is diagnostic, not a stable machine-readable contract.

## Runtime and model diagnostics

`dtl_info(NULL, &result, &error)` returns runtime/version/ABI identity, supported features and limits, and required/missing CPU features without loading a model. `features` lists `structured_decisions`, `sequential_batches`, `routing`, `language_routing`, `overflow_policies`, `observers`, `cooperative_cancellation` and `deadlines`; `overflow_policies` lists the accepted policies; `limits` includes `request_bytes`, `object_members`, `questions_per_request`, `requests_per_batch`, `questions_per_batch`, `default_context_tokens`, `minimum_context_tokens`, `scan_windows`, `windows_per_call`, `default_windows_per_call`, `routes` and `loaded_models`. Supply an engine (single or router) to include its `max_windows_per_call`; a single-model engine adds its model architecture, name (`null` when the model has no `general.name`), head/token budgets and calibration temperatures. The allocated JSON follows normal `dtl_free` ownership; unknown fields must be ignored for forward compatibility. It contains no model path, request, prediction or telemetry destination. Invalid UTF-8 in model metadata is replaced (U+FFFD). Metadata is not proof of a model's hash/provenance; use the immutable manifest for that. On a single-model engine, `dtl_info` takes the engine's lock, so it waits for a running call.

C++ exposes `ducktape::info()` and `engine.info()`. Rust exposes `runtime_info()` and `Engine::info()`. Node exposes `Engine.runtimeInfo()` and async `engine.info()`; browser engines expose async `info()`. Python, Go, Java and .NET expose `runtime_info()`/`RuntimeInfo()`/`Engine.runtimeInfo()`/`Engine.RuntimeInfo()` and `info()` on an engine. Routers report common runtime capabilities in every binding, since models are loaded lazily. Inference and model identities are shared C ABI results, not independently maintained binding constants.

`numerical_profile` contains an `id` derived from the backend and required CPU features, plus `cross_profile_equivalence: false`. Bindings using the same native binary, model and settings return identical results; different profiles (CPU architectures, WebAssembly, WebGPU) can change probabilities, winning choices and threshold projections, so qualify thresholds on the profiles you deploy. The ID is a label, not a complete reproducibility fingerprint: also record the runtime and model hashes, the build and the device. In the browser, initialization fallback may select another profile, so inspect `info()` afterwards; inference failures never silently switch profiles. Profiles, measured differences and the CPU check behind status 7: [PLATFORMS.md](PLATFORMS.md#numerical-results-across-platforms).
