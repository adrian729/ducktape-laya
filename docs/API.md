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
| Rust | `Engine::open(path, &Config)` | `engine.execute(json)`; `execute_with(json, CallOptions)` | `Engine::open_router(router_json, &Config)` |
| Node/TS | `Laya.open(path, options)` | `execute(request, options)` | `Laya.openRouter(config, options)` |
| Python | `Engine.open(path, threads=, context_tokens=, temperatures=)` | `execute(request, on_event=, request_id=, timeout=, cancel=)`; `execute_async` | `Engine.open_router(config, ...)` |
| Go | `laya.Open(path, *Config)` | `Execute(json)`; `ExecuteContext(ctx, json, *CallOptions)` | `laya.OpenRouter(routerJSON, *Config)` |
| Java | `Laya.open(path, Config)` | `execute(json)`; `execute(json, CallOptions)` | `Laya.openRouter(routerJson, Config)` |
| .NET | `LayaEngine.Open(path, LayaConfig)` | `Execute(json, options, token)`; `ExecuteAsync` | `LayaEngine.OpenRouter(routerJson, LayaConfig)` |
| Browser | `Laya.open(assets, options)` | Same as Node/TS | `Laya.openRouter(modelAssets, options)`; explicit URLs and browser asset limits |

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

Every binding reports native failures with the C status (`status`, `Status`
constants), maps cancellation to 5 and deadlines to 6 (.NET raises the standard
`OperationCanceledException` when its token cancels), waits for running calls on
close, and allows closing from inside an event callback (the handle is released
when the running call finishes; Go uses `CloseDeferred()` there). Arguments a
binding checks itself (thread counts, timeout ranges) use the language's standard
argument errors.

## Engine configuration

Opening takes an optional JSON config; each binding exposes the same keys as
native options. Unknown keys are rejected.

| Key | Meaning |
|---|---|
| `threads` | 0 (automatic, at most 4) or 1–256 CPU threads |
| `context_tokens` | The longest prompt the engine reserves memory for: 32 up to the model's trained length. Default 512. Compute follows each prompt's real length; only memory grows with this value. The pinned English model's file allows 8,192, but the original Laya uses it with 512 tokens, so longer prompts are untested for accuracy |
| `overflow` | Default policy for prompts over their limit; see [Long input](#long-input) |
| `temperatures` | Calibration overrides, keyed like the model's metadata: a type (`choice`, `score`, `noul`) or a type and option-count bucket (`choice.2`, `choice.3_5`, `choice.6_10`, `choice.11`, `score.3_5`, `noul.2`, ...). Values must be finite and greater than 0 |

A temperature is looked up most specific first: the bucket key, then the type
key, then 1. An override replaces only the keys it names, so to change a
two-option `choice` when the model defines `choice.2`, override `choice.2`; an
override of plain `choice` applies only to buckets the model leaves undefined.
Fit temperatures on your own data with the original Laya's calibration tools and
pass them here.

A router's config applies to every route; a route given as
`{"path": "...", "config": {...}}` overrides it key by key. Routes with the same
file and the same effective config share one loaded model.

Each request may add `max_tokens` (a prompt limit up to the engine's
`context_tokens`) and `temperatures` (overrides for that request only, merged over
the engine's). A prompt over its limit is rejected with status 1. `info()` on a
single-model engine reports `context_tokens` and `trained_context_tokens`.

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
(the model was trained that way); `reject` refuses such questions instead.
`usage` then always reports `state_tokens`, `state_tokens_dropped` (the most
any question dropped) and `truncated_questions` (questions whose state or head
was cut).

With `scan`, a `noul` answer is the maximum over windows ("does any part say
this?"), or the minimum with `"aggregate": "all"` on the question ("does the whole
text say this?"). `choice` and `score` answers come from the window with the
highest `answer_confidence`. A scanned answer carries `window`: the deciding
window's `index`, the window `count`, and its `token_start`/`token_end` in the
tokenized state. Its probabilities are that window's, not a calibrated
statement about the whole document. A state that fits is read once, exactly as
without `scan`. `max_windows` (1–1024, default 32) caps the windows per
question; a state needing more fails with status 1. `input_tokens` counts every
window.

## Primitive requests and results

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
| `score` | Array of 2–10 ordered level descriptions | `score`: expected zero-based level index; `legend` and `probabilities` |

Every answer also has `answer_confidence` and `confidence`:

- `answer_confidence` is the probability of the reported answer: the winning
  option's or level's probability, and for `noul` the larger of `noul` and
  `1 − noul`. It is the calibrated quantity: once the temperatures are fitted on
  held-out data, answers reported at `answer_confidence` c are right about c of
  the time. Gate or abstain on this value.
- `confidence` is how concentrated the whole distribution is, `1 − H(p)/log k`
  for k options or levels (k = 2 for `noul`): 0 for a uniform distribution, 1 for
  certainty. It is not calibrated.

A `choice` with a single option is answered without running the model: that
option, with probability, `confidence` and `answer_confidence` 1, using no input
tokens.

The adapter applies the GGUF's type/option-count temperature to the selected
output scores, then computes a normalized softmax. It falls back to the type's
temperature, then 1 when no corresponding calibration entry exists. Both
confidence fields are model-derived statistics, not application-specific
decision thresholds. The definitions follow the original Laya, except that its
`noul` `confidence` is max(p), which here is `answer_confidence`.

Results contain `answers` keyed by question ID and `usage` with `input_tokens`
and `output_tokens: 0`. No chat text is generated. Input insertion order is
preserved by the core and can affect inference; ties choose the first option.
JavaScript object serialization reorders integer-like keys: pass a raw JSON
string when literal ordering matters. Node and browser results are parsed JavaScript
objects, so their members follow ECMAScript property order (integer-like keys
ascending, then insertion order); names and values match the core result exactly. Literal mask text in values is sanitized
before rendering; prompt/head/option overflow is rejected rather than truncated.

The strict `execute` API accepts only `state`, `questions`, `schema`,
`boolean_threshold` and (on routers) `model`, subject to their documented
combinations. Legacy single-model `decide` ignores unrelated top-level metadata.
Neither API supports top-level media `images` or `files`.

### Example requests and option order

`examples/presets/` has ready requests for common uses: support triage, email
triage, prompt-injection screening and moderation. They are ordinary requests:
copy one and adapt its questions.

Option order can influence a `choice`. To reduce that bias, ask the same
question several times in one request with the options in rotated order (for
example `intent_r0`, `intent_r1`, `intent_r2`), then average each label's
probability across the copies. Each copy costs one more model pass.

## Batches

Pass a JSON array of 1–32 requests; results are an array in the same order.
Primitive and structured requests can be mixed. Limits apply to the entire
call: 1 MiB UTF-8, 128 questions total, 32 questions per request, depth 64; each
prompt must fit its token limit (`max_tokens`, at most the engine's
`context_tokens`). Existing model instruction/option limits still apply. Every
binding's `execute` accepts an array:

```js
const results = await engine.execute([requestA, requestB]);
```

On a single engine, every request is planned and tokenized before any inference.
A router validates all schemas and route IDs first, then loads, tokenizes and
executes in input order. A later model/tokenization failure may follow earlier
inference, but returns no partial results. Cache changes are not rolled back.

Execution currently uses one forward pass per question. This is a bounded
convenience/consistency API, not a simultaneous-inference optimization. Accepted
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
are strings only in this version. Confidence formulas remain those of the
original primitive API; they have not been replaced with upstream TS formulas.

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

Node forwards native events from the inference worker to the host thread. Its
request IDs are generated per instance. Event handlers run before the associated
result settles; their promises are not awaited. Throws/rejections are contained.
Reentrant Node calls enter the ordinary bounded FIFO queue. Binding-level errors
such as queue overflow, closed handles, NUL bytes or unserializable input occur
before native execution and do not emit native events.

## Explicit local routing

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
unknown IDs fail with invalid argument. Single-model strict execution rejects
routing fields; legacy `decide` still ignores unrelated metadata. Routed results
include the chosen `model` ID.

Loads are lazy and serialized. Before inference, each model must pass the core's
Laya GGUF metadata, three-column output, template, marker, head-budget and
calibration checks. This verifies format compatibility, **not model quality or
artifact authenticity**. Applications should verify bundled weights against
`models/manifest.json` using `scripts/verify-model.py` and keep resources immutable.
Only the pinned English Q8_0 model has been validated here. Other models require
their own tokenizer, calibration and decision tests before use. No automatic
language detection, multilingual claim, fallback model or network download.

`max_loaded` defaults to 1, allows 1–4, and bounds the number of loaded canonical
paths. Least-recently-used models are unloaded **before** another is loaded at
capacity. This avoids temporarily holding one extra model. A failed load can
therefore evict a previously cached model. Failed loads are not cached.
This is a model-count bound, not a byte-accurate RAM quota; size and runtime
buffers vary with the artifact. Closing releases every cached model/context.
Native close must never race an active call; every binding's close waits for
running calls (Rust through ownership, JS by draining its queue).

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
Model loading and template execution may finish before the next safe check.
Router cache mutations are not rolled back by a cancelled request.

C++ `execute` takes a `call_options` with `timeout_ms`, `should_cancel` and
`on_event`. Rust `execute_with` takes `CallOptions` with `timeout_ms`, a borrowed
`AtomicU32` cancel flag and an event callback. Hosts
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

C inputs are borrowed UTF-8 NUL-terminated strings. Initialize options with the
provided defaults; incompatible layouts/ABI versions and reserved fields are
rejected. Output pointers are reset on entry. Successful result buffers and any
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

`dtl_info(NULL, &result, &error)` returns runtime/version/ABI identity, supported
features and limits, and required/missing CPU features without loading a model.
Supply an engine to include its model architecture, name, head/token budgets and
calibration temperatures. The allocated JSON follows normal `dtl_free` ownership;
unknown fields must be ignored for forward compatibility. It contains no model
path, request, prediction or telemetry destination. Metadata is not proof of a
model's hash/provenance; use the immutable manifest for that.

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
