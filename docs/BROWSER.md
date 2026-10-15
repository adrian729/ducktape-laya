# Browser package and validation

The browser uses the same C++ core, llama.cpp pin, GGUF, request planning,
calibration, routing, batching and structured projection as native bindings.
It runs in a private module worker. JavaScript owns asset fetching, queueing,
shutdown and platform errors; it does not implement a second classifier.

**Status: experimental; full release qualification is pending.** Native CPU ISA
profiles, WASM and WebGPU are distinct numerical profiles. Probabilities and
threshold decisions are not interchangeable across profiles. Strict same-backend
upstream tolerance is owned by `release/browser-toolchain.json`. API/structure
equivalence, same-backend upstream agreement and cross-profile drift measurement are
separate requirements.

## Build and package

`release/browser-toolchain.json` pins emsdk/Emscripten and its Dawn port. Install
that SDK in a local directory, without requiring a global shell profile change:

```sh
git clone https://github.com/emscripten-core/emsdk.git .research/emsdk
git -C .research/emsdk checkout 8f685c26ee9f672081e61e22c6aa60fa972d00a9
.research/emsdk/emsdk install 6.0.12
.research/emsdk/emsdk activate 6.0.12
python3 scripts/build-browser.py --emsdk .research/emsdk --backend wasm --build build-browser-wasm
python3 scripts/build-browser.py --emsdk .research/emsdk --backend webgpu --build build-browser-webgpu
python3 scripts/package-browser.py --wasm build-browser-wasm --webgpu build-browser-webgpu --output build/browser-package
```

The resulting npm tarball contains loader, worker, shared protocol declarations,
WASM and WebGPU assets, pinned toolchain metadata and license notices. It excludes
weights, test references, demos and native addons. The development upstream
reference is optional (`--reference`) and links upstream server processing only
into a separate test executable; those dependencies never enter the product.

## Asset contract

Serve the extracted package under an application-controlled URL, preserving its
relative paths. A bundler may copy it into the application's public assets.
For example, with the package at `/vendor/laya/`:

```js
import { Laya } from '/vendor/laya/index.mjs';
const engine = await Laya.open({
  modelUrl: '/models/Laya-Q8_0.gguf',
  sha256: 'c06528c5746d3bb8baa72a27938be95abbfd0b226f8471e8a9e365ed0bb066d2',
  backend: 'auto',
});
const result = await engine.execute(request, { timeoutMs: 10000, signal });
await engine.close();
```

`moduleUrl`, `wasmUrl`, `webgpuModuleUrl`, `webgpuWasmUrl` and `workerUrl` can
override every asset location. No public CDN or remote model repository is
hardcoded. Model URLs are always supplied by the host. A browser fetch still
occurs; offline apps must serve/package/cache these assets themselves. The
library does not install a service worker or persist user data/model weights.
Serve JavaScript with a JavaScript MIME type and WASM as `application/wasm`.
Normal same-origin/CORS, secure-context and CSP requirements apply, including
permission to create module workers and compile WASM.

`openRouter({id: {url, sha256}}, options)` maps logical routes to assets. Identical
URL/hash pairs are downloaded once and use one canonical model path. Native
routing validation/cache limits remain authoritative. `maxModelBytes` limits
total fetched model bytes (default 1 GiB), not peak process/GPU RAM. Download
buffers, filesystem storage, weights and inference buffers add memory overhead.
This is a wasm32 build with a 4 GiB heap ceiling; OOM may terminate its worker.

## Execution and failure semantics

The default backend is single-threaded WASM in a worker; it requires neither
SharedArrayBuffer nor cross-origin isolation. WebGPU requires a usable adapter,
the model's device limits/operations, and the JSPI/WASM-exceptions capabilities
of the pinned runtime. `auto` tries WebGPU initialization then WASM. An inference
failure does not silently retry against another backend or change probabilities.
GPU device loss/runtime abort fails pending work and terminates that engine's
worker; the host can explicitly reopen a WASM engine.

`maxPending` bounds accepted work, including cancelled calls awaiting worker
acknowledgment. `signal` rejects the host promise promptly. Isolated pages with
SharedArrayBuffer permit cooperative running CPU cancellation; otherwise the
worker processes cancellation when it can receive messages. WebGPU checks shared
control at question boundaries, without claiming interruption of a GPU kernel.
`capabilities.runningCancellation` reports the verified CPU capability. Deadlines
include queue time and use shared-core checks once execution starts. A rejection
does not prove immediate resource release. `close()` drains work;
`close({cancelPending:true})` requests cancellation and waits for cleanup.
Repeated `close()` returns the same promise.
`initializationTimeoutMs` accepts integer milliseconds from 1 through 86400000
(default 120000 per backend attempt). Aborting initialization terminates its
worker and prevents automatic backend fallback.

Hooks use the same native event contract and execute on the host thread. Callback
failures are passive. Low-level runtime text is suppressed unless `onDiagnostic`
is provided. Requests and results are not logged or sent to telemetry endpoints.

## Conformance and release gates

Browser release checks must cover primitive/schema/batch outputs, ordered hooks,
invalid/closed calls, asset budgets, route aliases, pre-cancellation, deadlines,
cancelling/idempotent shutdown and explicit WASM initialization fallback. Include
missing/corrupt assets, hash mismatch, initialization aborts, device loss, worker
failure, memory pressure and deployment of the extracted package. Capture the
browser version, GPU/driver, launch flags, model identity and build configuration;
flags that enable WebGPU experimentally do not establish default browser support.

`tests/browser.html` and `tests/browser-server.py` provide the browser test harness.
Export its `window.state` after completion to a local report. Compare against a
native run of the same model and fixtures, not a historical checked-in result:

```sh
python3 tests/parity.py /path/to/Laya-Q8_0.gguf --report build/native-results.json
python3 tests/compare-browser.py build/browser-results.json --native build/native-results.json
```

The comparator requires matching fixture sets, native key order mapped to JavaScript property order, a responsive main-thread
heartbeat, ordered events and finite values. It reports numeric drift and changed
choice/structured decisions without a cross-profile tolerance gate. Metadata,
token counts and result shapes remain exact. `tests/compare-browser-reference.py` separately compares with unchanged
upstream decision processing on the same backend.

For repeatable real-browser runs, install the locked developer tools and use an
installed Chrome. Build both backend reference probes with `--reference` first:

```sh
npm ci --ignore-scripts --prefix tests/browser-automation
node tests/browser-automation/run.mjs --model /path/to/Laya-Q8_0.gguf \
  --native build/native-results.json --output build-browser-check
```

The runner starts its own loopback-only harness, verifies the model, runs both
backends, upstream comparisons, lifecycle/assets/device-loss checks, and closes
the browser and server afterward. It records browser/platform, model, fixture
and asset identities. Same-backend upstream failures cause a failed exit;
cross-profile drift is retained as measured evidence, not probability equivalence. It defaults to headed
Chrome without custom launch flags; use `--headless` only when that environment
provides the intended GPU. `--isolated` adds COOP/COEP for shared-memory controls.
`--package-dir EXTRACTED_PACKAGE` tests shipped assets; `--backend wasm|webgpu`
selects a backend. Explicit `--skip-reference`/`--skip-controls` leave those checks
not run. Selected-check success is never full release approval.

Store run results in ignored build directories or CI/PR artifacts. Broader GPU,
physical CPU, isolated-page cancellation and minimum-browser coverage remain
release requirements. See [PLAN.md](PLAN.md) and [RELEASE_CHECKS.md](RELEASE_CHECKS.md).
