# Ducktape Laya

Embed local Laya classification in C/C++, Rust, Node.js and Electron applications.
One C++ implementation uses a pinned llama.cpp runtime; thin bindings share the
same model loading, inference and result formatting. No HTTP server, Ollama,
Python runtime, child inference process or automatic downloads.

**Status: working Linux CPU prototype.** Real `choice`, `noul` (probability of
true) and ordinal `score` inference is implemented for the pinned English Q8_0
model. It does not generate chat. Windows/macOS builds, Electron installers,
prebuilt packages and GPU support remain release work. See the
[reviewed plan](docs/PLAN.md) and [validation report](docs/VALIDATION.md).

**Scope after comparing existing libraries:** focus on compact Q8 GGUF deployment
and a shared native engine with a managed Node worker. General C/Rust embedding
already exists in [LibLayaX](https://github.com/DaragonTech/LibLayaX) and
[RLaya](https://github.com/DaragonTech/RLaya); rich TypeScript/browser workflows
already exist in [laya-ts](https://github.com/NandhaKishorM/laya/tree/main/laya-ts).
Our measured footprint is smaller than the tested FP32 alternatives, but this
prototype has fewer features and platforms. The [comparison and feature plan](docs/PLAN.md#evaluation-decisions-2026-10-13)
records the tradeoffs, real-model checks and remaining gaps. No ONNX switch or
binding removal was warranted by this evaluation.

## Build and try

Requires CMake 3.22+, a C/C++17 compiler and Git. The default Linux x86-64 CPU
build requires AVX2, FMA, F16C, BMI2 and SSE4.2; it is not yet a universal binary
for older CPUs. Only Linux x86-64 has been tested. Initialize the pinned source:

```sh
git submodule update --init --recursive
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j 3
```

Supply a local model, using `models/manifest.json` for the exact tested artifact.
For this machine, the existing cached file works:

```sh
export LAYA_MODEL="$HOME/.cache/huggingface/hub/models--ggml-org--Laya-GGUF/snapshots/22265007700297ba9e128297e82540cf28c5d7d4/Laya-Q8_0.gguf"
python3 scripts/verify-model.py "$LAYA_MODEL"
./build/laya-decide "$LAYA_MODEL" < examples/request.json
```

Python 3.11+ is only used for optional developer verification, never by the library.
The model is about 449 MB on disk and is intentionally not in Git. An app can
ship it as an ordinary resource in the same installer as the native library.

## C and C++

The public ABI is [ducktape_laya.h](include/ducktape_laya.h): `dtl_open`,
`dtl_decide`, `dtl_close`, `dtl_free`. See [the plain-C example](examples/decide.c).
All errors cross the ABI as integer statuses with optional owned messages.
Release every returned string with `dtl_free`, including error messages.

```cpp
#include <ducktape_laya.hpp>
ducktape::laya engine("resources/Laya-Q8_0.gguf");
std::string result = engine.decide(request_json);
```

The RAII wrapper releases the engine automatically. Link `ducktape_laya` and
ship its shared library with your app. `cmake --install build --prefix staging`
stages headers and the library. Model weights are not installed by CMake.

## Rust

Use the path crate in `bindings/rust/ducktape-laya`. It links the same native
library, with a raw sys crate underneath. It does not rebuild/download llama.cpp.

```rust
let engine = ducktape_laya::Engine::open("resources/Laya-Q8_0.gguf", 2)?;
let result_json = engine.decide(&request_json)?;
```

```sh
export DUCKTAPE_LAYA_LIB_DIR="$PWD/build"
export LD_LIBRARY_PATH="$PWD/build${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
cargo run --manifest-path bindings/rust/Cargo.toml -p ducktape-laya \
  --example decide -- "$LAYA_MODEL" < examples/request.json
```

`Engine` is `Send + Sync`; native calls on one handle serialize. Calls block the
calling thread. Use a blocking worker pool from async Rust. Dropping the last
owner unloads the model.

## Node.js / Electron

Requires Node.js 20+ and matching platform development headers to build the addon:

```sh
cmake -S . -B build -DDUCKTAPE_BUILD_NODE=ON \
  -DNODE_INCLUDE_DIR=/path/to/include/node
cmake --build build -j 3
node bindings/node/decide.cjs "$LAYA_MODEL" < examples/request.json
```

```js
const { Laya } = require('./bindings/node/index.cjs');
const engine = await Laya.open(modelPath, { threads: 2, maxPending: 16 });
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
```

The public API is asynchronous. A dedicated worker thread owns the native handle;
there is no child process. Accepted requests run FIFO, excess work is rejected,
and `close()` drains accepted work. Always close the engine so the worker can
exit. Pass `addonPath` or set `DUCKTAPE_LAYA_ADDON` when moving the addon.

In Electron, create this engine in the main process and expose a narrow,
validated IPC method to renderers with context isolation enabled. Package the
GGUF, `.node`, and shared library as unpacked resources, then resolve paths under
`process.resourcesPath`. A signed, clean-machine-tested installer is a later
milestone; no ready-made Electron package is claimed here.

## Request contract

The shape matches the text-only `/v1/systemone` request in
[examples/request.json](examples/request.json). Results contain `answers` and
`usage`; filesystem paths and server routing fields are intentionally omitted.
`score` is the expected zero-based criterion index, not a probability.

- Non-null `state` and `instructions` can be text or structured JSON.
- `choice`: 2–255 named options, subject to the token budget.
- `score`: 2–10 ordered levels. `noul`: optional `false`/`true` descriptions.
- At most 32 questions, 1 MiB of UTF-8 JSON, and 512 prompt tokens per question.
- JSON nesting is limited to 64 levels.
- Model-specific instruction/option budgets are also enforced. Where the server
  truncates, this library returns an error so the application can shorten input.
- Questions run separately, one forward pass each. No model text is generated.
- JSON insertion order affects answers. The core preserves it. JavaScript
  reorders integer-like object keys; pass a raw JSON string to preserve their
  literal order. Avoid duplicate keys in requests.
- One handle serializes calls; independent engines use separate model/context
  memory. Native callers must never race destruction against calls. Backend
  registration lasts for the process lifetime; model/context resources unload.

The model file contains a Jinja template and must be a trusted app resource.
Use the pinned checksum during packaging. Reported probabilities/confidence
are model estimates; evaluate them on your application's own data.

## Tests

```sh
cmake -S . -B build -DDUCKTAPE_TEST_MODEL="$LAYA_MODEL"
cmake --build build -j 3
ctest --test-dir build --output-on-failure
cargo test --manifest-path bindings/rust/Cargo.toml
LAYA_MODEL="$LAYA_MODEL" node --test bindings/node/test.cjs
```

Without a model path, CTest runs ABI/error tests only and Node skips its inference
test. Rust tests use the native library loader path shown above.

Code is MIT. See [third-party notices](THIRD_PARTY_NOTICES.md) for runtime and
model licenses. This repository is independent of the upstream Laya project.

For developer-only comparison with a server built from the same source/settings:

```sh
cmake -S . -B build -DDUCKTAPE_BUILD_REFERENCE_SERVER=ON
cmake --build build --target llama-server -j 3
./build/bin/llama-server -m "$LAYA_MODEL" --host 127.0.0.1 --port 18474 \
  --device none --gpu-layers 0 --no-op-offload --threads 2 --threads-batch 2 \
  --ctx-size 512 --batch-size 512 --ubatch-size 512 --parallel 1 \
  --flash-attn auto --no-webui
# In another terminal:
python3 tests/parity.py "$LAYA_MODEL" --server-url http://127.0.0.1:18474
```

The reference server is a test tool only; the library never links it. Turn its
build off again with `-DDUCKTAPE_BUILD_REFERENCE_SERVER=OFF` for normal builds.
Run `tests/parity.py` without `--server-url` for exact agreement across the four
language interfaces with no server or network access.
