# Shared-core architecture

Ducktape Laya is an embedded inference library. It uses llama.cpp and GGUF with
one C++ implementation behind a shared C ABI. C++, Rust, Node/TypeScript and
browser interfaces must preserve that implementation's decision semantics.
The product requirements are in [PLAN.md](PLAN.md); observable behavior is in
[API.md](API.md) and [BROWSER.md](BROWSER.md).

## Responsibility boundaries

| Responsibility | Owner |
| --- | --- |
| Weights, vocabulary, systemone template, calibration temperatures and head limits | App-supplied GGUF; approved artifact identity in `models/manifest.json` |
| GGUF loading, tokenization, ModernBERT decision graph and tensor execution | Pinned llama.cpp and ggml |
| Template parsing and rendering | Upstream `common/jinja` and `common/json`, linked privately |
| Prompt inputs, marker positions, output columns, calibration and primitive answers | `src/backend_llama.cpp` |
| Request bounds, schema planning/projection, batch order, routing/cache and passive events | `src/laya.cpp` |
| Cooperative stop state, deadlines and cancellable lock waits | `src/execution.hpp` |
| Runtime diagnostics and native CPU compatibility checks | `src/platform.cpp` |
| Ownership, type conversion, error translation and platform scheduling | Thin language bindings |
| Source/artifact identity, packaging and dependency upgrade checks | CMake and maintainer scripts; no runtime dependency on Python |

Bindings must not reimplement prompt rendering, calibration, schema projection,
routing policy or probability calculations. Duplicate implementation requires a
specific platform constraint or measured benefit, shared conformance tests and a
removal condition. Demos are optional manual test tools and do not determine the
product architecture, dependencies or acceptance criteria.

## Upstream integration

The runtime revision and patch hashes are owned by
[release/compatibility.json](../release/compatibility.json). The model adapter is
adapted from upstream `tools/server/server-decision.cpp`, especially
`server_decision_context::init`, `render`, `fill_task_laya`, `get_temperature`
and `format_answer`. Attribution lives in source and
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

At the pinned runtime, that server component depends on server tasks, common
chat and MTMD helpers; it is not a standalone Laya library target. The adapter
therefore reuses upstream loading, tokenization, Jinja and tensor execution while
keeping the small decision-processing adaptation private. Reassess this boundary
during upgrades; adopt a reusable upstream component only if it preserves the
embedded contract and does not bring an inference server into consumer apps.

Intentional differences from server processing: text-only Laya, explicit request
limits, positive finite temperatures, rejection of prompt/head/option overflow
instead of silent truncation, and sequential question execution. Keep the model's
calibration and output interpretation intact. Runtime fixes use separately pinned,
hash-checked patches applied to build-directory copies. The submodule stays clean;
see [patch policy](../patches/README.md) and [UPGRADES.md](UPGRADES.md).

## Internal engine and ownership

`backend.hpp` exposes question data, prepare/run operations and input-token counts.
Prepared tokens, marker positions, output columns, llama handles and template
objects remain private to the backend. Prepared questions belong to the backend
that created them and may only execute before that backend closes.

Each engine owns its model/context and a mutex. Calls on one engine serialize;
independent engines may execute concurrently. A single-engine batch is entirely
planned and tokenized before inference. The router owns canonical paths, a bounded
LRU of engines and its own mutex: it validates the whole request plan first, then
loads, prepares and executes routes in order. Errors return no partial output;
cache mutations and completed internal computation are not rolled back.

The C boundary returns stable statuses and allocated JSON/error buffers freed by
`dtl_free`. C++ RAII and Rust ownership wrap those handles. Rust shared references
permit concurrent calls because native execution serializes; destruction must not
race a call. Observers run outside inference/cache locks. Cancellation callbacks
have a different contract: they may run on backend threads and must not reenter
the active handle. See [API.md](API.md) for both callback contracts.

## Native and browser delivery

Native builds link a private, static llama.cpp into the shared Ducktape library.
Only the public C ABI is exported; upstream types must not appear in public
headers. Node-API delegates to that ABI in a dedicated worker thread. Its host
wrapper owns bounded FIFO admission, promise settlement and graceful shutdown.
C++ and Rust calls are blocking. Native inference has no runtime network access,
child process or inference server. Model/context resources belong to handles;
backend registration lasts for the process lifetime.

Browser builds compile the same core and runtime to wasm32. Each instance owns a
module worker, virtual-filesystem GGUF assets and one runtime. WASM CPU execution
is single-threaded; the WebGPU build enables GPU offload and JSPI/WASM exceptions.
The loader owns explicit asset URLs, downloads and optional hash checks, backend
selection, initialization failure cleanup and queueing. It verifies the actual
runtime backend before loading models. `auto` may fall back during initialization;
inference failures never silently switch backends or recompute a result.

Browser requests still pass through the C ABI and shared planner. Platform code
may adapt memory, scheduling, cancellation transport and device-loss handling,
but must preserve request/result semantics. Browser/native numeric equivalence
remains an unmet release gate; browser delivery is experimental. Do not equate
structural/API parity with interchangeable probabilities across execution backends.

## Conformance and maintenance

Keep regression inputs in `tests/fixtures` and automated public-API consumers in
`tests/consumers`, independently of demos. Same-native-binary binding outputs must
agree exactly. Compare each browser adapter with upstream processing on the same
backend separately from native/browser comparisons at the declared tolerance.
Changes to ownership, bounds, ordering, errors, calibration or lifecycle require
corresponding contract updates and regression coverage.

[PACKAGING.md](PACKAGING.md) defines artifact integrity and installation;
[RELEASE_CHECKS.md](RELEASE_CHECKS.md) defines repeatable verification. Run outputs,
audit findings and review narratives belong in commit/PR history or CI artifacts,
not in permanent architecture documents. Keep these documents aligned with the
accepted implementation and mark future release requirements explicitly.
