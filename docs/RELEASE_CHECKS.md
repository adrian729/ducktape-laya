# Validation and release checks

How to run each check and produce the evidence each release gate needs. The gates, tiers and acceptance criteria are defined in [QUALIFICATION.md](QUALIFICATION.md) and `release/qualification.json`; `scripts/check-candidate.py` decides whether a candidate passes all of them ([the candidate check](#the-candidate-check)). The release procedure that uses this evidence is [RELEASING.md](RELEASING.md).

Output directories must be new so earlier reports are not overwritten. Keep local outputs under ignored `build*/` paths and attach relevant results to the commit, PR or CI run; do not commit run reports. Models are supplied explicitly and checked against their pinned manifest before real inference; no runner downloads one.

## Native validation

```sh
npm ci --ignore-scripts --prefix tests/typescript
python3 scripts/validate.py --packages --output build-check-model-free
python3 scripts/validate.py --model /path/to/Laya-Q8_0.gguf \
  --extra-model /path/to/laya-multilingual-f16.gguf \
  --extra-model /path/to/laya-typed-decisions-q8_0.gguf \
  --packages --upstream-reference --output build-check-native
python3 scripts/check-reproducibility.py --output build-repro --jobs 2
```

The native runner configures/builds the library and addon, checks required CTest registration, runs native, Rust, Node, Python, Java, .NET and Go suites, and checks public exports. It also runs compiled C/C++/Rust ABI-layout checks, TypeScript consumers in CommonJS, ESM and bundler modes, artifact-identity and browser lifecycle/backend regressions. TypeScript test tools are development-only and installed explicitly with the locked command above. `tests/typescript/check.py --node-package NPM_TARBALL --browser-package BROWSER_TARBALL` additionally checks extracted package declarations. Reused candidate source/revision overrides are cleared, and built runtime diagnostics must match the approved compatibility record before any test gate is accepted. With a model, it also compares C, C++, Rust, Node, Python, Java, .NET and Go fixture outputs. `--packages` builds actual artifacts and runs `tests/package-smoke.py`, which needs no model: outside the checkout, a C program links the native tarball, the npm package loads its addon, the wheel imports its bundled library, and the Maven jar and the NuGet package load theirs, and each must report the recorded ABI version. It also reads the three jars' manifests (the main jar's version and module name, as `java.util.jar` sees them) and checks that the NuGet package loads its bundled library and never a copy in the current directory or on the search path. With a model, `--packages` also runs `tests/installed-packages.py`, which exercises the installed consumers with real inference. `--reproducibility` adds two independent builds; it is intentionally optional because it recompiles the runtime.

Every run retains commands, logs, exit codes and source/runtime/model/fixture/binary identities. `report.json` distinguishes selected checks from release readiness. Without a model, native real-model and binding-conformance gates stay `not_run`. `--upstream-reference` builds a separate test executable using upstream primitive processing and compares every fixture request, including batch members, without an HTTP server. It never links that processor into the product library. Structured requests are compared through their schema-derived primitive questions (`tests/reference_requests.py`); value projection remains covered by the shared-core contracts. Both sides receive structured values already serialized the way the original Laya writes them, so they render identical prompts. Without this option, the upstream-reference gate stays not run.

With a model, `tests/original-reference.py` also runs the original Laya implementation's recorded cases (`tests/original-reference/<model>/`, Apache-2.0, with provenance) through the C consumer at the checkpoint's trained sequence length: the `original_reference` gate. Input token counts must match exactly, and probabilities must stay within 0.05 with unchanged decisions. On near ties (the original's top two within 0.05) the decision may differ, since the model is undecided and quantization noise can reorder options; the probabilities are still gated. The original truncates over-long input by default, so its long-state and many-option cases run with `overflow: truncate_end`, and their `state_tokens` and `state_tokens_dropped` must match too. Cases for features the core does not support (the embedding shortlist) must still be refused; when one starts passing, it moves into the supported set.

`--extra-model PATH` (repeatable) qualifies an additional pinned model, one listed under `additional_models` in `release/compatibility.json` (the multilingual and typed-decisions models), matched to its pinned manifest by file hash. Each runs the `original_reference` gate against its own recorded cases and, with `--upstream-reference`, the upstream comparison. The results go to `additional_model_gates` in `report.json`. `check-candidate.py` requires both gates passed for every additional model, so a candidate cannot qualify without them. The primary model is still passed with `--model`.

The core bounds the temperatures stored in a model to [0.5, 5], as the original does; llama.cpp applies them unbounded. The upstream-reference checks therefore compare our probabilities re-expressed at the stored temperature (exact up to rounding, since probabilities are a softmax of scores over the temperature). Sanitizer, stress, browser and platform evidence remain separate; the runner does not silently transplant historical results to a different binary. This runner currently covers a subset of release gates and cannot approve a release by itself. A successful normal exit means its selected checks passed.

`tests/examples-smoke.py` (run by the validator on non-Windows hosts, model-free) checks that every `examples/*.json` and `examples/presets/*.json` request passes the library's request validation (through a router engine, which validates before it loads a model) and that `examples/decide.c` and `decide.cpp` compile and fail cleanly without a model.

The binding suites also test how each package finds its library. On Windows the Java suite (`windowsExtraction`) extracts and loads the bundled DLL, reuses an existing copy, honours `-Dducktape.laya.tmpdir` and falls back to a fresh owner-only directory; with `icacls` it adds an inheritable Everyone entry to the base directory before and after extraction (it must reach neither the shared directory nor the copy, while it propagates to an ordinary sibling directory), lets Everyone write the shared directory and copy (the loader must make them owner-only again), and, when the suite runs elevated (GitHub's runners are), makes SYSTEM own the copy (it must be rewritten) and the directory (it must be refused); unelevated it prints a SKIP line for those. Elsewhere the owner and ACL cases are skipped. On every platform it checks that stray entries in the shared directory are removed, that the directory name depends on the owner, and (Linux and macOS) that the private extraction directory is gone while the library stays mapped. The Node and Java suites test musl detection with simulated loader directories (`ld-musl-*` without `ld-linux-*`), and CI's `musl` job runs the assembled npm package and jar in Alpine containers (`tests/musl-packages.sh`): both must fail with status 7 and the glibc/musl message, and npm must refuse the Linux-only npm package. On Linux and Windows `tests/package-smoke.py` runs the NuGet consumer from a directory holding a copy of the library and with another copy on the search path (`PATH` on Windows, `LD_LIBRARY_PATH` on Linux): the process must map only the bundled `runtimes/<rid>/native/` library; with the bundled copy removed it must fail with a status-7 "was not found" `LayaException` on Windows (neither copy is searched) and load the `LD_LIBRARY_PATH` copy on Linux (never the current directory's). On Linux it also checks that the npm package's addon has a `DT_RPATH` (`$ORIGIN`, no `DT_RUNPATH`) and maps only its own library with another copy on `LD_LIBRARY_PATH`.

Three options shorten a development run and make its report incomplete. `--bindings LIST` (comma-separated, default `rust,node,python,java,dotnet,go`) runs only the listed binding suites; the others, and the checks that need every binding (`tests/parity.py`, so `binding_conformance`, and the installed packages), are listed under `skipped` and their gates stay `not_run`. `--skip-native-tests` leaves out `ctest` (the registration check still runs): `native` is listed under `skipped` and `native_contracts` stays `not_run`. `--compiler-launcher {none,ccache,sccache}` (default `none`) compiles through that cache, passed as `CMAKE_C_COMPILER_LAUNCHER` and `CMAKE_CXX_COMPILER_LAUNCHER` with `GGML_CCACHE=ON`, or with no launcher and `GGML_CCACHE=OFF`: ggml otherwise uses any `ccache` on `PATH` without saying so, so the runner always sets both. The build runs with `CCACHE_BASEDIR` empty, overriding a `base_dir` in the user's ccache configuration: `base_dir` hands the compiler relative source paths that `-ffile-prefix-map` does not map, which changes the library's bytes. After the build, the runner (and `package-native.py`) refuses a library or addon holding a compiled-in source path outside the mapped directories, that is one that does not begin, after an optional `./` (GCC keeps it, Clang drops it), with a top-level entry of the checkout or `build` (`release_support.require_mapped_source_paths`). `report.json` records `selection` (the bindings and whether the native tests ran) and `compiler_launcher`, and `build_options` the launcher variables. `--cmake-arg` cannot set the launcher variables or `GGML_CCACHE` (the runner refuses it, passes its own values last and, after configuring, checks what the CMake cache holds). `check-candidate.py` refuses a native report, the native matrix reports and the CI reports of every target that left out a binding suite, the cross-binding checks or the native tests, or that was compiled through a launcher (`compiler_launcher`, and in `build_options` the launcher variables, which must be empty, and `GGML_CCACHE`, which must be `OFF`); the release evidence is built without one. The other scripts that configure a build (`check-sanitizers.py`, `check-fuzz.py`, `check-reproducibility.py`, `check-upgrade.py`, `build-browser.py`) set no launcher and `GGML_CCACHE=OFF`; `check-stress.py` builds in the validated build directory and keeps its configuration.

With `--model`, the runner requires the `cpu-guard` test to be registered, which needs `qemu-x86_64` (and `python3`) on `PATH` when CMake configures; without QEMU a real-model run fails. It also runs `tests/download-model.py` and records the Rust, Go, Java, .NET and Python versions it found under `toolchains`.

## Continuous integration

`native.yml` ("Native checks") runs on every push and pull request: model-free builds with clang (Node 24) and gcc (Node 26) on Ubuntu 24.04 x86-64, gcc on `ubuntu-24.04-arm`, the clang-cl Windows x64 build and its independent-build check, each running `scripts/validate.py --packages` and its binding suites, the per-platform packages, their multi-platform assembly and its installation on every platform, the musl check of the packages in Alpine containers, and the parser sanitizer job below. `platforms.yml` ("Other platforms") builds and tests macOS arm64. Manual dispatch with `real_model` adds the pinned model download, the QEMU CPU guard and the real-model checks; dispatch with `candidate=true` is the release candidate run ([below](#release-targets-and-the-ci-candidate-run)). CI uses the runners' default `clang` and `gcc`, which need not be the compiler versions `release/qualification.json` lists; the `native_matrix` evidence may therefore combine local and CI reports, as long as together they cover every listed compiler and Node version and each is bound to the candidate. Workflow dependencies are pinned by commit; Rust and Node versions are explicit. Workflow definitions alone are not release evidence; CI does not publish packages or update dependencies.

How CI spends its time. A first `changes` job (`scripts/ci-changes.py`) compares the push with the previous `main` (a push to `main`), with the branch's merge base with `main` (a push to another branch) or with the pull request's base. When only documentation changed (`docs/`, `assets/` and the top-level notes such as `README.md` and `CHANGELOG.md`; not the package READMEs under `bindings/`, `THIRD_PARTY_NOTICES.md` or `patches/README.md`), the build jobs are skipped and a `docs` job runs `tests/docs-links.py`, `tests/qualification.py`, `tests/result-comparison.py` and `tests/ci-changes.py`; anything else, and every dispatch, runs everything. The Windows independent-build job runs on a push or pull request only when a library input changed (`src/`, `include/`, `cmake/`, `patches/`, `CMakeLists.txt`, `release/compatibility.json`, `.gitmodules`, `third_party/`, the Node addon's sources, or the job's own scripts and workflow), and on every dispatch except a candidate run, which reproduces inside the tier-2 validation jobs. A newer push to a branch other than `main` cancels that branch's older push run; runs on `main`, pull requests and dispatches are never cancelled. The build jobs use 4 parallel jobs (the Linux and Windows runners have 4 vCPUs; macOS 3) and, except in a candidate run, a compiler cache kept between runs (ccache through `hendrikmuhs/ccache-action` on Linux and macOS, sccache on Windows; keyed by OS, architecture, compiler and the hashes of `release/compatibility.json` and `.gitmodules`, restored from the newest entry of the same OS, architecture and compiler, 500 MB): ccache checks the compiler by content and has no `base_dir`, which would make source paths relative and change the objects' bytes. The independent-build (reproducibility) jobs never use a cache, and a candidate run builds every target without one (`--compiler-launcher none`), so its evidence depends on no earlier run. The cache cannot change the shipped bytes: a hit returns the object the same compiler made from the same preprocessed input and arguments, and the CI linux-x64 library and addon must still equal the release host's. Toolchain setup, dependency fetches and the binding suites take seconds each, so their package caches are not kept.

A separate model-free CI job instruments the shared library with ASan/UBSan, runs the model-free CTest suite and `tests/input-fuzz.cpp` (deterministic request/schema/config mutations, nesting and size boundaries); in parallel, another job builds `laya-input-fuzzer` in its own build directory and runs a 60-second coverage-guided libFuzzer smoke from the generated corpus. The clang validation job also checks that reconfiguring its finished build recompiles nothing and that an edited patched runtime copy is restored and recompiled alone (`tests/reconfigure.py --edit-copy`). The smoke exercises the harness; it does not meet the one-hour fuzz budget or replace real-model sanitizer runs. No sanitizer recovery or suppression is enabled.

## Performance budgets

The benchmark (`tests/benchmark.py`, one fresh process per run) records model open/close, first and warm p50/p95/p99 latency for small, structured, near-limit and batch requests, two engines in concurrent threads, router cold loads with evictions, cancellation response and deadline overshoot, RSS, descriptors and threads. Repeated outputs must match and descriptors/threads must return to their starting counts. Budgets are frozen per profile, model, thread count and reference CPU from at least three independent runs, then candidates are checked against them:

```sh
python3 tests/benchmark.py /path/to/Laya-Q8_0.gguf --report build/bench-1.json  # repeat per run
python3 scripts/performance-budgets.py derive build/bench-{1,2,3}.json --output release/budgets/native-performance.json
python3 scripts/performance-budgets.py check build/bench-candidate.json
```

Limits are the worst baseline value times a declared headroom (1.5 for time, 1.25 for memory); leaks have none. The committed budgets in `release/budgets/native-performance.json` were frozen from a 0.1.0-era baseline (source commit `baaf568`, an ancestor of `v0.1.0`); a 0.2.0 build still met them on 2026-10-21. Each additional model has its own budget, named by `performance_budgets` in its manifest (`release/budgets/native-performance-<model id>.json`, derived 2026-10-21 from three 0.2.0 runs); `tests/benchmark.py` takes any pinned model, and check-candidate reads the benchmark report for each additional model from the evidence key `performance_<model id>`. They detect regressions on the reference host, not portable latency promises. Run without competing builds or inference and keep the reports as CI/PR artifacts.

## Stress and soak

The native stress target supports a bounded round smoke test and elapsed-time qualification runs. Configure first, then explicitly build this optional target:

```sh
cmake -S . -B build -DDUCKTAPE_BUILD_TESTS=ON
cmake --build build --target laya-stress --parallel 2
build/laya-stress /path/to/Laya-Q8_0.gguf 10
build/laya-stress /path/to/Laya-Q8_0.gguf --seconds 3600
# Candidate soak uses --seconds 28800 instead.
```

Each duration run completes at least ten rounds, then runs the concurrent-caller check. Every round exercises open/use/close, malformed input, deadline recovery, a cancellation inside the forward pass (at a should_cancel poll counted to fall in the middle of the graph, so ggml abandons it) and the reuse of that context, and concurrent callers on one handle with two inference threads; periodic rounds add mixed batches, an independent second engine, and a router whose two hard-linked model paths (created in the working directory, which must share the model's filesystem) force cold loads and evictions. After every close, descriptors/threads must return to baseline and RSS must stay within the declared 128 MiB allocator retention allowance. From 40 rounds, the last quarter's median closed RSS must not exceed the second quarter's by more than 32 MiB, so slow leaks inside that allowance fail long runs. Progress goes to stderr; the final JSON on stdout includes elapsed time and resource measurements. A short smoke run does not satisfy the one-hour or eight-hour gates, and this native test does not cover browser memory.

## Long and instrumented gates

Identity-bound runners write a `report.json` with source commit, build and model identities for the remaining long or instrumented gates:

```sh
python3 scripts/check-stress.py /path/to/Laya-Q8_0.gguf --build build-validation --output build-stress  # --seconds 28800 for soak
python3 scripts/check-sanitizers.py /path/to/Laya-Q8_0.gguf --output build-sanitizers
python3 tests/rollback.py LKG_PACKAGES CANDIDATE_PACKAGES /path/to/Laya-Q8_0.gguf --report build/rollback.json
node tests/electron/run.cjs PACKAGES/ducktape-laya-0.2.0.tgz /path/to/Laya-Q8_0.gguf build/electron.json
python3 scripts/check-candidate.py build-candidate/evidence.json --candidate COMMIT --report build-candidate/gates.json
```

The sanitizer runner builds ASan+UBSan and TSan configurations, runs every registered native test with the model (allocation-failure injection is skipped under sanitizer runtimes) and a short stress run. The `sanitizers` gate also lists the coverage-guided fuzz campaign (`scripts/check-fuzz.py`, below) among its checks; its report is the separate `fuzz` evidence.

The rollback rehearsal switches an installed application from retained last-known-good packages to the candidate and back, requiring the LKG library and byte-identical outputs after rollback. Make `LKG_PACKAGES` from the previous GitHub release: `gh release download vX.Y.Z -D build/lkg-assets` and `python3 scripts/release-assets.py retain build/lkg-assets --output build/lkg-packages`, which restores the native package directory (`inventory.json` and its artifacts) and verifies it. When both sides have the same ABI, one consumer binary must work with both. Across an ABI break (0.1.0 to 0.2.0), an application must rebuild, so each side runs a consumer built from its own packaging source commit against its own headers.

Exact native results on a second physical CPU are checked with one unchanged binary. A manually dispatched real-model CI run bundles its built library and C conformance consumer, records raw fixture outputs with the runner's CPU identity and uploads both. Download that artifact and run the same bundle on a different CPU:

```sh
python3 tests/cross-host.py record --bundle ARTIFACT/cross-host/bundle \
  --model /path/to/Laya-Q8_0.gguf --report build/cross-host-local.json
python3 tests/cross-host.py compare ARTIFACT/cross-host/record.json build/cross-host-local.json
```

Comparison requires identical bundle, model and fixture hashes, distinct CPU identities and byte-identical outputs. It covers that build's ISA profile only.

## Fault injection and the CPU guard

`real-faults` (`tests/faults.cpp`, Linux, registered with a test model) feeds damaged models to `dtl_open`: empty, non-GGUF and unreadable files, a directory, truncation from the header to the last byte, and in-place corruption of the magic, version, tensor/metadata counts, a string length, the decision type, temperatures and the head budget. Each must return `DTL_MODEL_ERROR` with a message and no descriptor leak; corrupted tensor bytes are not detectable without the host's SHA-256 check. It then injects allocation failure: model loading under an address-space limit, and failing the Nth C++ allocation of `dtl_execute` (every point through 384, then every 97th) and of `dtl_open` (geometric points). Request failures must return `DTL_OUT_OF_MEMORY`; failures absorbed internally must still produce exact results; the engine must keep returning exact results afterwards. `real-c-faults` (same executable, `--c-allocations`; not in sanitizer builds) fails each C allocation (`malloc`, `calloc`, `realloc`, `posix_memalign`, `aligned_alloc`, `memalign`) of one `dtl_execute` and one `dtl_open`, each in its own child process. Every point must end in an exact result, `DTL_OUT_OF_MEMORY`/`DTL_MODEL_ERROR`, or `SIGABRT` from ggml's own allocation checks; any other signal fails (an unchecked NULL). At least one inference point must reach the caller as `DTL_OUT_OF_MEMORY` and leave the engine returning exact results.

`real-runtime-faults` (same executable, `--runtime-faults`) fails work inside a forward pass, each in its own child process: worker-thread creation under `RLIMIT_NPROC` and, outside sanitizer builds, the CPU work buffer's growth for a request larger than the baseline. Each call must return `DTL_OUT_OF_MEMORY` and the engine must then return exact results for smaller and larger requests; after the failed growth, the next smaller request must allocate a new work buffer instead of reusing the released one. Where `RLIMIT_NPROC` is not enforced (root), that check cannot run: the test exits 77, which CTest reports as skipped (never as passed) and `validate.py` records under `skipped`. `ggml-faults` (`tests/ggml-faults.cpp`, Linux, model-free) checks the same two failures directly on ggml, plus a refused first, second or third worker thread (the started ones must be joined). `tokenizer-partition` (`tests/tokenizer.cpp`, model-free, on upstream's vocabulary-only test files) and `real-tokenizer` (the test model's vocabulary) compare token sequences with `tests/fixtures/tokenizer-partition.txt`, recorded with the unpatched runtime, and check that tokenization time grows linearly with the number of special-token matches (4 KiB against 128 KiB of matches: linear growth is x32, the limit x96, the quadratic partition about x1000). A vocabulary whose 4 KiB sample already takes over 0.25 s is checked for tokens only and prints a `SKIPPED:` line, which `validate.py` records. `real-control-tokenize` (`tests/control-tokenize.cpp`, every platform, registered with a test model; further models may be passed after it) checks in the calling thread's CPU time that a deadline or cancellation at a tenth of a 1 MiB state's tokenization ends the call within half of it for text that special tokens split into short pieces (double-spaced English and long words; newline-separated lines for the additional models), and that a cancellation callback calling another engine during tokenization leaves the call cancellable. `real-decision-head` compares the selected decision head's scores with the full graph's. With `--extra-model`, `validate.py` also runs the tokenizer golden entries and the decision-head comparison on each additional model and records them under `additional_model_gates` (`tokenizer`, `decision_head`); `check-candidate.py` requires both for every additional model.

`real-control` (`tests/control.cpp`) checks cancellation and deadlines. It counts the `should_cancel` polls of a call (the library's own checks, tokenization, and ggml's abort callback after every graph node), takes the forward pass's share from the difference between a one-window and a multi-window `scan` request with the same tokens, and stops calls at chosen polls inside the graph: a cross-thread cancel held at the middle poll, a one-shot cancel that must stay latched (and end the pass early: the time from the stop to the return must be under half the rest of a full pass), and a deadline expiring at the middle poll. Each must end with its own status at exactly that poll and leave the context giving unchanged results. Separately, deadlines and cancellations at a tenth of a 1 MiB state's tokenization time must stop tokenization within half of it (fastest of three calls each). On Linux it also checks the automatic thread count: with the affinity mask cut to one CPU, an engine opened with `threads` 0 starts no worker threads during a pass (counted in `/proc/self/task`, against one worker for `threads` 2).

`cpu-guard` (`tests/cpu-guard.py`, x86-64, registered with a test model when `qemu-x86_64` and `python3` are found; not in sanitizer builds) runs a C probe and the Node binding under QEMU user-mode CPU models. Nehalem (no AVX) and Sandy Bridge (AVX, no AVX2/FMA/F16C/BMI2) must return `DTL_UNSUPPORTED_PLATFORM` naming the first missing feature, with `dtl_info` listing every missing one. QEMU executes AVX-family instructions regardless of the emulated CPU, so the probe runs with execution logging and each executed block is decoded: no instruction from a feature the emulated CPU lacks may run. An emulated CPU with every feature must open the model, and the scan must see AVX/AVX2 there (positive control). It does not replace a run on physical older hardware.

## ThreadSanitizer and fuzzing

ThreadSanitizer covers the native library, llama.cpp/ggml (its own pthread pool; OpenMP is disabled) and the native test executables, including the real-model suites and the stress loop's concurrent callers, second engines and router evictions, and the Rust binding's tests (one engine and one router shared across threads, cancellation set from another thread, close on a different thread). It does not cover the Node worker threads. No suppressions:

```sh
CC=clang CXX=clang++ cmake -S . -B build-tsan -DCMAKE_BUILD_TYPE=RelWithDebInfo \
  -DDUCKTAPE_BUILD_TESTS=ON -DDUCKTAPE_BUILD_NODE=OFF -DDUCKTAPE_TEST_MODEL=/path/to/Laya-Q8_0.gguf \
  '-DCMAKE_C_FLAGS=-fsanitize=thread -fno-omit-frame-pointer' \
  '-DCMAKE_CXX_FLAGS=-fsanitize=thread -fno-omit-frame-pointer'
cmake --build build-tsan --parallel 2 && cmake --build build-tsan --target laya-stress
TSAN_OPTIONS=halt_on_error=1 ctest --test-dir build-tsan --output-on-failure
TSAN_OPTIONS=halt_on_error=1 LD_LIBRARY_PATH=build-tsan build-tsan/laya-stress /path/to/Laya-Q8_0.gguf 10
```

Rust needs a nightly toolchain with `rust-src` (`rustup toolchain install nightly --component rust-src`) and the instrumented library from the build above:

```sh
RUSTFLAGS='-Zsanitizer=thread -Clink-arg=-Wl,--export-dynamic' \
  DUCKTAPE_LAYA_LIB_DIR=build-tsan LD_LIBRARY_PATH=build-tsan LAYA_MODEL=/path/to/Laya-Q8_0.gguf \
  cargo +nightly test --locked -Zbuild-std --target x86_64-unknown-linux-gnu --lib \
  --manifest-path bindings/rust/Cargo.toml
```

`--export-dynamic` exports the runtime's interceptors to the shared library as a Clang link does; without it, guarded static initialization inside the library (`__cxa_guard_*`) is reported as a race. `scripts/check-sanitizers.py` runs this.

For coverage-guided request/planner/router fuzzing, use a separate Linux Clang build. The optional target instruments the shared core and links libFuzzer only into the test executable. It needs no model; lazy inference is directed at a known non-model file. Generated/mutated corpus files and crash reproducers belong in ignored build directories. Preserve minimized regressions in tests after triage.

```sh
CC=clang CXX=clang++ cmake -S . -B build-fuzzer \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo -DDUCKTAPE_BUILD_FUZZERS=ON \
  -DDUCKTAPE_BUILD_TESTS=OFF -DDUCKTAPE_BUILD_NODE=OFF
cmake --build build-fuzzer --target laya-input-fuzzer --parallel 2
python3 tests/fuzz-corpus.py build-fuzzer/corpus
ASAN_OPTIONS=detect_leaks=1:strict_string_checks=1 \
UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 \
  build-fuzzer/laya-input-fuzzer build-fuzzer/corpus -seed=444211238 \
  -max_total_time=3600 -max_len=1048577 -timeout=30 \
  -artifact_prefix=build-fuzzer/ -print_final_stats=1
```

This covers instrumented library parsing, planning and router boundaries; it does not claim coverage-guided testing of upstream tensor kernels or model loading. Do not package the instrumented library. Short smoke runs exercise the harness; they do not satisfy the one-hour qualification budget.

Use the campaign runner to retain identities, seeded corpus, commands, statistics and failures for qualification:

```sh
python3 scripts/check-fuzz.py --build build-fuzzer --seconds 3600 \
  --output build-fuzz-campaign
```

It requires an explicitly configured fuzz build, resets runtime overrides to the approved pin, rebuilds the target, verifies shared-library coverage instrumentation, and bounds runtime and memory. A shorter `--seconds` smoke can pass while `qualification_budget_met` remains false. Campaign success alone never makes `release_ready` true. Do not modify its build directory during the run.

## Installed packages

Installed C/C++, pkg-config, npm, Rust, Python wheel, Maven, NuGet and Go consumers must perform real-model inference outside the checkout and agree exactly on outputs (`tests/installed-packages.py --report` records each consumer's output hash).

Rust packages contain library source, metadata and license, excluding demos. The sys crate locates an installed native library using `DUCKTAPE_LAYA_LIB_DIR`, or `pkg-config` when the variable is absent. It no longer assumes a monorepo `build/` directory. Crates express their sibling dependency by version in the published manifest. Before publication, the artifact consumer test uses a local Cargo patch to the extracted sys crate, never a path into this repository.

The reproducible Electron integration consumer lives under `tests/electron`:

```sh
npm ci --prefix tests/electron
node tests/electron/node_modules/electron/install.js
node tests/electron/run.cjs build/packages/ducktape-laya-X.Y.Z.tgz /path/to/Laya-Q8_0.gguf build/electron.json
```

Only the test setup downloads Electron; applications do not acquire this test dependency. The test installs the actual tarball into a temporary app, uses a pinned ASAR packager, and removes the app after both runs. Compare both outputs and validate every Electron version advertised by a release.

### Linux userspaces

```sh
python3 tests/download-userspaces.py --output build-platforms
python3 tests/linux-userspace.py ARTIFACT_DIR MODEL \
  build-platforms/ubuntu-24.04.5/rootfs build-platforms/ubuntu-26.04.1/rootfs \
  --report build/userspaces.json
```

Provisioning is an explicit maintainer download. `tests/linux-userspaces.json` pins official Ubuntu image URLs, hashes and the image-signing fingerprint. The provisioner verifies the checksum signature with a trusted local Ubuntu keyring and the pinned archive hash before extraction; it records image provenance. Device entries are omitted and absolute symlinks become equivalent relative links inside the extracted root. No system packages are installed on the host.

The consumer runner verifies the artifact inventory and approved model hash, retains distinct records for every root (even when directory names match), and requires exact ordered result agreement. Supply locally verified root filesystems when using other images; missing provenance is explicit in the report. It uses Bubblewrap with read-only artifact/model mounts, each userspace's own runtime libraries and network access disabled. It executes an installed C consumer and compares results. This tests userspace loading/inference, not a different host kernel, physical CPU or building all bindings inside those distributions. Keep run results with CI/PR artifacts and define the release matrix from those checks.

### SBOM and inventory

Each packager emits a checksum-covered CycloneDX 1.6 component inventory (`sbom.cdx.json`). Native binaries are inspected through the C ABI before packaging, so a stale version/runtime/patch identity is rejected, and the library's runtime source record must match the runtime tree, so a library built from a modified and since restored llama.cpp tree is rejected too ([build records](BUILDING.md#build-records); `tests/packaging-identity.py` simulates the modify, build and restore sequences). Browser packages include Emscripten, musl, LLVM runtime and Dawn notices in addition to shared runtime notices. The inventory separates incorporated components from host system libraries, host browser/GPU drivers, excluded model weights and test-only tools. Checksums detect changes; the release's attestation establishes who published the files ([RELEASING.md](RELEASING.md#provenance)).

## Reproducible builds

Independent reproducibility compares the bytes, permission bits and symlink targets of all installed native and Node files from a single captured working-tree snapshot compiled in two separate source/build/install roots. Runtime source is archived from its recorded commit and patches are hash checked. The report identifies dirty source and hashes the actual snapshot. The guarantee is for the same toolchain and options; it does not assert identical output across compilers or architectures. Packaging determinism is a separate check for identical prebuilt inputs.

`scripts/check-reproducibility.py --output build-repro-new` compiles one captured source snapshot in two independent source/build/install roots and compares all installed native/Node bytes, modes and symlink targets. Source/build paths are normalized; generated GGML identity uses the explicit runtime pin. Keep Node build/install RPATHs relative to `$ORIGIN`, and package the installed addon. Installed consumer tests reject absolute library search paths.

On Windows, clang-cl builds with `/Brepro` (no timestamps in objects; a content hash instead of the link time in the DLL, addon and import library), and CI runs the same check from a Visual Studio x64 developer prompt:

```bat
python scripts/check-reproducibility.py --output build-repro --cc "%CLANG%" --cxx "%CLANG%" --node-include "%NODE_INCLUDE_DIR%" --cmake-arg=-GNinja "--cmake-arg=-DNODE_LIB=%NODE_LIB%" "--cmake-arg=-DDTL_PATCH_EXECUTABLE=C:/Program Files/Git/usr/bin/patch.exe"
```

This check covers identical inputs and toolchains, not different compilers or architectures. Across hosts, the Linux x86-64 library and Node addon built by CI (`ubuntu-24.04`, gcc 13.3.0, Node 26.7.0 headers) are byte-identical to the release host's build of the same commit; the release procedure compares their hashes (`library_sha256`, `addon_sha256`) and stops when they differ. Keep its reports in CI/PR artifacts or ignored build directories. A reproducible build is one release requirement, not a substitute for runtime, platform or installed-consumer checks.

Native packaging rejects binaries exposing sanitizer, libFuzzer coverage or LLVM profile instrumentation symbols (on Windows and macOS also binaries that load a sanitizer or debug runtime) before loading them. Use separate diagnostic builds for testing and rebuild uninstrumented release artifacts for distribution.

The DLL's bytes depend on the runner image, not only on the sources: it links the image's MSVC and Windows SDK libraries (UCRT, vcruntime), and two images built different, individually reproducible DLLs from one commit. Package inventories and reports therefore record `build_environment` (`ImageOS`, `ImageVersion`, `VCToolsVersion`, `WindowsSDKVersion`, `VSCMD_VER`), and the reproducibility check must run on the image of the build it validates; it rejects a reproduction from another environment. An ordinary Linux host sets none of these variables, and Linux x86-64 builds with the same compiler and Node headers matched across hosts (below).

## Browser

Browser release checks must cover primitive/schema/batch outputs, ordered events, invalid/closed calls, asset budgets, route aliases, pre-cancellation, deadlines, cancelling/idempotent shutdown and explicit WASM initialization fallback. Include missing/corrupt assets, hash mismatch, initialization aborts, device loss, worker failure, memory pressure and deployment of the extracted package. Capture the browser version, GPU/driver, launch flags, model identity and build configuration; flags that enable WebGPU experimentally do not establish default browser support.

`tests/browser.html` and `tests/browser-server.py` provide the browser test harness. Export its `window.state` after completion to a local report. Compare against a native run of the same model and fixtures, not a historical checked-in result:

```sh
python3 scripts/validate.py --model /path/to/Laya-Q8_0.gguf --output build-check-native
python3 tests/compare-browser.py build/browser-results.json --native build-check-native/outputs.json
```

`validate.py` writes the native fixture results to `outputs.json` (from `tests/parity.py --report`); `check-candidate.py` accepts browser drift only against that file of the candidate's native run. The comparator requires matching fixture sets, native key order mapped to JavaScript property order, a responsive main-thread heartbeat, ordered events and finite values. It reports numeric drift and changed choice/structured decisions without a cross-profile tolerance gate. Metadata, token counts and result shapes remain exact. `tests/compare-browser-reference.py` separately compares with unchanged upstream decision processing on the same backend.

For repeatable real-browser runs, install the locked developer tools and use an installed Chrome. Build both backend reference probes with `--reference` first, and the constrained WASM test build for the `memory` stage:

```sh
python3 scripts/build-browser.py --emsdk EMSDK --backend wasm --maximum-memory 469762048 --build build-browser-wasm-constrained
npm ci --ignore-scripts --prefix tests/browser-automation
node tests/browser-automation/run.mjs --model /path/to/Laya-Q8_0.gguf \
  --native build-check-native/outputs.json --constrained-wasm build-browser-wasm-constrained --output build-browser-check
```

The runner starts its own loopback-only harness, verifies the model, runs both backends, upstream comparisons, lifecycle/assets/device-loss checks, WebGPU allocation failure (`gpu_memory`: a test worker fails chosen GPU buffers; opening and a router's model-loading request must reject with status 2, a failed weights buffer while loading the model and a failed compute buffer while creating the inference context (checked by their messages), the router must keep answering unchanged, and the page must then open a working engine), and closes the browser and server afterward. It records browser/platform, model, fixture and asset identities. Same-backend upstream failures cause a failed exit; cross-profile drift is retained as measured evidence, not probability equivalence. It defaults to headed Chrome without custom launch flags; use `--headless` only when that environment provides the intended GPU. `--isolated` adds COOP/COEP for shared-memory controls. `--package-dir EXTRACTED_PACKAGE` tests shipped assets; `--backend wasm|webgpu` selects a backend. Explicit `--skip-reference`/`--skip-controls` leave those checks not run. `--constrained-wasm BUILD` adds a WASM `memory` stage using a test build with a lowered heap ceiling (`scripts/build-browser.py --maximum-memory 469762048`, 448 MiB): opening an engine and a router's first request there must reject, never hang or succeed, and the page must then open a working engine with the product build. `check-candidate.py` requires it in both browser reports, like the WebGPU `gpu_memory` stage. Packaging rejects builds whose ceiling is not 4 GiB. Selected-check success is never full release approval.

Store run results in ignored build directories or CI/PR artifacts. Broader GPU, physical CPU, isolated-page cancellation and minimum-browser coverage are not covered yet ([roadmap](ROADMAP.md)).

## Security review

The `security_review` gate has no runner: the maintainer reviews the threat boundaries in `SECURITY.md`, the pinned dependencies' advisories and the release permissions, and writes `security_review` evidence by hand as a JSON object that `check-candidate.py` validates (`release_support.security_review_problems`):

| Field | Required value |
|---|---|
| `source_commit` | The candidate commit (or, for the hours-long reuse rule, an ancestor with no input change since) |
| `source_tracked_dirty` | `false` |
| `runtime_commit` | The runtime pin in `release/compatibility.json` |
| `runtime_patches` | The SHA-256 of each runtime patch in that record, in order |
| `release_blocking_code_issues` | A list; it must be empty to pass |
| `model_licence` | A non-empty string recording that the model and component licence obligations were reviewed (the `license_inventory` gate reads it) |

Other fields (notes, advisory references) are free-form and not checked: the gate proves the review was recorded against this candidate, not what it concluded.

The `model_upgrade` gate accepts `comparison.status` of `passed` or `failed` by design: a changed model is expected to change outputs, so it requires the candidate-model checks (`native_contracts`, `binding_conformance`, `abi`) to pass and the comparison to complete, not to match the baseline.

## The candidate check

`check-candidate.py` takes a JSON map of evidence names to report paths and writes `gates.json`:

```sh
python3 scripts/check-candidate.py build-candidate/evidence.json --candidate COMMIT --report build-candidate/gates.json
```

Tier-1 evidence names and the commands that produce them (paths as used in a candidate directory `build-candidate/`; `MODEL`, `ML` and `TD` are the English, multilingual and typed-decisions files):

| Evidence | Produced by |
| --- | --- |
| `native` | `CC=gcc CXX=g++ python3 scripts/validate.py --model MODEL --extra-model ML --extra-model TD --packages --upstream-reference --output build-candidate/native` |
| `native_matrix` | A JSON list of `report.json` paths that together cover every compiler and Node version in `release/qualification.json`: the gcc run above, the same run with clang, and the CI clang report; each a complete validation without a compiler cache |
| `browser_package`, `browser`, `browser_isolated` | The browser package's `inventory.json` and the two `tests/browser-automation/run.mjs` reports ([browser](#browser)) |
| `userspaces`, `electron`, `rollback`, `installed_release` | [Installed packages](#installed-packages), run on the CI-assembled packages ([CI candidate run](#release-targets-and-the-ci-candidate-run)) |
| `cross_cpu` | `tests/cross-host.py compare` output ([above](#long-and-instrumented-gates)) |
| `sanitizers`, `fuzz` | `scripts/check-sanitizers.py`, `scripts/check-fuzz.py --seconds 3600` |
| `stress`, `soak` | `scripts/check-stress.py` with `--seconds 3600` and `--seconds 28800` |
| `reproducibility` | `scripts/check-reproducibility.py` ([reproducible builds](#reproducible-builds)) |
| `performance`, `performance_<model id>` | `tests/benchmark.py` per model on an idle host, checked against its budget ([performance budgets](#performance-budgets)) |
| `upgrade` | `scripts/check-upgrade.py --runtime COMMIT --model MODEL` ([UPGRADES.md](UPGRADES.md)) |
| `model_upgrade` | `scripts/check-upgrade.py` with `--candidate-model` and `--candidate-manifest` |
| `security_review` | The hand-written review ([security review](#security-review)) |

`scripts/fetch-ci-evidence.py` adds the candidate run's evidence (next section). Binding rules:

- A gate passes only when its evidence passed and is bound to the candidate commit, the validated library and packages, the browser package assets and the pinned model; missing evidence is `not_run`.
- In particular: the security review must name its `source_commit` and `source_tracked_dirty` (false) like other reports; the cross-CPU bundle must contain the candidate library; the rollback's previous side must be an earlier release whose source is an ancestor of the candidate; the native packages must be built from the candidate's source identity; browser runs must use a Chrome version listed in `release/qualification.json` and, for WebGPU, listed non-fallback hardware; and each reproduced build must contain the candidate library and Node addon (run `check-reproducibility.py` with the Node headers of the validated build).
- The checks that take packages (`installed_release`, `userspaces`, `electron`, `rollback`, the licence texts of every platform archive) use the release packages the candidate run assembled, which are what is published; `artifact_integrity` also requires the CI linux-x64 library and Node addon to be byte-identical to the locally validated ones, every release package to carry exactly each target's fragment payload, and the CI linux-x64 fragment to equal this host's own `package-native.py` run (`build-candidate/native/packages`) entry by entry (headers, CMake and pkg-config files, Python, Java and C# code, crates, SBOM; compared by entry name and sha256, not container bytes, because deflate differs between hosts; three host-toolchain stamps cannot be equal and are normalized or partly compared, each listed under `package_comparison` in `gates.json`: the `cmake_policy(VERSION 2.8.3...X)` line CMake writes into `ducktape_laya-targets.cmake`, the `.NET` runtime version in the NuGet `nuget.psmdcp` `lastModifiedBy`, and the javadoc jar, whose HTML depends on the exact JDK (CI pins Temurin 25.0.4), so only its manifest and the API page names are compared; measured on a real candidate run, every other entry, class files and DLLs included, was equal).
- The `electron` evidence must be the `linux-x64` run of the candidate `ducktape-laya-X.Y.Z.tgz`, and `installed_release` must be this host's own linux-x64 run (its `host` field: Linux x86-64, not GitHub Actions, a hostname other than the CI report's), not CI's `installed.json`.
- A candidate whose own release record or targets are invalid (empty `required_gates`, unknown gate, missing workflow) is rejected before any gate runs, and `ci-run.json` must name `adrian729/ducktape-laya`.
- Hours-long evidence (`soak`, `stress`, `fuzz`, `sanitizers`, `performance`, `upgrade`, `model_upgrade`) and the `security_review` may come from an ancestor commit when the diff since touches no library input (`src/`, `include/`, `cmake/`, `patches/`, `CMakeLists.txt`, the runtime record and pin, the model manifest) and none of that gate's own inputs: for the security review the binding code and package manifests (`bindings/**/package.json` included), `scripts/` and `.github/`; for fuzz its harness and `tests/fixtures/` (the corpus seeds); for sanitizers the native tests, their fixtures and `tests/original-reference/routing/`; for the others their harness files (`HARNESS` in `scripts/check-candidate.py`). Soak, stress and performance must also have run on the candidate's exact library bytes, and stress, soak and fuzz must meet the candidate's own budgets (`requested_seconds` against `routine_stress_seconds`, `candidate_soak_seconds` and `fuzz_seconds_per_target` of its `release/qualification.json`). Everything else (native validation, packages, browser runs, reproducibility, install checks, all tier-2 evidence) is produced from the candidate itself. The report lists reused evidence under `reused_evidence`.
- The release record, policy, budgets, notices and source identity are read from the candidate commit's own tree, so later tooling commits cannot alter what is checked.
- `release_ready` requires every gate of every release target. Publication is a separate step ([RELEASING.md](RELEASING.md)).

## Release targets and the CI candidate run

`release/compatibility.json` lists the release `targets` (schema 2). `linux-x64` is tier 1: every gate above, qualified on this host. `linux-arm64` and `windows-x64` are tier 2: CI-tested on `ubuntu-24.04-arm` and `windows-2025`, primary (English) model only, with the gates in `release/qualification.json` `target_gates`: `native_contracts`, `binding_conformance`, `upstream_reference`, `original_reference` (Windows may use only its documented `json_escaping/spam` deviation, capped at 0.06), `abi`, `installed_packages` (the target's own packages and the assembled release packages, Electron as directory and ASAR), `artifact_integrity`, `reproducibility`, `short_stress` (`budgets.short_stress_seconds`, 600 s) and `cross_profile_drift` (drift against the tier-1 profile reported; any changed decision fails). Each target names its numerical profile, compiler and math library: Windows reports the same runtime profile label as Linux x86-64 (`native-cpu:avx+avx2+fma+f16c+bmi2+sse4.2`), but it is a clang-cl/UCRT build whose results differ slightly (see [COMPATIBILITY.md](COMPATIBILITY.md#known-deviations)); the record carries the distinction, the runtime label does not. Editing `release/compatibility.json` is a library input change: it invalidates reuse of hours-long evidence, so land record edits before a candidate's long gates start.

This host builds only Linux x86-64. The packages of every target, and the tier-2 evidence, come from one CI candidate run on the exact candidate commit: `gh workflow run native.yml --ref BRANCH -f candidate=true` (the commit must be the branch head; `gh workflow run` cannot target a SHA). The run validates every target with `scripts/validate.py` and the real model, packages each target's fragment, assembles the multi-platform release packages and installs them with the real model on every target's runner (including Electron), runs `scripts/check-stress.py --budget short` and `scripts/check-reproducibility.py` in each tier-2 target's validation job, and keeps for 30 days: `evidence-<platform>` (validation report, `outputs.json`, reference reports), `packages-<platform>` (fragments), `packages-assembled` (the release packages), `installed-<platform>-real-model` (`installed.json`, `electron.json`), `stress-<platform>`, `reproducibility-<platform>` and the usual `native-*` artifacts. Rerun only failed jobs, and only for environmental failures; reruns replace the job's artifacts. The reports record their toolchain environment (`build_environment`: the runner image and, on Windows, the MSVC toolset and Windows SDK). The reproduction must run in the validated build's environment: the Windows DLL links the image's MSVC and SDK libraries, and on 2026-10-22, during a `windows-2025` image rollout, a job on image 20261021.278.1 built a different (self-consistent) DLL from the validated one built on 20261007.264.1. The checker rejects a reproduction from another environment.

`python3 -I scripts/fetch-ci-evidence.py --candidate COMMIT --output build-candidate/ci --merge build-candidate/evidence.json` finds the newest successful complete candidate run of the commit (or takes `--run`), requires its head commit, `workflow_dispatch` event, success, workflow file and repository, downloads each artifact, checks the zip against GitHub's sha256 digest, extracts it without links, absolute or `..` paths, and writes `ci-run.json` (the run and the sha256 of every file) and the evidence keys: `ci_run`, `release_packages`, and per target `<platform>:native`, `<platform>:fragment`, `<platform>:installed`, `<platform>:electron`, plus `<platform>:stress` and `<platform>:reproducibility` for tier 2. `check-candidate.py` stays offline: a CI file that is not listed in `ci-run.json` or has changed since fails its gate, as does a run of another commit or event. It warns when an artifact expires within 7 days; expired artifacts mean a new candidate run.

The CI linux-x64 library and Node addon must be byte-identical to this host's `validate.py` build of the same commit (gcc, Node 26.7.0 headers on both sides); check `library_sha256` and `addon_sha256` of `build-candidate/native/report.json` against `build-candidate/ci/evidence-linux-x64/report.json` as soon as both exist. A difference is not environmental: stop and report (a toolchain or Node header change on one side). Then, on this host, run the package checks on the shipped packages and the drift reports:

```sh
python3 tests/installed-packages.py build-candidate/ci/packages-assembled MODEL --platform linux-x64 --report build-candidate/installed-release.json
node tests/electron/run.cjs build-candidate/ci/packages-assembled/ducktape-laya-X.Y.Z.tgz MODEL build-candidate/electron.json
python3 tests/linux-userspace.py build-candidate/ci/packages-assembled MODEL ROOTFS... --report build-candidate/userspaces.json
python3 tests/rollback.py LKG_PACKAGES build-candidate/ci/packages-assembled MODEL --report build-candidate/rollback.json
python3 tests/compare-profiles.py build-candidate/native/outputs.json build-candidate/ci/evidence-windows-x64/outputs.json --target-platform windows-x64 --report build-candidate/drift-windows-x64.json
```

and add `installed_release`, `electron`, `userspaces`, `rollback` and `<platform>:drift` (one per tier-2 target) to the evidence map. `gates.json` (schema 2) keeps the tier-1 gates under `gates` with their meaning unchanged, adds `targets` (`{platform: {tier, gates, ci_run}}`, the tier-1 target repeating the release-level statuses), records the release packages' inventory as `inventories_sha256.native`, and names the candidate run as `ci_run` (repository, workflow, run id and attempt, head commit), which `attest-release` checks the published native inventory against. `checker_tree_dirty` is true when the checker's checkout has tracked changes, or any untracked file under `scripts/` other than bytecode caches (the checker imports its modules from there); a dirty checker's report is never `release_ready`.

`scripts/validate.py` runs on every target; on Windows pass `--node-lib` and the clang-cl build through `--cmake-arg` (see the `windows` job in `native.yml`). Checks that do not apply are listed under `skipped` in `report.json` with the reason, never left out silently: fault injection (`real-faults`, `real-c-faults`, `real-runtime-faults`, `ggml-faults`) is Linux-only (prctl, rlimits), the QEMU CPU guard is Linux x86-64 only (recorded as skipped on Windows and on Linux arm64), and the release checker and asset tests run on the Linux host only. Tests that CTest reports as skipped, and checks inside a passing test that print a `SKIPPED:` line (read from CTest's JUnit output, `ctest-junit.xml`), are recorded there too. The report also records `platform`, `addon_sha256` and the registered native tests. On Windows the stress test measures committed private bytes (`PrivateUsage`) instead of `VmRSS`, the process handle count instead of descriptors, and threads from a Toolhelp snapshot excluding the workers Windows starts for its own thread pools (threads whose start address is in `ntdll.dll`: the loader's three workers, which retire 30 s after start-up, and I/O or timer workers, which can linger for minutes after their last work); every thread the process itself starts (`std::thread`, ggml's workers) is counted, and a thread pool the library leaked would still show as leaked handles. The baseline is taken once the counts have been stable for 40 s, and after each close the counts must return exactly to the baseline (within 60 s; `settle_waits` reports how often that wait was needed). The memory limits are the same.
