# Building from source

Build from a checkout to use unreleased changes, a platform without released packages (macOS on Apple silicon, or Linux arm64 and Windows x64 until the next release), or to work on the project. Applications that use a release do not need any of this; see the [README](../README.md#install).

## Get the source

You need Git, CMake 3.22+ and a C/C++17 compiler. The runtime (llama.cpp) is a submodule:

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

The default x86-64 build requires AVX2, FMA, F16C, BMI2 and SSE4.2 at run time and the arm64 build Armv8.2-A with dot product and FP16 ([platforms](PLATFORMS.md#cpu-check-at-open)). The installed prefix holds the headers, the shared library, CMake and pkg-config files, licences, the model manifest and `download-model.py`; [PACKAGING.md](PACKAGING.md) explains how applications link it. To point a binding at the build tree instead, set `DUCKTAPE_LAYA_LIB_DIR` to `build` (each package README has a "From a checkout" section).

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
| `DUCKTAPE_TEST_MODEL` | empty | A local GGUF; registers the real-model tests (`real-model`, `real-features`, `real-faults`, `cpu-guard` when `qemu-x86_64` is found, ...) |
| `DUCKTAPE_REAL_TEST_TIMEOUT` | `600` | Per-test timeout in seconds for the long real-model tests; raise it for instrumented builds |
| `DUCKTAPE_BUILD_EXAMPLES` | `OFF` | Developer examples: `laya-decide`, `laya-decide-cpp` and the demo programs |
| `DUCKTAPE_BUILD_NODE` | `OFF` | The Node-API addon; supply `NODE_INCLUDE_DIR`, and on Windows `NODE_LIB` |
| `DUCKTAPE_BUILD_FUZZERS` | `OFF` | Linux Clang libFuzzer target `laya-input-fuzzer`; instruments the library, never for artifacts |
| `DUCKTAPE_BUILD_REFERENCE_PROBE` | `OFF` | Test-only `laya-reference` executable linking upstream decision processing; never packaged |
| `DUCKTAPE_REPRODUCIBLE_PATHS` | `ON` | With GCC or Clang, maps source and build paths out of compiled artifacts (`-ffile-prefix-map`) |
| `DUCKTAPE_LLAMA_SOURCE` | `third_party/llama.cpp` | The llama.cpp source to build; any other directory reports its runtime commit as `unverified-custom-source` unless `DUCKTAPE_LLAMA_REVISION` is set (upgrade tooling does this) |
| `DUCKTAPE_WASM_OPTIMIZATION` | `2` | Emscripten builds only: link optimization `0`, `1`, `2`, `3`, `s` or `z`. `scripts/build-browser.py` sets `1`, the only value browser packaging accepts |
| `DUCKTAPE_WASM_MAXIMUM_MEMORY` | `4294967296` | Emscripten builds only: WebAssembly memory ceiling in bytes, a 64 KiB multiple from 64 MiB to 4 GiB. Lower values are for memory-pressure tests; packaging accepts only 4 GiB |

ggml's `GGML_CCACHE` compiles through `ccache` or `sccache` whenever one is on `PATH`; with `DUCKTAPE_REPRODUCIBLE_PATHS` (the default) this project defaults it to `OFF`. To compile through a cache, set `CMAKE_C_COMPILER_LAUNCHER` and `CMAKE_CXX_COMPILER_LAUNCHER` (the `dev` preset does). A cache does not change the compiled bytes as long as it hands the compiler the same arguments: ccache's `base_dir` (`CCACHE_BASEDIR`, or `base_dir` in a ccache configuration file) makes source paths relative, so `-ffile-prefix-map` no longer maps them and `__FILE__` strings in the library change; build with `CCACHE_BASEDIR=` (empty) to override a configured one. `validate.py` and `package-native.py` refuse a library or addon holding a source path outside the mapped directories (`./`, `./third_party/llama.cpp` and `./build`; Clang writes them without the leading `./`), such as `../src/laya.cpp` or an absolute path. The project's scripts set the launcher and `GGML_CCACHE` explicitly.

The GGML ISA options (`GGML_AVX2`, `GGML_FMA`, ...) select the CPU profile and are set explicitly for x86-64; AVX-512 and VNNI variants are refused. The library, ggml and llama.cpp are built with `-fstack-protector-strong`, `_FORTIFY_SOURCE=3` (optimised builds), full RELRO (`-z relro -z now`), `-mbranch-protection=standard` on Linux arm64 and Control Flow Guard (`/guard:cf`) with clang-cl.

## Run the tests

```sh
cmake -S . -B build -DDUCKTAPE_BUILD_TESTS=ON -DDUCKTAPE_TEST_MODEL=/path/to/Laya-Q8_0.gguf
cmake --build build -j 3
ctest --test-dir build --output-on-failure
```

Without `DUCKTAPE_TEST_MODEL`, only the model-free tests run. The binding suites run from their directories ([Rust](../bindings/rust/ducktape-laya/README.md#from-a-checkout), [Node](../bindings/node/test.cjs), [Python](../bindings/python/README.md#from-a-checkout), [Go](../bindings/go/README.md#from-a-checkout), [Java](../bindings/java/README.md#from-a-checkout), [.NET](../bindings/dotnet/README.md#from-a-checkout)) with `LAYA_MODEL` set for their real-model tests. `scripts/validate.py` builds and runs all of them at once:

```sh
npm ci --ignore-scripts --prefix tests/typescript   # TypeScript test tools, once
python3 scripts/validate.py --output build-check-model-free
python3 scripts/validate.py --model /path/to/Laya-Q8_0.gguf --output build-check
```

The model-free run also checks the Markdown links (`tests/docs-links.py`) and that every example request is valid. For a faster loop while working on one binding or on the bindings only, `--bindings rust,node` runs only the listed binding suites (and leaves out the cross-binding checks) and `--skip-native-tests` leaves out `ctest`; `--compiler-launcher ccache` compiles through ccache. Such a report lists what it left out under `skipped` and is never release evidence. What each check covers and the options for release evidence: [RELEASE_CHECKS.md](RELEASE_CHECKS.md).

### CMake presets

`CMakePresets.json` (CMake 3.22+) names three configurations, each in its own build directory:

| Preset | Build directory | Configuration |
|---|---|---|
| `dev` | `build-dev` | Ninja, Release, native tests, compiled through `ccache` (which must be on `PATH`) |
| `product` | `build-product` | The configuration `validate.py` packages: Release, library and Node addon, no tests, no compiler cache. Pass `-DNODE_INCLUDE_DIR=...`; on Linux use `CC=gcc CXX=g++` for the release compiler |
| `sanitize` | `build-sanitize` | Ninja, Clang AddressSanitizer and UndefinedBehaviorSanitizer at RelWithDebInfo, as `scripts/check-sanitizers.py` builds them, native tests; never packaged |

```sh
cmake --preset dev && cmake --build --preset dev -j 4 && ctest --preset dev
cmake --preset sanitize -DDUCKTAPE_TEST_MODEL=/path/to/Laya-Q8_0.gguf && cmake --build --preset sanitize -j 4 && ctest --preset sanitize
```

## Try it

`./demo [c|cpp|rust|js|ts] [model.gguf]` builds one small interactive example and asks three questions; `./demo --help` explains it. The model comes from the argument, `LAYA_MODEL`, or the Hugging Face cache. The ready-made requests and example programs are described in [examples/](../examples/README.md).

## Build the packages

`scripts/package-native.py` builds every package for the platform it runs on, in one run (`linux-x64`, `linux-arm64`, `macos-arm64` or `windows-x64`): the native archive, the npm package, the Rust crates, the Python wheel, the Maven jar with its sources and javadoc jars and POM, the NuGet package, an SBOM and an inventory. It needs a Release build with the Node addon, `npm`, Cargo with the locked dependencies fetched, a JDK (`javac`, `javadoc`), the .NET SDK, Python 3.11+ and, on Linux, `readelf`. The native tests are not needed (`cmake --preset product -DNODE_INCLUDE_DIR=/path/to/include/node` makes the same build in `build-product`):

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

Reconfiguring a build directory recompiles only what changed. The configure step copies and patches the runtime sources in a staging directory and replaces a patched copy in `runtime-patched/`, and the normalized `ggml-version.h`, only when its content differs (`cmake/generated_files.cmake`), so unchanged copies keep their timestamps; an edited copy is restored and recompiled. `python3 tests/reconfigure.py` checks this on a fresh build of the library (`--build DIR` on an existing one); CI runs it on the clang validation build.

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
