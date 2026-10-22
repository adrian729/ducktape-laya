# Validation and release checks

The complete target matrix, gate mapping and measurement requirements are in
[QUALIFICATION.md](QUALIFICATION.md) and `release/qualification.json`. Those are
acceptance requirements; the runners below do not yet cover every required gate.

Run the native checks with one command. Output directories must be new so earlier
reports are not overwritten. Keep local outputs under ignored `build*/` paths
and attach relevant results to the commit, PR or CI run; do not commit run reports.
Models are supplied explicitly and checked against the pinned
manifest before real inference; the runner never downloads one.

```sh
npm ci --ignore-scripts --prefix tests/typescript
python3 scripts/validate.py --packages --output build-check-model-free
python3 scripts/validate.py --model /path/to/Laya-Q8_0.gguf \
  --extra-model /path/to/laya-multilingual-f16.gguf \
  --extra-model /path/to/laya-typed-decisions-q8_0.gguf \
  --packages --upstream-reference --output build-check-native
python3 scripts/check-reproducibility.py --output build-repro --jobs 2
```

The native runner configures/builds the library and addon, checks required CTest
registration, runs native, Rust, Node, Python, Java, .NET and Go suites, and checks
public exports. It also
runs compiled C/C++/Rust ABI-layout checks, TypeScript consumers in CommonJS,
ESM and bundler modes, artifact-identity and browser lifecycle/backend regressions.
TypeScript test tools are development-only and installed explicitly with the
locked command above. `tests/typescript/check.py --node-package NPM_TARBALL
--browser-package BROWSER_TARBALL` additionally checks extracted package declarations.
Reused candidate
source/revision overrides are cleared, and built runtime diagnostics must match
the approved compatibility record before any test gate is accepted. With a model,
it also compares C, C++, Rust, Node, Python, Java, .NET and Go fixture outputs. `--packages` builds actual artifacts and runs `tests/package-smoke.py`, which needs no model: outside the checkout, a C program links the native tarball, the npm package loads its addon and the wheel imports its bundled library, and each must report the recorded ABI version. With a model, `--packages` also runs `tests/installed-packages.py`, which exercises the installed consumers with real inference. `--reproducibility` adds two
independent builds; it is intentionally optional because it recompiles the runtime.

Every run retains commands, logs, exit codes and source/runtime/model/fixture/binary
identities. `report.json` distinguishes selected checks from release readiness.
Without a model, native real-model and binding-conformance gates stay `not_run`.
`--upstream-reference` builds a separate test executable using upstream primitive
processing and compares every fixture request, including batch members, without an
HTTP server. It never links that processor into the product library. Structured
requests are compared through their schema-derived primitive questions
(`tests/reference_requests.py`); value projection remains covered by the
shared-core contracts. Both sides receive structured values already serialized the
way the original Laya writes them, so they render identical prompts. Without this
option, the upstream-reference gate stays not run.

With a model, `tests/original-reference.py` also runs the original Laya implementation's recorded cases (`tests/original-reference/<model>/`, Apache-2.0, with provenance) through the C consumer at the checkpoint's trained sequence length: the `original_reference` gate. Input token counts must match exactly, and probabilities must stay within 0.05 with unchanged decisions. On near ties (the original's top two within 0.05) the decision may differ, since the model is undecided and quantization noise can reorder options; the probabilities are still gated. The original truncates over-long input by default, so its long-state and many-option cases run with `overflow: truncate_end`, and their `state_tokens` and `state_tokens_dropped` must match too. Cases for features the core does not support (the embedding shortlist) must still be refused; when one starts passing, it moves into the supported set.

`--extra-model PATH` (repeatable) qualifies an additional pinned model, one listed under `additional_models` in `release/compatibility.json` (the multilingual and typed-decisions models), matched to its pinned manifest by file hash. Each runs the `original_reference` gate against its own recorded cases and, with `--upstream-reference`, the upstream comparison. The results go to `additional_model_gates` in `report.json`. `check-candidate.py` requires both gates passed for every additional model, so a candidate cannot qualify without them. The primary model is still passed with `--model`.

The core bounds the temperatures stored in a model to [0.5, 5], as the original
does; llama.cpp applies them unbounded. The upstream-reference checks therefore
compare our probabilities re-expressed at the stored temperature (exact up to
rounding, since probabilities are a softmax of scores over the temperature).
Sanitizer, stress, browser and platform evidence remain separate;
the runner does not silently transplant historical results to a different binary.
`--require-release` exits unsuccessfully if any required gate is failed or unrun.
This runner currently covers a subset of release gates and cannot approve a release
by itself. A successful normal exit means its selected checks passed.

With `--model`, the runner requires the `cpu-guard` test to be registered, which needs `qemu-x86_64` (and `python3`) on `PATH` when CMake configures; without QEMU a real-model run fails. It also runs `tests/download-model.py` and records the Rust, Go, Java, .NET and Python versions it found under `toolchains`.

The GitHub workflow defines Ubuntu 24.04 model-free checks for Clang/Node 24 and GCC/Node 26, including `--packages` and its package smoke test. Manual dispatch can explicitly enable the pinned model download, QEMU and the real-model installed-package checks. CI uses the runner's default `clang` and `gcc`, which need not be the compiler versions `release/qualification.json` lists; the `native_matrix` evidence for `check-candidate.py` may therefore combine local and CI reports, as long as together they cover every listed compiler and Node version and each is bound to the candidate. Workflow dependencies are pinned by commit; Rust and Node
versions are explicit. Consult the relevant GitHub Actions run for results;
workflow definitions alone are not release evidence. CI does not publish packages
or update dependencies.

A separate model-free CI job instruments the shared library with ASan/UBSan, runs
the model-free CTest suite and `tests/input-fuzz.cpp` (deterministic
request/schema/config mutations, nesting and size boundaries), then builds
`laya-input-fuzzer` and runs a 60-second coverage-guided libFuzzer smoke from the
generated corpus. The smoke exercises the harness; it does not meet the one-hour
fuzz budget or replace real-model sanitizer runs. No sanitizer recovery or
suppression is enabled.

The benchmark (`tests/benchmark.py`, one fresh process per run) records model
open/close, first and warm p50/p95/p99 latency for small, structured, near-limit
and batch requests, two engines in concurrent threads, router cold loads with
evictions, cancellation response and deadline overshoot, RSS, descriptors and
threads. Repeated outputs must match and descriptors/threads must return to their
starting counts. Budgets are frozen per profile, model, thread count and reference
CPU from at least three independent runs, then candidates are checked against them:

```sh
python3 tests/benchmark.py /path/to/Laya-Q8_0.gguf --report build/bench-1.json  # repeat per run
python3 scripts/performance-budgets.py derive build/bench-{1,2,3}.json --output release/budgets/native-performance.json
python3 scripts/performance-budgets.py check build/bench-candidate.json
```

Limits are the worst baseline value times a declared headroom (1.5 for time, 1.25
for memory); leaks have none. The committed budgets in
`release/budgets/native-performance.json` were frozen from a 0.1.0-era baseline
(source commit `baaf568`, an ancestor of `v0.1.0`); a 0.2.0 build still met them
on 2026-10-21. Each additional model has its own budget, named by
`performance_budgets` in its manifest
(`release/budgets/native-performance-<model id>.json`, derived 2026-10-21 from
three 0.2.0 runs); `tests/benchmark.py` takes any pinned model, and
check-candidate reads the benchmark report for each additional model from the
evidence key `performance_<model id>`. They detect regressions on the reference host, not
portable latency promises. Run without competing builds or inference and keep
the reports as CI/PR artifacts.

The native stress target supports a bounded round smoke test and elapsed-time
qualification runs. Configure first, then explicitly build this optional target:

```sh
cmake -S . -B build -DDUCKTAPE_BUILD_TESTS=ON
cmake --build build --target laya-stress --parallel 2
build/laya-stress /path/to/Laya-Q8_0.gguf 10
build/laya-stress /path/to/Laya-Q8_0.gguf --seconds 3600
# Candidate soak uses --seconds 28800 instead.
```

Each duration run completes at least ten rounds, then runs the concurrent-caller
check. Every round exercises open/use/close, malformed input, deadline recovery
and concurrent callers on one handle with two inference threads; periodic rounds
add mixed batches, an independent second engine, and a router whose two hard-linked
model paths (created in the working directory, which must share the model's
filesystem) force cold loads and evictions. After every close, descriptors/threads
must return to baseline and RSS must stay within the declared 128 MiB allocator
retention allowance. From 40 rounds, the last quarter's median closed RSS must not
exceed the second quarter's by more than 32 MiB, so slow leaks inside that
allowance fail long runs. Progress goes to stderr; the final JSON on stdout includes
elapsed time and resource measurements. A short smoke run does not satisfy the
one-hour or eight-hour gates, and this native test does not cover browser memory.

Independent reproducibility compares the bytes, permission bits and symlink targets
of all installed native and Node files from a single captured working-tree snapshot
compiled in two separate source/build/install roots. Runtime source is archived from
its recorded commit and patches are hash checked. The report identifies dirty source
and hashes the actual snapshot. The guarantee is for the same toolchain and options;
it does not assert identical output across compilers or architectures. Packaging
determinism is a separate check for identical prebuilt inputs.

Identity-bound runners write a `report.json` with source commit, build and model
identities for the remaining long or instrumented gates:

```sh
python3 scripts/check-stress.py /path/to/Laya-Q8_0.gguf --build build-validation --output build-stress  # --seconds 28800 for soak
python3 scripts/check-sanitizers.py /path/to/Laya-Q8_0.gguf --output build-sanitizers
python3 tests/rollback.py LKG_PACKAGES CANDIDATE_PACKAGES /path/to/Laya-Q8_0.gguf --report build/rollback.json
node tests/electron/run.cjs PACKAGES/ducktape-laya-0.2.0.tgz /path/to/Laya-Q8_0.gguf build/electron.json
python3 scripts/check-candidate.py build-candidate/evidence.json --candidate COMMIT --report build-candidate/gates.json
```

The sanitizer runner builds ASan+UBSan and TSan configurations, runs every registered native test with the model (allocation-failure injection is skipped under sanitizer runtimes) and a short stress run. The `sanitizers` gate also lists the coverage-guided fuzz campaign (`scripts/check-fuzz.py`, below) among its checks; its report is the separate `fuzz` evidence. The rollback rehearsal switches an installed application from retained last-known-good packages to the candidate and back, requiring the LKG library and byte-identical outputs after rollback. Make `LKG_PACKAGES` from the previous GitHub release: `gh release download vX.Y.Z -D build/lkg-assets` and `python3 scripts/release-assets.py retain build/lkg-assets --output build/lkg-packages`, which restores the native package directory (`inventory.json` and its artifacts) and verifies it. When both sides have the same ABI, one consumer binary must work with both. Across an ABI break (0.1.0 to 0.2.0), an application must rebuild, so each side runs a consumer built from its own packaging source commit against its own headers. `check-candidate.py` takes a JSON map of evidence names to report paths. The tier-1 (release-level) names are `native`, `native_matrix`, `browser_package`, `browser`, `browser_isolated`, `userspaces`, `electron`, `cross_cpu`, `sanitizers`, `fuzz`, `stress`, `soak`, `reproducibility`, `performance`, `security_review`, `upgrade`, `model_upgrade`, `rollback` and `installed_release`; `scripts/fetch-ci-evidence.py` adds the candidate run's (next section). A gate passes only when its evidence passed and is bound to the candidate commit, the validated library and packages, the browser package assets and the pinned model; missing evidence is `not_run`. In particular: the security review must name its `source_commit` and `source_tracked_dirty` (false) like other reports; the cross-CPU bundle must contain the candidate library; the rollback's previous side must be an earlier release whose source is an ancestor of the candidate; the native packages must be built from the candidate's source identity; browser runs must use a Chrome version listed in `release/qualification.json` and, for WebGPU, listed non-fallback hardware; and each reproduced build must contain the candidate library. The checks that take packages (`installed_release`, `userspaces`, `electron`, `rollback`, the licence texts of every platform archive) use the release packages the candidate run assembled, which are what is published; `artifact_integrity` also requires the CI linux-x64 library and Node addon to be byte-identical to the locally validated ones and every release package to carry exactly each target's fragment payload. Hours-long evidence (`soak`, `stress`, `fuzz`, `sanitizers`, `performance`, `upgrade`, `model_upgrade`) may come from an ancestor commit when the diff since touches no library input (`src/`, `include/`, `cmake/`, `patches/`, `CMakeLists.txt`, the runtime record and pin, the model manifest) and none of that gate's harness files; soak, stress and performance must also have run on the candidate's exact library bytes. Everything else (native validation, packages, browser runs, reproducibility, install checks, all tier-2 evidence) is produced from the candidate itself. The report lists reused evidence under `reused_evidence`. The release record, policy, budgets, notices and source identity are read from the candidate commit's own tree, so later tooling commits cannot alter what is checked. `release_ready` requires every gate of every release target. Publication gates stay separate.

Before publication, require all applicable recorded gates, review current evidence and numeric-profile limits, verify the security contact and release hosting, and retain the prior artifacts for rollback. Publication is an explicit maintainer action. Assemble the release assets with `scripts/release-assets.py assemble` from the release packages (see [PUBLISHING.md](PUBLISHING.md#release-day-order)) and check them with `scripts/check-release-assets.sh DIR COMMIT`. After the GitHub release exists, run the `attest-release` workflow from `main` with its tag (it refuses other refs). It runs the same `scripts/check-release-assets.sh` from `main` on the published assets: every asset is listed once in `SHA256SUMS` with matching bytes; `gates.json` passes `scripts/release-gates.jq` (release ready, produced by a clean checker for the tagged commit, every release-level gate passed and, for schema 2, every gate of every release target, exactly one of them tier 1); the published native and browser inventories are the ones `gates.json` qualified and were packaged from the tagged commit; every artifact they list is published with the listed hash; for schema 2 the native inventory's platforms are exactly the qualified targets and every fragment was packaged from the tagged commit on a clean tree. Any asset outside those inventories fails the run. It then signs a release-qualification attestation for each asset, whose predicate also names each target's tier. It is not build provenance: the packages are built on GitHub-hosted runners in the candidate run and qualified on the release host, which requires its own Linux x86-64 library and addon to be byte-identical to CI's. `scripts/verify-release-assets.sh` (used by `publish-release`) applies the same `release-gates.jq`; 0.1.0 and 0.2.0 (schema 1, no targets) still pass it. Registry status, missing publishing work and the release-day order are in [PUBLISHING.md](PUBLISHING.md). Each numerical profile must pass strict same-backend upstream checks. Cross-profile comparisons report probability and decision drift and must not be described as probability equivalence.

Exact native results on a second physical CPU are checked with one unchanged
binary. A manually dispatched real-model CI run bundles its built library and C
conformance consumer, records raw fixture outputs with the runner's CPU identity
and uploads both. Download that artifact and run the same bundle on a different CPU:

```sh
python3 tests/cross-host.py record --bundle ARTIFACT/cross-host/bundle \
  --model /path/to/Laya-Q8_0.gguf --report build/cross-host-local.json
python3 tests/cross-host.py compare ARTIFACT/cross-host/record.json build/cross-host-local.json
```

Comparison requires identical bundle, model and fixture hashes, distinct CPU
identities and byte-identical outputs. It covers that build's ISA profile only.

`real-faults` (`tests/faults.cpp`, Linux, registered with a test model) feeds
damaged models to `dtl_open`: empty, non-GGUF and unreadable files, a directory,
truncation from the header to the last byte, and in-place corruption of the magic,
version, tensor/metadata counts, a string length, the decision type, temperatures
and the head budget. Each must return `DTL_MODEL_ERROR` with a message and no
descriptor leak; corrupted tensor bytes are not detectable without the host's
SHA-256 check. It then injects allocation failure: model loading under an
address-space limit, and failing the Nth C++ allocation of `dtl_execute` (every
point through 384, then every 97th) and of `dtl_open` (geometric points). Request
failures must return `DTL_OUT_OF_MEMORY`; failures absorbed internally must still
produce exact results; the engine must keep returning exact results afterwards.
`real-c-faults` (same executable, `--c-allocations`; not in sanitizer builds)
fails each C allocation (`malloc`, `calloc`, `realloc`, `posix_memalign`,
`aligned_alloc`, `memalign`) of one `dtl_execute` and one `dtl_open`, each in its
own child process. Every point must end in an exact result,
`DTL_OUT_OF_MEMORY`/`DTL_MODEL_ERROR`, or `SIGABRT` from ggml's own allocation
checks; any other signal fails (an unchecked NULL). At least one inference point
must reach the caller as `DTL_OUT_OF_MEMORY` and leave the engine returning exact
results.

`cpu-guard` (`tests/cpu-guard.py`, x86-64, registered with a test model when
`qemu-x86_64` and `python3` are found; not in sanitizer builds) runs a C probe and
the Node binding under QEMU user-mode CPU models. Nehalem (no AVX) and Sandy Bridge
(AVX, no AVX2/FMA/F16C/BMI2) must return `DTL_UNSUPPORTED_PLATFORM` naming the first
missing feature, with `dtl_info` listing every missing one. QEMU executes
AVX-family instructions regardless of the emulated CPU, so the probe runs with
execution logging and each executed block is decoded: no instruction from a
feature the emulated CPU lacks may run. An emulated CPU with every feature must
open the model, and the scan must see AVX/AVX2 there (positive control). It does
not replace a run on physical older hardware.

ThreadSanitizer covers the native library, llama.cpp/ggml (its own pthread pool;
OpenMP is disabled) and the native test executables, including the real-model
suites and the stress loop's concurrent callers, second engines and router
evictions, and the Rust binding's tests (one engine and one router shared across
threads, cancellation set from another thread, close on a different thread). It
does not cover the Node worker threads. No suppressions:

```sh
CC=clang CXX=clang++ cmake -S . -B build-tsan -DCMAKE_BUILD_TYPE=RelWithDebInfo \
  -DDUCKTAPE_BUILD_TESTS=ON -DDUCKTAPE_BUILD_NODE=OFF -DDUCKTAPE_TEST_MODEL=/path/to/Laya-Q8_0.gguf \
  '-DCMAKE_C_FLAGS=-fsanitize=thread -fno-omit-frame-pointer' \
  '-DCMAKE_CXX_FLAGS=-fsanitize=thread -fno-omit-frame-pointer'
cmake --build build-tsan --parallel 2 && cmake --build build-tsan --target laya-stress
TSAN_OPTIONS=halt_on_error=1 ctest --test-dir build-tsan --output-on-failure
TSAN_OPTIONS=halt_on_error=1 LD_LIBRARY_PATH=build-tsan build-tsan/laya-stress /path/to/Laya-Q8_0.gguf 10
```

Rust needs a nightly toolchain with `rust-src` (`rustup toolchain install nightly
--component rust-src`) and the instrumented library from the build above:

```sh
RUSTFLAGS='-Zsanitizer=thread -Clink-arg=-Wl,--export-dynamic' \
  DUCKTAPE_LAYA_LIB_DIR=build-tsan LD_LIBRARY_PATH=build-tsan LAYA_MODEL=/path/to/Laya-Q8_0.gguf \
  cargo +nightly test --locked -Zbuild-std --target x86_64-unknown-linux-gnu --lib \
  --manifest-path bindings/rust/Cargo.toml
```

`--export-dynamic` exports the runtime's interceptors to the shared library as a
Clang link does; without it, guarded static initialization inside the library
(`__cxa_guard_*`) is reported as a race. `scripts/check-sanitizers.py` runs this.

For coverage-guided request/planner/router fuzzing, use a separate Linux Clang
build. The optional target instruments the shared core and links libFuzzer only
into the test executable. It needs no model; lazy inference is directed at a
known non-model file. Generated/mutated corpus files and crash reproducers belong
in ignored build directories. Preserve minimized regressions in tests after triage.

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

This covers instrumented library parsing, planning and router boundaries; it does
not claim coverage-guided testing of upstream tensor kernels or model loading.
Do not package the instrumented library. Short smoke runs exercise the harness;
they do not satisfy the one-hour qualification budget.

Use the campaign runner to retain identities, seeded corpus, commands, statistics
and failures for qualification:

```sh
python3 scripts/check-fuzz.py --build build-fuzzer --seconds 3600 \
  --output build-fuzz-campaign
```

It requires an explicitly configured fuzz build, resets runtime overrides to the
approved pin, rebuilds the target, verifies shared-library coverage instrumentation,
and bounds runtime and memory. A shorter `--seconds` smoke can pass while
`qualification_budget_met` remains false. Campaign success alone never makes
`release_ready` true. Do not modify its build directory during the run.

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

and add `installed_release`, `electron`, `userspaces`, `rollback` and `<platform>:drift` (one per tier-2 target) to the evidence map. `gates.json` (schema 2) keeps the tier-1 gates under `gates` with their meaning unchanged, adds `targets` (`{platform: {tier, gates, ci_run}}`, the tier-1 target repeating the release-level statuses), and records the release packages' inventory as `inventories_sha256.native`.

`scripts/validate.py` runs on every target; on Windows pass `--node-lib` and the clang-cl build through `--cmake-arg` (see the `windows` job in `native.yml`). Checks that do not apply are listed under `skipped` in `report.json` with the reason, never left out silently: fault injection (`real-faults`, `real-c-faults`) is Linux-only (prctl, rlimits), the QEMU CPU guard is Linux x86-64 only, and the release checker and asset tests run on the Linux host only. The report also records `platform`, `addon_sha256` and the registered native tests. On Windows the stress test measures committed private bytes (`PrivateUsage`) instead of `VmRSS`, the process handle count instead of descriptors, and threads from a Toolhelp snapshot excluding the workers Windows starts for its own thread pools (threads whose start address is in `ntdll.dll`: the loader's three workers, which retire 30 s after start-up, and I/O or timer workers, which can linger for minutes after their last work); every thread the process itself starts (`std::thread`, ggml's workers) is counted, and a thread pool the library leaked would still show as leaked handles. The baseline is taken once the counts have been stable for 40 s, and after each close the counts must return exactly to the baseline (within 60 s; `settle_waits` reports how often that wait was needed). The memory limits are the same.
