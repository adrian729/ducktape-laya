# Building from source

Build from a checkout to use unreleased changes, a platform without released packages (macOS on Apple silicon), or to work on the project. Applications that use a release do not need any of this; see the [README](../README.md#install).

## Get the source

You need Git, CMake 3.22+ and a C/C++17 compiler; the [development loop](#development-loop) also needs Ninja, ccache and, for cache hits across worktrees, CMake 4.1+. The runtime (llama.cpp) is a submodule:

```sh
git clone --recurse-submodules https://github.com/adrian729/ducktape-laya.git
cd ducktape-laya
```

Python 3 runs the optional setup wizard and `scripts/download-model.py`; the maintainer scripts (`convert-model.py`, `verify-model.py`, `check-release.py`, packaging and validation) need Python 3.11+.

## Build the library

Linux and macOS:

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DDUCKTAPE_BUILD_TESTS=OFF
cmake --build build -j 3
cmake --install build --prefix "$PWD/build/staging"
```

`-DDUCKTAPE_BUILD_TESTS=OFF` leaves out the native test executables, which a checkout builds by default ([run the tests](#run-the-tests)); the library is the same either way.

The default x86-64 build requires AVX, AVX2, FMA, F16C, BMI2 and SSE4.2 at run time and the arm64 build Armv8.2-A with dot product and FP16 ([platforms](PLATFORMS.md#cpu-check-at-open)). The installed prefix holds the headers, the shared library, CMake and pkg-config files, licences, the model manifest and `download-model.py`; [PACKAGING.md](PACKAGING.md) explains how applications link it. To point a binding at the build tree instead, set `DUCKTAPE_LAYA_LIB_DIR` to `build` (the Python, Go, Java, .NET and Rust package READMEs have a "From a checkout" section).

Windows x64: open an x64 Native Tools Command Prompt for Visual Studio and build with its clang-cl and Ninja. CMake also needs GNU `patch`, which Git for Windows includes:

```bat
set "CLANG=%VCINSTALLDIR%Tools\Llvm\x64\bin\clang-cl.exe"
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DDUCKTAPE_BUILD_TESTS=OFF "-DCMAKE_C_COMPILER=%CLANG%" "-DCMAKE_CXX_COMPILER=%CLANG%" "-DDTL_PATCH_EXECUTABLE=C:/Program Files/Git/usr/bin/patch.exe"
cmake --build build -j 3
```

The build produces `ducktape_laya.dll` and its import library `ducktape_laya.lib`.

### Node.js addon

Add `-DDUCKTAPE_BUILD_NODE=ON -DNODE_INCLUDE_DIR=/path/to/include/node` (the Node-API headers of your Node version). On Windows the addon also links `node.lib`: `python scripts/fetch-node-sdk.py build-node-sdk --lib` downloads the headers and `node.lib` for the running Node version, checks them against pinned SHA-256 values, and prints the `NODE_INCLUDE_DIR` and `NODE_LIB` values to pass. Run the checkout's binding with the built addon:

```sh
export DUCKTAPE_LAYA_ADDON="$PWD/build/ducktape_laya.node"
node bindings/node/decide.cjs /path/to/Laya-Q8_0.gguf < examples/request.json
```

### Setup wizard

`python3 scripts/setup.py` does the build, install and optional model download interactively: it asks where to install the library and model, can stage a local Node.js package (`--node`), and prints the paths and settings for your project at the end. It does not edit your shell profile or `PATH`. `--yes` accepts the defaults (`~/.local`), `--yes --dry-run` only prints the commands, `--skip-model` skips the download, and `--help` lists the other options.

### CMake options

| Option | Default | Meaning |
|---|---|---|
| `DUCKTAPE_BUILD_TESTS` | `ON` in a checkout | Native test executables and CTest registration. `OFF` when the project is added to another one (`add_subdirectory`, `FetchContent`), unless that project sets `BUILD_TESTING`, whose value it then takes |
| `DUCKTAPE_TEST_MODEL` | empty | A local GGUF; registers the real-model tests (`real-model`, `real-features`, `real-faults`, `cpu-guard` on Linux x86-64 non-sanitizer builds with `python3`; it finds QEMU when it runs, ...) |
| `DUCKTAPE_REAL_TEST_TIMEOUT` | `600` | Per-test timeout in seconds for the long real-model tests; raise it for instrumented builds |
| `DUCKTAPE_BUILD_EXAMPLES` | `OFF` | Developer examples: `laya-decide`, `laya-decide-cpp` and the demo programs |
| `DUCKTAPE_BUILD_NODE` | `OFF` | The Node-API addon; supply `NODE_INCLUDE_DIR`, and on Windows `NODE_LIB` |
| `DUCKTAPE_BUILD_FUZZERS` | `OFF` | Linux Clang libFuzzer target `laya-input-fuzzer`; instruments the library, never for artifacts |
| `DUCKTAPE_BUILD_REFERENCE_PROBE` | `OFF` | Test-only `laya-reference` executable linking upstream decision processing; never packaged |
| `DUCKTAPE_REPRODUCIBLE_PATHS` | `ON` | With GCC or Clang, maps source and build paths out of compiled artifacts (`-ffile-prefix-map`); the `dev` preset turns it off |
| `DUCKTAPE_DEV_BUILD` | `OFF` | Developer build: test programs, and the core where results cannot change, at `-O1` ([development loop](#development-loop)); never packaged |
| `DUCKTAPE_DEV_PCH` | `OFF` | Developer build: precompiled JSON and standard headers for the core and its JSON-heavy tests (GCC and Clang); never packaged |
| `DUCKTAPE_REFERENCE_PROBE_LAUNCHER` | empty | A compiler launcher (such as `ccache`) for the reference probe's own targets only; a configure-time guard fails if it would reach a target of the library or addon, and records the split in `generated/launcher-guard.json` |
| `DUCKTAPE_LLAMA_SOURCE` | `third_party/llama.cpp` | The llama.cpp source to build; any other directory reports its runtime commit as `unverified-custom-source` unless `DUCKTAPE_LLAMA_REVISION` is set (upgrade tooling does this) |
| `DUCKTAPE_WASM_OPTIMIZATION` | `2` | Emscripten builds only: link optimization `0`, `1`, `2`, `3`, `s` or `z`. `scripts/build-browser.py` sets `1`, the only value browser packaging accepts |
| `DUCKTAPE_WASM_MAXIMUM_MEMORY` | `4294967296` | Emscripten builds only: WebAssembly memory ceiling in bytes, a 64 KiB multiple from 64 MiB to 4 GiB. Lower values are for memory-pressure tests; packaging accepts only 4 GiB |

ggml's `GGML_CCACHE` compiles through `ccache` or `sccache` whenever one is on `PATH`; with `DUCKTAPE_REPRODUCIBLE_PATHS` (the default) this project defaults it to `OFF`. To compile through a cache, set `CMAKE_C_COMPILER_LAUNCHER` and `CMAKE_CXX_COMPILER_LAUNCHER` (the `dev` preset does). A cache does not change the compiled bytes as long as it hands the compiler the same arguments: ccache's `base_dir` (`CCACHE_BASEDIR`, or `base_dir` in a ccache configuration file) makes source paths relative, so `-ffile-prefix-map` no longer maps them and `__FILE__` strings in the library change. The `dev` preset sets `base_dir` on purpose, for cache hits across worktrees, and is never packaged; the other presets set no launcher, and every script that compiles through ccache sets `CCACHE_BASEDIR` and `CCACHE_SLOPPINESS` empty (overriding a configured `base_dir`) and `CCACHE_COMPILERCHECK=content`, and records `ccache --show-config` in its report. `validate.py` and `package-native.py` refuse a library or addon holding a source path outside the mapped directories (`./`, `./third_party/llama.cpp` and `./build`; Clang writes them without the leading `./`), such as `../src/laya.cpp` or an absolute path. The project's scripts set the launcher and `GGML_CCACHE` explicitly.

The GGML ISA options (`GGML_AVX2`, `GGML_FMA`, ...) select the CPU profile and are set explicitly for x86-64; AVX-512 and VNNI variants are refused. The library, ggml and llama.cpp are built with `-fstack-protector-strong`, `_FORTIFY_SOURCE=3` (optimised builds), full RELRO (`-z relro -z now`), `-mbranch-protection=standard` on Linux arm64 and Control Flow Guard (`/guard:cf`) with clang-cl. On Linux arm64 that gives return-address signing (PAC); the BTI landing pads are compiled in too, but the shipped binaries are not marked BTI-compatible, so BTI is not enforced: Ubuntu 24.04's glibc start files (`crti.o`, `crtn.o`) carry no BTI property, and the linker marks an output only when every input is marked. `package-native.py` checks the shipped Linux library and addon for full RELRO, the stack protector and library search paths only relative to the binary (`$ORIGIN`), the library for fortified calls, and on arm64 both for PAC instructions.

## Run the tests

```sh
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DDUCKTAPE_TEST_MODEL=/path/to/Laya-Q8_0.gguf
cmake --build build
ctest --test-dir build --output-on-failure
```

Without `DUCKTAPE_TEST_MODEL`, only the model-free tests are registered. The binding suites run from their directories ([Rust](../bindings/rust/ducktape-laya/README.md#from-a-checkout), [Node](../bindings/node/test.cjs), [Python](../bindings/python/README.md#from-a-checkout), [Go](../bindings/go/README.md#from-a-checkout), [Java](../bindings/java/README.md#from-a-checkout), [.NET](../bindings/dotnet/README.md#from-a-checkout)) with `LAYA_MODEL` set for their real-model tests; `scripts/validate.py` builds and runs them together ([validation runs](#validation-runs)).

The C++ conformance consumer (`tests/consumers/cpp.cpp`, target `laya-conformance-cpp`) is compiled at C++11, the oldest standard `ducktape_laya.hpp` supports: the installed-package check compiles the same file at the application's default standard (C++14 with MSVC), so a newer language feature in it fails the build here first.

### Development loop

```sh
cmake --preset dev
cmake --build --preset dev
ctest --preset dev
```

This is the loop after every change. The `dev` preset builds in `build-dev` with Ninja, Release and every target compiled through ccache (on `PATH`); `ctest --preset dev` runs the [generic tier](#test-tiers-and-categories), model-free, in about 2 s, four tests at a time. What the preset adds, and why:

- `DUCKTAPE_DEV_BUILD=ON`: the test programs are compiled at `-O1` everywhere, and the core's own sources (`src/`) at `-O1` on x86-64 and with Clang. Results cannot change there: the tests compare floating-point values within tolerances, x86-64 keeps ggml's FMA flags private to ggml, and Clang contracts per statement at every level; GCC on arm64 contracts differently by level, so the core keeps `-O3` there. clang-cl (where `-O1` means "minimize size") and the browser build ignore the option.
- `DUCKTAPE_DEV_PCH=ON`: `nlohmann/json.hpp` and the common standard headers are precompiled for the core and the JSON-heavy tests (GCC and Clang); the vendored runtime sources compiled into the library keep their own includes. The build preset sets the ccache sloppiness (`pch_defines,time_macros,include_file_mtime,include_file_ctime`) that ccache needs to cache users of a precompiled header.
- `CMAKE_LINK_DEPENDS_NO_SHARED=ON`: test programs are not relinked when only the library's content changed, so a library edit relinks the library alone.
- `DUCKTAPE_REPRODUCIBLE_PATHS=OFF`, `CMAKE_UNITY_BUILD_RELOCATABLE=ON` and, in the build preset, `CCACHE_BASEDIR=${sourceParentDir}`: no `-ffile-prefix-map` of the worktree's absolute path enters the command lines, the runtime's unity sources include their files by relative path, and ccache hashes paths relative to the build directory, so a dev build in another worktree hits the same cache (seconds instead of a cold build). Relocatable unity sources need CMake 4.1 or later; an older CMake ignores the variable and a second worktree recompiles the runtime's unity sources.

Together they make a one-file edit rebuild in seconds. Dev builds are never packaged or used as evidence: `validate.py`, `check-reproducibility.py`, `check-upgrade.py` and `build-browser.py` configure with `DUCKTAPE_DEV_BUILD=OFF` and `DUCKTAPE_DEV_PCH=OFF`, `package-native.py` and `package-browser.py` refuse a build with either on, `check-candidate.py` refuses such a report, and the packagers refuse the unmapped source paths a dev build compiles in.

For the real-model tests, configure the same build directory with the model and run the `dev-real` preset (every registered test, one at a time unless you pass `-j`):

```sh
cmake --preset dev -DDUCKTAPE_TEST_MODEL=/path/to/Laya-Q8_0.gguf
cmake --build --preset dev
ctest --preset dev-real            # or -j 4: the CPU-time tests stay serial
```

### Test tiers and categories

Every native test is registered in `CMakeLists.txt` with `dtl_add_test`, which requires a tier and a category and turns them into the CTest labels `tier-<tier>` and `cat-<category>`; a missing or unknown one is a configure error:

```cmake
dtl_add_test(NAME n COMMAND ... TIER generic|specialized|release CATEGORY native|real_model|faults
             [LABELS timing qemu] [PROCESSORS n] [SERIAL] [TIMEOUT s] [SKIP_RETURN_CODE n])
```

| Tier | Holds | Runs |
|---|---|---|
| `generic` | Model-free and fast: `abi-errors`, `control-errors`, `decision-rules`, `feature-errors`, `language-routing`, `stress-rule`, `tokenizer-partition`, and on Linux `ggml-faults` | After every change (`ctest --preset dev`), in every `generic-*` CI job and every `validate.py` run |
| `specialized` | Needs the real model, a platform tool (QEMU) or a quiet machine: the `real-*` tests, `cpu-guard`, and the timing checks `tokenizer-scaling` and `request-parse-scaling` | `ctest --preset dev-real`, `validate.py --model` (the `cat-real_model` tests; `--checks ctest` and `--full` run every test), the real-model CI shards |
| `release` | Release evidence only | None registered yet |

Categories: `native` (the library without a model), `real_model` (with the model) and `faults` (fault injection, Linux only). Extra labels: `timing` (a CPU-time ratio check; registered `SERIAL`, so `ctest -j` runs it alone) and `qemu` (the emulated-CPU guard). `PROCESSORS` is the most threads an engine of the test uses, which `ctest -j` counts. Select with anchored label regexes, since a bare `-L generic` matches any label containing the word:

```sh
ctest --test-dir build-dev -L '^tier-specialized$'
ctest --test-dir build-dev -L '^cat-faults$'
ctest --test-dir build-dev -L '^timing$'          # on an otherwise idle machine
ctest --test-dir build-dev -R '^tokenizer-scaling$'
```

Classifying a new test: `TIER generic` only when it needs no model and adds well under a second, because every change pays for it; anything slower, any model, and any timing or scaling measurement is `specialized`. A timing check is its own test with `LABELS timing` and `SERIAL`, never part of a correctness test (`tokenizer-partition` checks the tokens, `tokenizer-scaling` the time). A check the host prevents exits with the test's `SKIP_RETURN_CODE` (77), which CTest reports as skipped, never as passed. The required registrations stay listed in `scripts/validate.py` and `scripts/release_targets.py`, so a test that disappears fails validation.

### CMake presets

`CMakePresets.json` names one configuration per purpose, each in its own build directory. Every compile flag of the product comes from `CMakeLists.txt`; the presets add no compiler flags to it.

| Preset | Build directory | Configuration |
|---|---|---|
| `dev` | `build-dev` | The [development loop](#development-loop): Ninja, Release, ccache, native tests, the dev options |
| `product` | `build-product` | The options of the build `validate.py` packages, without the tests it also builds: Release, library and Node addon, no compiler launcher, no examples, fuzzers or reference probe. Pass `-DNODE_INCLUDE_DIR=...`; on Linux use `CC=gcc CXX=g++` for the release compiler; on Windows also pass `-G Ninja` and the clang-cl compilers, which the preset does not set |
| `sanitize` | `build-sanitize` | Clang AddressSanitizer and UndefinedBehaviorSanitizer (`-fno-sanitize-recover=all`) at RelWithDebInfo, native tests; never packaged |
| `sanitize-thread` | `build-sanitize-thread` | Clang ThreadSanitizer at RelWithDebInfo, native tests; never packaged |
| `fuzz` | `build-fuzz` | Clang, `DUCKTAPE_BUILD_FUZZERS=ON` with ASan and UBSan, `compile_commands.json` exported; builds `laya-input-fuzzer`; never packaged |

Test presets: `dev` (`^tier-generic$`, four at a time), `dev-real` (every test), `sanitize` and `sanitize-thread` (every test, with halting sanitizer options). `scripts/check-sanitizers.py`, `scripts/check-fuzz.py`, `scripts/verify.py` and CI's sanitizer and fuzz jobs configure through these presets and use these directories.

```sh
cmake --preset sanitize -DDUCKTAPE_TEST_MODEL=/path/to/Laya-Q8_0.gguf && cmake --build --preset sanitize && ctest --preset sanitize
cmake --preset fuzz && cmake --build --preset fuzz
```

### Validation runs

`scripts/validate.py` configures and builds with Ninja and the release options, runs a selection of checks and writes `report.json` and its logs to a new `--output` directory. Without options it runs the generic checks, the same as the `generic-*` CI jobs: the `tier-generic` tests, `tests/examples-smoke.py`, the ABI export check and `scripts/check-binary.py` (glibc floor, hardening, exports):

```sh
npm ci --ignore-scripts --prefix tests/typescript   # TypeScript test tools, once
python3 scripts/validate.py --output build-check-generic
python3 scripts/validate.py --model /path/to/Laya-Q8_0.gguf --output build-check-model     # adds the cat-real_model tests
python3 scripts/validate.py --library-only --bindings rust,node --output build-check-rust-node
python3 scripts/validate.py --library-only --policy all --packages --output build-check-policy
```

`--bindings LIST|all` adds the binding suites (concurrently; with `--library-only` and no `node` in the list the run builds no Node addon and needs no Node: the report has `selection.node_addon` false, `addon_sha256` and `node_version` null), `--packages` the packages and `tests/package-smoke.py`, `--policy all|platform` the policy checks (through `scripts/check-policy.py`), `--parity smoke|full` the cross-binding comparison, `--upstream-reference` the reference probe, `--checks LIST` names checks directly, and `--full` runs every check for release evidence ([RELEASE_CHECKS.md](RELEASE_CHECKS.md#native-validation)). Build jobs default to `DUCKTAPE_JOBS`, else as many as the CPUs and the available memory (0.6 GB per job) allow. A report that is not `--full` is never release evidence.

### Verify a batch

`scripts/verify.py` runs, once per batch of changes, the specialized checks a branch push leaves out. It works out which jobs the changes since each job's last success make stale (`scripts/ci_paths.py`), runs the stale local jobs, dispatches `native.yml` and `platforms.yml` for the stale CI jobs meanwhile, then prints one verdict and each job's time against its budget:

```sh
python3 scripts/verify.py --list        # what is stale; runs nothing
python3 scripts/verify.py               # every stale local and CI job: the run before merging to main
python3 scripts/verify.py --ci --all    # the push profile of both workflows (plus the real-model jobs with --real-model), whatever is stale
python3 scripts/verify.py --rerun-failed
```

| Local job | Runs |
|---|---|
| `local-bindings` | `validate.py --library-only --bindings all` |
| `local-packaging` | `validate.py --library-only --packages` |
| `local-sanitizers` | the `sanitize` preset and `ctest --preset sanitize -L '^tier-generic$'` (model-free, as CI's `sanitizers` job, even when `build-sanitize` was configured with a model) |
| `local-fuzz` | the `fuzz` preset and `check-fuzz.py --seconds 60` |
| `local-real-model` (with `--real-model`) | `validate.py --model MODEL --parity smoke --upstream-reference` |
| `local-reproducibility` | `check-reproducibility.py` |
| `local-browser-wasm`, `local-browser-webgpu` (with `--emsdk DIR`) | `build-browser.py --reference` into `build-browser-<backend>` |

Outputs go to `build-verify/<job>/`; the state of each local job (the last commit it verified) is kept per worktree at `git rev-parse --git-path verify-state.json`. A change to `native.yml`, `platforms.yml` or `.github/actions/` makes every CI job stale but no local job, since none reads the workflows or actions; a change to the classifier does. The release workflows (`attest-release.yml`, `publish-release.yml`) run no verification job: a change to them runs only their policy checks. Windows, Linux arm64 and macOS run only in CI. Other options: `--local` or `--ci` (one side; any CI dispatch, so also the default run when a CI job is stale, refuses a dirty tree or a HEAD not pushed to its branch, since a dispatch cannot target a SHA), `--all` (every job whatever is stale), `--only JOB[,JOB]` (refuses names a verify dispatch would not run, the candidate, qualify, warm and experiment jobs, and real-model jobs without `--real-model`; a named job that is not run, such as a browser job without `--emsdk`, fails the verdict), and `--real-model` with `--model PATH` (default `DUCKTAPE_TEST_MODEL` or the pinned file in the Hugging Face cache; without one the run prints `NOT RUN: real_model` and exits 1).

## Try it

`./demo [c|cpp|rust|js|ts] [model.gguf]` builds one small interactive example and asks three questions; `./demo --help` explains it. The model comes from the argument, `LAYA_MODEL`, or the Hugging Face cache. The ready-made requests and example programs are described in [examples/](../examples/README.md).

## Build the packages

`scripts/package-native.py` builds every package for the platform it runs on, in one run (`linux-x64`, `linux-arm64`, `macos-arm64` or `windows-x64`): the native archive, the npm package, the Rust crates, the Python wheel, the Maven jar with its sources and javadoc jars and POM, the NuGet package, an SBOM and an inventory. It needs a Release build with the Node addon, `npm`, Cargo with the locked dependencies fetched, a JDK (`javac`, `javadoc`), the .NET SDK, Python 3.11+ and, on Linux, `readelf`. The native tests are not needed (`cmake --preset product -DNODE_INCLUDE_DIR=/path/to/include/node` configures the same options in `build-product`; on Windows also pass the generator and compilers below):

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DDUCKTAPE_BUILD_TESTS=OFF -DDUCKTAPE_BUILD_NODE=ON -DNODE_INCLUDE_DIR=/path/to/include/node
cmake --build build -j 3
python3 scripts/package-native.py --build build --output build/packages
python3 tests/package-smoke.py build/packages                                  # no model needed
python3 tests/installed-packages.py build/packages /path/to/Laya-Q8_0.gguf      # real inference
```

Only the product build is packaged. The script refuses a build with a custom runtime source or revision, a build type other than `Release`, or compiler flags other than CMake's defaults. On Linux it strips the library and addon (`--strip-unneeded`, dynamic symbols kept) and requires full RELRO; on Windows it requires Control Flow Guard. It checks every binary before shipping it: on Linux the glibc and libstdc++ symbol versions against the declared floor, on macOS the deployment target (14.0), on Windows that the DLL exports exactly the `dtl_*` functions; and it rejects sanitizer, fuzzer-coverage or profiling instrumentation. Existing output directories are rejected, and nothing is published or downloaded. The [package artifacts](PACKAGING.md#package-artifacts) table lists what each file contains.

Other ways to build single packages are incomplete and must not be published: a `pip wheel` or `python -m build` of `bindings/python` has no native library, and neither has a plain `dotnet pack` without `-p:DucktapeLayaNativeLibrary=PATH`.

A release ships one npm package, one jar and one NuGet package for every platform. `scripts/assemble-packages.py` merges the per-platform outputs, using only the Python standard library:

```sh
python3 scripts/assemble-packages.py --output build/release-packages \
  packages-linux-x64 packages-linux-arm64 packages-windows-x64
python3 tests/package-smoke.py build/release-packages
```

The result keeps each platform's archive and wheel unchanged and adds one npm package (`os` and `cpu` list every platform; npm checks the two lists separately, so an unlisted combination such as Intel macOS installs but fails at load with a clear error), one jar with each `native/<platform>/`, one NuGet package with each `runtimes/<rid>/native/`, and the crates, sources and javadoc jars, POM and SBOM once. Everything except each platform's native files must be identical in every input, compared entry by entry; inputs from different commits, build sources or `SOURCE_DATE_EPOCH` are refused. The output depends only on the inputs' contents: sorted entries, fixed timestamps and permissions, LF text, no NuGet PDB, and the same JDK on every CI runner (javadoc output changes between JDK patch releases). The CI workflow `native.yml` builds each platform's packages on a GitHub-hosted runner, keeps them as the artifacts `packages-<platform>` and assembles them in `packages-assembled` ([RELEASING.md](RELEASING.md)).

### Build records

Packaging requires complete build records. CMake records a path-independent SHA-256 manifest of the project's sources, headers, binding sources, build configuration, runtime patches and packaged metadata, and each native target's binary hash, so rebuilding only the library cannot make an old Node addon pass. Run `cmake --build` after changing any of these inputs, including when reusing an older build directory; packagers reject missing records, changed inputs and changed binaries. Upstream llama.cpp keeps its separately checked runtime pin and patch identity: packaging requires the runtime checkout at the pinned commit without modifications, and the patches' recorded hashes. Because that is the tree's state at packaging time, the build also records the runtime files it compiles (`cmake/runtime_sources.cmake`: llama.cpp's and ggml's build scripts and headers, ggml's core and CPU backend, `src`, `common`, `vendor`, and the patched copies in `runtime-patched/`): at configure time in `generated/runtime-sources.configure.json`, and each time the library links, with the library's hash, in `ducktape_laya.runtime-sources.json`. `package-native.py`, `validate.py` and `check-stress.py` refuse a library whose link-time record differs from the runtime tree as it is now, or whose patched copies changed after configure or were made from other sources, so a library built from a modified runtime tree cannot be packaged after the tree is restored; rebuild (or reconfigure and rebuild) from the pinned tree. The record describes the tree when the library last linked. A runtime file reverted with a timestamp older than that link is not recompiled, so when an unrelated change relinks the library, the library still holds the change: such a link lists the file as `stale` (it changed since the previous link but is dated before it, compared to the second), and packaging refuses the build until the file is touched and rebuilt, or the build directory is fresh. Nothing of the record reaches the library, so the library's bytes do not change. The inventory's `packaging_source_commit` and `packaging_source_tracked_dirty` identify the packaging checkout, while `build_source` identifies the verified build inputs, so documentation-only commits do not become binary provenance. Packages normalize timestamps, ordering, ownership and permissions; `SOURCE_DATE_EPOCH` defaults to the packaging commit's timestamp. The model-free identity tests run with `python3 tests/packaging-identity.py` (also part of `validate.py`).

Reconfiguring a build directory recompiles only what changed. The configure step copies and patches the runtime sources in a staging directory and replaces a patched copy in `runtime-patched/`, and the normalized `ggml-version.h`, only when its content differs (`cmake/generated_files.cmake`), so unchanged copies keep their timestamps; an edited copy is restored and recompiled. `python3 tests/reconfigure.py` checks this on a fresh build of the library (`--build DIR` on an existing one); CI runs it with `--edit-copy` in the `generic-linux-x64-clang` job.

## Build the browser package

`release/browser-toolchain.json` pins the Emscripten SDK; `scripts/build-browser.py` checks the emsdk commit, SDK version and Emscripten commit before building (the Dawn WebGPU port's version and hash are recorded there but not verified). Install that SDK in a local directory; no global shell profile change is needed:

```sh
git clone https://github.com/emscripten-core/emsdk.git .research/emsdk
git -C .research/emsdk checkout 8f685c26ee9f672081e61e22c6aa60fa972d00a9
.research/emsdk/emsdk install 6.0.12
.research/emsdk/emsdk activate 6.0.12
python3 scripts/build-browser.py --emsdk .research/emsdk --backend wasm --build build-browser-wasm
python3 scripts/build-browser.py --emsdk .research/emsdk --backend webgpu --build build-browser-webgpu
python3 scripts/package-browser.py --wasm build-browser-wasm --webgpu build-browser-webgpu --output build/browser-package
```

The builds use ggml's WASM SIMD CPU kernels (`EMSCRIPTEN_SYSTEM_PROCESSOR=wasm32`; configuration fails otherwise) with the rounding patch described in [patches/README.md](../patches/README.md), for the CPU operations of both backends. The product build is what `build-browser.py` configures: link optimization `-O1`, no GPU operation tracing and the 4 GiB memory ceiling. Each build records these values, and `package-browser.py` rejects any other; `--trace-gpu` and `--maximum-memory` exist for test builds only. Browser build records bind both asset hashes to the source manifest and the actual WASM or WebGPU backend, so swapped builds are rejected.

The npm tarball contains the loader, worker, protocol declarations, WASM and WebGPU assets, toolchain metadata and licence notices; it excludes weights, test references, demos and native addons. `--reference` additionally builds a separate test executable that links upstream decision processing; it never enters the package. Browser validation: [RELEASE_CHECKS.md](RELEASE_CHECKS.md#browser).
