# Prototype validation

## Shared features (2026-10-14)

Implemented and tested on the same Linux CPU machine and pinned Q8_0 artifact.
The original C ABI remains intact; four additive entry points provide execution
and local routing. Algorithms remain native; bindings provide ownership, worker
transport and callback adaptation. See [API.md](API.md) for exact contracts.

Passing checks for this change:

- Release shared library, C/C++ examples, Node addon and native feature tests.
- CTest **4/4**: existing ABI/concurrent inference checks plus batch bounds,
  exact parity across varied/reversed/repeated requests, mixed structured batches,
  enum/boolean/negative ordinal projection, optional/required schema fields,
  unsupported schema rejection, hook order/errors/reentry/exception containment.
- Router real-model tests: explicit IDs and result parity, missing/unknown IDs,
  canonical-path alias reuse after unlinking a test hard link, eviction at a
  one-model cap, reload failure after eviction, invalid GGUF rejection, recovery,
  and ordered batches. The original model resource is never modified.
- Node **5/5**, including queue bounds, drain-on-close, mixed batches, hooks,
  observer promise rejection/reentry, local routing and host event-loop progress.
- Rust **3/3** with `LAYA_MODEL` set: structured/batch parity, observer events,
  panic containment/reentry, router use and input errors; formatting and Clippy
  with warnings denied pass.
- TypeScript strict type check of `bindings/node/types.test.ts`, including a
  rejected unsupported free-string schema.
- `tests/parity.py --request examples/batch.json`: **exact agreement across C,
  C++, Rust and Node**, including primitive distributions, structured values and
  token usage. This supplements the existing single-request reference check;
  the reference server was not rerun because inference itself is unchanged.
- Linux exports remain restricted to the ten public `dtl_*` entry points.
  No vendored llama.cpp changes or new runtime dependencies.

The isolated multi-sequence experiment is preserved in
[evidence/encoder-batch.json](evidence/encoder-batch.json). Six questions from two
existing test states were encoded individually and together using independent
sequence IDs/reset positions, then together in reverse order. Winners matched
6/6, but probabilities drifted by up to 0.007069 versus individual calls and
0.003427 with reversed order. Sequential took 6.23 seconds; simultaneous took
7.51 seconds in one exploratory run. Peak process RSS was 535,764 KiB across
both modes. These are diagnostic observations, not a stable benchmark or an
accuracy evaluation. Simultaneous encoding is **not enabled** in the library.

Reproduce that optional experiment:

```sh
cmake --build build --target laya-encoder-probe -j 3
./build/laya-encoder-probe "$LAYA_MODEL" tests/encoder-inputs.json
```

The token fixture was exported from the already-used decision fixtures through
the official tokenizer during the ONNX comparison; it is specific to the pinned
English model. The public batch path keeps one forward pass per question and
exact individual-call output parity. It does not promise better throughput.

Remaining limitations: model-count cache limit rather than exact RAM accounting;
only the pinned English model validated; no automatic language routing; no
browser build, GPU validation, Windows/macOS distribution or clean Electron
installer test. Existing staged installation artifacts were not refreshed by
this feature validation; rebuild/install them when packaging the new library.

## Historical baseline

## Comparison work (2026-10-13)

The [evaluation in PLAN.md](PLAN.md#evaluation-decisions-2026-10-13) adds real
laya-ts/ONNX and LibLayaX/RLaya checks, measured footprint/responsiveness and a
scoped feature plan. Reproducible developer probes are under tests/; upstream
checkouts and downloaded/exported weights remain ignored research artifacts.
The library implementation had not changed during that comparison phase. These comparisons do not establish
production accuracy, cross-platform support or a stable latency ranking.

Tested on 2026-10-13, Linux x86-64 (Pop!_OS 24.04), Intel i7-1165G7,
Clang 22.1.8, Node 26.7.0, Cargo 1.98.0, CPU only. The runtime/model commits and checksum are recorded in
`models/manifest.json`. The actual cached GGUF checksum was verified.

## Passing checks

- Release build of the shared C library, C and C++ examples, and Node-API addon.
- CTest: ABI/version checks, missing model, invalid requests, oversized prompts,
  real inference and concurrent calls on one handle (2 CTest cases).
- Corrupt GGUF: returns `DTL_MODEL_ERROR` without an engine handle.
- Rust: two error-handling tests, formatting and Clippy with warnings denied.
- Node: missing model/invalid options, real inference, invalid JSON, bounded
  queue, rejection after closing starts, draining accepted work and repeat close.
- C, C++, Rust and Node produce exactly equal JSON values for the three-question
  fixture in `examples/request.json`; questions cover choice, noul and score.
- A server compiled from the same pinned source and CPU configuration gives
  exactly the same answers and token counts for that fixture (maximum absolute
  numeric difference: **0**, tested against a 0.000001 tolerance).
- Staged C headers/library compile and run using the system C compiler.
- Staged Node addon resolves the shared library beside it and performs inference.
- Linux symbol inspection: only the six `dtl_*` entry points are public.
- Dynamic dependencies are standard system libraries; no external libllama,
  libggml, Ollama, HTTP client or Python dependency is required at runtime.
- Vendored llama.cpp source is unmodified and pinned in the Git submodule.

The fixture routes to `billing` (probability about 0.978765), assigns refund
probability about 0.964984 and urgency score about 1.732993 on levels 0–2.
These are regression observations, not evidence of general application accuracy.

## Reference comparison

Comparing with the separately distributed b11540 `llama` executable produced
small differences in numeric results. With one slot and CPU inference the maximum
absolute difference was 0.004670285 in the fixture's urgency confidence, exceeding
the test's declared 0.002 tolerance. This result is retained here rather than
raising the tolerance to make it pass.

The controlled comparison subsequently passed with **zero numerical difference**
using `build/bin/llama-server`, compiled from the same pinned source and CPU
configuration as the embedded library. Both used 512-token context/batches,
unpooled embeddings, automatic flash attention, one sequence and CPU inference.
The reference server had one slot and two threads. Commands are in the README;
`tests/parity.py` now defaults to the tighter 0.000001 tolerance. The temporary
server was shut down after testing and its build option was turned off again.

The separately distributed binary is not bit-for-bit equivalent in this test.
Quantized kernels/build configuration are possible causes of cross-build
variation; the particular cause has not been isolated. This is why model/runtime
versions and the distributed native build must be tested together. The test is
a small regression fixture, not a broad parity or accuracy certification.

## Still needed before release

- A broader decision regression corpus and application-specific accuracy checks.
- ASan/UBSan and leak/lifecycle stress testing; cancellation and long-running load.
- Unsupported-but-valid model fixtures and more model/schema compatibility cases.
- Windows/macOS builds and Electron integration/installer tests on clean machines.
- An explicit minimum OS/CPU distribution matrix and portable CPU fallback.
- Binary/model redistribution artifact audit, notices, signing and release CI.
- Performance/memory measurements with controlled load; no latency claim is made.

Tests do not download weights. Run `tests/parity.py` for the binding comparison;
its optional `--server-url` additionally checks a supplied reference server.
