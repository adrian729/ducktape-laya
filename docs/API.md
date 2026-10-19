# Shared decision API

All planning, validation, projection, inference and model caching run in C++.
C++, Rust, Node/TypeScript, Python, Go, Java, .NET and browser bindings adapt
the same C ABI. Native inference performs no downloads and uses no server.
Browser asset fetching is explicitly controlled by the host; see
[BROWSER.md](BROWSER.md).

The original ABI v1 functions and `dtl_options` layout remain available.
`dtl_execute` adds the features below; `dtl_router_open`, `dtl_router_execute`
and `dtl_router_close` add a local registry. The options still select CPU threads.
All results/errors use `dtl_free`. No partial result is returned on failure.

The existing single-model `decide()` methods retain `dtl_decide` semantics:
primitive requests only, with unrelated top-level metadata ignored. Bindings
with hooks or controls use `dtl_decide_with_options`, preserving that validation
behavior. `dtl_decide_observed` remains available to C callers. Use `execute` for
strict validation, structured decisions or batches. Router methods, including
`decide` on a router, always use the strict API: requests need `model` and
unrelated metadata is rejected.

| Interface | Single, structured or batch request | Registry |
|---|---|---|
| C | `dtl_execute(engine, json, observer_or_NULL, &result, &error)` | `dtl_router_open(config, options, &router, &error)`; `dtl_router_execute` |
| C++ | `engine.execute(json, observer_or_nullptr)` | `ducktape::router(config, options).execute(json)` |
| Rust | `engine.execute(json)`; `execute_observed(json, id, callback)` | `Engine::open_router(config_json, threads)` |
| Node/TS | `execute`, `decide`, `decideStructured`, `decideBatch` | `Laya.openRouter(config, options)` |
| Python | `engine.execute(request, on_event=, timeout=, cancel=)`; `execute_async` | `Engine.open_router(config, threads)` |
| Go | `engine.Execute(json)`; `ExecuteContext(ctx, json, &CallOptions{})` | `laya.OpenRouter(configJSON, threads)` |
| Java | `laya.execute(json)`; `execute(json, CallOptions)` | `Laya.openRouter(configJson, threads)` |
| .NET | `engine.Execute(json)`; `Execute(json, options, token)`; `ExecuteAsync` | `LayaEngine.OpenRouter(configJson, threads)` |
| Browser | Same execution methods as Node/TS | `Laya.openRouter(modelAssets, options)`; explicit URLs and browser asset limits |

The bindings differ only where their language has a convention:

| Binding | Engine type | Threads | Results | Deadline | Cancellation | Events run on | Async | Closed engine |
|---|---|---|---|---|---|---|---|---|
| C++ | `ducktape::laya`, `ducktape::router` | `dtl_options` | JSON string | `timeout_ms` | `should_cancel` callback | calling thread | — | destructor |
| Rust | `Engine` | required (0 = auto) | JSON string | `timeout_ms` | `&AtomicBool` | calling thread | — | ownership |
| Node/TS | `Laya` | default 0 | parsed | `timeoutMs` | `AbortSignal` | host thread | promises | `code: 'ERR_LAYA_CLOSED'` |
| Python | `Engine` | default 0 | parsed | `timeout` (seconds) | `CancelToken` | thread running the call | `execute_async` | `EngineClosedError` |
| Go | `*Engine` | required (0 = auto) | JSON string | context deadline | context | calling goroutine | — | `ErrClosed` |
| Java | `Laya` | default 0 | JSON string | `Duration` | `CancellationToken` | calling thread | — | `LayaClosedException` |
| .NET | `LayaEngine` | default 0 | JSON string | `TimeSpan` | `CancellationToken` | calling thread (pool thread for `*Async`) | `ExecuteAsync` | `ObjectDisposedException` |

Every binding reports native failures with the C status (`status`, `Status`
constants), maps cancellation to 5 and deadlines to 6 (.NET raises the standard
`OperationCanceledException` when its token cancels), waits for running calls on
close, and allows closing from inside an event callback (the handle is released
when the running call finishes; Go uses `CloseDeferred()` there). Arguments a
binding checks itself (thread counts, timeout ranges) use the language's standard
argument errors.
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
call: 1 MiB UTF-8, 128 questions total, 32 questions per request, 512 tokens per
question, depth 64. Existing model instruction/option limits still apply. In
Node, `decideBatch` checks for an array; every binding's `execute` accepts one:

```js
const results = await engine.decideBatch([requestA, requestB]);
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
await engine.decideStructured(request, {
  onEvent(event) { console.log(event.event, event.request_id); }
});
```

Native callbacks run synchronously on the caller's thread, outside the engine
and router locks. A `dtl_observer` and its user data are borrowed until the call
returns; each event string is borrowed only during the callback. Reentrant calls
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
  const result = await router.decide({ ...request, model: 'english' });
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

The additive C APIs `dtl_execute_with_options`, `dtl_decide_with_options` and
`dtl_router_execute_with_options` take a borrowed `dtl_call_options`. Initialize
it with `dtl_default_call_options()`. Existing entry points retain their behavior.
`timeout_ms` is zero (disabled) or 1..86400000. The native deadline begins during
execution setup after the start observer, and includes validation, waiting for
the handle lock and inference. Cancellation callbacks must be nonblocking and
thread-safe, may run on backend threads, and must not reenter the active handle.
Keep their user data alive until the call returns.
Return zero to continue, `DTL_DEADLINE_EXCEEDED` for a host deadline, or another
nonzero value to cancel. The first observed stop is latched even if a caller
subsequently resets its flag. C++ callback exceptions count as cancellation.

New statuses: `DTL_CANCELLED` (5) and `DTL_DEADLINE_EXCEEDED` (6). Failure returns
no partial result. Waiting calls check controls while acquiring the mutex;
preparation and question boundaries check them too. CPU execution uses the
upstream abort callback and resets it before releasing the inference lock.
Cancellation is cooperative, not a thread kill or a hard wall-clock guarantee.
Model loading and template execution may finish before the next safe check.
Router cache mutations are not rolled back by a cancelled request.

C++ `execute` accepts an optional third `dtl_call_options*`; `decide` accepts an
optional second one. Rust `execute_with_options`/`decide_with_options` accept
`CallOptions`, including a borrowed `AtomicBool` flag and `timeout_ms`, and the
`*_observed_with_options` variants combine them with an event callback. Hosts
whose own callbacks are costly on backend threads (interpreter locks, VM thread
attachment) pass `dtl_cancel_flag` as `should_cancel` with a pointer to an aligned
`uint32_t` they set; the Python, Java, .NET and Go bindings do this.

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
