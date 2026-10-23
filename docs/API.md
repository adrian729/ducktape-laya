# Shared decision API

All planning, validation, projection, inference and model caching run in C++.
C++, Rust, Node/TypeScript, Python, Go, Java, .NET and browser bindings adapt
the same C ABI. Native inference performs no downloads and uses no server.
Browser asset fetching is explicitly controlled by the host; see
[BROWSER.md](BROWSER.md).

The C ABI (version 2) has one handle type for a single model or a router, one
call function and JSON for configuration and requests. All results/errors use
`dtl_free`. No partial result is returned on failure. Requests are strict:
unsupported fields are rejected.

| Interface | Open | Call | Router |
|---|---|---|---|
| C | `dtl_open(path, config_json, &engine, &error)` | `dtl_execute(engine, json, call_options, &result, &error)` | `dtl_open_router(router_json, config_json, &engine, &error)` |
| C++ | `ducktape::engine::open(path, config_json)` | `engine.execute(json, call_options)` | `ducktape::engine::open_router(router_json, config_json)` |
| Rust | `Engine::open(path, &Config)` (`path` is any `AsRef<Path>` that is valid UTF-8) | `engine.execute(json)`; `execute_with(json, CallOptions)` | `Engine::open_router(router_json, &Config)` |
| Node/TS | `Laya.open(path, options)` | `execute(request, options)` | `Laya.openRouter(config, options)` |
| Python | `Engine.open(path, threads=, context_tokens=, temperatures=, overflow=)` | `execute(request, on_event=, request_id=, timeout=, cancel=)`; `execute_async` | `Engine.open_router(config, ...)` |
| Go | `laya.Open(path, *Config)` | `Execute(json)`; `ExecuteContext(ctx, json, *CallOptions)` | `laya.OpenRouter(routerJSON, *Config)` |
| Java | `Laya.open(path, Config)` | `execute(json)`; `execute(json, CallOptions)` | `Laya.openRouter(routerJson, Config)` |
| .NET | `LayaEngine.Open(path, LayaConfig)` | `Execute(json, options, token)`; `ExecuteAsync` | `LayaEngine.OpenRouter(routerJson, LayaConfig)` |
| Browser | `Laya.open(options)` (model URL and open options together) | Same as Node/TS | `Laya.openRouter(modelAssets, options)`; explicit URLs and browser asset limits |

The bindings differ only where their language has a convention:

| Binding | Engine type | Results | Deadline | Cancellation | Events run on | Async | Closed engine |
|---|---|---|---|---|---|---|---|
| C++ | `ducktape::engine` | JSON string | `timeout_ms` | `should_cancel` callback | calling thread | — | destructor |
| Rust | `Engine` | JSON string | `timeout_ms` | `&AtomicU32` flag | calling thread | — | ownership |
| Node/TS | `Laya` | parsed | `timeoutMs` | `AbortSignal` | host thread | promises | `code: 'ERR_LAYA_CLOSED'` |
| Python | `Engine` | parsed | `timeout` (seconds) | `CancelToken` | thread running the call | `execute_async` | `EngineClosedError` |
| Go | `*Engine` | JSON string | context deadline | context | calling goroutine | — | `ErrClosed` |
| Java | `Laya` | JSON string | `Duration` | `CancellationToken` | calling thread | — | `LayaClosedException` |
| .NET | `LayaEngine` | JSON string | `TimeSpan` | `CancellationToken` | calling thread (pool thread for `*Async`) | `ExecuteAsync` | `ObjectDisposedException` |

Every binding reports native failures with the C status (`status`, `Status` constants) and maps cancellation to 5 and deadlines to 6 (.NET raises the standard `OperationCanceledException` when its token cancels). Arguments a binding checks itself (thread counts, timeout ranges) use the language's standard argument errors. Bindings that convert strings reject text that is not well-formed Unicode (lone surrogates) instead of replacing it. The Rust, Go, Node, Python, Java, .NET and browser bindings check `dtl_abi_version()` and refuse any ABI but 2. The check matters for the bindings that load the library by file name at run time (Python, Java and .NET, for example `libducktape_laya.so` from `DUCKTAPE_LAYA_LIB_DIR`): Python raises `OSError`, Java and .NET report status 7. Rust, Go and the Node addon link against `libducktape_laya.so.1` and its `DUCKTAPE_LAYA_2` symbol versions, so with only an ABI 1 library (`libducktape_laya.so.0`) the program or addon fails to load before any call.

Closing differs by binding:

| Binding | Close while a call runs | Close from an event callback |
|---|---|---|
| C | Not allowed: `dtl_close` frees the handle at once | Not allowed |
| C++ | Not allowed: the destructor closes the handle at once | Not allowed |
| Rust | Prevented by ownership: dropping needs every borrow to end | Prevented by ownership |
| Node, browser | `close()` drains accepted calls; `close({ cancelPending: true })` cancels them first | Allowed; resolves after the running call |
| Python, Java, .NET | Waits for running calls | Returns at once; the handle is released when the running call finishes |
| Go | `Close` waits for running calls | Use `CloseDeferred()`: `Close` would wait for its own call |

In Python, Go, Java and .NET, a call made after closing has started fails at once with the binding's closed error, even while the close still waits for running calls.

## Engine configuration

Opening takes an optional JSON config; each binding exposes the same keys as
native options. Unknown keys are rejected.

| Key | Meaning |
|---|---|
| `threads` | 0 (automatic, at most 4) or 1–256 CPU threads |
| `context_tokens` | The longest prompt the engine reserves memory for: 32 up to the model's trained length. Default 512 (or the trained length if smaller). Compute follows each prompt's real length; only memory grows with this value. The model files allow 8,192, but the original Laya runs the English checkpoint at 512 and the multilingual and typed-decisions checkpoints at 1,024, so longer prompts are untested for accuracy |
| `overflow` | Default policy for prompts over their limit; see [Long input](#long-input) |
| `temperatures` | Calibration overrides, keyed like the model's metadata: a type (`choice`, `score`, `noul`) or a type and option-count bucket (`choice.2`, `choice.3_5`, `choice.6_10`, `choice.11`, `score.3_5`, `noul.2`, ...). Values must be greater than 0 and finite as a 32-bit float (`1e-50` and `1e39` are rejected) |

A temperature is looked up most specific first: the bucket key, then the type
key, then 1. An override replaces only the keys it names, so to change a
two-option `choice` when the model defines `choice.2`, override `choice.2`; an
override of plain `choice` applies only to buckets the model leaves undefined.
Fit temperatures on your own data with the original Laya's calibration tools and
pass them here.

Temperatures stored in the model are bounded to [0.5, 5], as the original Laya
does: the shipped `choice.11` value, 0.1006, sharpens scores about tenfold and
would report a 0.24 top probability as 0.9999. Temperatures you pass are used as
given. `info()` reports the applied values as `temperatures` and the stored ones
as `metadata_temperatures`.

A router's config applies to every route; a route given as `{"path": "...", "config": {...}}` overrides it key by key. Routes share one loaded model when they have the same canonical path and the same configured `threads`, `context_tokens` and `temperatures` after that merge; each route still applies its own default `overflow`. The configured values are compared as given, not the effective ones: a route without `context_tokens` and a route with `context_tokens` 512 load the model separately even though both run at 512. `context_tokens` below 32 is rejected when the config is parsed; its upper bound, the model's trained length, is checked when the model loads, which for a router route is the first request that uses it.

Each request may add `max_tokens` (a prompt limit up to the engine's `context_tokens`), `temperatures` (overrides for that request only, merged over the engine's), `overflow` and `max_windows` (see [Long input](#long-input)). A prompt over its limit is handled by the overflow policy; the default, `reject`, fails with status 1. `info()` on a single-model engine reports `context_tokens` and `trained_context_tokens`.

## Long input

A prompt longer than its limit (the request's `max_tokens`, at most the engine's
`context_tokens`) is handled by the request's `overflow` policy. The engine's
config can set a default `overflow`; a request's own value wins.

| `overflow` | What happens |
|---|---|
| `reject` (default) | The request fails with status 1. Nothing is decided from partial text |
| `truncate_end` | Keeps the beginning of the state and drops the rest |
| `truncate_start` | Keeps the end of the state and drops the beginning |
| `scan` | Reads the whole state in overlapping windows (each the room the question leaves, stepping by half a window) and combines them per question |

Under every policy except `reject`, option texts and instructions over the
model's head budget are shortened exactly as llama.cpp's decision server does
(the model was trained that way); `reject` refuses such questions instead. The question and options, after any shortening, must fit within the limit on their own; otherwise the request fails with status 1 under every policy. When the effective policy is not `reject` (the request's own `overflow`, or else the engine's or route's default), `usage` always reports `state_tokens`, `state_tokens_dropped` (the most any question dropped), `truncated_questions` (questions whose state was cut or whose instructions or options were shortened) and `windows` (the most state windows any question used: 1 when the state fitted, 0 when every question was a single-option `choice`). Under `reject` these fields are absent.

With `scan`, a `noul` answer is the maximum over windows ("does any part say
this?"), or the minimum with `"aggregate": "all"` on the question ("does the whole
text say this?"). `choice` and `score` answers come from the window with the
highest `answer_confidence`. A scanned answer carries `window`: the deciding
window's `index`, the window `count`, and its `token_start`/`token_end` in the
tokenized state. Its probabilities are that window's, not a calibrated
statement about the whole document. A state that fits is read once, exactly as
without `scan`. `max_windows` (1–1024, default 32) caps the windows per
question; a state needing more fails with status 1. Each window is one forward pass, and `input_tokens` counts every window. One call may plan at most 4,096 forward passes in total over all its questions and requests (`limits.windows_per_call`); a call needing more fails with status 1 before any inference on a single engine, and before the request that exceeds it on a router.

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

A primitive request contains non-null `state` and 1–32 named `questions`.
Each question requires a supported `type` and non-null `instructions`; state,
instructions and criterion descriptions may be text or structured JSON.
Structured values enter the prompt as the original Laya writes them, the format
the model was trained on: one-line JSON with `", "` and `": "` separators,
non-ASCII text kept, and numbers at full precision (`149.0` stays `149.0`).
A criterion description that is `null` or `""` means no description; `0` and
`false` are descriptions.

| Type | Criteria | Answer |
| --- | --- | --- |
| `choice` | Object with 1–255 named options (label → description), or a list of 1–255 distinct labels without descriptions | Highest-probability `choice` and a `probabilities` object |
| `noul` | Omitted/null or an object containing only `false`/`true` descriptions | `noul`: probability of true |
| `score` | Array of 2–10 ordered level descriptions, none `null` | `score`: expected zero-based level index; `legend` and `probabilities` |

Every answer also has `answer_confidence` and `confidence`:

- `answer_confidence` is the probability of the reported answer: the winning
  option's or level's probability, and for `noul` the larger of `noul` and
  `1 − noul`. It is the calibrated quantity: once the temperatures are fitted on
  held-out data, answers reported at `answer_confidence` c are right about c of
  the time. Gate or abstain on this value.
- `confidence` is how concentrated the whole distribution is, `1 − H(p)/log k`, where H(p) = −Σ p log p is the entropy of the probabilities and k the number of options or levels (k = 2 for `noul`): 0 for a uniform distribution, 1 for certainty. It is not calibrated.

A `choice` with a single option is answered without running the model: that
option, with probability, `confidence` and `answer_confidence` 1, using no input
tokens.

Probabilities are a softmax of the model's scores divided by the calibration temperature for the question's type and option count (see [Engine configuration](#engine-configuration)). Both confidence fields are model-derived statistics, not application-specific decision thresholds. The definitions follow the original Laya, except that its `noul` `confidence` is max(p), which here is `answer_confidence`.

Results contain `answers` keyed by question ID and `usage` with `input_tokens`
and `output_tokens: 0` (plus the overflow fields under [Long input](#long-input)). No chat text is generated. Input insertion order is
preserved by the core and can affect inference; ties choose the first option.
JavaScript object serialization reorders integer-like keys: pass a raw JSON
string when literal ordering matters. Node and the browser serialize request
objects with `JSON.stringify`, which also writes `149.0` as `149`, so a structured
value's prompt text, and with it the results, can differ from the same request
sent as a raw JSON string or from Python. Bindings return identical results for
the same request text. Node and browser results are parsed JavaScript
objects, so their members follow ECMAScript property order (integer-like keys
ascending, then insertion order); names and values match the core result exactly. Literal mask text in values is replaced by a space before rendering, and the text between mask tokens is tokenized on its own, as the model was trained. Other special tokens written literally in text (for example `[SEP]` or `[CLS]`) are tokenized as those special tokens, as upstream llama.cpp and the original Laya's tokenizer do; a `[SEP]` in the last option's description therefore breaks the prompt layout and is rejected with status 1. Input longer than its limit follows the request's [overflow policy](#long-input).

### Example requests and option order

`examples/request.json` is a primitive request, `examples/structured.json` a
structured one and `examples/batch.json` a batch. `examples/presets/` has ready
requests for common uses: support triage, email triage, prompt-injection
screening and moderation. They are ordinary requests: copy one and adapt its
questions. To run one, build the `laya-decide` (C, `examples/decide.c`) or
`laya-decide-cpp` (C++, `examples/decide.cpp`) target with
`-DDUCKTAPE_BUILD_EXAMPLES=ON`, or the Rust example
(`cargo run --example decide` in `bindings/rust/ducktape-laya`), and pass the
model path with the request on standard input.

Option order can influence a `choice`. To reduce that bias, ask the same
question several times in one request with the options in rotated order (for
example `intent_r0`, `intent_r1`, `intent_r2`), then average each label's
probability across the copies. Each copy costs one more model pass.

## Batches

Pass a JSON array of 1–32 requests; results are an array in the same order.
Primitive and structured requests can be mixed. Limits apply to the entire
call: 1 MiB UTF-8, 128 questions total, 32 questions per request, depth 64, at
most 1,024 members in any JSON object (also in open and router configs), and at
most 4,096 planned forward passes; each
prompt must fit its token limit (`max_tokens`, at most the engine's `context_tokens`, checked even when every question is a single-option `choice`) or follow its overflow policy. The model's instruction/option head budget still applies. A run of more than 4,096 consecutive ASCII spaces in a question or in the state is rejected, because llama.cpp's tokenizer takes quadratic time on such runs. Every
binding's `execute` accepts an array:

```js
const results = await engine.execute([requestA, requestB]);
```

On a single engine, every request is planned and tokenized before any inference. A router first validates every request and resolves every route (including `"model": "auto"` detection), then loads, tokenizes and executes request by request in input order. A later model/tokenization failure may follow earlier inference, but returns no partial results. Cache changes are not rolled back.

Questions run one at a time: one forward pass per question, or one per window under `scan`; a `choice` with a single option runs none. A request's state is tokenized once and reused by its questions. This is a bounded convenience/consistency API, not a simultaneous-inference optimization; [ROADMAP.md](ROADMAP.md#research-questions) records why questions are not batched into one pass. Accepted
Node calls occupy one queue slot per whole batch; `close()` drains the batch.

## Structured decisions

Pass `state`, `schema`, and optionally `boolean_threshold` (default 0.5).
Do not also pass `questions`. [The shared fixture](../tests/fixtures/structured.json)
defines a representative request. Results include the original `answers`
and `usage`, plus a `values` object.

The supported JSON Schema subset is deliberately small:

- Root `type: "object"` with 1–32 named `properties`.
- String properties require a unique string `enum` of 1–255 values; ordinary
  choice probabilities select the value with highest probability.
- Boolean properties use `noul`; projection is `probability >= boolean_threshold`.
  The threshold must be finite and between 0 and 1 inclusive.
- Integer properties require inclusive integer `minimum` and `maximum` within
  int32, spanning 2–10 levels. They use ordinal score questions with numeric
  level descriptions. Projection is **argmax level plus minimum**, not a rounded
  expected score. Raw `score` remains the expected zero-based level index.
- Ties choose the first declared enum/ordinal level.
- Property `description` supplies instructions; its name is the default.
- `required` is checked for existing, unique property names. Every declared
  property is populated, including optional properties. There is no automatic
  omission or confidence-based abstention.
- `additionalProperties`, when present, must be false. Title/description metadata
  is accepted as strings only. No additional values are generated.

Free text, nested objects, arrays, references, unions, nullable/constant values,
other constraints and unknown schema/request keywords are rejected. Enum values
are strings only in this version. The `answers` carry the same `answer_confidence` and `confidence` fields as primitive answers.

## Passive observers

One opted-in call emits `start` followed by either `end` or `error`. This applies
to the whole batch, including validation failures reaching the native API.
Events contain `event`, `request_id`, numeric `status`, and `elapsed_ms`.
They contain no state, prompts, answers or paths. Duration includes validation,
loading, lock waits and inference; native observer execution can affect elapsed
time. There is no promise that observers provide precise inference benchmarks.

```js
await engine.execute(request, {
  onEvent(event) { console.log(event.event, event.request_id); }
});
```

Native callbacks run synchronously on the caller's thread, outside the engine
and router locks. The observer fields of `dtl_call_options` (`request_id`,
`on_event`, `event_user_data`) are borrowed until the call returns; each event string is borrowed only during the callback. Reentrant calls
are allowed, but C/C++ callbacks must not destroy the active handle, and no
callback may create circular waits. The other bindings defer a close requested
from inside a callback until the running call returns. Keep callbacks short. Observer errors cannot change decision results:
C++ exceptions are contained; C callbacks must never unwind; Rust catches
unwinding panics (not process-aborting panics).
If a Rust panic payload itself panics during destruction, that secondary payload
is retained to prevent further destructor panics from escaping across the C ABI.

Node and the browser forward native events from the inference worker to the host thread. Request IDs are generated per instance unless `requestId` is given. A call's events reach its handler before its promise settles. If an abort or deadline settles the promise first, the handler is dropped and late events from that call are discarded. Handler promises are not awaited; throws/rejections are contained.
Reentrant Node calls enter the ordinary bounded FIFO queue. Binding-level errors
such as queue overflow, closed handles, NUL bytes or unserializable input occur
before native execution and do not emit native events.

## Local routing

```js
const router = await Laya.openRouter({
  models: { english: '/app/resources/Laya-Q8_0.gguf' },
  max_loaded: 1
}, { threads: 2, maxPending: 16 });
try {
  const result = await router.execute({ ...request, model: 'english' });
  console.log(result.model, result.answers);
} finally { await router.close(); }
```

The registry accepts 1–16 model IDs (1–128 bytes each) mapped to local regular
files. Paths are canonicalized at open, so relative paths resolve then and
symlink aliases share a cache entry. Hard links with different canonical paths
are separate entries. Requests must specify a registered `model`; missing or
unknown IDs fail with invalid argument. Single-model engines reject routing
fields. Routed results include the chosen `model` ID. `auto` is reserved and cannot be a model ID; `"model": "auto"` chooses a route by language (below).

Loads are lazy and serialized. Before inference, each model must pass the core's
Laya GGUF metadata, three-column output, template, marker, head-budget and
calibration checks. This verifies format compatibility, **not model quality or
artifact authenticity**. Applications should verify bundled weights against
`models/manifest.json` and keep resources immutable. `scripts/verify-model.py`
does this from a checkout (it imports the repository's `scripts/release_support.py`
and needs Python 3.11+); the installed and npm `download-model.py` checks the hash
of the file it downloads.
The pinned English, multilingual and typed-decisions models are qualified here: each is checked against the original Laya's recorded results (identical token counts, probabilities within 0.05) and against upstream llama.cpp processing (see [RELEASE_CHECKS.md](RELEASE_CHECKS.md)). Other models require their own tokenizer, calibration and decision tests before use. There is no fallback model or network download.

### Choosing the model by language

Routing by language is opt-in. Name the routes in `auto`, then send requests with
`"model": "auto"`:

```json
{"models": {"en": "Laya-Q8_0.gguf", "ml": "laya-multilingual-f16.gguf"},
 "max_loaded": 2,
 "auto": {"english": "en", "default": "ml"}}
```

`max_loaded` 2 keeps both models loaded; with the default of 1, every switch of language reloads a model. The two files together are about 1.1 GB, so in the browser raise `maxModelBytes` above its 1 GiB default (see [BROWSER.md](BROWSER.md#open-options)).

Text that is confidently English goes to `english`; everything else goes to
`default`, which should be the multilingual model since it also reads English.
Only the string values of `state` are read (keys are usually English field
names), down to nesting depth 6, joined with spaces, and only the first 4,000
characters (Unicode code points) of the result. Text is non-Latin when at least a fifth of its letters are non-Latin, or at least a tenth with 10 or more non-Latin letters. Otherwise it is English when it has at least four words, no other language leads (another language leads when it has a function word no other list has and at least 2 function-word hits, and at least 2 more than English), it has at least one English function word, and letters English does not use (é, ș, ł, ...) are rare. Short or undecided text goes to `default`. The word lists and
thresholds come from the original Laya, and the detector agrees with all 180 of
its recorded detection and routing cases (`tests/language.cpp`), but it is a
heuristic: pass `lang` when the application knows the language. `"lang"`
(`en`, `en-US`, `eng`, `English`, ...) skips detection; any other tag goes to
`default`. `lang` is accepted only with `"model": "auto"`.

`usage.routing` reports why: `reason` is `lang` (with the request's `lang`), `english_text` (with `language: "en"`), `no_letters`, `non_latin_script` (with `script`), `too_short`, `other_language` (with `language`), `non_english_letters` (with `language` when another language's words were found) or `no_english_words`. Explicitly routed requests have no `routing`.

`max_loaded` defaults to 1, allows 1–4, and bounds the number of loaded models: one per canonical path and configured `threads`, `context_tokens` and `temperatures` (see [Engine configuration](#engine-configuration)). Least-recently-used models are unloaded **before** another is loaded at
capacity. This avoids temporarily holding one extra model. A failed load can
therefore evict a previously cached model. Failed loads are not cached.
This is a model-count bound, not a byte-accurate RAM quota; size and runtime
buffers vary with the artifact. Closing releases every cached model/context. Native close must never race an active call; see the closing table under [Shared decision API](#shared-decision-api) for what each binding does.

## Cancellation and deadlines

`dtl_execute` takes an optional borrowed `dtl_call_options`. Initialize it with
`dtl_default_call_options()`. `timeout_ms` is zero (disabled) or 1..86400000. The native deadline begins during
execution setup after the start observer, and includes validation, waiting for
the handle lock and inference. Cancellation callbacks must be nonblocking and
thread-safe, may run on backend threads, and must not reenter the active handle.
Keep their user data alive until the call returns.
Return zero to continue, `DTL_DEADLINE_EXCEEDED` for a host deadline, or another
nonzero value to cancel. The first observed stop is latched even if a caller
subsequently resets its flag. C++ callback exceptions count as cancellation.

Statuses: `DTL_CANCELLED` (5) and `DTL_DEADLINE_EXCEEDED` (6). Failure returns
no partial result. Waiting calls check controls while acquiring the mutex;
preparation and question boundaries check them too. CPU execution uses the
upstream abort callback and resets it before releasing the inference lock.
Cancellation is cooperative, not a thread kill or a hard wall-clock guarantee.
Model loading, template execution and tokenization may finish before the next safe
check; tokenization cannot be interrupted, and the request limits above bound its
cost.
Router cache mutations are not rolled back by a cancelled request.

C++ `execute` takes a `call_options` with `request_id`, `timeout_ms`, `should_cancel`, `cancel_user_data` and `on_event`. Rust `execute_with` takes `CallOptions` built with `request_id`, `timeout_ms`, `cancel_flag` (a borrowed
`AtomicU32`) and `on_event`. Hosts
whose own callbacks are costly on backend threads (interpreter locks, VM thread
attachment) pass `dtl_cancel_flag` as `should_cancel` with a pointer to an aligned
`uint32_t` they set; the Rust, Python, Java, .NET and Go bindings do this.

Node calls accept `{ signal, timeoutMs }`. Their deadline starts at enqueue and
includes time waiting in the worker queue. Abort/deadline rejects the promise
promptly and signals the same native control through shared memory. Rejection
does not mean resources have already been freed. Cancelled entries still count
against the queue bound until acknowledged, preventing cancellation from
bypassing admission limits. `await close({ cancelPending: true })` cancels
accepted work and waits for cleanup; ordinary `close()` still drains it. Repeated
close calls share one promise. Never race native handle destruction with calls.

## Ownership, admission and errors

Native `threads` is 0 (automatic, capped at 4) or an integer in 1..256. Node's
`maxPending` defaults to 16 and accepts 1..1024; each accepted call or entire
batch occupies one slot. Work is FIFO and excess work is rejected. `close()`
stops admission and drains accepted calls. Worker failure rejects pending work;
callers must open a new instance. Browser admission follows the same bounds,
with platform-specific controls and initialization rules in [BROWSER.md](BROWSER.md).

C inputs are borrowed UTF-8 NUL-terminated strings. Initialize call options with
`dtl_default_call_options()`; a `struct_size` other than this ABI's
`sizeof(dtl_call_options)` or a nonzero `reserved` field is rejected with status 1. Output pointers are reset on entry. Successful result buffers and any
non-null error buffers belong to the caller and must be released by `dtl_free`.
An error message may be absent on allocation failure. Null close/free is allowed;
closing a non-null handle invalidates it and must not race any active call.

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

Allocation failure while parsing, planning, rendering or returning a request
yields status 4 and leaves the engine usable. During model loading the runtime
loader may report it as status 2. The library does not terminate the process on
C++ allocation failure. Inside llama.cpp/ggml C code, failing to allocate model
weights or compute buffers is reported as status 2 or 4, and a failed CPU
thread-pool allocation during inference as status 4. ggml aborts the process
(`SIGABRT`) when one of its small bookkeeping allocations fails, such as a graph
context or scheduler array; that is upstream design. On Linux with the default
memory overcommit such allocations practically never fail, because the kernel's
OOM killer stops a process under memory pressure first. In the browser an abort
ends only that worker's WebAssembly instance (see BROWSER.md). Applications that
must survive allocation failure inside ggml should run the engine in a separate
process.

Every binding exposes native statuses through its error type, with named
constants. JS rejects with an Error carrying `status`; binding-level validation
errors carry status 1, and JS queue, closed and worker failures carry `code`
(`ERR_LAYA_QUEUE_FULL`, `ERR_LAYA_CLOSED`, `ERR_LAYA_WORKER_EXITED`). Error
message wording is diagnostic, not a stable machine-readable contract.

## Runtime and model diagnostics

`dtl_info(NULL, &result, &error)` returns runtime/version/ABI identity, supported features and limits, and required/missing CPU features without loading a model. `features` lists `structured_decisions`, `sequential_batches`, `routing`, `language_routing`, `overflow_policies`, `observers`, `cooperative_cancellation` and `deadlines`; `overflow_policies` lists the accepted policies; `limits` includes `request_bytes`, `object_members`, `questions_per_request`, `requests_per_batch`, `questions_per_batch`, `default_context_tokens`, `minimum_context_tokens`, `scan_windows`, `windows_per_call`, `routes` and `loaded_models`.
Supply an engine to include its model architecture, name, head/token budgets and
calibration temperatures. The allocated JSON follows normal `dtl_free` ownership;
unknown fields must be ignored for forward compatibility. It contains no model
path, request, prediction or telemetry destination. Invalid UTF-8 in model
metadata is replaced (U+FFFD). Metadata is not proof of a
model's hash/provenance; use the immutable manifest for that. On a single-model
engine, `dtl_info` takes the engine's lock, so it waits for a running call.

C++ exposes `ducktape::info()` and `engine.info()`. Rust exposes `runtime_info()`
and `Engine::info()`. Node exposes `Laya.runtimeInfo()` and async `engine.info()`;
browser engines expose async `info()`. Python, Go, Java and .NET expose
`runtime_info()`/`RuntimeInfo()`/`Laya.runtimeInfo()`/`LayaEngine.RuntimeInfo()`
and `info()` on an engine. Routers report common runtime capabilities in every
binding, since models are loaded lazily. Inference and model identities are
shared C ABI results, not independently maintained binding constants.

`numerical_profile` contains an `id` derived from the backend and required CPU
features, plus `cross_profile_equivalence: false`. The ID is a profile label, not
a complete reproducibility fingerprint: also record runtime/patch and model
hashes, artifact/compiler/flags, execution settings, and device/driver identity.
Bindings using the same native binary, model and settings must return identical
results. Each backend must match upstream processing at its declared tolerance.
Different profiles can change probabilities, winning choices and threshold
projections. Consumers should qualify thresholds for the profiles they deploy.
Initialization fallback may select another profile; inspect `info()` afterward.
Inference failures never silently switch profiles.

On the Linux x86-64 build, CPU features selected by the supported GGML options
are checked before calling the backend. Missing required features produce
`DTL_UNSUPPORTED_PLATFORM` (7). This guard covers the declared configuration;
custom compiler ISA flags can invalidate that guarantee. AVX512/VNNI variants
are rejected by this package's CMake configuration until their guard/coverage is
implemented. A portable build disables GGML_AVX, GGML_AVX2, GGML_FMA, GGML_F16C,
GGML_BMI2 and GGML_SSE42. Different CPU profiles can change floating-point outputs
and require their own conformance results. The `cpu-guard` test checks the guard on
emulated CPUs (QEMU user mode); older physical CPUs remain untested.
