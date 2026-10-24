# Shared-core architecture

Ducktape Laya is an embedded inference library. It uses llama.cpp and GGUF with one C++ implementation behind a shared C ABI. C++, Rust, Node/TypeScript, Python, Go, Java, .NET and browser interfaces must preserve that implementation's decision semantics. Observable behaviour is in [API.md](API.md) and [BROWSER.md](BROWSER.md).

## Design principles and scope

- **One shared implementation.** The C++ core owns every behaviour the bindings share. Bindings adapt language types, ownership, errors, async scheduling and platform integration, nothing more.
- **Reuse upstream first.** Prefer llama.cpp's own loading, tokenization, templates and tensor execution over a parallel implementation, and prove equivalence before replacing working code. Keep the upstream pin unchanged by default; a necessary runtime patch is small, recorded with its hash, justified, and has a removal condition ([patches](../patches/README.md)). No silent vendor edits or long-lived fork.
- **Duplicate only with a reason.** Duplicating shared logic needs a specific platform constraint or a measured benefit; record why, what is duplicated, how it stays equivalent (shared conformance tests) and when it can be removed.
- **Narrow public surface.** Upstream types and headers never appear in the public ABI. Supported limits, decision types and model and runtime identity are exposed through `info()`, so consumers detect capabilities without depending on an upstream revision.
- **Models are separate.** Weights are versioned apart from the library and pinned by manifests; no package contains or implicitly downloads a model, and nothing updates the runtime or a model inside an installed application.
- **Demos are disposable.** The demos are optional developer tools; they never drive the architecture, APIs, dependencies or release acceptance, and they stay out of consumer packages. Package tests are automated consumers of the public API.
- **The browser shares the core.** The browser package compiles the same core to WebAssembly instead of redirecting users to another SDK; platform code adapts memory, scheduling and asset loading only. A browser limitation stays visible rather than being relabelled as support.
- **Advertise only what is tested.** A platform, backend or runtime version is supported only when the release checks cover it ([PLATFORMS.md](PLATFORMS.md)); compiling is not enough. Native GPU support is a separate capability, not implied by browser WebGPU.

Out of scope: text or chat generation, an HTTP or inference server, model training, implicit downloads or updates, and untested operating systems. Deliberately omitted features of the original Laya are listed in the [roadmap](ROADMAP.md#not-planned).

## Responsibility boundaries

| Responsibility | Owner |
| --- | --- |
| Weights, vocabulary, systemone template, calibration temperatures and head limits | App-supplied GGUF; approved artifact identity in `models/manifest.json` |
| GGUF loading, tokenization, ModernBERT decision graph and tensor execution | Pinned llama.cpp and ggml |
| Template parsing and rendering | Upstream `common/jinja` and `common/json`, linked privately |
| Prompt inputs, tokenization between markers, overflow handling (truncation, scan windows), marker positions, output columns and stored-temperature bounds | `src/backend_llama.cpp` |
| Calibrated probabilities, confidence fields, tie rule and single-option answers (model-independent arithmetic) | `src/decision.hpp` |
| Serializing structured values as the original Laya does (Python `json.dumps`) | `src/python_json.hpp` |
| A request's state as its prompts carry it, prepared once per request and model | `src/request_state.hpp` |
| Language detection for `"model": "auto"` | `src/language.cpp` |
| Request bounds, open config, schema planning/projection, batch order, routing/cache and passive events | `src/laya.cpp` |
| Cooperative stop state, deadlines and cancellable lock waits | `src/execution.hpp` |
| Runtime diagnostics, native CPU compatibility checks and the automatic thread count (CPU affinity, cgroup CPU quota) | `src/platform.cpp`, `src/threads.hpp` |
| Ownership, type conversion, error translation and platform scheduling | Thin language bindings |
| Source/artifact identity, packaging and dependency upgrade checks | CMake and maintainer scripts; no runtime dependency on Python |

Bindings must not reimplement prompt rendering, calibration, schema projection, routing policy or probability calculations. Duplicate implementation requires a specific platform constraint or measured benefit, shared conformance tests and a removal condition. Demos are optional manual test tools and do not determine the product architecture, dependencies or acceptance criteria.

## Upstream integration

The runtime revision and patch hashes are owned by [release/compatibility.json](../release/compatibility.json). The model adapter is adapted from upstream `tools/server/server-decision.cpp`, especially `server_decision_context::init`, `render`, `fill_task_laya`, `get_temperature` and `format_answer`. Attribution lives in source and [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

At the pinned runtime, that server component depends on server tasks, common chat and MTMD helpers; it is not a standalone Laya library target. The adapter therefore reuses upstream loading, tokenization, Jinja and tensor execution while keeping the small decision-processing adaptation private. Reassess this boundary during upgrades; adopt a reusable upstream component only if it preserves the embedded contract and does not bring an inference server into consumer apps.

Intentional differences from server processing:

- Text-only Laya and explicit request limits.
- Input over the limit is handled by an explicit overflow policy. The default, `reject`, refuses it instead of truncating silently; `truncate_end`, `truncate_start` and `scan` are opt-in, and only they shorten heads the way the server does.
- Temperatures stored in the model are bounded to [0.5, 5], as the original Laya does; the server applies them unbounded. App-supplied temperatures must be positive and finite and are used as given.
- Structured state, instructions and descriptions are serialized as the original Laya writes them (Python `json.dumps`) before rendering; the template's own JSON output rounds floats.
- The text between mask tokens is tokenized on its own, as in training; one tokenization of the whole prompt drops whitespace before a left-stripping mask token (mmBERT).
- Questions run sequentially, one forward pass each (one per window under `scan`).

Keep the model's calibration and output interpretation intact. Runtime fixes use separately pinned, hash-checked patches applied to build-directory copies. The submodule stays clean; see [patch policy](../patches/README.md) and [UPGRADES.md](UPGRADES.md).

## Internal engine and ownership

`backend.hpp` exposes question data, prepare/run operations and input-token counts. Prepared tokens, marker positions, output columns, llama handles and template objects remain private to the backend. Prepared questions belong to the backend that created them and may only execute before that backend closes.

Each engine owns its model/context and a mutex. Calls on one engine serialize; independent engines may execute concurrently. A single-engine batch is entirely planned and tokenized before inference. The router owns canonical paths, a bounded LRU of loaded models keyed by path and config, the `auto` routes and its own mutex: it validates the whole request plan and resolves routes first, then loads, prepares and executes request by request. Errors return no partial output; cache mutations and completed internal computation are not rolled back.

The C boundary returns stable statuses and allocated JSON/error buffers freed by `dtl_free`. C++ RAII and Rust ownership wrap those handles. Rust shared references permit concurrent calls because native execution serializes; destruction must not race a call (the C++ destructor closes at once, and Rust ownership prevents dropping during a call). Observers run outside inference/cache locks. Cancellation callbacks have a different contract: they may run on backend threads and must not reenter the active handle. See [API.md](API.md) for both callback contracts.

## Native and browser delivery

Native builds link a private, static llama.cpp into the shared Ducktape library. Only the public C ABI is exported; upstream types must not appear in public headers. Node-API delegates to that ABI in a dedicated worker thread. Its host wrapper owns bounded FIFO admission, promise settlement and graceful shutdown. Calls in every native binding except Node block the calling thread (Python's `execute_async` and .NET's `ExecuteAsync` move the blocking call to a worker thread); Node and the browser return promises. Native inference has no runtime network access, child process or inference server. Model/context resources belong to handles; backend registration lasts for the process lifetime.

Browser builds compile the same core and runtime to wasm32. Each instance owns a module worker, virtual-filesystem GGUF assets and one runtime. WASM CPU execution is single-threaded; the WebGPU build enables GPU offload and JSPI/WASM exceptions. The loader owns explicit asset URLs, downloads and optional hash checks, backend selection, initialization failure cleanup and queueing. It verifies the actual runtime backend before loading models. `auto` may fall back during initialization; inference failures never silently switch backends or recompute a result.

Browser requests still pass through the C ABI and shared planner. Platform code may adapt memory, scheduling, cancellation transport and device-loss handling, but must preserve request/result semantics. Native ISA profiles, WASM and WebGPU are distinct numerical profiles: probability differences can change choices and threshold projections. Browser delivery is experimental: 0.1.0 and 0.2.0 passed the browser gates on Chromium 152 only. Structural/API parity does not promise interchangeable predictions.

## Conformance and maintenance

Keep regression inputs in `tests/fixtures` and automated public-API consumers in `tests/consumers`, independently of demos. Same-native-binary binding outputs must agree exactly with identical model bytes and settings. Compare each adapter with upstream processing on the same backend at its strict declared tolerance. Measure cross-profile numeric drift and changed decisions separately; never use that measurement to weaken same-backend checks. Changes to ownership, bounds, ordering, errors, calibration or lifecycle require corresponding contract updates and regression coverage.

[BUILDING.md](BUILDING.md) defines builds, packages and their identity records; [RELEASE_CHECKS.md](RELEASE_CHECKS.md) defines repeatable verification. Keep architecture, contracts, support limitations, build and upgrade procedures current with accepted changes, and keep reusable tests and fixtures in source. Run outputs, benchmark samples, audit findings and review narratives belong in commit or PR history and CI artifacts (local outputs under ignored `build*/` directories), not in these documents; do not add dated audit documents.
