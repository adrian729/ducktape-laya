# Shared core and upstream boundary

Audit of llama.cpp `1e6f04a75e91da4bb66b5248f3fd4bdeff169d41` (b11540).
Source paths below are relative to `third_party/llama.cpp`. This is a source
audit at that pin, not a claim about every later upstream version.

| Responsibility | Owner and implementation |
|---|---|
| Weights, vocabulary, systemone template, decision temperatures/head limits | Model GGUF; identified by `models/manifest.json` |
| GGUF loading, tokenization, ModernBERT decision graph, tensor execution | Upstream `include/llama.h`, `src/models/modern-bert.cpp` and ggml |
| Template parsing/rendering | Upstream `common/jinja` and `common/json` sources linked privately |
| Laya prompt inputs, marker positions, output-column selection, calibration and answer construction | Our isolated `src/backend_llama.cpp`, adapted from `tools/server/server-decision.cpp` |
| Bounds, primitive validation, schema planning/projection, ordering, sequential batching, routing/cache, passive hooks and ABI errors | Shared `src/laya.cpp` |
| Language ownership, Rust panic containment, Node worker scheduling/queue | Thin bindings; no model algorithms |

## Reuse decision

`server-decision.h` exposes `server_decision_context` through `server-common.h`
and `server-task.h`. Prompt preparation writes server tasks and accepts MTMD
contexts/options. `server-common.h` includes common chat, MTMD helpers and
subprocess support. The implementation also uses the staging `llama-ext.h` API
for other decision models. It is not an independently linkable Laya component.

Extracting a standalone upstream component remains a useful future opportunity,
but at this pin would require maintaining a source transformation or fork and
testing other model/server paths. Linking the whole server dependency set would
add code beyond our embedded Laya contract. Keep the existing small adaptation,
reuse upstream runtime/tokenizer/Jinja implementations directly, and reassess
this choice during upgrades. No upstream source patch is introduced.

Adaptation references: `server_decision_context::init`, `render`,
`fill_task_laya`, `get_temperature`, and `format_answer` in
`tools/server/server-decision.cpp`. Required attribution remains in source and
`THIRD_PARTY_NOTICES.md`.

Intentional differences: text-only Laya; reject prompt/head/option overflow
instead of upstream truncation; finite, positive temperatures; explicit request
limits; sequential inference after preparation. CPU-only offload settings are
an implementation constraint to revisit for the browser backend. Model-level
calibration formulas and output interpretation are preserved.

## Internal seam

`backend.hpp` exposes model-independent question data, prepare/run operations,
and input token counts only. Prepared tokens, marker positions, output columns,
llama handles and template objects are private to `backend_llama.cpp`. The
backend is owned by the shared engine, and calls remain serialized by the engine
or router. Prepared questions are used only by their creating backend before it
closes. The shared planner prepares all questions before single-engine batch
inference; routing retains its existing per-model loading behavior.

This is a private C++ implementation boundary, not a new exported API or a claim
that arbitrary backends can be substituted. Public headers and bindings depend
only on the C ABI. Shared error definitions live in `errors.hpp` so status codes
retain their existing behavior across both translation units.

Conformance lives in `tests/fixtures` and `tests/consumers`, with `tests/parity.py`
as the runner. It exercises primitive decisions, schema projection, structured
state, Unicode, literal mask text and repeated inputs within ordered batches.
Native and binding suites additionally cover errors, bounds, hooks, routing and
lifecycle. This small corpus is a starting point, not broad model accuracy data.

## Browser feasibility: initial source audit (historical)

The pinned ModernBERT graph includes normalization, RoPE, attention, matrix
multiplication, row selection, activation functions (including GELU_ERF in the
decision head), addition and concatenation. The WebGPU backend declares cases
for these operation families and Q8_0 matrix/row operations in
`ggml/src/ggml-webgpu/ggml-webgpu.cpp`; actual support depends on tensor types,
shapes, device capabilities and the graph selected at runtime. This inspection
does not prove full GPU execution or parity.

`ggml/src/ggml-webgpu/CMakeLists.txt` has an Emscripten build path using
`emdawnwebgpu`, C++20, and JSPI/WASM exceptions (or Asyncify). Its shader generator
uses Python at build time. Emscripten is not installed on this development
machine (`emcc` absent at review), and no browser model run has been made.

Next experiment: pin and provision the browser toolchain locally, compile the
shared core with virtual-filesystem model assets and CPU WASM first, then enable
WebGPU and inspect actual operation placement/fallback. Test the same fixtures,
memory needs, initialization, threading/isolation, worker lifecycle and device
loss. The current shared-library build, native filesystem routing and forced
CPU offload policy require platform integration work. Browser support remains
unimplemented; this audit is not its acceptance gate.

The experiment above has now been implemented; see [BROWSER.md](BROWSER.md) for
actual WASM/WebGPU runs, shared-core packaging and remaining numeric gates.
