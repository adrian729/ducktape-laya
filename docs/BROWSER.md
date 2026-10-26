# Browser

The browser uses the same C++ core, llama.cpp pin, GGUF, request planning, calibration, routing, batching and structured projection as native bindings. It runs in a private module worker. JavaScript owns asset fetching, queueing, shutdown and platform errors; it does not implement a second classifier.

**Status: experimental.** 0.1.0 and 0.2.0 passed the browser release checks on Chromium 152 with one GPU (Intel Gen12LP), and 0.3.0 on Chrome 156.0.8078.47 with the same GPU. Other browsers and GPUs are untested. The WebAssembly and WebGPU builds compute slightly different probabilities from each other and from native builds, and a close decision can change between them ([numerical profiles](PLATFORMS.md#numerical-results-across-platforms)); each still matches upstream llama.cpp running on the same backend.

## Requirements

- A secure context (HTTPS or localhost) that allows module workers and WebAssembly compilation, with WASM SIMD. No cross-origin isolation or SharedArrayBuffer is needed, though an isolated page allows cancelling a running CPU call (below).
- WebGPU is optional: the `webgpu` backend needs a usable adapter, the model's device limits and operations, and JSPI and WebAssembly exceptions.
- The model file, served by your application. No CDN or model URL is built in.
- Memory: the build is wasm32 with a 4 GiB heap ceiling; see [memory limits](#memory-limits).

## Asset contract

Serve the extracted package under an application-controlled URL, preserving its relative paths. A bundler may copy it into the application's public assets. For example, with the package at `/vendor/laya/`:

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

`backend` defaults to `'wasm'`; `'auto'` tries WebGPU first. `moduleUrl`, `wasmUrl`, `webgpuModuleUrl`, `webgpuWasmUrl` and `workerUrl` can override every asset location. No public CDN or remote model repository is hardcoded. Model URLs are always supplied by the host. A browser fetch still occurs; offline apps must serve/package/cache these assets themselves. The library does not install a service worker or persist user data/model weights. Serve JavaScript with a JavaScript MIME type and WASM as `application/wasm`. Normal same-origin/CORS, secure-context and CSP requirements apply, including permission to create module workers and compile WASM.

## Open options

`Laya.open(options)` takes the model asset (`modelUrl`, optional `sha256`) together with the open options; `Laya.openRouter(models, options)` takes the same options without the model asset. The full option table (`backend`, `contextTokens`, `overflow`, `temperatures`, `maxPending`, `maxModelBytes`, `maxLoaded`, `auto`, `initializationTimeoutMs`, `signal`, `onDiagnostic` and the asset URL overrides) is in the [package README](../bindings/browser/README.md#opening); the native meaning of each key is in the [API guide](API.md#engine-configuration). The engine always runs one thread. Initialization that exceeds `initializationTimeoutMs` rejects with `status` 6.

`engine.execute(request, options)` takes one request (object or JSON string) or an array of up to 32, with the call options `signal`, `timeoutMs`, `onEvent` and `requestId` as in Node. Request objects are serialized with `JSON.stringify`, which reorders integer-like keys and writes `149.0` as `149`; pass a JSON string when the literal text matters.

`openRouter({id: {url, sha256, contextTokens, overflow, temperatures}}, options)` maps logical routes to assets. A model's own `contextTokens`, `overflow` and `temperatures` override the shared options for that route, like a native route `config`. Identical URL/hash pairs are downloaded once and use one canonical model path. The `auto: {english, default}` option enables `"model": "auto"` (see [API.md](API.md#choosing-the-model-by-language)):

```js
const router = await Laya.openRouter({
  en: { url: '/models/Laya-Q8_0.gguf', sha256: 'c06528c5746d3bb8baa72a27938be95abbfd0b226f8471e8a9e365ed0bb066d2' },
  ml: { url: '/models/laya-multilingual-f16.gguf', sha256: '2742df1845b29bff811082916da331b34ca214816f4fa65d7f56dd09a8f942db',
        contextTokens: 1024 },
}, { auto: { english: 'en', default: 'ml' }, maxLoaded: 2, maxModelBytes: 1200 * 1024 * 1024 });
```

Native routing validation/cache limits remain authoritative. `maxModelBytes` limits total fetched model bytes (default 1 GiB), not peak process/GPU RAM. The English (449 MB) and multilingual (658 MB) models together exceed the default, so raise it for a router that holds both. Download buffers, filesystem storage, weights and inference buffers add memory overhead.

### Memory limits

This is a wasm32 build with a 4 GiB heap ceiling: the loaded weights and inference buffers of every loaded model must fit in it. Inference memory grows with `contextTokens` ([memory measurements](PERFORMANCE.md) are for native builds), so a large `contextTokens`, especially with several loaded models, can exceed the heap. When the browser cannot grow the heap, large allocations (weights, inference buffers) fail with a native status; a failed small ggml allocation aborts the WebAssembly runtime, which rejects the pending work with status 3 and closes that engine. Other engines and the page are unaffected, and the host can open a new engine.

With WebGPU, a model's weights and inference buffers are GPU buffers. A buffer the GPU cannot create fails opening the engine with status 2, and a router request that loads a model with status 2; the router stays usable. Other WebGPU errors, device loss, a GPU wait longer than 30 s, and a failure to create the few-kilobyte buffer that reads results back during a request end that worker's WebAssembly instance instead (status 3, engine closed).

## Execution and failure semantics

The default backend is single-threaded WASM in a worker; it requires neither SharedArrayBuffer nor cross-origin isolation. WebGPU requires a usable adapter, the model's device limits/operations, and the JSPI/WASM-exceptions capabilities of the pinned runtime. `auto` tries WebGPU initialization then WASM; a failure to fetch, hash or size a model asset (status 2 or 1) is not retried, since WASM would fail the same way. An inference failure does not silently retry against another backend or change probabilities. GPU device loss/runtime abort fails pending work and terminates that engine's worker; the host can explicitly reopen a WASM engine.

`maxPending` bounds accepted work, including cancelled calls awaiting worker acknowledgment. `signal` rejects the host promise promptly. Isolated pages with SharedArrayBuffer permit cooperative running CPU cancellation; otherwise the worker processes cancellation when it can receive messages. WebGPU checks shared control at question boundaries, without claiming interruption of a GPU kernel. `capabilities.runningCancellation` reports the verified CPU capability. Deadlines include queue time and use shared-core checks once execution starts. A rejection does not prove immediate resource release. `close()` drains work; `close({cancelPending:true})` requests cancellation and waits for cleanup. Repeated `close()` returns the same promise. `initializationTimeoutMs` accepts integer milliseconds from 1 through 86400000 (default 120000 per backend attempt). Aborting initialization rejects with status 5, terminates its worker and prevents automatic backend fallback. The model is read into one buffer, sized up front from `Content-Length` when the server sends it, so a download holds the model's bytes once rather than twice.

Observers use the same native event contract and execute on the host thread. Callback failures are passive. Low-level runtime text is suppressed unless `onDiagnostic` is provided. Requests and results are not logged or sent to telemetry endpoints.

Building the package from source: [BUILDING.md](BUILDING.md#build-the-browser-package). How the browser build is validated for a release: [RELEASE_CHECKS.md](RELEASE_CHECKS.md#browser).
