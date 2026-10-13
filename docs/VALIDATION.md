# Prototype validation

## Comparison work (2026-10-13)

The [evaluation in PLAN.md](PLAN.md#evaluation-decisions-2026-10-13) adds real
laya-ts/ONNX and LibLayaX/RLaya checks, measured footprint/responsiveness and a
scoped feature plan. Reproducible developer probes are under tests/; upstream
checkouts and downloaded/exported weights remain ignored research artifacts.
The library implementation has not changed. These comparisons do not establish
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
