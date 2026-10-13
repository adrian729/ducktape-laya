# Shared decision API

All planning, validation, projection, inference and model caching run in C++.
C++, Rust and Node/TypeScript adapt the same C ABI. No model downloads, HTTP
server or separate TS inference implementation is involved.

The original ABI v1 functions and `dtl_options` layout remain available.
`dtl_execute` adds the features below; `dtl_router_open`, `dtl_router_execute`
and `dtl_router_close` add a local registry. The options still select CPU threads.
All results/errors use `dtl_free`. No partial result is returned on failure.

| Interface | Single, structured or batch request | Registry |
|---|---|---|
| C | `dtl_execute(engine, json, observer_or_NULL, &result, &error)` | `dtl_router_open(config, options, &router, &error)`; `dtl_router_execute` |
| C++ | `engine.execute(json, observer_or_nullptr)` | `ducktape::router(config, options).execute(json)` |
| Rust | `engine.execute(json)`; `execute_observed(json, id, callback)` | `Engine::open_router(config_json, threads)` |
| Node/TS | `execute`, `decide`, `decideStructured`, `decideBatch` | `Laya.openRouter(config, options)` |

## Batches

Pass a JSON array of 1–32 requests; results are an array in the same order.
Primitive and structured requests can be mixed. Limits apply to the entire
call: 1 MiB UTF-8, 128 questions total, 32 questions per request, 512 tokens per
question, depth 64. Existing model instruction/option limits still apply.

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
Do not also pass `questions`. [The example](../examples/structured.json) works
through all four command-line examples. Results include the original `answers`
and `usage`, plus a `values` object.

The supported JSON Schema subset is deliberately small:

- Root `type: "object"` with 1–32 named `properties`.
- String properties require a unique string `enum` of 2–255 values; ordinary
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
  is accepted. No additional values are generated.

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
are allowed, but callbacks must not destroy the active handle or create circular
waits. Keep callbacks short. Observer errors cannot change decision results:
C++ exceptions are contained; C callbacks must never unwind; Rust catches
unwinding panics (not process-aborting panics).

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
unknown IDs fail with invalid argument. Single-model engines reject routing
fields. Routed results include the chosen `model` ID.

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
Native close must never race an active call; Rust ownership and Node draining
handle their respective lifetimes.
