<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/ducktape-lockup-dark.svg">
    <img src="assets/ducktape-lockup-light.svg" alt="ducktape laya" width="440">
  </picture>
</p>

Embed local Laya decisions in C/C++, Rust, Node.js and Electron. One native
library loads a GGUF model and returns `choice`, `noul` (probability of true) and
ordinal `score` results. Inference needs no server, Python or network access.

**Status:** unreleased development library. Linux x86-64 and the pinned English
Q8_0 model are the validated native target. The browser build is
[experimental](docs/BROWSER.md). This is a decision model; it does not generate
chat text.

## Optional setup wizard

From a clone with submodules (see below), run `python3 scripts/setup.py`. It asks
where to install the library and model, and can build a local Node.js package.
At the end, it prints project paths and settings. It does not edit your shell
profile or `PATH`. The wizard requires Python 3.11+.

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
requires AVX2, FMA, F16C, BMI2 and SSE4.2.

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

## Use it in a project

For C++, point CMake at the installed prefix and link the target:

```sh
cmake -S . -B build -DCMAKE_PREFIX_PATH=/path/to/ducktape-laya/build/staging
```

```cmake
find_package(ducktape_laya 0.1 CONFIG REQUIRED)
target_link_libraries(my_application PRIVATE ducktape::laya)
```

```cpp
#include <ducktape_laya.hpp>
ducktape::laya engine("/absolute/path/to/Laya-Q8_0.gguf");
std::string result = engine.execute(request_json);
```

`request_json` is your JSON request; see [the request example](examples/request.json)
and [API guide](docs/API.md). For C, use [ducktape_laya.h](include/ducktape_laya.h).
Set the normal shared-library loader path or bundle the library with your app.
Relative model paths use the process working directory. See
[packaging](docs/PACKAGING.md) for pkg-config, loader and distribution details.

For Rust, add the checkout crate to your project's `Cargo.toml` and point it at
the installed native library:

```toml
[dependencies]
ducktape-laya = { path = "/path/to/ducktape-laya/bindings/rust/ducktape-laya" }
```

```sh
export DUCKTAPE_LAYA_LIB_DIR=/path/to/ducktape-laya/build/staging/lib
export LD_LIBRARY_PATH="$DUCKTAPE_LAYA_LIB_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
```

```rust
let engine = ducktape_laya::Engine::open("/absolute/path/to/Laya-Q8_0.gguf", 2)?;
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
    const result = await engine.decide({
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
`require('ducktape-laya')`. For Electron, keep the GGUF and native files
unpacked and open the engine in the main process.

Structured results, batches, model routing, limits and the full request format
are in the [API guide](docs/API.md). Browser setup is in the
[browser guide](docs/BROWSER.md).

## Verify and explore

Run `./demo cpp "$LAYA_MODEL"` for an interactive example. To run native tests
against the downloaded model:

```sh
cmake -S . -B build -DDUCKTAPE_TEST_MODEL="$LAYA_MODEL"
cmake --build build -j 3
ctest --test-dir build --output-on-failure
```

See [release checks](docs/RELEASE_CHECKS.md) for installed-package, Rust, Node
and browser validation. Code is MIT; see [third-party notices](THIRD_PARTY_NOTICES.md)
for runtime and model licenses. This repository is independent of the upstream
Laya project.

## Alternatives

- [Official Laya / laya-ts](https://github.com/NandhaKishorM/laya/tree/main/laya-ts):
  richer TypeScript workflows and an ONNX browser path.
- [LibLayaX](https://github.com/DaragonTech/LibLayaX) and
  [RLaya](https://github.com/DaragonTech/RLaya): native C and Rust embedding.
- [laya.cpp](https://github.com/lkarlslund/laya.cpp): a dedicated native C++ runtime.
- [Candle-based laya-rust](https://github.com/aovestdipaperino/laya-rust) and
  [GGUF laya-rust](https://github.com/zerodegress/laya-rust): Rust inference.
- [receptron/laya](https://github.com/receptron/laya): another Node/ONNX integration.
