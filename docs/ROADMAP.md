# Roadmap

Work that is wanted but not scheduled, open research questions, and features deliberately not planned, with the reasons. Nothing here is scheduled; when an item is taken up, remove it from here.

## Wanted later

### GPU backends

CPU comes first only because it runs everywhere, is the simplest to qualify, and the model is already fast enough on it. GPUs matter most for long documents (8,192 tokens), large batches and high-throughput servers. Start once the CPU platforms are fully qualified.

| Backend | Platforms | Notes |
|---|---|---|
| Metal | macOS arm64 | Built into ggml; a possibility once macOS arm64 is released (it is the next tier-2 candidate, see below) |
| CUDA | Linux and Windows x64 with NVIDIA GPUs | Fastest server option; a large toolkit dependency, so ship it as a separate package variant |
| Vulkan | Linux and Windows (any vendor), Android later | One portable backend across GPU vendors |
| WebGPU | Browser | Already exists (experimental) |

Each backend needs:
- a build option and runtime selection, with CPU fallback when no device is found and the active backend reported in `info()`;
- its own numerical profile, held to the CPU bar: the upstream reference on that backend, cross-profile drift against CPU, and no changed decisions on the fixtures;
- device-loss and out-of-memory handling and tests (the WebGPU work is the pattern);
- packaging variants, so CPU users don't download GPU runtimes;
- qualification like the CPU profile: the release gates in [QUALIFICATION.md](QUALIFICATION.md), run on CI or listed hardware for that backend.

### More release targets and release provenance

- **macOS arm64 as the next tier-2 target.** It builds, passes the real-model checks and packages its own fragment in CI (`platforms.yml`), but is not in the CI release assembly or `release/compatibility.json` `targets`. Promoting it means moving the macOS job into `native.yml` beside Linux and Windows, adding its fragment to the candidate run's assembly, its short stress and reproducibility jobs, and a `targets` entry; the tier-2 checks in `scripts/release_targets.py` already apply per target.
- **Build-provenance attestations.** The release attestation (`attest-release`) states what that workflow checked before signing ([provenance](RELEASING.md#provenance)); it is not build provenance. The candidate run's assembly job could sign SLSA build provenance for every native artifact (`actions/attest-build-provenance`, needs `id-token: write` and `attestations: write`, only when dispatched from `main`).
- **Registry installation on every target.** The release host can install the published packages only for Linux x86-64. A `registry-check.yml` dispatched with a tag would install from npm, PyPI, NuGet and Maven Central on `windows-2025` and `ubuntu-24.04-arm` and run a real-model consumer: the `registry_installation` publication gate for the tier-2 targets.
- **Per-platform native packages.** From 0.3.0 the npm package, the jar and the NuGet package each carry the library of every release platform (an estimated 8 to 9 MB each instead of about 3 MB; not yet measured on a candidate run), so every install downloads the other platforms' libraries; the Python wheels are already per platform. Each ecosystem has a standard split: npm platform packages as `optionalDependencies` with `os`/`cpu` (as esbuild does; installs with `--no-optional` then get no library), NuGet runtime packages (`DucktapeLaya.runtime.<rid>`) referenced by the main package, and Maven classifier jars. It multiplies the packages each release publishes and attests and changes packaging, release assembly and the install checks; the saving grows with each added platform.

### Build, CI and packaging

Small improvements that were considered and deferred.
- **Windows CI jobs over budget.** `build-windows-x64` has taken 231 s against its 210 s budget, with a run-to-run variance not explained yet; `bindings-windows-x64` (the candidate run's binding suites and platform policy checks) 294 s against 270 s. Find where the time goes before changing either budget (`ci_paths.JOB_BUDGETS`).
- **Windows inference speed.** Windows inference is 1.2 to 1.5 times slower than Linux x86-64. It is not the optimization level: the `bench-windows-x64` experiment ([Windows benchmark](RELEASE_CHECKS.md#windows-benchmark)) measured the same p50 latency with the product's `/O2` (`-O3` for the compiler) and with `-O2`. Remaining candidates are the UCRT math functions and first-touch page faults while opening the model; a change is adopted only if faster with byte-identical outputs.
- **Dev Drive on Windows runners**, if the Defender exclusion still leaves file-heavy steps (`abi-layouts`, `package-smoke`, `dotnet restore`) more than twice as slow as on Linux.
- **The tier-2 short stress rule.** The 300 s run (100 to 135 rounds) applies only the 32 MiB late-growth rule. With the per-round series every candidate run now records for Linux arm64 and Windows, decide whether a median or slope limit is robust at that length.
- **The soak in CI.** The three-hour soak fits a 6-hour CI job; moving it there would shorten the release host's work further, at the cost of running it on a shared runner instead of the qualified tier-1 machine.
- **A tiny test model.** A small deterministic model for binding parity and the model-independent real-model tests would make those checks cheap enough for every change; it needs a model design and pinning.
- **mold or lld for developers.** Document them as an option of the development loop for those who install them; not a default, since linking is about 2% of build time and the dev preset relinks only the library.
- **.NET on Alpine.** The `musl` CI job checks the npm package and the jar; the NuGet package's musl message is tested only with simulated loader directories.
- **macOS library search.** dyld searches `DYLD_LIBRARY_PATH` by file name even before an absolute path, so a library of the same name there can still replace the one a package bundles (as `LD_LIBRARY_PATH` could on Linux before the Node addon used `DT_RPATH`). Fix it before macOS packages are released.
- **BTI on Linux arm64.** The arm64 binaries have return-address signing but are not marked BTI-compatible, because Ubuntu 24.04's glibc start files (`crti.o`, `crtn.o`) carry no BTI property and the linker marks an output only when every input is marked ([building](BUILDING.md)). Mark them once the build image's glibc has BTI start files.

### API unification before 1.0

None of these has backward-compatibility constraints before 1.0.
- **Router open shape:** the browser takes `openRouter(models{url, contextTokens...}, {maxLoaded, auto})`, Node takes `{models: {id: path | {path, config}}, max_loaded, auto}`; pick one shape.
- **Request id:** a signed long in Java and a safe integer in JavaScript against u64 elsewhere, and generated by default in Node and the browser against 0 in the others; unify type and default.
- **Go close from a callback:** `Close` deadlocks there and needs `CloseDeferred`, while every other binding defers automatically; hand callbacks a call-scoped closer or make `Close` non-blocking with a separate `Wait`.
- **Local validation:** Node, Python, Java and .NET pre-validate arguments with language exceptions, Go and Rust defer to the native side (status 1).
- **Model path types:** Python rejects `bytes` paths and paths with non-UTF-8 bytes.
- **Rust MSRV:** `rust-version = "1.98"` is the current stable; the crates probably build on a much older compiler, but this has not been verified with older toolchains.

### Broader test coverage

- **Physical CPUs, GPUs and browsers.** The CPU check is tested on emulated CPUs only; the browser on one Chrome version (Chromium 152 for 0.2.0, Chrome 156 for the next release) with one GPU; tier-2 platforms with the English model only. Add older physical x86-64 CPUs, more GPUs, Firefox and Safari, and the multilingual and typed-decisions models on Linux arm64 and Windows.
- **Resource pressure.** No browser soak run exists (the browser checks only that allocation failures are reported: a lowered WebAssembly heap ceiling and failed WebGPU buffers), and performance budgets exist only for Linux x86-64 on one machine.
- **WebGPU failures that still end the worker.** A failed read-back buffer, a device loss and waits longer than 30 s still abort the worker (status 3, `ERR_LAYA_WORKER_EXITED`); only the buffer allocations covered by `ggml-webgpu-allocation-failure.patch` report status 2. Real GPU out-of-memory has not been tested on hardware: the `gpu_memory` stage makes chosen buffers fail in a test worker.
- **Results across `threads` values** are not compared by any check.
- **Larger accuracy corpus.** The original-Laya comparison has 29 answers per model, and `scan` and truncation are not compared with the original.
- **Documentation snippets.** The README and package README code is not compiled by CI (the example request files are).

### Higher-precision model variants

`ggml-org/Laya-GGUF` also has `Laya-BF16.gguf`: the same English model at roughly the original Laya's precision, 844 MB against 449 MB for the Q8_0 file we ship.
- It should remove most of the English model's mean probability difference from the original (0.0068 in the 0.2.0 qualification run; [accuracy](PERFORMANCE.md#accuracy-against-the-original-laya)), at about twice the memory and slower CPU inference.
- Supporting it means pinning its hash, the same qualification as Q8_0, and documenting it as the accuracy-first option next to the memory-first Q8_0.
- The same applies to BF16 versions of other supported Laya models.

### Larger Laya models

The English model (ModernBERT-large, about 421M parameters) is the largest Laya checkpoint published as of 2026-10-20. GGUF and llama.cpp run this architecture at any size, so a larger release would be supported the same way: convert or download, pin, qualify. Check memory per engine and the context capacity.

### Downloadable multilingual and typed-decisions models

Users currently build these two GGUFs with `scripts/convert-model.py`. Make them downloadable: upload the exact pinned files (for example to a Hugging Face repository under the maintainer's account, with a model card giving the Apache-2.0 notice, source revision and hashes) and set each manifest's `url`, or switch to official GGUFs if the Laya authors or ggml-org publish them and they qualify. Steps: [RELEASING.md](RELEASING.md#converted-models).

### Mobile, and the languages that depend on it

iOS and Android need their own packaging (XCFramework, AAR), toolchains and memory testing. The 0.5 GB model fits on phones.
- **Swift for iOS:** a Swift package; the same package could serve macOS once it is a release target.
- **Kotlin on Android:** needs a JNI binding, because Android lacks Java's FFM API.
- **Dart/Flutter:** a `dart:ffi` binding across mobile and desktop.

### More language bindings on request

Ruby (`ffi` gem) and PHP (`FFI`) would each be a small wrapper like the Python binding, for Rails and Laravel backends. Add one when a user asks.

## Research questions

- **The original Laya's `act_probability`:** a second "act" output alongside the decisions. The original's config prices an `escalate` action (`act_costs`), so it appears to be the probability that the answer should be acted on rather than escalated. The GGUF and llama.cpp path don't carry it; supporting it would need converter and llama.cpp changes. First confirm its meaning and whether the authors treat it as public.
- **Batching questions into one forward pass:** measured 2026-10-20 on CPU (4 cores), questions packed as separate sequences in one `llama_encode` were only 1.15× faster on a 4-question request, and results changed with the batch composition: the same question got different probabilities depending on its position and neighbours (up to 0.04 on the fixtures), most likely because matrix kernels tile rows differently. Not adopted. Revisit for GPU backends, where batching pays more, and only with results independent of the batch.
- **Windows agreement with the original Laya:** the clang-cl build runs the same ggml kernels as Linux x86-64 and agrees exactly with upstream llama.cpp, but one fixture answer (`json_escaping/spam`) drifts 0.054 from the original Laya (limit 0.05; Linux 0.037). Measured 2026-10-22, the whole gap comes from the Windows C runtime's single-precision `erff` (GEGLU) and `sinf`/`cosf` (RoPE cache); computed as `(float)f((double)x)`, Windows and Linux give identical results on all 29 answers (largest drift 0.039 on both). Upstream deliberately uses the platform math library on CPU (#14445), so bit-identical results across platforms are not an upstream goal. Our own `erff`/`sinf`/`cosf` in a runtime patch would make CPU platforms identical, but would move Linux from 0.037 to 0.039, diverge from stock llama.cpp on every upgrade, cost unmeasured time (`erff` runs about 3.4M times per request) and still not cover GPU backends. Not planned: it is a documented [known deviation](COMPATIBILITY.md#known-deviations) with its own cap.
- **Loading a model from memory:** llama.cpp has no public load-from-buffer API. Workarounds (memfd, temporary files) add little real protection for encrypted assets. Revisit with mobile.

## Not planned

Features the original Laya has that ducktape-laya deliberately leaves out.

| Feature | Why |
|---|---|
| `min_confidence` abstention in the library | A one-line check on `answer_confidence` in the caller; thresholds are application policy |
| Hooks that rewrite questions or budgets | Observers stay passive, so planning is deterministic and nothing reenters it |
| Presets and option-order rotations as API | They are request content: ship as example request files and a documented recipe |
| Email cleaner, embedding shortlist | Application helpers; the shortlist needs a second model |
| Automatic typed-decisions workflow detection (question IDs matching fixed workflows) | Hidden routing by question names; route to the typed-decisions model explicitly |
| Naming the language of Latin-script text in detection | Routing only decides English or not; `usage.routing.language` comes from function words alone, and the detector already agrees with all 180 of the original's routing cases |
| `option_layout: "parallel"` | The core renders the one prompt layout defined by the GGUF template, as llama.cpp's decision server does; option-order bias has a documented rotation recipe |
| `usage.truncated` | Redundant with a non-empty `usage.truncated_questions`, which here also lists questions whose instructions or options were shortened |
| `batch_tokens` open setting | It sized the batched forward pass, which was not adopted (see Research questions) |
| Temperature fitting, fine-tuning, evaluation harnesses | Training tooling: use the original's tools, then convert the model or pass the fitted temperatures |
| HTTP server, MCP server, LangChain/LlamaIndex/CrewAI integrations | In-process embedding is a project goal; integrations could later sit on top of the Python binding |
| Intel Macs | macOS is not released yet and Apple silicon comes first ([platforms](PLATFORMS.md)); Intel Macs would still be left out, since Apple stopped selling them in 2023 and CI runners are being retired. Measured fact, not a support promise: on a 2019 Intel MacBook Pro (macOS 15.7.2, Apple clang 17) the Release build, the 6 model-free tests and the 4 real-model tests with the English model passed at commit 8994a03 (the suites have grown since), and the benchmark ran ([performance](PERFORMANCE.md#a-second-machine-intel-macbook-pro)) |
