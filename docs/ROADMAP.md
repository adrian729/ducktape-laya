# Roadmap

Scheduled work in order, then work that is wanted but not scheduled, open research questions, and features deliberately not planned, with the reasons. When an item is done, remove it from here.

## Scheduled

In this order; each step is one batch, with its slow checks run once.

1. [macOS on both architectures](#macos-on-both-architectures): an application (Big Brother, a universal macOS app) needs Laya on Apple silicon and Intel Macs.
2. [API unification](#api-unification-before-10), phases 1 and 2 (names, errors, configuration), with the per-engine cost ceiling.
3. API unification, phases 3 to 5 (calls, lifecycle, binding tests).
4. [Native core](#native-core).
5. The release items of [security hardening](#security-hardening), before the next release.

Tooling items under [build, CI and packaging](#build-ci-and-packaging) join whichever batch already runs their checks.

### macOS on both architectures

macOS arm64 and x86-64 (Intel) become tier-2 release targets, like Linux arm64 and Windows x64.

- **Apple silicon.** It builds, passes the real-model checks and packages its own fragment in CI (`platforms.yml`), but is not in the CI release assembly or `release/compatibility.json` `targets`. Promoting it means moving the macOS job into `native.yml` beside Linux and Windows, adding its fragment to the candidate run's assembly, its short stress and reproducibility jobs, and a `targets` entry; the tier-2 checks in `scripts/release_targets.py` already apply per target.
- **Intel Macs.** No CI and no release so far, but on a 2019 Intel MacBook Pro (macOS 15.7.2, Apple clang 17) the Release build, the 6 model-free tests and the 4 real-model tests with the English model passed at commit 8994a03 (the suites have grown since), and the benchmark ran ([performance](PERFORMANCE.md#a-second-machine-intel-macbook-pro)). CI uses GitHub's `macos-15-intel` runner, reported as supported until about fall 2027; after that, the maintainer's Intel MacBook runs the Intel checks. Needs the x86-64 CPU-feature check on macOS, its own numerical profile (Apple's libm, which could drift from the original Laya as Windows' C runtime does: measure it), and every package carrying the x86-64 slice.
- **Universal applications.** An app built for both architectures needs both slices: decide between one universal `libducktape_laya.dylib` and a library per architecture, per package format, and whether a source build can configure both architectures at once.
- **macOS library search.** dyld searches `DYLD_LIBRARY_PATH` by file name even before an absolute path, so a library of the same name there can still replace the one a package bundles (as `LD_LIBRARY_PATH` could on Linux before the Node addon used `DT_RPATH`). Fix it before macOS packages are released.
- **macOS bare-name loading.** Python (`_native.py`) and .NET (`Native.cs`) fall back to `dlopen` of a bare `libducktape_laya.1.dylib`, which dyld can resolve from directories the caller does not control; Java loads by absolute path. Fix before macOS packages ship (with the `DYLD_LIBRARY_PATH` item above).

## Wanted later

### API unification before 1.0

The highest-priority item: the eight bindings expose the same core with different names, defaults and semantics, which every user of more than one binding pays for, and before 1.0 it can change without compatibility constraints. Each phase is one batch: design it across all bindings first, then run every binding's suites, `tests/typescript`, the browser run, binding parity and the CI binding jobs once per phase (the real-model suites once per batch). The inventories came from two independent reviews of 0.3.0 (`main` at 1478334).

1. **Names, statuses and errors.**
   - One engine name (`Laya` in Node, the browser and Java; `Engine` in Python, Go and Rust; `LayaEngine` in .NET) and one stem for config, call options, errors (`LayaError`/`LayaException` against Go and Rust `Error`), the closed-engine error (`EngineClosedError`, `LayaClosedException`, `ErrClosed`, `ERR_LAYA_CLOSED`; .NET throws `ObjectDisposedException` without a status) and the cancellation token (`CancelToken` against `CancellationToken`).
   - A status enum in every language (Java has only int constants on `LayaException`, Rust only `status::X`), with the error's status typed as it, and one policy check that compares every binding's constants with the enum in `include/ducktape_laya.h` (today Rust and Node compare them with literals typed out again; Python, Java, .NET and Go are not compared). Java's hard-coded call-options offsets come from `CALL_OPTIONS.byteOffset(...)` and join `tests/abi-layout.py`.
   - A load-error type everywhere (Python has `LayaLoadError`; Java and .NET use status 7 with a cause; JS `ERR_LAYA_UNSUPPORTED`), load failures retried on the next call everywhere (Java caches them for the process), and the Node addon's package-version check in Python, Java and .NET too.
   - One error order: closed, then argument errors, then cancellation, and arguments validated before the library loads (Python and .NET report 7 for a NUL path with no library, Java and Node 1; Go and the browser report cancellation before closed). Go's `ErrClosed` is a shared mutable `*Error`.
2. **Open and configuration.**
   - **Router shape:** one shape everywhere, `{models: {id: path | url | {path | url, sha256?, config: {native keys}}}, max_loaded, auto}`, with native snake_case keys inside JSON-shaped config and idiomatic names only for binding-only options; typed bindings accept a `RouterConfig` as well as a string. Today the browser takes `openRouter({id: {url, contextTokens, ...}}, {maxLoaded, auto})` and Node mixes camelCase open options with snake_case route config. The browser's `open(options)` takes the model URL inside the options and accepts an undocumented `open({models})`: make it `open(modelUrl, options)`.
   - **Validation:** the native library is the only validator of config values (status 1); bindings check only their own conversions (types, units, NUL, Unicode, binding-only options) and raise the language's standard type carrying a status. Today Node, Python, Java and .NET pre-validate different subsets (threads, `contextTokens` ≥ 1 or > 0, overflow names, temperatures) with exceptions that mostly carry no status, and Go and Rust defer everything.
   - **Unset values:** 0 is invalid everywhere and unset is `null`/`None` (Go omits `ContextTokens: 0`, Python and Rust send it and get status 1, Node, Java and .NET reject it locally); `null` options mean the defaults everywhere (Node and the browser reject them, Java throws for a null config).
   - **Paths:** accept the OS path type and pass raw bytes on POSIX in every binding. Python rejects `bytes` and non-UTF-8 paths, Rust rejects non-UTF-8 paths on Unix although the core takes bytes, Go passes them through, and Java and .NET take strings only (add `Path` overloads). Go's `json.Marshal` replaces invalid UTF-8 in config keys and `Overflow` with U+FFFD instead of rejecting it.
   - **Library overrides:** `DUCKTAPE_LAYA_LIB_DIR` everywhere plus one explicit file override with the same name in each binding (today Node `addonPath`/`DUCKTAPE_LAYA_ADDON`, Java `-Dducktape.laya.library`, Go and Rust at build time), each required to be an absolute path.
   - **Smaller:** Rust `Engine::open(path)` beside `open_with(path, &Config)`; the browser rejects a `threads` other than 1; one temperature setter shape (Rust adds one entry, Java replaces the map).
3. **Calls, cancellation, deadlines and observers.**
   - **Deadlines** are measured from call entry everywhere (JS counts from enqueue, Go from the context's deadline, the others from native setup) with one range rule (Go allows more than 24 h through a watcher, the others reject it); Rust takes a `Duration`.
   - **Cancellation:** one-shot tokens (drop Java's `reset()`), a Rust `CancelFlag` newtype instead of a raw `AtomicU32`, and an already-cancelled call fails fast with no native call and no events everywhere (Python, Java, Rust and .NET's sync `Execute` make the call and emit `start` and `error`).
   - **Request id:** one type and default (a signed long in Java, a safe integer generated per instance in JavaScript, u64 defaulting to 0 elsewhere).
   - **Payloads:** a typed `Event {event, request_id, status, elapsed_ms}` everywhere instead of parsed objects in JS and Python and JSON strings elsewhere; a raw-string result option in JS and Python, so every binding can return the core's exact text (`JSON.parse` rounds integers above 2^53).
   - **Async:** Java `executeAsync` returning a `CompletableFuture` whose `cancel(true)` fires a call-scoped token; `info()` in Node and the browser exempt from `maxPending`, as `close` is.
4. **Lifecycle.** `close()` defers automatically when called from a callback everywhere (Go's `Close` deadlocks there and needs `CloseDeferred`); an optional cancel-running flag on `close` in Python, Java, .NET and Go, matching JS `cancelPending` (or drop it from JS); Rust `Engine::close(self)` beside `Drop`; the Node worker `unref()`ed while idle so an unclosed engine does not keep the process alive; after a worker failure the browser's "Laya is closing or closed" message comes with `ERR_LAYA_WORKER_EXITED`, so reword it.
5. **Binding tests.** Keep in each binding only what it owns (status propagation once, NUL and Unicode checks, local conversions, lifecycle, callbacks, loading) and delete the native-validation repeats (per-request `max_tokens` and temperatures, the unknown `metadata` key, bad overflow and context sizes, router `auto` validation, the missing model, open config reaching the model) that `tests/features.cpp`, `tests/control.cpp` and the parity open-config pass already own; move model-free checks out of real-model tests; extend the parity `options.json` to `overflow` and `context_tokens`. Fix the tests that do not check what they claim: Python's `execute_async` cancellation (passes whether or not the native call stopped) and its cancel race, .NET's "cancelled while running" (passes when the run completes), Go's `time.Sleep` before `Close`, and bad-path asserts that accept any nonzero status.
6. **Documented, not unified:** results and inputs as parsed values in JS and Python and JSON strings in the typed bindings; deadline units as language idioms; observer delivery (synchronous on the calling thread, asynchronous on the host in JS); bounded admission in JS against blocking elsewhere; open-time cancellation only in the browser; finalizer safety nets. API.md gets one table of these per binding.

- **Rust MSRV:** `rust-version = "1.98"` is the current stable; the crates probably build on a much older compiler, but this has not been verified with older toolchains.

### Native core

From the 0.3.0 reviews; each changes the library, so each batch needs the real-model tests and requalification.

- **Tokenizer stop checks across pieces.** `tokenizer-stop-check.patch` resets its work counter whenever the hook is installed, which happens per piece, so a question of up to 255 short options can tokenize its whole head without a stop check, against API.md's "counted over the whole call". Reset it only when going from no hook to a hook (or install it once per `prepare()`), and call `control->check()` between pieces. Needs `check-upgrade.py --configure-only` and `tokenizer-scaling`.
- **Memory per call.** Scan windows are materialized with full copies of the head and state slice (about 130 to 160 MiB at 8,192 context tokens before the window cap rejects a request); each question copies all cached state tokens (up to about 8 MB for a 2 MiB state); the request JSON is held three times (`dtl_execute`'s body, `requests`, and `plan` taking each request by value); the first tokenization buffer of `text.size() + 2` is too small for mmBERT's per-line `▁` (about 1.5 tokens per byte), which then tokenizes again; and the previous request's state (up to about 10 MiB) stays cached after the call, which matters to multi-tenant hosts.
- **Waiting for the engine lock** polls `try_lock` every millisecond whenever a timeout or cancel callback is set, with no fairness between waiters.
- **Routers load for trivial requests:** a router loads (and may evict) a route's model even when every question is a single-option `choice`; `"model": "auto"` batches from untrusted input can force one load per request unless `max_loaded` covers the routes (SECURITY.md says so).
- **Simplifications:** the dead non-string-state branch in `prepare` (`separate_state`, `no_state`; keep the `[SEP]`-in-last-option rejection it implies), the duplicate overflow default in `backend_llama.cpp`, the limits repeated as literals in `runtime_info.cpp`, and `request_state`'s owner tracking, which assumes several backends could prepare one request.
- **A threadpool per engine.** Each `llama_encode` creates and joins a disposable ggml threadpool (upstream's default without an attached pool); attaching one could help short prompts. Measure first.

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
- **macOS runner queueing.** In verify run 40524536755 (`platforms.yml`, every macOS job stale) the seven macOS jobs ran 17 to 60 s each but waited up to 664 s for a runner, so the run took 720 s against its 300 s budget. The wait is GitHub's macOS runner concurrency, not the jobs' work. Fewer, larger macOS jobs (one job for the six binding suites, sharing one setup) would queue less when several bindings are stale, at the cost of rerunning all six when one changes; measure both before choosing. `binding-node-windows-x64` (123 s against 110 s) still has its provisional budget: set it from ten runs.
- **Check CI's library early in the qualify run.** `qualify-host.py` now stops when reused reports name another library and when the CI download is of another candidate; it should also compare the CI linux-x64 library and addon hashes with this host's right after the native step, not only in `check-candidate.py` at the end.
- **Timing inside correctness tests.** `real-tokenizer` and `real-control` mix CPU-time ratios into correctness tests (AGENTS.md wants them split, as `tokenizer-partition` and `tokenizer-scaling` are), and `tokenizer-partition` takes about 2.6 s of the generic tier on its own.
- **Budget bases and plan checks.** Most `ci_budgets.py` budgets lack the comment with their measured basis; the plan checks in `gate_plan.py` cannot see per-model performance keys; the browser product (Chrome or Chromium) is not recorded in the browser evidence.
- **Concurrent verify runs.** Two `verify.py` runs share its state file, match their dispatched runs by branch and time, and share the `build-browser*` directories.
- **Binding jobs and `src/*`.** The binding CI jobs do not list the core sources as inputs and rely on the library build job; decide whether a core change should rerun them.
- **Jobs over budget.** `local-fuzz` took 301 s and then 345 s against 300 s; in the verify of e3b5521 `native-linux-x64` took 290 s against 270 s, the CI `fuzzer` 131 s against 120 s, and `binding-node-windows-x64` and `binding-rust-windows-x64` a few seconds over theirs. Find where the time goes before changing any budget.
- **Windows inference speed.** Windows inference is 1.2 to 1.5 times slower than Linux x86-64. It is not the optimization level: the `bench-windows-x64` experiment ([Windows benchmark](RELEASE_CHECKS.md#windows-benchmark)) measured the same p50 latency with the product's `/O2` (`-O3` for the compiler) and with `-O2`. Remaining candidates are the UCRT math functions and first-touch page faults while opening the model; a change is adopted only if faster with byte-identical outputs.
- **Dev Drive on Windows runners**, if the Defender exclusion still leaves file-heavy steps (`abi-layouts`, `package-smoke`, `dotnet restore`) more than twice as slow as on Linux.
- **The tier-2 short stress rule.** The 300 s run (100 to 135 rounds) applies only the 32 MiB late-growth rule. With the per-round series every candidate run now records for Linux arm64 and Windows, decide whether a median or slope limit is robust at that length.
- **The soak in CI.** The three-hour soak fits a 6-hour CI job; moving it there would shorten the release host's work further, at the cost of running it on a shared runner instead of the qualified tier-1 machine.
- **A tiny test model.** A small deterministic model for binding parity and the model-independent real-model tests would make those checks cheap enough for every change; it needs a model design and pinning.
- **mold or lld for developers.** Document them as an option of the development loop for those who install them; not a default, since linking is about 2% of build time and the dev preset relinks only the library.
- **.NET on Alpine.** The `musl` CI job checks the npm package and the jar; the NuGet package's musl message is tested only with simulated loader directories.
- **BTI on Linux arm64.** The arm64 binaries have return-address signing but are not marked BTI-compatible, because Ubuntu 24.04's glibc start files (`crti.o`, `crtn.o`) carry no BTI property and the linker marks an output only when every input is marked ([building](BUILDING.md)). Mark them once the build image's glibc has BTI start files.

### Security hardening

Non-blocking notes from the 0.3.0 security review.
- **Leaks on out-of-memory in ggml-webgpu and AMX `alloc_buffer`.** The paths `c-api-no-throw.patch` adds in ggml-webgpu leak when an allocation fails, and so does `ggml-cpu/amx/amx.cpp` (not built in the generic builds); only on out-of-memory, which is then reported. Free them in the patch.
- **The PyPI comparison's hashes.** `publish-release.yml`'s "Compare with PyPI" step downloads `SHA256SUMS` again and compares PyPI's files with it, while the wheels in `dist` were verified against their attestations; the file itself is not attested. Compare with the hashes of the verified wheels instead (`scripts/pypi-missing-wheels.py`), and run that script with `python3 -I` like the workflow's other Python calls.
- **Verify the last-known-good packages before the rollback test.** RELEASING.md and RELEASE_CHECKS.md download the previous release with `gh release download` and `release-assets.py retain` checks the files only against the downloaded inventory; `tests/rollback.py` then compiles and runs a consumer against that library on the release host. Fetch them with `scripts/verify-release-assets.sh vPREVIOUS DIR` instead (SHA256SUMS, `gates.json` and the pinned attestation). `retain` already refuses inventory names with a directory part.
- **Bind `gates.json` before attesting.** `attest-release.yml` downloads `gates.json` from the release and trusts it, and the browser tarball is tied only to its `inventories_sha256.browser`, so someone who can edit release assets could swap both. Add a required `gates_sha256` input computed from the local `build-candidate/gates.json` and refuse a mismatch (`publish-release` already pins the predicate's `gates_sha256`).
- **A cost ceiling per engine.** A request's own `overflow: "scan"` and `max_windows` override the engine's default, so a 60 KB request can plan about 3,900 forward passes (the per-call cap is 4,096); only deadlines stop it. Add an engine or route ceiling on planned passes (or make the engine's `overflow` binding), and tell untrusted-input applications in SECURITY.md to set `timeout_ms` and filter `overflow`/`max_windows`.
- **Repository settings** (the maintainer's decision): no branch protection on `main`, no required reviewer on the release environment, immutable releases off, Dependabot alerts off.

### Broader test coverage

- **Physical CPUs, GPUs and browsers.** The CPU check is tested on emulated CPUs only; the browser on one Chrome version (Chromium 152 for 0.2.0, Chrome 156 for 0.3.0) with one GPU; tier-2 platforms with the English model only. Add older physical x86-64 CPUs, more GPUs, Firefox and Safari, and the multilingual and typed-decisions models on Linux arm64 and Windows.
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
