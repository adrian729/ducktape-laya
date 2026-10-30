# Roadmap

Scheduled work in order, then work that is wanted but not scheduled, open research questions, and features deliberately not planned, with the reasons. When an item is done, remove it from here.

## Scheduled

In this order; each step is one batch, with its slow checks run once.

1. [macOS on both architectures](#macos-on-both-architectures): an application (Big Brother, a universal macOS app) needs Laya on Apple silicon and Intel Macs.
2. [API unification](#api-unification-before-10), phases 3 to 5 (calls, lifecycle, binding tests).
3. [Native core](#native-core).
4. The release items of [security hardening](#security-hardening), before the next release.

Tooling items under [build, CI and packaging](#build-ci-and-packaging) join whichever batch already runs their checks.

### macOS on both architectures

macOS arm64 and x86-64 (Intel) become tier-2 release targets, like Linux arm64 and Windows x64. The code is done: both targets are in `release/compatibility.json` `targets` and `release/qualification.json`, `native.yml` has their push, real-model, candidate and warm jobs (`macos-15` and `macos-15-intel`), and the loaders refuse bare-name loading and a `DYLD_LIBRARY_PATH` swap. A pre-check run passed on both. What remains:

- **The first full candidate and release.** Run the candidate run with both macOS targets, add `macos-arm64:drift` and `macos-x64:drift` to the evidence, re-read the macOS numbers in [PLATFORMS.md](PLATFORMS.md#numerical-results-across-platforms) and [QUALIFICATION.md](QUALIFICATION.md#release-targets-and-tiers) from it, and release the packages.
- **Benchmark thread-leak gate on macOS.** `tests/benchmark.py` still skips its thread-leak check on macOS, a skip that dates from builds with Accelerate on, whose libdispatch workers varied between runs. Release builds now have Accelerate off, and the stress test's Mach thread count stayed at 1 on both runners, so measure the benchmark once on each architecture with Accelerate off and enforce the gate if the count is stable.
- **Intel runner end of life.** GitHub reports the `macos-15-intel` runner as supported until about fall 2027; after that, the maintainer's Intel MacBook runs the Intel checks.

## Wanted later

### API unification before 1.0

The highest-priority item: the eight bindings expose the same core with different names, defaults and semantics, which every user of more than one binding pays for, and before 1.0 it can change without compatibility constraints. Each phase is one batch: design it across all bindings first, then run every binding's suites, `tests/typescript`, the browser run, binding parity and the CI binding jobs once per phase (the real-model suites once per batch). The inventories came from two independent reviews of 0.3.0 (`main` at 1478334).

3. **Calls, cancellation, deadlines and observers.**
   - **Deadlines** are measured from call entry everywhere (JS counts from enqueue, Go from the context's deadline, the others from native setup) with one range rule (Go allows more than 24 h through a watcher, the others reject it); Rust takes a `Duration`.
   - **Request id:** one type and default (a signed long in Java, a safe integer generated per instance in JavaScript, u64 defaulting to 0 elsewhere).
   - **Payloads:** a typed `Event {event, request_id, status, elapsed_ms}` everywhere instead of parsed objects in JS and Python and JSON strings elsewhere; a raw-string result option in JS and Python, so every binding can return the core's exact text (`JSON.parse` rounds integers above 2^53).
   - **Async:** Java `executeAsync` returning a `CompletableFuture` whose `cancel(true)` fires a call-scoped token; `info()` in Node and the browser exempt from `maxPending`, as `close` is.
4. **Lifecycle.** An optional cancel-running flag on `close` in Python, Java, .NET and Go, matching JS `cancelPending` (or drop it from JS); Rust `Engine::close(self)` beside `Drop`; the Node worker `unref()`ed while idle so an unclosed engine does not keep the process alive; after a worker failure the browser's "Laya is closing or closed" message comes with `ERR_LAYA_WORKER_EXITED`, so reword it.
5. **Binding tests.** Keep in each binding only what it owns (status propagation once, NUL and Unicode checks, local conversions, lifecycle, callbacks, loading) and delete the native-validation repeats (per-request `max_tokens` and temperatures, the unknown `metadata` key, bad overflow and context sizes, router `auto` validation, the missing model, open config reaching the model) that `tests/features.cpp`, `tests/control.cpp` and the parity open-config pass already own; move model-free checks out of real-model tests. Fix the tests that do not check what they claim: Python's `execute_async` cancellation (passes whether or not the native call stopped) and its cancel race, .NET's "cancelled while running" (passes when the run completes), Go's `time.Sleep` before `Close`, and bad-path asserts that accept any nonzero status.

- **Rust MSRV:** `rust-version = "1.98"` is the current stable; the crates probably build on a much older compiler, but this has not been verified with older toolchains.

### Native `sha256` verification of router routes

`url` and `sha256` stay browser-only: a route's `sha256` is checked by the browser binding when it fetches the model, and no native binding verifies a local route's hash. A native check (an optional `sha256` per route, verified when the route's model loads) would make the router shape the same in every binding.

### Native core

From the 0.3.0 reviews; each changes the library, so each batch needs the real-model tests and requalification.

- **A threadpool per engine.** Each `llama_encode` creates and joins a disposable ggml threadpool (upstream's default without an attached pool); attaching one could help short prompts. Measured 2026-10-28 (English model, 4 threads): a 32-token pass took 0.19 s, no more than the per-token rate of longer passes predicts, so the pool's cost is not visible; low priority.

### GPU backends

CPU comes first only because it runs everywhere, is the simplest to qualify, and the model is already fast enough on it. GPUs matter most for long documents (8,192 tokens), large batches and high-throughput servers. Start once the CPU platforms are fully qualified.

| Backend | Platforms | Notes |
|---|---|---|
| Metal | macOS arm64 | Built into ggml; a possibility once macOS arm64 is released ([scheduled](#macos-on-both-architectures)) |
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

- **Build-provenance attestations.** The release attestation (`attest-release`) states what that workflow checked before signing ([provenance](RELEASING.md#provenance)); it is not build provenance. The candidate run's assembly job could sign SLSA build provenance for every native artifact (`actions/attest-build-provenance`, needs `id-token: write` and `attestations: write`, only when dispatched from `main`).
- **Registry installation on every target.** The release host can install the published packages only for Linux x86-64. A `registry-check.yml` dispatched with a tag would install from npm, PyPI, NuGet and Maven Central on `windows-2025` and `ubuntu-24.04-arm` and run a real-model consumer: the `registry_installation` publication gate for the tier-2 targets.
- **Per-platform native packages.** From 0.3.0 the npm package, the jar and the NuGet package each carry the library of every release platform (7.5 MB each instead of 2.9 to 5.7 MB for Linux x86-64 alone; [package sizes](PERFORMANCE.md#package-sizes)), so every install downloads the other platforms' libraries; the Python wheels are already per platform. Each ecosystem has a standard split: npm platform packages as `optionalDependencies` with `os`/`cpu` (as esbuild does; installs with `--no-optional` then get no library), NuGet runtime packages (`DucktapeLaya.runtime.<rid>`) referenced by the main package, and Maven classifier jars. It multiplies the packages each release publishes and attests and changes packaging, release assembly and the install checks; the saving grows with each added platform.

### Build, CI and packaging

Small improvements that were considered and deferred.
- **The next runtime bump.** Upstream ed019cd8b (#30255) merged what `llama-embeddings-no-logits-buffer.patch` backports, so that patch goes when the pin moves past it ([patches](../patches/README.md#llama-embeddings-no-logits-bufferpatch)). Upstream b9f3599fb (#30405, "do not map foreign ranges into a backend buffer") changes code `c-api-no-throw.patch` patches, which then no longer applies and must be adapted. The 0.3.0 runtime-upgrade rehearsal used 98801578, the newest upstream revision on which every patch applies.
- **Windows CI jobs over budget.** `build-windows-x64` has taken 231 s against its 210 s budget, with a run-to-run variance not explained yet; `bindings-windows-x64` (the candidate run's binding suites and platform policy checks) 294 s against 270 s. Find where the time goes before changing either budget (`ci_budgets.JOB_BUDGETS`).
- **macOS sets the real-model wall.** Since both macOS targets joined `native.yml` (one `binding-macos-<arch>` job per architecture for the six model-free suites), a real-model dispatch waits on them: in verify run 40572053784 every other job was done at 428 s and the last macOS job at 935 s, after waits of up to 245 s for a macOS runner (GitHub's macOS concurrency) and `bindings-real-macos-x64`'s 557 s (six real-model suites in parallel on 4 cores). More shards would not use less macOS runner time. Options to measure: run the real-model binding suites on one architecture per push and both in the candidate run; or a self-hosted Apple silicon runner. `binding-node-windows-x64` (123 s against 110 s) still has its provisional budget: set it from ten runs.
- **Check CI's library early in the qualify run.** `qualify-host.py` now stops when reused reports name another library and when the CI download is of another candidate; it should also compare the CI linux-x64 library and addon hashes with this host's right after the native step, not only in `check-candidate.py` at the end.
- **Timing inside correctness tests.** `real-tokenizer` and `real-control` mix CPU-time ratios into correctness tests (AGENTS.md wants them split, as `tokenizer-partition` and `tokenizer-scaling` are), and `tokenizer-partition` takes about 2.6 s of the generic tier on its own.
- **Budget bases and plan checks.** Most `ci_budgets.py` budgets lack the comment with their measured basis; the plan checks in `gate_plan.py` cannot see per-model performance keys; the browser product (Chrome or Chromium) is not recorded in the browser evidence.
- **Concurrent verify runs.** Two `verify.py` runs share its state file, match their dispatched runs by branch and time, and share the `build-browser*` directories.
- **Real-model binding suites in local verify.** `verify.py --real-model` runs the binding suites against the model only in CI (`bindings-real-*`); locally, `local-bindings` is model-free and `local-real-model` runs the native tests and the parity smoke. After the API batch four binding failures appeared only in the CI run, after a dispatch. A local job running `validate.py --model ... --bindings all` against the host build would find such failures before the dispatch.
- **Binding jobs and `src/*`.** The binding CI jobs do not list the core sources as inputs and rely on the library build job; decide whether a core change should rerun them.
- **Jobs over budget.** `local-fuzz` took 301 s and then 345 s against 300 s; in the verify of e3b5521 `native-linux-x64` took 290 s against 270 s, the CI `fuzzer` 131 s against 120 s, and `binding-node-windows-x64` and `binding-rust-windows-x64` a few seconds over theirs. Find where the time goes before changing any budget.
- **Windows inference speed.** Windows inference is 1.2 to 1.5 times slower than Linux x86-64. It is not the optimization level: the `bench-windows-x64` experiment ([Windows benchmark](RELEASE_CHECKS.md#windows-benchmark)) measured the same p50 latency with the product's `/O2` (`-O3` for the compiler) and with `-O2`. Remaining candidates are the UCRT math functions and first-touch page faults while opening the model; a change is adopted only if faster with byte-identical outputs.
- **Dev Drive on Windows runners**, if the Defender exclusion still leaves file-heavy steps (`abi-layouts`, `package-smoke`, `dotnet restore`) more than twice as slow as on Linux.
- **The tier-2 short stress rule.** The 300 s run (100 to 135 rounds) applies only the 32 MiB late-growth rule. With the per-round series every candidate run now records for Linux arm64 and Windows, decide whether a median or slope limit is robust at that length.
- **The soak in CI.** The three-hour soak fits a 6-hour CI job; moving it there would shorten the release host's work further, at the cost of running it on a shared runner instead of the qualified tier-1 machine.
- **A tiny test model.** A small deterministic model for binding parity and the model-independent real-model tests would make those checks cheap enough for every change; it needs a model design and pinning.
- **.NET on Alpine.** The `musl` CI job checks the npm package and the jar; the NuGet package's musl message is tested only with simulated loader directories.
- **BTI on Linux arm64.** The arm64 binaries have return-address signing but are not marked BTI-compatible, because Ubuntu 24.04's glibc start files (`crti.o`, `crtn.o`) carry no BTI property and the linker marks an output only when every input is marked ([building](BUILDING.md)). Mark them once the build image's glibc has BTI start files.

### Security hardening

Non-blocking notes from the 0.3.0 security review.
- **Leaks on out-of-memory in ggml-webgpu and AMX `alloc_buffer`.** The paths `c-api-no-throw.patch` adds in ggml-webgpu leak when an allocation fails, and so does `ggml-cpu/amx/amx.cpp` (not built in the generic builds); only on out-of-memory, which is then reported. Free them in the patch.
- **Verify the last-known-good packages before the rollback test.** RELEASING.md and RELEASE_CHECKS.md download the previous release with `gh release download` and `release-assets.py retain` checks the files only against the downloaded inventory; `tests/rollback.py` then compiles and runs a consumer against that library on the release host. Fetch them with `scripts/verify-release-assets.sh vPREVIOUS DIR` instead (SHA256SUMS, `gates.json` and the pinned attestation). `retain` already refuses inventory names with a directory part.
- **Bind `gates.json` before attesting.** `attest-release.yml` downloads `gates.json` from the release and trusts it, and the browser tarball is tied only to its `inventories_sha256.browser`, so someone who can edit release assets could swap both. Add a required `gates_sha256` input computed from the local `build-candidate/gates.json` and refuse a mismatch (`publish-release` already pins the predicate's `gates_sha256`).
- **Repository settings** (the maintainer's decision): no branch protection on `main`, no required reviewer on the release environment, immutable releases off, Dependabot alerts off.

### Broader test coverage

- **Physical CPUs, GPUs and browsers.** The CPU check is tested on emulated CPUs only; the browser on one Chrome version (Chromium 152 for 0.2.0, Chrome 156 for 0.3.0) with one GPU; tier-2 platforms with the English model only. Add older physical x86-64 CPUs, more GPUs, Firefox and Safari, and the multilingual and typed-decisions models on Linux arm64 and Windows.
- **Resource pressure.** No browser soak run exists (the browser checks only that allocation failures are reported: a lowered WebAssembly heap ceiling and failed WebGPU buffers), and performance budgets exist only for Linux x86-64 on one machine.
- **WebGPU failures that still end the worker.** A failed read-back buffer, a device loss and waits longer than 30 s still abort the worker (status 3, `ERR_LAYA_WORKER_EXITED`); only the buffer allocations covered by `ggml-webgpu-allocation-failure.patch` report status 2. Real GPU out-of-memory has not been tested on hardware: the `gpu_memory` stage makes chosen buffers fail in a test worker.
- **Results across `threads` values** are not compared by any check.
- **Larger accuracy corpus.** The original-Laya comparison has 29 answers per model, and `scan` and truncation are not compared with the original. Nor are `noul` `labels`: the real-model test checks that default labels render the default prompt and that other labels reach it, but no answer with labels is recorded from the original Laya.
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
- **A faster CPU profile.** Inference is compute-bound: on the Intel Core i7-1165G7 a pass costs about 6–7 ms per prompt token with 4 threads and has no visible fixed cost (2026-10-28, [performance](PERFORMANCE.md#latency-and-memory)), so speed comes from the matrix kernels, not the call path. The build refuses AVX-512 and VNNI, which ggml's Q8_0 kernels can use on many x86-64 CPUs. Measured 2026-10-30 on that CPU (gcc, English Q8_0 model, 4 threads; `GGML_AVX512`, `GGML_AVX512_VBMI` and `GGML_AVX512_VNNI` on, the build's refusal removed for the experiment): 1.23 times faster at 80 prompt tokens, 1.27 at 233, 1.30 at 439 and 1.32 at 848 (medians of 6 rounds alternating the two builds), with answers within 0.0095 of the AVX2 build. The laptop's own heat moved the same build's time by up to 2 times between rounds, more than the build did. Not worth it at that gain: an AVX-512 build would be its own numerical profile, with its own qualification, a second x86-64 library in every package and a CPU check that picks it at run time. Revisit for a user who needs throughput on AVX-512 servers, measured there.
- **Real-data accuracy.** The [usage guide](USAGE.md) rests on one private evaluation (240 messages and 33 tickets from one application). A public labelled set for each kind of task the presets in `examples/presets` claim (support triage, email triage, prompt-injection screening, moderation), run through `scripts/evaluate-questions.py`, would show where the models fit and let releases track it.
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
| Temperature fitting, fine-tuning | Training tooling: use the original's tools, then convert the model or pass the fitted temperatures |
| HTTP server, MCP server, LangChain/LlamaIndex/CrewAI integrations | In-process embedding is a project goal; integrations could later sit on top of the Python binding |
