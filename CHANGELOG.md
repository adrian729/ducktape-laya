# Changelog

User-facing changes per release. Each section lists breaking changes first; "Release tooling" covers how releases are built and checked, which does not change the packages' behaviour.

## Unreleased

Linux arm64 and Windows x64 become release platforms (tier 2) next to Linux x86-64 (tier 1); the next release publishes packages for all three ([platforms](docs/PLATFORMS.md)). The 0.2.0 packages stay Linux x86-64 only.

### Breaking changes

- **Node and browser load failures:** a missing or unloadable addon or runtime, or one with the wrong ABI, now rejects with status 7 and the code `ERR_LAYA_UNSUPPORTED` (it was a plain error). `info()` after `close()` reports `ERR_LAYA_CLOSED` (it reported `ERR_LAYA_WORKER_EXITED` once the worker had exited). `Laya.open(path, null)` and other `null` options give a status-1 `TypeError`.
- **Serialized state size:** a structured state can serialize several times longer than its request text (`1e15` is written `1000000000000000.0`); a state whose serialized text exceeds 2 MiB is now rejected with status 1, like a rendered question over 2 MiB, so the 1 MiB request limit bounds tokenization.
- **Large integers in structured values** outside the 64-bit range entered the prompt as floats (`100000000000000000000000` as `1e+23`); they now keep all their digits, as the original Laya writes them. Prompts, and so results, change for such values.
- **.NET library resolution:** the library bundled in the package (`runtimes/<rid>/native/`) is now tried before the system's `libducktape_laya.so.1`, so an unrelated installed build (`LD_LIBRARY_PATH`, ldconfig, `/usr/local/lib`) can no longer shadow it. The system fallback searches only the assembly directory and the safe system directories (on Windows not the current directory or `PATH`).
- **Windows on Arm:** where the CPU check cannot read the required dot-product and FP16 features, opening an engine now fails with status 7 instead of skipping the check.
- **Package licence metadata:** the native wheel, npm, NuGet and Maven packages declare `MIT AND Apache-2.0` (they bundle the Apache-2.0 Laya language data and an Abseil snippet), the browser package `MIT AND Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-3-Clause`. The notices now attribute the code embedded in the vendored nlohmann/json header (Grisu2, the UTF-8 decoder, Hedley, Abseil) and name every package that carries `licenses/`.

### Features

- **Packages for Linux arm64 and Windows x64:** the archive `ducktape-laya-VERSION-linux-arm64.tar.gz` and `ducktape-laya-VERSION-windows-x64.zip` (headers, `bin/ducktape_laya.dll`, the import library `lib/ducktape_laya.lib`, CMake and pkg-config files), wheels tagged `manylinux_2_38_aarch64` and `win_amd64`, and one npm package, one jar and one NuGet package that carry every platform's library and pick the running one. The DLL needs the Microsoft Visual C++ Redistributable. Package descriptions no longer say Linux only.
- **Bindings load bundled libraries on every packaged platform:** the Python wheel on macOS arm64 and Windows x64; the jar from `native/<platform>/`; the NuGet package from `runtimes/<rid>/native/` under the library's own file name; the npm package from `native/<platform>-<arch>/`.
- **Windows x64:** the library builds with clang-cl (`/Brepro`, reproducible). The Node addon links `node.lib` of the exact Node version and delay-loads Node-API from the host executable, so it loads in Node and in Electron. The Go binding builds with MinGW-w64 gcc against the import library ([Windows setup](bindings/go/README.md#windows)), and the Rust crate links `ducktape_laya.lib`.
- **Faster forward passes:** the decision graph evaluated the decision head for all three question types and the library read one; it now evaluates only the asked question's type (runtime patch `laya-selected-decision-head.patch`), with bit-identical scores. On a busy desktop each pass took about 12% less time with the English and typed-decisions models and 21 to 25% less with the multilingual model; the latencies in [performance](docs/PERFORMANCE.md) were measured before this change.
- **No limit on runs of spaces:** the limit of 4,096 consecutive spaces, which guarded one slow tokenization case, is removed; such runs now tokenize faster than ordinary text (see the tokenization fix below).
- **Rust:** `Engine::open` accepts any `AsRef<Path>` (non-UTF-8 paths fail with status 1).
- **musl (Alpine):** the Linux-only npm package declares `"libc": ["glibc"]`, so npm refuses it on musl (a multi-platform package cannot declare it, because npm treats the field as a mismatch on macOS and Windows), and the Node addon resolver and the Java loader say that the glibc library cannot load on musl instead of failing with an opaque loader error.

### Fixes

- **Slow tokenization of ordinary text:** splitting text at special tokens took time quadratic in the number of matches, and the English and typed-decisions vocabularies match every run of 2 to 24 spaces, the multilingual one every newline. One request could hold an engine for minutes (64 KiB of double-spaced text took about 31 s, 1 MiB hours), and deadlines could not interrupt it. The split is now linear (runtime patch `tokenizer-linear-partition.patch`) with identical tokens, checked against token sequences recorded before the change; the same 64 KiB takes 0.2 s and 1 MiB of any measured input at most about 11 s on a busy desktop.
- **Unused logits buffer:** every engine reserved and zero-filled a buffer of `n_vocab` logits per prompt token that the Laya decision graph never reads: about 98 MiB per 512 tokens of prompt with the English and typed-decisions models and about 500 MiB per 512 tokens with the multilingual model. It is no longer allocated (runtime patch `llama-embeddings-no-logits-buffer.patch`, a backport of upstream ed019cd8b); outputs are byte-identical. On a busy desktop the multilingual near-limit request used 479 MiB less resident memory; the memory figures in [performance](docs/PERFORMANCE.md) were measured before this change.
- **Text was tokenized twice:** each piece of a prompt was tokenized once to count its tokens and again to store them, and both passes do the full work (about 4.3 s each for 1 MiB of state on a busy desktop). It is now tokenized once into a buffer sized from the text, with a second pass only if that buffer is too small; tokens are identical.
- **Automatic thread count ignored CPU limits:** `threads` 0 used up to 4 threads whenever the machine had 4 online CPUs, even when the process could run on fewer (`taskset`, a container's CPU quota). ggml's workers wait by spinning, so 4 threads on 1 allowed CPU ran 1.6 to 2 times slower in the audit's measurement. The automatic count is now also at most the CPUs in the process's affinity mask (Linux, Windows) and its cgroup v2 CPU quota rounded up (Linux); an explicit `threads` is unchanged.
- **Deadlines and cancellation during tokenization:** tokenization did not check the call's controls, so a call over a long text ran its tokenization to the end (seconds for 1 MiB) before a deadline or cancellation took effect. Tokenization now checks them while splitting text at special tokens and while merging tokens (runtime patch `tokenizer-stop-check.patch`) and ends with status 5 or 6; the engine stays usable. On a busy desktop, with the English model, a 1 MiB state of long words whose tokenization took 3.2 s ended 0.42 s after the call started under a 0.32 s deadline; the rest is request parsing and the unchecked pre-split, which for ordinary text is about a quarter of tokenization. The regular-expression pre-split of the text into words and each word's symbol setup are still not checked.
- **A context allocation on every forward pass:** ggml created and freed a context on every graph computation only to make sure its timer was initialized; it now does so only until that has happened once (runtime patch `ggml-cpu-init-once.patch`): two small allocations fewer per pass, a saving too small to have been measured; outputs are unchanged.
- **Allocation failure in the CPU work buffer:** when the backend's work buffer could not grow, it kept the freed buffer, so the next call wrote into freed memory and a later one or closing the engine could crash. The call now fails with status 4 and the engine stays usable (runtime patch `ggml-cpu-work-buffer.patch`).
- **Thread creation failure aborted the process:** when the operating system refused a CPU worker thread (`RLIMIT_NPROC`, a container's pids limit), ggml aborted the host process. The call now fails with status 4 and the engine stays usable (runtime patch `ggml-thread-creation-failure.patch`).
- **Go:** an `Engine` dropped without `Close` was never released (the engine state referenced itself through its `sync.Cond`, so the finalizer never ran) and the model stayed in memory for the life of the process. The state now sits behind a thin handle that carries the finalizer.
- **Java:** on Windows the bundled DLL was extracted to a predictable directory in `java.io.tmpdir` that was reused without ownership checks, so on a shared temp directory another local user could swap the DLL. It now goes to `%LOCALAPPDATA%\ducktape-laya` (or `-Dducktape.laya.tmpdir`) with an owner check, or to a fresh owner-only directory. On Linux and macOS the extracted copy is deleted right after loading, so a killed JVM no longer leaves a 7 MB copy in `/tmp`; `-Dducktape.laya.tmpdir` selects the directory (for a `noexec` `/tmp`).
- **Maven jar manifest:** the 0.2.0 jars (main, sources, javadoc) carry an unreadable `META-INF/MANIFEST.MF` (literal `\r\n` instead of line breaks), so `Implementation-Version` and `Automatic-Module-Name` were lost; the library still loads (`module-info.class` is present). Fixed.
- **Python:** x64 Python on Windows ARM64 was rejected because `platform.machine()` reports the OS architecture; the wheel's bundled library is now loaded whenever it exists.
- **C++:** `ducktape::engine::info()` on a moved-from engine throws instead of returning runtime info. Rust checks for a null info result.
- **Node addon built from source:** its RUNPATH had empty entries (`$ORIGIN::::`), which glibc resolves to the current directory; it now uses the install RPATH (`$ORIGIN:$ORIGIN/../..`).
- **CPU check on Windows:** clang-cl x86 builds did not check for FMA and F16C at open, although they use them, and reported the profile `native-cpu:avx+avx2+bmi2+sse4.2`; they now require both like the other x86-64 builds and report the x86-64 profile. On Windows x64 the check reads CPUID leaf 7 only when the CPU reports it.
- **Non-ASCII router paths on Windows:** route paths were read as ANSI code page text instead of UTF-8, so a route with a non-ASCII character in its path did not open (`dtl_open` was not affected).
- **CPU profile pinned at configure time:** ggml turns off AVX, AVX2, FMA, F16C, BMI2 and SSE4.2 when `SOURCE_DATE_EPOCH` is set or when cross-compiling, which would have built a different numerical profile without notice. The x86-64 options are now set explicitly (they remain cache options for a portable build); the Linux x86-64 library is unchanged.
- **Hardened builds:** the library, ggml, llama.cpp and the Node addon are built with `-fstack-protector-strong`, `_FORTIFY_SOURCE=3` when optimising, full RELRO (`-z relro -z now`), `-mbranch-protection=standard` on Linux arm64 and Control Flow Guard (`/guard:cf`) with clang-cl, instead of relying on the distribution compiler's defaults. Linux libraries and addons ship stripped (`--strip-unneeded`, dynamic symbols kept), and the npm package ships one copy of the Linux library (`libducktape_laya.so.1`) instead of two. These change the library bytes of the next release.

### Platforms

- **Release targets and tiers:** `release/compatibility.json` (schema 2) lists the release `targets`: `linux-x64` tier 1 with every release gate, `linux-arm64` and `windows-x64` tier 2. Tier 2 is CI-tested with the English model: native contracts, binding conformance, upstream and original references, ABI exports, installed packages including Electron, artifact integrity, reproducibility, a 600-second stress run, and probability drift against Linux x86-64 (arm64 0.0334, Windows 0.0865 on the fixture corpus, no changed decision). Each target records its CPU baseline and numerical profile with compiler and math library; `tested_backend` is removed ([qualification](docs/QUALIFICATION.md#release-targets-and-tiers)).
- **Windows known deviation:** one answer of the original-Laya comparison (`json_escaping/spam`, 0.0541 against a 0.06 cap) differs because the Windows C runtime's `erff`/`sinf`/`cosf` round differently from glibc; it is recorded under the target's `known_deviations` ([compatibility](docs/COMPATIBILITY.md#known-deviations)).
- macOS arm64 packages are built and tested in CI (minimum macOS 14) but not released.

### Release tooling

- **Packaging:** `scripts/package-native.py` (formerly `package-linux.py`) builds the packages of the platform it runs on (Linux x86-64 and arm64, macOS arm64, Windows x64); `scripts/assemble-packages.py` merges the per-platform outputs into the multi-platform packages, depending only on the inputs. `package-native.py` fails unless the shipped ELF files have `BIND_NOW` and the PE files `GUARD_CF`, and refuses builds with a custom runtime source or revision, a non-Release build type or extra compiler flags; the linux-arm64 inventory no longer lists x86 CPU options. The jar manifest is written with real CRLF line ends and `tests/package-smoke.py` parses all three jars' manifests.
- **Release candidate runs:** `native.yml` dispatched with `candidate=true` builds, validates and packages every target on GitHub-hosted runners, assembles the release packages, installs them with the real model on every target's runner (Electron included), and runs the tier-2 stress and reproducibility checks. Reports record the runner image and, on Windows, the MSVC and SDK versions (`build_environment`), because the Windows DLL depends on the image.
- **Candidate checks for several targets:** `scripts/fetch-ci-evidence.py` downloads a candidate run, checks every artifact against GitHub's digest and records every file's hash in `ci-run.json` (bound to `adrian729/ducktape-laya`). `scripts/check-candidate.py` checks every target (`gates.json` schema 2 with `targets`; `release_ready` needs every gate of every target) and validates the candidate commit's own release record and targets. Tier-1 checks are unchanged or stricter: the CI Linux x86-64 library and Node addon must be byte-identical to the locally validated build; the installed-package, userspace, Electron, rollback and licence checks run on the shipped (CI-assembled) packages; the tier-1 Electron evidence must be the `linux-x64` run of the candidate npm package and `installed_release` this host's own run (`tests/installed-packages.py` records `host`); `artifact_integrity` requires this host's linux-x64 package run to equal the CI fragment entry by entry, with the documented exceptions listed as `package_comparison`. `tests/compare-profiles.py` measures drift between native profiles.
- **Release assets and publishing:** `scripts/release-assets.py` assembles from the release packages; `scripts/check-release-assets.sh` (run by `attest-release`) also checks that the native platforms are exactly the qualified targets and compares the `gates.json` schema in jq (requiring schema 2 for a multi-platform inventory); `scripts/release-gates.jq` holds the `gates.json` check shared with `verify-release-assets.sh` (0.1.0 and 0.2.0 still pass). The attestation predicate names each target's tier. `verify-release-assets.sh` pins the attestation signer by exact certificate identity including `@refs/heads/main` (and checks the certificate fields) instead of the prefix-matching `--signer-workflow`. `publish-release` uploads every target's wheel to PyPI without `skip-existing`: `scripts/pypi-missing-wheels.py` skips only wheels PyPI already holds with the `SHA256SUMS` hash, fails on differing bytes, and uploads the rest.
- **Windows in CI and `validate.py`:** `scripts/validate.py` runs on Windows (`--node-lib`, `--cmake-arg`) and lists every check that does not apply under `skipped` with its reason. CI checks that the DLL exports exactly the `dtl_*` functions (`scripts/check-abi.py` reads PE and Mach-O exports, and fails with explicit exits, also under `python -O`), runs the Node and Go suites and the Electron consumer (directory and ASAR), and with the real model gets exact agreement between all eight bindings. The stress test measures committed memory, handles and threads on Windows. The Windows job moved to `native.yml` ("Native checks"); `platforms.yml` keeps macOS.
- **Toolchain pins:** the .NET SDK is pinned to 10.0.402 in the workflows; `scripts/fetch-node-sdk.py` checks the Node headers and `node.lib` against SHA-256 values pinned in `scripts/node-sdk-sha256.json`; the SBOM reads the nlohmann/json version from the vendored header.
- **Tests:** the real-model cross-thread cancellation test (`real-control`) no longer depends on timing; tests on every platform open a model through a path with Latin-1, CJK and non-BMP characters, directly and as a route; a model-free test checks every example request (`tests/examples-smoke.py`) and the Markdown links and anchors (`tests/docs-links.py`).

## 0.2.0 (2026-10-22)

C ABI 2 (`libducktape_laya.so.1`). Published on GitHub Releases, npm, crates.io, PyPI, NuGet, Maven Central and the Go module proxy, Linux x86-64 only. Breaking before 1.0, hence the minor version. Migration notes are in [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md#migrating-from-010-abi-1-to-020-abi-2).

### Breaking changes

- **C ABI 2.** One `dtl_engine` handle for a single model or a router; `dtl_open(path, config_json)` and `dtl_open_router(router_json, config_json)` take a JSON config instead of `dtl_options`; `dtl_execute(engine, request, call_options)` is the only call, with the observer (`request_id`, `on_event`, `event_user_data`) inside `dtl_call_options`, whose cancellation user data is now `cancel_user_data`. `dtl_router*`, `dtl_decide*`, `dtl_execute_with_options`, `dtl_observer`, `dtl_options` and `dtl_default_options` are removed. New export: `dtl_cancel_flag`, a ready-made `should_cancel` that reads a host-set `uint32_t`. SONAME `libducktape_laya.so.1`, symbol version `DUCKTAPE_LAYA_2`.
- **Strict requests only.** The lenient legacy `decide`, which ignored unknown top-level fields, is removed from every binding. Unknown keys inside a question are rejected too (0.1.0 ignored them).
- **C++:** `ducktape::engine::open` and `open_router` replace `ducktape::laya` and `ducktape::router`; `execute` takes a `ducktape::call_options`.
- **Rust:** `Engine::open(path, &Config)` and `Engine::open_router(json, &Config)`; `execute` and `execute_with(json, CallOptions)` replace `decide*`, `execute_observed` and `execute_with_options`. `CallOptions` fields are private and set with builders (`request_id`, `timeout_ms`, `cancel_flag`, `on_event`); the cancel flag is a borrowed `AtomicU32` instead of an `AtomicBool` (nonzero cancels, 6 reports a host deadline), and `execute_with` takes the options by value. Status constants are in the new `ducktape_laya::status` module.
- **Rust `ducktape-laya-sys`:** `Router`, `Options`, `Observer`, `dtl_default_options` and the removed C functions are gone; `dtl_open_router` and `dtl_cancel_flag` are added, and `CallOptions` has the ABI 2 layout.
- **Node and browser:** `decide`, `decideStructured` and `decideBatch` are removed; `execute` takes one request or an array.
- **Confidence fields.** Every answer reports `answer_confidence` (the probability of the reported answer) and `confidence` (`1 − H(p)/log k`); the old `choice`/`score` `confidence` formulas are replaced.
- **Diagnostics.** Model info reports `context_tokens`, `trained_context_tokens`, `temperatures` and `metadata_temperatures` instead of `max_tokens`; the runtime limit `tokens_per_question` is now `default_context_tokens`.
- **Stored temperatures are bounded to [0.5, 5]**, as in the original Laya. With the English model this changes `choice` probabilities for 11 or more options (its stored `choice.11` is 0.1006).
- **Structured values are serialized as the original Laya does** (Python `json.dumps`: full float precision, `0`/`false` descriptions kept), so prompts for structured input change.
- **`"auto"` is reserved** and cannot be a router route ID.

### Features

- **Open config:** `threads`, `context_tokens` (prompt capacity, 32 up to the model's trained length, default 512, replacing the fixed 512), `overflow` and `temperatures`, for an engine, for every route of a router, and per route. Requests may set `max_tokens`, `temperatures`, `overflow` and `max_windows`. Temperatures must be finite and positive after conversion to a 32-bit float (`1e-50` and `1e39` are rejected). Routes share a loaded model only when their path and configured values are equal, and each keeps its own default `overflow`.
- **Long input:** overflow policies `reject` (default), `truncate_end`, `truncate_start` and `scan`. Except under `reject`, over-long instructions and options are shortened as llama.cpp's decision server does; the question and options must still fit the limit on their own under every policy. Whenever the effective policy (the request's, or the engine's or route's default) is not `reject`, `usage` reports `state_tokens`, `state_tokens_dropped`, `truncated_questions` and `windows` (the most state windows any question used); scanned answers carry `window`. `"aggregate": "all"` makes a scanned `noul` question take the minimum over windows.
- **Choices:** `choice` criteria may be a list of labels; a single-option `choice` (and a one-value schema `enum`) is answered without inference.
- **Models:** the multilingual (F16) and typed-decisions (Q8_0) checkpoints, built with `scripts/convert-model.py` from `models/laya-multilingual.json` and `models/laya-typed-decisions.json` (no download URL yet). The text between mask tokens is tokenized on its own, as the models were trained; tokenizing the whole prompt at once dropped whitespace before a mask token with the multilingual vocabulary.
- **Routing by language:** a router's `"auto": {"english", "default"}` and requests with `"model": "auto"` and an optional `"lang"` (1–64 bytes); `usage.routing` reports the reason.
- **Runtime info:** features `language_routing` and `overflow_policies`, an `overflow_policies` list, and limits `minimum_context_tokens`, `scan_windows`, `object_members` and `windows_per_call`.
- **Bindings:** Python (PyPI), Go (module proxy), Java/Kotlin (Maven Central) and .NET (NuGet). Node `execute` and the browser take a `requestId`; the browser `openRouter` takes `auto` and per-model `contextTokens`, `overflow` and `temperatures`.
- **Node and browser errors:** both packages export `Status`; argument errors carry `status` 1; queue, closed and worker failures carry `code` `ERR_LAYA_QUEUE_FULL`, `ERR_LAYA_CLOSED` or `ERR_LAYA_WORKER_EXITED`.
- **Examples:** request presets in `examples/presets/` (support triage, email triage, prompt-injection screening, moderation).

### New limits (breaking)

These reject requests or configs that 0.1.0 accepted or that are new in 0.2.0. All fail with status 1.

- A JSON object in a request, an open config or a router config may have at most 1,024 members (`limits.object_members`).
- A run of more than 4,096 consecutive ASCII spaces in a question (instructions and options) or in the state is rejected: llama.cpp's tokenizer takes quadratic time on such runs, and tokenization cannot be interrupted.
- One call may plan at most 4,096 forward passes in total, counting every `scan` window (`limits.windows_per_call`).
- `score` levels must not be `null`.
- `context_tokens` below 32 is rejected when the config is parsed; the upper bound (the model's trained length) is checked when the model loads, which for a router route is its first request.
- `max_tokens` above the engine's `context_tokens` is rejected even when every question is a single-option `choice`.
- Integer settings (`threads`, `context_tokens`, `max_tokens`, `max_windows`, `max_loaded`) are range-checked correctly for integers above 2^63.

### Fixes

Changes to behavior that 0.1.0 already had:

- Model metadata strings longer than 255 bytes are read in full; 0.1.0 refused such models with status 2.
- `dtl_info` replaces invalid UTF-8 in model metadata instead of failing.
- Node and the browser reject strings that are not well-formed Unicode (lone surrogates) instead of replacing them.
- Node and the browser drop a call's `onEvent` handler once an abort or deadline has settled it.
- Node and browser `close()` resolve even when the worker has already exited or failed.
- The browser worker passes requests through heap memory instead of the 2 MiB WebAssembly stack.
- The Rust crate and the Node addon also call `dtl_abi_version()` and refuse any ABI but 2. In practice an ABI 1 library fails earlier, when the loader looks for `libducktape_laya.so.1` and its `DUCKTAPE_LAYA_2` symbols.

### Platforms

- **Windows x64 (in progress):** the library builds with clang-cl; the model-free native tests and the Python, Rust, Java and .NET suites pass in CI (the loaders find `ducktape_laya.dll`, Rust links its import library). Node, Go, packages and real-model checks are not ported yet. Text files are checked out with LF everywhere (`.gitattributes`), so patch and source hashes match on Windows.
- **macOS on Apple silicon** builds from a checkout and is tested in CI (macOS 15 arm64) with and without the real model; the library exports only `dtl_*`, uses an `@rpath` install name and checks dot product and FP16 at open. The Python, Java, .NET and Rust loaders find `libducktape_laya.dylib`. No macOS packages yet.
- **Linux arm64** builds (Armv8.2-A with dot product and half precision, checked at open like AVX2 on x86-64) and is tested in CI with and without the real model. Packaging on arm64 produces linux-arm64 packages (tarball, npm, wheel, jar, NuGet), and the Node, Python, Java and .NET loaders pick the library for their architecture; releases publish x86-64 packages only until arm64 is release-qualified.

### Performance

- A request's state is tokenized once and reused by its questions, instead of once per question.

### Release tooling

- New `original_reference` gate: the original Laya's recorded results, at each checkpoint's trained length.
- `validate.py --extra-model` qualifies the additional models against the original and upstream references; `check-candidate.py` requires them.
- Release packages also include the Java jar (library bundled) with sources and javadoc jars and a POM, and the NuGet package (library bundled); both are byte-reproducible and tested installed, as is a Go consumer built against the tarball. The new models have their own performance budgets.
- `validate.py --packages` works without a model (`tests/package-smoke.py`), and CI runs it. `validate.py` records the Rust, Go, Java, .NET and Python versions, runs `tests/download-model.py`, and with a model requires the `cpu-guard` test.
- The rollback rehearsal handles an ABI break by building each side's consumer from its own source commit.
- `scripts/release-assets.py`: `assemble` builds the GitHub release asset set (renamed inventories and SBOMs, `gates.json`, `SHA256SUMS`) from the qualified package directories; `retain` turns downloaded release assets into the last-known-good package directory for `tests/rollback.py`.
- The `attest-release` workflow runs only from `main`, requires every gate passed, checks that the published inventories are the ones `gates.json` qualified and were packaged from the tagged commit, that every artifact they list is published with its hash, and attests nothing outside them.
- `check-candidate.py` binds more evidence to the candidate: the security review's source commit, the cross-CPU bundle's library, the rollback's previous side (an earlier release and an ancestor commit), the native packages' build source, the browser runs' Chrome version and non-fallback WebGPU hardware, and the reproduced builds' library.
- Browser builds record their optimization level and GPU tracing; packaging accepts only the product build from `scripts/build-browser.py` (`-O1`, no GPU tracing).
- Packaged tarballs normalize file modes (0644, or 0755 for directories and executables).
- `check-release.py` checks that the ABI version, SONAME, `exports.map` version node and header agree, and that scripts name the matching `.so`.
- The pkg-config file finds its prefix from multiarch library directories (`lib/x86_64-linux-gnu`).

## 0.1.0

First release, from tag `v0.1.0`, on GitHub Releases (native tarball, SBOMs, inventories, attested), npm (`ducktape-laya`, `ducktape-laya-browser`) and crates.io (`ducktape-laya`, `ducktape-laya-sys`).

- C ABI 1 (`libducktape_laya.so.0`) with C, C++, Rust, Node.js/Electron and experimental browser (WASM and WebGPU) bindings.
- The pinned English Laya Q8_0 GGUF on Linux x86-64 (AVX2-era CPUs, glibc 2.38+).
- `choice`, `noul` and `score` questions, structured decisions, batches, explicit local routing, passive events, deadlines and cooperative cancellation.
- Prompts limited to 512 tokens; over-long input rejected.
