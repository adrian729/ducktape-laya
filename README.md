<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/ducktape-lockup-dark.svg">
    <img src="assets/ducktape-lockup-light.svg" alt="ducktape laya" width="440">
  </picture>
</p>

Embed local Laya decisions in C/C++, Rust, Node.js and Electron, Python, Go, Java/Kotlin, .NET and the browser (WebAssembly/WebGPU). One native library loads a GGUF model and returns `choice`, `noul` (probability of true) and ordinal `score` results. Inference needs no server, Python or network access. This is a decision model; it does not generate chat text.

**Status:**

- **0.2.0 (current release, 2026-10-22):** [GitHub Releases](https://github.com/adrian729/ducktape-laya/releases/tag/v0.2.0), npm (`ducktape-laya`, `ducktape-laya-browser`), crates.io (`ducktape-laya`, `ducktape-laya-sys`), PyPI (`ducktape-laya`), NuGet (`DucktapeLaya`) and the Go module proxy (`bindings/go`, tag `bindings/go/v0.2.0`). Every release asset is attested. C ABI 2 with a breaking API change ([migration](docs/COMPATIBILITY.md#migrating-from-010-abi-1-to-020-abi-2), [changelog](CHANGELOG.md)), new features (below), and the multilingual and typed-decisions models next to the English one, all held to the same gates: agreement with the original Laya's recorded results and with upstream llama.cpp. All registry packages are Linux x86-64 only; the browser build is [experimental](docs/BROWSER.md). Maven Central (`io.github.adrian729:ducktape-laya`) is coming shortly; until then the Java jar, sources and javadoc jars and POM are on the GitHub release. Linux arm64, macOS and Windows build from a checkout (see the requirements below).
- **0.1.0:** earlier release (C ABI 1, English model), still on npm and crates.io under the 0.1 versions.

This README and the guides in `docs/` describe main, which is 0.2.0 until the next release.

## Features

- Three question types, batches of up to 32 requests, and [structured decisions](docs/API.md#structured-decisions) from a small JSON Schema subset.
- [`answer_confidence` and `confidence`](docs/API.md#primitive-requests-and-results) on every answer: the calibrated probability of the answer, and how concentrated the distribution is.
- [Long input](docs/API.md#long-input): reject (default), truncate from either end, or scan the whole text in windows.
- [Engine configuration](docs/API.md#engine-configuration): threads, prompt capacity up to the model's trained length, and calibration temperatures.
- [Local routing](docs/API.md#local-routing) between several models, including [routing by language](docs/API.md#choosing-the-model-by-language) between the English and multilingual models.
- Deadlines, cooperative cancellation and passive events in every binding, with the same results from every binding on the same build for the same request text (Node and the browser serialize request objects with `JSON.stringify`, which writes `149.0` as `149`; see [the API guide](docs/API.md#primitive-requests-and-results)).

## Requirements

Native packages (C/C++, Rust, Node.js, Electron) need:

- **CPU:** Linux x86-64 with AVX2, FMA, F16C, BMI2 and SSE4.2: Intel Haswell,
  AMD Excavator/Zen or newer. Some low-power Atom, Celeron and Pentium chips lack
  AVX2. On those, opening an engine fails with status 7
  (`DTL_UNSUPPORTED_PLATFORM`) and names the missing feature.
- **System libraries:** glibc 2.38+ and libstdc++ with `GLIBCXX_3.4.29`, as in
  Ubuntu 24.04+, Debian 13+ and Fedora 39+. Tested on Ubuntu 24.04 and 26.04.
  Windows is not supported yet: the library builds with clang-cl, and the
  model-free native tests and the Python, Rust, Java and .NET suites pass in CI;
  Node, Go, packages and real-model checks are not ported.
- **Linux arm64 (0.2.0, from a checkout):** CI builds and tests it on Ubuntu
  24.04 arm64, including real-model agreement with llama.cpp and the original
  Laya and the installed packages (tarball, npm, wheel, jar, NuGet). It needs
  Armv8.2-A with dot product and half precision (Graviton2+, Ampere Altra,
  Cortex-A76 / Raspberry Pi 5); opening an engine elsewhere fails with status 7.
  Releases still publish x86-64 packages only: arm64 is not yet release-qualified.
- **macOS on Apple silicon (0.2.0, from a checkout):** CI builds and tests it on
  macOS 15 arm64, with and without the real model (agreement with llama.cpp and
  the original Laya, every binding suite). Build it from a checkout and point
  the bindings at `libducktape_laya.dylib` with `DUCKTAPE_LAYA_LIB_DIR`; there
  are no macOS packages yet. Intel Macs are not supported.
- **Memory:** about 0.5 GB per open engine with the English model and the default 512-token context (measured natively: 475 MiB after opening, 600 MiB while deciding). Plan for 1 GB per engine. Other models and longer contexts need more; see [other checkpoints](#other-laya-checkpoints).
- **Disk:** 449 MB for the English model, plus 15 MB for the library or npm package.
- **Model:** `Laya-Q8_0.gguf`, downloaded separately (see below).

What each package needs on top:

| Package | Install | Also needs |
| --- | --- | --- |
| Node.js | `npm install ducktape-laya` | Node.js 20+ (tested on 24 and 26). The native addon is prebuilt; no compiler. |
| Electron | `npm install ducktape-laya` | Tested with Electron 44. Unpack the whole `node_modules/ducktape-laya` package (its worker script and native files) and the model from ASAR. |
| Rust | `cargo add ducktape-laya` | The native library from the [v0.2.0 release tarball](https://github.com/adrian729/ducktape-laya/releases/tag/v0.2.0), found through `pkg-config` or `DUCKTAPE_LAYA_LIB_DIR`. Tested with Rust 1.98. |
| C/C++ | [v0.2.0 release tarball](https://github.com/adrian729/ducktape-laya/releases/tag/v0.2.0) (`ducktape-laya-0.2.0-linux-x64.tar.gz`) | A C or C++ compiler; CMake 3.22+ or `pkg-config` to link. |
| Python | `pip install ducktape-laya` | Python 3.10+. The wheel (`manylinux_2_38_x86_64`) bundles the native library; no compiler. See [bindings/python](bindings/python/README.md). |
| Go | `go get github.com/adrian729/ducktape-laya/bindings/go@v0.2.0` | Go 1.22+, cgo with a C compiler, and the native library and header from the [v0.2.0 release tarball](https://github.com/adrian729/ducktape-laya/releases/tag/v0.2.0). See [bindings/go](bindings/go/README.md). |
| Java/Kotlin | `ducktape-laya-0.2.0.jar` from the [GitHub release](https://github.com/adrian729/ducktape-laya/releases/tag/v0.2.0); Maven Central coming shortly | Java 22+ (FFM API), run with `--enable-native-access`. The jar bundles the native library. See [bindings/java](bindings/java/README.md). |
| .NET | `dotnet add package DucktapeLaya` | .NET 10. The package bundles the native library. See [bindings/dotnet](bindings/dotnet/README.md). |
| Browser (experimental) | `npm install ducktape-laya-browser` | Tested on Chromium 152 only. WASM SIMD in a module worker, secure context; WebGPU is optional. See the [browser guide](docs/BROWSER.md) and [package README](bindings/browser/README.md). |

The versions above are minimums the packages declare. CI tests Node 24 and 26, Rust 1.98, Go 1.27, Java 25, .NET 10 and the Ubuntu 24.04 runner's system Python; the older minimums (Node 20, Go 1.22, Java 22, Python 3.10) are not tested. `./demo ts` needs Node 22.6+ (`--experimental-strip-types`).

The packages and the release tarball above are 0.2.0, for Linux x86-64. The [Node](bindings/node/README.md), [Rust](bindings/rust/ducktape-laya/README.md), [Python](bindings/python/README.md), [Go](bindings/go/README.md), [Java](bindings/java/README.md) and [.NET](bindings/dotnet/README.md) READMEs describe the 0.2.0 APIs. On other platforms, build the library from a checkout ([manual setup](#manual-setup)).

Building from source instead needs CMake 3.22+, a C/C++17 compiler and Git. The
setup wizard and `scripts/download-model.py` need Python 3; the maintainer
scripts that import `scripts/release_support.py` (`convert-model.py`,
`verify-model.py`, `check-release.py`, the packaging and validation scripts) need
Python 3.11+.

## Optional setup wizard

From a clone with submodules (see below), run `python3 scripts/setup.py`. It asks
where to install the library and model, and can build a local Node.js package.
At the end, it prints project paths and settings. It does not edit your shell
profile or `PATH`.

```sh
python3 scripts/setup.py                 # interactive
python3 scripts/setup.py --yes --dry-run # preview without installing
python3 scripts/setup.py --yes           # accept defaults (~/.local)
```

Use `--node` for Node.js or `--skip-model` if you already have the GGUF. Run
`python3 scripts/setup.py --help` for prefix, model-directory and Node-header
options. The wizard is optional; the manual steps follow.

## Manual setup

You need CMake 3.22+, a C/C++17 compiler and Git. The default Linux x86-64 build
requires AVX2, FMA, F16C, BMI2 and SSE4.2; the Linux arm64 build requires
Armv8.2-A with dot product and half precision.

```sh
git clone --recurse-submodules https://github.com/adrian729/ducktape-laya.git
cd ducktape-laya
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j 3
cmake --install build --prefix "$PWD/build/staging"
```

The 449 MB model is not in Git or the native, npm or Rust packages. Download the
exact tested file and check it against [the pinned manifest](models/manifest.json):

```sh
mkdir -p resources
curl --fail --location \
  'https://huggingface.co/ggml-org/Laya-GGUF/resolve/22265007700297ba9e128297e82540cf28c5d7d4/Laya-Q8_0.gguf' \
  --output resources/Laya-Q8_0.gguf
printf '%s  %s\n' \
  'c06528c5746d3bb8baa72a27938be95abbfd0b226f8471e8a9e365ed0bb066d2' \
  'resources/Laya-Q8_0.gguf' | sha256sum --check
export LAYA_MODEL="$PWD/resources/Laya-Q8_0.gguf"
```

For a shorter explicit download command, use
`python3 scripts/download-model.py resources`; it verifies the file before
placing it there. Applications must pass their own local model path to the
library. `LAYA_MODEL` is only a convenience for examples and tests.

### Other Laya checkpoints

The original Laya also publishes a multilingual checkpoint (mmBERT-base, 100+ languages) and a typed-decisions checkpoint. On main both are supported and qualified like the English model: against the original's recorded results and against upstream llama.cpp processing. No official GGUF exists yet, so build them from the official checkpoint with the pinned converter (in the repository root, where `.venv-convert` is ignored by Git). The script
downloads the source files at a fixed revision, checks their hashes, converts,
and checks the result against the manifest:

| Model | Manifest | File | Precision |
|---|---|---|---|
| Multilingual | [models/laya-multilingual.json](models/laya-multilingual.json) | 658 MB | F16: Q8_0 moved probabilities up to 0.056 from the original's, over the 0.05 limit |
| Typed decisions | [models/laya-typed-decisions.json](models/laya-typed-decisions.json) | 449 MB | Q8_0 |

```sh
python3 -m venv .venv-convert
.venv-convert/bin/pip install -r third_party/llama.cpp/requirements/requirements-convert_hf_to_gguf.txt
cmake -S third_party/llama.cpp -B build-llama-tools -DGGML_OPENMP=OFF -DBUILD_SHARED_LIBS=OFF
cmake --build build-llama-tools --target llama-quantize -j 3
python3 scripts/convert-model.py models/laya-typed-decisions.json resources/laya-typed-decisions-q8_0.gguf \
  --python .venv-convert/bin/python --quantize build-llama-tools/bin/llama-quantize
```

Both were trained with 1,024-token prompts; open them with `context_tokens`
1024 to match the original. The files accept up to 8,192, but cost grows faster
than the prompt length, and longer prompts are untested for accuracy. Native measurements, one yes/no question, 4 threads, on an i7-1165G7 that was also running other work (the browser build is wasm32, with a heap of at most 4 GiB, so the largest contexts do not fit there):

| Prompt tokens | Multilingual (F16): time, peak memory | Typed decisions (Q8_0): time, peak memory |
|---|---|---|
| 512 | 3.2 s, 1.3 GB | 10 s, 0.6 GB |
| 1,024 | 7 s, 1.8 GB | 22 s, 0.7 GB |
| 2,048 | 19 s, 2.8 GB | 52 s, 1.0 GB |
| 4,096 | 55 s, 5.0 GB | 137 s, 1.5 GB |
| 8,192 | 172 s, 6.6 GB | 413 s, 2.7 GB |

## Use it in a project

For C++, point CMake at the installed prefix and link the target:

```sh
cmake -S . -B build -DCMAKE_PREFIX_PATH=/path/to/ducktape-laya/build/staging
```

```cmake
find_package(ducktape_laya 0.2 CONFIG REQUIRED)
target_link_libraries(my_application PRIVATE ducktape::laya)
```

The CMake package accepts only the same minor version, so a 0.1.0 installation (ABI 1) does not satisfy `0.2`.

```cpp
#include <ducktape_laya.hpp>
auto engine = ducktape::engine::open("/absolute/path/to/Laya-Q8_0.gguf");
std::string result = engine.execute(request_json);
```

`request_json` is your JSON request; see [the request example](examples/request.json)
and [API guide](docs/API.md). For C, use [ducktape_laya.h](include/ducktape_laya.h).
Set the normal shared-library loader path or bundle the library with your app.
Relative model paths use the process working directory. See
[packaging](docs/PACKAGING.md) for pkg-config, loader and distribution details.

For Rust, add the crate and point it at the installed native library (from the v0.2.0 release tarball, or your own build's `lib` directory):

```sh
cargo add ducktape-laya
```

```toml
[dependencies]
ducktape-laya = "0.2"
```

```sh
export DUCKTAPE_LAYA_LIB_DIR=/path/to/ducktape-laya/build/staging/lib
export LD_LIBRARY_PATH="$DUCKTAPE_LAYA_LIB_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
```

```rust
let engine = ducktape_laya::Engine::open("/absolute/path/to/Laya-Q8_0.gguf", &ducktape_laya::Config::default())?;
let result_json = engine.execute(&request_json)?;
```

For Node.js 20+, build the addon with Node development headers, then run the
source-checkout example:

```sh
cmake -S . -B build -DDUCKTAPE_BUILD_NODE=ON -DNODE_INCLUDE_DIR=/path/to/include/node
cmake --build build -j 3
export DUCKTAPE_LAYA_ADDON="$PWD/build/ducktape_laya.node"
node bindings/node/decide.cjs "$LAYA_MODEL" < examples/request.json
```

In an application, call `Laya.open(modelPath)` and close the engine when done:

```js
const { Laya } = require('./bindings/node/index.cjs');
async function main() {
  const engine = await Laya.open(process.env.LAYA_MODEL);
  try {
    const result = await engine.execute({
      state: 'Please refund the duplicate charge.',
      questions: {
        refund: { type: 'noul', instructions: 'Does the customer request a refund?' }
      }
    });
    console.log(result.answers.refund.noul);
  } finally {
    await engine.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
```

The wizard's `--node` option stages a package you can install into a separate
Node project. [Packaging](docs/PACKAGING.md) also shows how to build the npm
tarball manually. In an installed project, import it with
`require('ducktape-laya')`. For Electron, keep the GGUF and the whole
`node_modules/ducktape-laya` package unpacked from ASAR (the worker script is
started from the package directory) and open the engine in the main process.

Structured results, batches, model routing, limits and the full request format are in the [API guide](docs/API.md). Browser setup is in the [browser guide](docs/BROWSER.md). `npm install ducktape-laya` installs 0.2.0; the [Node README](bindings/node/README.md) describes its API (0.1.0 used `decide` and had no `contextTokens`).

## Verify and explore

Run `./demo cpp "$LAYA_MODEL"` for an interactive example. `examples/request.json`,
`examples/structured.json`, `examples/batch.json` and the presets in
`examples/presets/` are ready requests; `examples/decide.c` and
`examples/decide.cpp` (CMake targets `laya-decide` and `laya-decide-cpp`, built
with `-DDUCKTAPE_BUILD_EXAMPLES=ON`) and `bindings/rust/ducktape-laya/examples/decide.rs`
run one request from standard input. To run native tests against the downloaded
model:

```sh
cmake -S . -B build -DDUCKTAPE_TEST_MODEL="$LAYA_MODEL"
cmake --build build -j 3
ctest --test-dir build --output-on-failure
```

See [release checks](docs/RELEASE_CHECKS.md) for installed-package, Rust, Node and browser validation. Code is MIT; see [third-party notices](THIRD_PARTY_NOTICES.md) for runtime and model licenses. This repository is independent of the upstream Laya project.

## Documentation

- [API guide](docs/API.md): requests, results, configuration, routing, errors.
- [Changelog](CHANGELOG.md) and [compatibility and migration](docs/COMPATIBILITY.md).
- [Browser guide](docs/BROWSER.md) and [packaging](docs/PACKAGING.md).
- [Architecture](docs/ARCHITECTURE.md), [roadmap](docs/ROADMAP.md) and [product plan](docs/PLAN.md).
- [Release checks](docs/RELEASE_CHECKS.md), [qualification](docs/QUALIFICATION.md), [publishing](docs/PUBLISHING.md) and [upgrades](docs/UPGRADES.md).
- [Security](SECURITY.md): trust boundaries and vulnerability reporting.

## Alternatives

- [Official Laya / laya-ts](https://github.com/NandhaKishorM/laya/tree/main/laya-ts):
  richer TypeScript workflows and an ONNX browser path.
- [LibLayaX](https://github.com/DaragonTech/LibLayaX) and
  [RLaya](https://github.com/DaragonTech/RLaya): native C and Rust embedding.
- [laya.cpp](https://github.com/lkarlslund/laya.cpp): a dedicated native C++ runtime.
- [Candle-based laya-rust](https://github.com/aovestdipaperino/laya-rust) and
  [GGUF laya-rust](https://github.com/zerodegress/laya-rust): Rust inference.
- [receptron/laya](https://github.com/receptron/laya): another Node/ONNX integration.
