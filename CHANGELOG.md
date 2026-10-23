# Changelog

## Unreleased

Linux arm64 and Windows x64 become release targets (tier 2) next to Linux x86-64 (tier 1). The 0.2.0 registry packages stay Linux x86-64 only; the multi-platform packages arrive with the next release.

### New features

- **Release targets and tiers:** `release/compatibility.json` (schema 2) lists the release `targets`: `linux-x64` tier 1 with every release gate, `linux-arm64` and `windows-x64` tier 2. Tier 2 is CI-tested with the English model: native contracts, binding conformance, upstream and original references, ABI exports, installed packages including Electron, artifact integrity, reproducibility, a 600-second stress run, and probability drift against Linux x86-64 (arm64 0.0334, Windows 0.0865 on the fixture corpus, no changed decision). Each target records its CPU baseline and numerical profile with compiler and math library; `tested_backend` is removed. See [docs/QUALIFICATION.md](docs/QUALIFICATION.md#release-targets-and-tiers).
- **Windows x64:** the library builds with clang-cl (`/Brepro`, reproducible), and the zip `ducktape-laya-VERSION-windows-x64.zip` holds headers, `bin/ducktape_laya.dll`, the import library `lib/ducktape_laya.lib`, the CMake config and the pkg-config file. The DLL needs the Microsoft Visual C++ Redistributable. The Node addon links `node.lib` of the exact Node version (`scripts/fetch-node-sdk.py` downloads it with the Node-API headers and checks both against Node's `SHASUMS256.txt`) and delay-loads Node-API from the host executable, so it loads in Node and in Electron. The Go binding builds with MinGW-w64 gcc against the import library ([Windows setup](bindings/go/README.md#windows)), and the Rust crate links `ducktape_laya.lib`. One documented deviation from the original Laya (`json_escaping/spam`, 0.0541 against a 0.06 cap, caused by the Windows C runtime's `erff`/`sinf`/`cosf`) is recorded under the target's `known_deviations` ([COMPATIBILITY.md](docs/COMPATIBILITY.md#known-deviations)).
- **Packages for every platform:** `scripts/package-native.py` (formerly `package-linux.py`) builds the packages of the platform it runs on: Linux x86-64 and arm64, macOS arm64 and Windows x64. Wheel tags are `manylinux_2_38_x86_64`, `manylinux_2_38_aarch64`, `win_amd64` and `macosx_14_0_arm64`; the npm, Maven and NuGet packages use `linux-x64`, `linux-arm64`, `darwin-arm64`/`macos-arm64`/`osx-arm64` and `win32-x64`/`windows-x64`/`win-x64` directories. `scripts/assemble-packages.py` merges the per-platform outputs into one npm package (its `os` and `cpu` list every platform), one jar and one NuGet package carrying every platform's library, next to each platform's archive and wheel; the result depends only on the inputs. The next release publishes the packages of every release target; macOS packages are built but not released (macOS binaries target macOS 14).
- **Bindings load bundled libraries on every packaged platform:** the Python wheel loader on macOS arm64 and Windows x64, the jar from `native/<platform>/` (on Windows one verified copy per library content in the user's temporary directory, since a loaded DLL cannot be deleted at exit), the NuGet package from `runtimes/<rid>/native/` under the library's own file name, and the npm package from `native/<platform>-<arch>/`. Package descriptions no longer say Linux only.
- **Release candidate runs:** `native.yml` dispatched with `candidate=true` builds, validates and packages every target on GitHub-hosted runners, assembles the multi-platform release packages, installs them with the real model on every target's runner (Electron included), and runs the tier-2 stress and reproducibility checks. Reports record the runner image and, on Windows, the MSVC and SDK versions (`build_environment`), because the Windows DLL depends on the image.
- **Release checks for several targets:** `scripts/fetch-ci-evidence.py` downloads a candidate run, checks every artifact against GitHub's digest and records every file's hash in `ci-run.json`. `scripts/check-candidate.py` checks every target (`gates.json` schema 2 with `targets`; `release_ready` needs every gate of every target). Tier-1 checks are unchanged or stricter: the CI Linux x86-64 library and Node addon must be byte-identical to the locally validated build, and the installed-package, userspace, Electron, rollback and licence checks run on the shipped (CI-assembled) packages. `tests/compare-profiles.py` measures drift between native profiles.
- **Release assets for several platforms:** `scripts/release-assets.py` assembles from the release packages; `scripts/check-release-assets.sh` holds the checks `attest-release` runs (now also: the native platforms are exactly the qualified targets), `scripts/release-gates.jq` the `gates.json` check it shares with `verify-release-assets.sh` (0.1.0 and 0.2.0 still pass); the attestation predicate names each target's tier; `publish-release` uploads every target's wheel to PyPI.
- **Windows in CI and `validate.py`:** `scripts/validate.py` runs on Windows (`--node-lib`, `--cmake-arg`) and lists every check that does not apply under `skipped` with its reason (fault injection, the QEMU CPU guard, the release checker and asset tests). CI checks that the DLL exports exactly the `dtl_*` functions (`scripts/check-abi.py` reads PE and Mach-O exports), runs the Node and Go suites and the Electron consumer (directory and ASAR), and with the real model gets exact agreement between all eight bindings (`tests/parity.py`). The stress test measures committed memory, handles and threads on Windows, leaving out the workers Windows starts for its own thread pools. The Windows job moved to `native.yml` ("Native checks"); `platforms.yml` keeps macOS.

### Fixes

- **CPU guard on Windows:** MSVC-style x86 builds (clang-cl) did not check for FMA and F16C at open, although they use them, and reported the profile `native-cpu:avx+avx2+bmi2+sse4.2`; they now require both like the other x86-64 builds and report the x86-64 profile.
- **Non-ASCII router paths on Windows:** C ABI strings are UTF-8, but on Windows route paths were read as ANSI code page text, so a route with any non-ASCII character in its path did not open. `dtl_open` was not affected. Tests on every platform now open a model through a path with Latin-1, CJK and non-BMP characters, directly and as a route.
- **Test flake:** the real-model cross-thread cancellation test (`real-control`) no longer depends on timing.

## 0.2.0 (2026-10-22)

C ABI 2 (`libducktape_laya.so.1`). Breaking before 1.0, hence the minor version. Migration notes are in [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md#migrating-from-010-abi-1-to-020-abi-2).

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

### New features

- **Windows x64 (in progress):** the library builds with clang-cl; the model-free native tests and the Python, Rust, Java and .NET suites pass in CI (the loaders find `ducktape_laya.dll`, Rust links its import library). Node, Go, packages and real-model checks are not ported yet. Text files are checked out with LF everywhere (`.gitattributes`), so patch and source hashes match on Windows.
- **macOS on Apple silicon** builds from a checkout and is tested in CI (macOS 15 arm64) with and without the real model; the library exports only `dtl_*`, uses an `@rpath` install name and checks dot product and FP16 at open. The Python, Java, .NET and Rust loaders find `libducktape_laya.dylib`. No macOS packages yet.
- **Linux arm64** builds (Armv8.2-A with dot product and half precision, checked at open like AVX2 on x86-64) and is tested in CI with and without the real model. Packaging on arm64 produces linux-arm64 packages (tarball, npm, wheel, jar, NuGet), and the Node, Python, Java and .NET loaders pick the library for their architecture; releases publish x86-64 packages only until arm64 is release-qualified.

- **Open config:** `threads`, `context_tokens` (prompt capacity, 32 up to the model's trained length, default 512, replacing the fixed 512), `overflow` and `temperatures`, for an engine, for every route of a router, and per route. Requests may set `max_tokens`, `temperatures`, `overflow` and `max_windows`. Temperatures must be finite and positive after conversion to a 32-bit float (`1e-50` and `1e39` are rejected). Routes share a loaded model only when their path and configured values are equal, and each keeps its own default `overflow`.
- **Long input:** overflow policies `reject` (default), `truncate_end`, `truncate_start` and `scan`. Except under `reject`, over-long instructions and options are shortened as llama.cpp's decision server does; the question and options must still fit the limit on their own under every policy. Whenever the effective policy (the request's, or the engine's or route's default) is not `reject`, `usage` reports `state_tokens`, `state_tokens_dropped`, `truncated_questions` and `windows` (the most state windows any question used); scanned answers carry `window`. `"aggregate": "all"` makes a scanned `noul` question take the minimum over windows.
- **Choices:** `choice` criteria may be a list of labels; a single-option `choice` (and a one-value schema `enum`) is answered without inference.
- **Models:** the multilingual (F16) and typed-decisions (Q8_0) checkpoints, built with `scripts/convert-model.py` from `models/laya-multilingual.json` and `models/laya-typed-decisions.json` (no download URL yet). The text between mask tokens is tokenized on its own, as the models were trained; tokenizing the whole prompt at once dropped whitespace before a mask token with the multilingual vocabulary.
- **Routing by language:** a router's `"auto": {"english", "default"}` and requests with `"model": "auto"` and an optional `"lang"` (1–64 bytes); `usage.routing` reports the reason.
- **Runtime info:** features `language_routing` and `overflow_policies`, an `overflow_policies` list, and limits `minimum_context_tokens`, `scan_windows`, `object_members` and `windows_per_call`.
- **Bindings:** Python, Go, Java/Kotlin and .NET (not yet published to their registries). Node `execute` and the browser take a `requestId`; the browser `openRouter` takes `auto` and per-model `contextTokens`, `overflow` and `temperatures`.
- **Node and browser errors:** both packages export `Status`; argument errors carry `status` 1; queue, closed and worker failures carry `code` `ERR_LAYA_QUEUE_FULL`, `ERR_LAYA_CLOSED` or `ERR_LAYA_WORKER_EXITED`.
- **Examples:** request presets in `examples/presets/` (support triage, email triage, prompt-injection screening, moderation).

### New limits and validation

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
