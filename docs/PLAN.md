# Ducktape Laya implementation plan

## Evaluation decisions (2026-10-13)

Baseline commit: `69745a9`. Evaluate in this order: (1) laya-ts versus our
Node interface, (2) ONNX versus the native GGUF backend, (3) competing embedded
libraries, then (4) missing features only where this project retains value.
Working end-to-end delivery, including model export and packaging effort, counts
alongside features, correctness, latency, memory and maintenance. An export
failure is a practical disadvantage even when its cause is outside the runtime.

### 1. laya-ts: retain our binding for now

Inspected and built upstream commit `c73908d96ff73c0062c340a11bf7c54ec7d8bc4d`.
All 886 tests in 35 files, type checks and the external-package smoke test passed.
These suites largely use fake providers. Separately, actual inference with an
officially exported English checkpoint passed three-question requests and a
two-state batch; batch answers equalled the corresponding individual answers.
Across eight states / 24 questions, option winners matched our GGUF, with maximum
probability difference 0.01330. This is agreement, not measured task accuracy.

Upstream is substantially ahead on routing, true batching, hooks, structured
decisions, shortlist/tournament and browser-provider code. It runs inference
locally through ONNX, not an HTTP server. Nevertheless it does not clearly
supersede our embedded Node interface:

- We own inference in a Worker, bound accepted requests and drain them on close.
  Its public provider has no release/close method; Router unload drops references.
  The installed ORT Node 1.30.0 runs synchronous native work inside setImmediate;
  an async predict method alone does not isolate work from the calling JS thread.
  The measured timer-delay maximum was 7.3 ms for ours versus 1626 ms for direct
  laya-ts. An application could supply a worker around upstream, but that is
  additional integration work, not an existing upstream capability.
- Our local GGUF load never downloads. Upstream supports local directories but
  interprets missing paths as Hub identifiers. Its documented default Hub model
  currently has no split ONNX artifacts; export/package steps are necessary.
- Its numThreads option sets ort.env.numThreads. ORT Node uses session option
  intraOpNumThreads; WASM uses env.wasm.numThreads. The inspected setting therefore
  does not configure these runtimes as intended.
- Our budgets reject overflow. Upstream truncates and reports dropped state and
  collapsed options. These are different useful contracts, not interchangeable.
- Confidence formulas differ: ours follows llama.cpp server formulas; upstream
  uses entropy confidence, answer_confidence and an action head. Thresholds cannot
  be copied between them. Our backend lacks the action head and newer calibration
  features. Upstream also clamps temperatures; ours preserves GGUF metadata.
- npm registry lookup returned 404 for laya-ts. A pinned checkout can build and
  pack successfully; neither project yet offers a validated Electron installer.

Decision: no removal at this gate. Upstream wins on feature breadth; our current
worker/lifecycle, strict-input and compact GGUF path have tangible value. Do not
vendor upstream or create a compatibility wrapper just to preserve our API.

### 2. ONNX: retain GGUF for the compact native target

Tested CPU ORT 1.31.0 with two threads on the same eight tokenized requests, plus
the actual Node path on ORT 1.30.0. The official exporter ran with Torch 2.14.1+cpu
and Transformers 5.19.0. Its verification passed at batch sizes 1 and 2 and lengths
16 and 512. No upstream source changes were needed for this export.

| Measured path | Model assets | Peak resident memory | Median 3-question time |
|---|---:|---:|---:|
| Our GGUF Node worker, 2 threads | 449 MB | 534 MiB | 3.05 s (earlier run 1.85 s) |
| Official FP32 ONNX, native probe, 2 threads, questions batched | about 1.69 GB | 1553 MiB | 1.97 s |
| Community weight-only Q8 ONNX, native probe, 2 threads, serial questions | about 528 MB | 594 MiB | 3.42 s |
| laya-ts + official FP32, default runtime thread count | about 1.69 GB | 1691 MiB | 0.83 s |

The laptop had live background work and changing thermals. These are observations,
not stable performance rankings; the two GGUF runs demonstrate the variability.
Native probes exclude tokenization and JSON overhead. Node defaults are not a
thread-controlled comparison. See [raw measurements and artifact hashes](evidence/runtime-comparison.json).

The community Q8 export required I/O name/rank adaptation and failed multi-row
inference with a broadcast error. Serial questions worked. The official FP32
export removed that blocker, but required Python build tooling and produced a
much larger artifact. These integration costs count in the decision.

ONNX is viable and shows a latency advantage with FP32, with broader execution
provider options. It does not justify replacing the present compact C/C++/Rust
engine: higher model/memory cost, extra tokenizer/model-bundle integration, and
no clear win from the smaller tested ONNX artifact. ORT is an inference runtime,
not a replacement for tokenization, request validation, calibration, ownership or
language bindings. A native replacement would need these plus cross-language
parity/lifecycle tests. Browser support in ORT would not automatically make our
native C ABI usable in browsers.

Decision: retain GGUF and the existing Node interface; no backend rewrite or
removal. Revisit ONNX for a latency-first deployment or a validated compact
batched export. This is a decision for this embedded product, not a claim that
GGUF or ONNX is universally superior.

Reproduction helpers (developer-only, dependencies stay outside the library):
`tests/compare-node.mjs`, `tests/adapt-onnx-probe.py`,
`tests/export-probe-inputs.mjs`, `tests/compare-onnx.py`. Upstream checkouts, models
and export dependencies are under ignored `.research/`. Exact revisions and
SHA-256 hashes are in the evidence file. The official export uses upstream
`laya-ts/scripts/export_onnx.py --model-dir CHECKPOINT --out-dir OUTPUT`.

Sources: [laya-ts](https://github.com/NandhaKishorM/laya/tree/c73908d96ff73c0062c340a11bf7c54ec7d8bc4d/laya-ts),
[provider implementation](https://github.com/NandhaKishorM/laya/blob/c73908d96ff73c0062c340a11bf7c54ec7d8bc4d/laya-ts/src/providers.ts),
[ORT C API](https://onnxruntime.ai/docs/get-started/with-c.html),
[Q8 artifact](https://huggingface.co/nvkudva/laya-onnx-q8/tree/cccc9a01e06189cbb1c153a51ea32642102e306d).

### 3. Existing alternatives: the generic embedding layer is already available

The strongest direct alternative is **LibLayaX + RLaya**. Its pinned source has
the same essential architecture as ours: an opaque C handle, JSON requests and
owned responses, exception containment, serialized inference, explicit destroy,
and a safe Send + Sync Rust owner. It adds real batching, diagnostics, model
variants, GPU configuration and published native binaries. It also defaults to
rejecting truncation; this is not an advantage unique to our native core.

The Linux x86-64 AVX2 v1.0.15 release passed all 54 C API checks here with the
real English checkpoint, including errors, batching, concurrency, reload and
preserving caller floating-point settings. The release ZIP matched its published
SHA-256. Setting LD_LIBRARY_PATH to its bundled library directory was necessary
to launch the supplied test executable. Other platforms and GPUs were not tested
here; the release explicitly says its Linux GPU path is not hardware-validated.
RLaya also passed its four tests with LAYA_TEST_MODEL set, including actual
inference/batching/concurrency, plus its compile-only documentation test.
Our two-state / six-question C API probe matched all six GGUF option winners;
its batched results equalled its individual results. Peak resident memory was
1673 MiB versus our measured 534 MiB. The checkpoint bundle is approximately
846 MB versus the 449 MB GGUF. Its native library itself is smaller (5.1 MB
versus our 7.3 MB), so our advantage is the model/overall footprint, not the ABI
library's file size. Full probe results, revisions and the release checksum are
in [alternative-library evidence](evidence/alternatives.json).

| Alternative | Where it is ahead / overlap | What it does not replace in our tested target |
|---|---|---|
| LibLayaX + RLaya | Existing C/C++/Rust embedding; release binaries; CPU/GPU options; true batch; strict inputs; lifecycle | Uses safetensors, expands CPU weights to FP32; no corresponding managed Node binding found in these repos; no stock Q8 GGUF path |
| lkarlslund/laya.cpp | Public C++ agent/runtime and CMake library target, full model inference, batching, GPU backends | Not just a server: C++ embedding already exists; stable C ABI/Rust layer is supplied by LibLayaX/RLaya; no tested compact GGUF path |
| aovestdipaperino/laya-rust | Actual Rust library on Candle, typed questions/results, batched questions, CPU/Metal/CUDA options | Separate Rust engine, not the existing common C ABI plus Node worker; FP32 CPU footprint; source-inspected, not benchmarked here |
| zerodegress/laya-rust | Rust library/CLI, single custom GGUF, CUDA/CPU/MLX backends | Its GGUF is a different architecture/format contract from ggml-org's; CPU widens to FP32 and is documented as a slow reference; source/README review only |
| @receptron/laya | Published npm 0.1.2, local ONNX, typed outputs, batching, real session close, correct session-options surface | No built-in bounded Worker or drain-on-close contract; FP32 model bundle, Node-only provider; source-inspected, not real-model-tested here |
| laya-goish / felipenmoura/laya-node | Useful server/CLI integrations | Their documented integration requires a process/server, unlike the requested embedded deployment |

Source revisions: LibLayaX `9eead4f1d9278b13af7662663092bda6168eb2e2`,
RLaya `263eb958784834d014f56db1c3a8ba5ff67fd736`,
laya.cpp `e632da0428236be56978e53ddc92cdcd565fb493`,
Candle laya-rust `8c50e61b1507638c4c1c7630633f4ce776d127db`,
receptron/laya `6478649e723122ca24bbf5fb69ed1010023c9750`.

**Decision (updated 2026-10-14):** continue this project and retain every current
language interface, including TS. Compact Q8 GGUF deployment, a shared backend,
and a managed Node worker form a useful combination. Add the missing common
features in that backend rather than maintaining a separate inference/planning
implementation per language. Other repositories are alternatives and design
references, not a reason by themselves to remove working code.

LibLayaX/RLaya expose broader platform/GPU options, and laya-ts has more existing
high-level workflows. Those are concrete feature differences, not proof of
production maturity or overall superiority. Our actual validation is narrower
than their advertised platform lists. The comparisons above distinguish measured
behavior from source review; star counts do not settle engineering quality.
Keep the adapter against pinned, unmodified llama.cpp and evaluate optimizations
with parity and footprint measurements.

Sources: [LibLayaX C API](https://github.com/DaragonTech/LibLayaX/blob/9eead4f1d9278b13af7662663092bda6168eb2e2/laya.cpp/capi/laya_c.h),
[releases](https://github.com/DaragonTech/LibLayaX/releases/tag/v1.0.15),
[RLaya](https://github.com/DaragonTech/RLaya),
[laya.cpp C++ interface](https://github.com/lkarlslund/laya.cpp/blob/e632da0428236be56978e53ddc92cdcd565fb493/include/laya/runtime.hpp),
[Candle library](https://github.com/aovestdipaperino/laya-rust),
[custom GGUF library](https://github.com/zerodegress/laya-rust),
[receptron Node library](https://github.com/receptron/laya).

### 4. Missing features: extend only the compact shared engine's useful scope

Implementation update (2026-10-14): the first four rows now have a shared native
implementation and C/C++/Rust/Node/TS interfaces. The original ABI/options layout
and confidence formulas remain intact. [API.md](API.md) is the precise contract.
The table below preserves the reviewed design and broader acceptance targets.

- Batching: additive `dtl_execute`, arrays bounded to 32 requests/128 questions,
  sequential forward passes, exact individual-call parity, no partial results.
- Structured decisions: one native flat-schema planner and projection; raw
  distributions retained; optional fields also populated; unsupported constraints
  rejected. Bounded integers use ordinal argmax, not expected-score rounding.
- Hooks: per-call start/end/error observer outside locks, request IDs, native
  exception containment, Rust panic containment, Node worker event forwarding.
- Routing: explicit IDs/local paths, lazy Laya metadata validation, canonical-path
  load deduplication, LRU eviction before loading at capacity. `max_loaded` is a
  count limit (1–4), not a byte-level memory quota. Packaging checksum verification
  stays in the existing manifest/script. Only the pinned English artifact is
  validated; automatic language routing requires separately tested checkpoints.

The isolated multi-sequence encoder probe was run with six pinned tokenized
questions, independent sequence IDs and positions, a 512-token total cap, and
reversed input order. All six winners matched, but maximum probability drift was
0.007069 versus individual encoding and 0.003427 on reversal. One exploratory
run took 6.23 s sequentially versus 7.51 s simultaneously (not a stable benchmark).
We therefore retain sequential forward passes and exact output parity in the
public batch API. [Probe evidence](evidence/encoder-batch.json) and the optional
`laya-encoder-probe` target preserve this experiment for future runtime updates.

Decision: retain and develop our own TS interface over the shared GGUF backend.
The reference SDKs inform the design; these additions do not copy their code.
Browser execution remains a separate research target, not a feature of this
native addon. A browser runtime will require real WebGPU/WASM validation before
we can decide whether a shared GGUF build adds value there.

| Feature | How to add it / what is shared | Acceptance before shipping |
|---|---|---|
| Batching (first) | Add a native batch-request entry point and reuse it in C++, Rust and Node. Initially a bounded sequential batch provides an honest contract; separately prototype a real multi-sequence llama_encode pass with independent sequence IDs/positions, mapped output rows and a total-token cap. Do not call a loop a throughput optimization. | Batched vs individual probabilities on a varied corpus; padding/order isolation; overflow, partial-error policy, peak memory and measured throughput. Verify ModernBERT encoder support and context allocation before committing to true batching. |
| Structured decisions | Shared native planner for flat enum, boolean and bounded ordinal-integer properties; project decisions back to typed values with probability metadata. Use deterministic argmax/explicit boolean threshold and preserve ordinal semantics. Reject unsupported strings, arrays, nested schemas and references. | Same schema/errors/results in C, C++, Rust, Node; required/optional fields, limits and numeric mapping covered. Keep primitive distributions available. |
| Hooks | Start with passive start/end/error events and request IDs. Expose a C callback and thin language adapters, with documented callback thread, ownership and reentrancy rules. Host applications own telemetry sinks. | No callback while holding an inference lock that callbacks can re-enter; no exception/unwind across ABI; predictable order; close/error paths; bounded overhead. Avoid rebuilding upstream's full async hook-rewrite framework. |
| Routing | Start with explicit application-supplied model IDs and a small bounded model registry. Add automatic language routing only after additional GGUF checkpoints and their tokenizers/templates/calibration pass validation. English-only weights cannot provide multilingual coverage through routing code. | Validated model manifest per route; predictable unknown-language/fallback policy; load deduplication; memory-aware eviction and deterministic cleanup; no implicit network access. |
| Browser / WebGPU / WASM | Use laya-ts directly for browser applications; do not add a wrapper to this repo solely for a uniform API. Its browser path exists, but real browser/device validation is still required. A separate GGUF WASM experiment is worthwhile only if reusing the compact artifact delivers a measured benefit. | Real WebGPU and forced-WASM runs, offline assets, cross-origin isolation/thread settings, worker loading, model memory and fallback accuracy. Current Agent.load checks for window, so loading inside a Web Worker needs explicit provider integration or an upstream environment-detection fix. Q8 MatMulNBits cannot be assumed to work with WebGPU. |

Next, improve distribution and evidence: portable CPU build
selection, clean Electron packaging, broader decision fixtures and lifecycle
stress checks. These strengthen the part of this project that remains useful.
Do not reimplement language detection, shortlist/tournament, or a separate TS
decision engine while existing libraries already cover them.

## Reviewed architecture

One C++ inference implementation, a versioned C ABI, a C++ ownership wrapper,
a safe Rust crate over that ABI, and a Node-API addon with TypeScript declarations.
Electron consumes the Node addon behind application-owned IPC. No HTTP or child
inference process is part of the library. Model files are application resources.

Review findings that change the original plan:

1. libllama does not expose a complete typed-decision API. The template, marker,
   temperature, and result code lives in tools/server/server-decision.cpp. Adapt
   only Laya's path into a small adapter against a pinned upstream commit. Avoid
   linking the HTTP server and unrelated model implementations.
2. Start with the exact existing English Q8_0 GGUF. Its backbone context metadata
   is not a promise of useful long-context decisions. Explicitly enforce a
   conservative 512-token combined request budget in the first adapter and reject
   overflow rather than silently truncating.
3. Preserve question and option insertion order. JSON libraries that sort keys
   can change model answers. Return ordered results across every binding.
4. Define ownership, errors and concurrency before shipping bindings: opaque
   handles, explicit buffer release, fixed-width/versioned options, per-call error
   output, and exceptions caught inside the ABI. A handle serializes inference;
   destruction must not race with active calls. Wrappers enforce that lifecycle.
5. CPU is the first tested backend. GPU acceleration, new models and context
   extensions each require their own validation and are later milestones.
6. Node-API provides a stable addon ABI, not universal OS/CPU binary portability.
   Publish separate builds and test Electron packaging outside ASAR archives.
7. Parity against the pinned native server checks implementation correctness, not
   task accuracy or probability calibration on an application's data.

## Milestones and acceptance

### M1: native proof and C ABI

- Pin llama.cpp as a git submodule; record model revision, checksum, provenance.
- Load a local model, evaluate choice/noul/score directly, unload safely.
- No network calls, listening ports, executable launches or model downloads.
- CMake build, plain-C example, RAII C++ wrapper.
- Validate bad inputs and missing/corrupt/unsupported models.
- Check all three question types against the same model/runtime server.

### M2: language interfaces

- Rust sys bindings and safe Engine with Result errors and deterministic cleanup.
- Node-API addon; asynchronous load/decide/close; bounded outstanding work.
- TypeScript request/result types; preserve order and consistent errors.
- Demonstrate identical fixture results through C, C++, Rust and Node.

### M3: distribution and Electron

- Linux x86-64 CPU first; Windows x86-64 and macOS ARM64 build/test matrix next.
- Electron example with context isolation and a narrow decision IPC interface.
- CPU portable builds (avoid compiling only for the build machine's ISA).
- Model packaged separately from library; checksum and upstream notices included.
- Clean-machine installer test without Python, Ollama or llama.cpp installed.
- Native dependencies, runtime search paths, signing and resource paths verified.

### M4: robustness and performance

- Sanitizer checks, repeated open/decide/close, concurrent requests and queue limits.
- Token overflow, Unicode, malformed JSON, missing models and invalid schemas.
- Startup, resident memory and latency measurements on representative hardware.
- GPU variants and cancellation only after backend-specific correctness checks.

## Non-goals for the first release

Chat generation, HTTP serving, model training, implicit downloads, automatic model
updates, mobile/web-browser binaries and a universal runtime for arbitrary models.

## Initial implementation status (2026-10-13)

The CPU core, versioned C ABI, C++ wrapper, Rust crates and asynchronous Node
interface are implemented. Real inference covers all three question types.
The four interfaces and a source-built upstream server agree exactly on the
initial fixture. Error handling, concurrent native calls, Node queue bounds and
draining on close are tested. Linux staging has been smoke-tested.

This is the first implementation of M1/M2, not a completed distribution release.
Broader compatibility, stress/sanitizer checks, other operating systems, prebuilt
packages and Electron installer testing remain. The detailed evidence and limits
are recorded in [VALIDATION.md](VALIDATION.md).

## Sources

- https://github.com/ggml-org/llama.cpp/pull/29818
- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/server-decision.cpp
- https://huggingface.co/convaiinnovations/laya
- https://huggingface.co/ggml-org/Laya-GGUF
- https://nodejs.org/api/n-api.html
- https://www.electronjs.org/docs/latest/tutorial/using-native-node-modules
