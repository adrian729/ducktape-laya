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
