# ducktape-laya-browser

Local [Laya](https://github.com/NandhaKishorM/laya) decisions in the browser: ask named questions about a text (`choice` picks one of several options, `noul` gives the probability that a statement is true, `score` picks a level on an ordered scale) and get probabilities, using the same C++ core and llama.cpp runtime as the native library, compiled to WebAssembly with an optional WebGPU build. Inference runs in a module worker. **Experimental:** it is tested on one browser (Chromium 152 for 0.1.0 and 0.2.0, Chrome 156 from 0.3.0) and one GPU, and its results differ slightly from native builds ([platforms](https://github.com/adrian729/ducktape-laya/blob/main/docs/PLATFORMS.md#numerical-results-across-platforms)). 0.1.0 had a different API (see [the migration notes](https://github.com/adrian729/ducktape-laya/blob/main/docs/COMPATIBILITY.md)).

```sh
npm install ducktape-laya-browser
```

## Requirements

- A secure context that allows module workers and WebAssembly compilation, with WASM SIMD. Serve `.wasm` as `application/wasm`.
- WebGPU is optional: the `webgpu` backend needs a usable adapter and the JSPI/WASM-exceptions support of the pinned runtime.
- The model file, served by your application. No model or CDN URL is built in.

Serve the package directory under an application URL, keeping its relative paths (a bundler may copy it into public assets).

## Usage

```js
import { Engine } from '/vendor/laya/index.mjs';

const engine = await Engine.open({
  url: '/models/Laya-Q8_0.gguf',
  sha256: 'c06528c5746d3bb8baa72a27938be95abbfd0b226f8471e8a9e365ed0bb066d2',
}, { backend: 'auto' });
try {
  const result = await engine.execute({
    state: 'Please refund the duplicate charge.',
    questions: {
      refund: { type: 'noul', instructions: 'Does the customer request a refund?', criteria: { false: 'no money back is asked for', true: 'the customer asks for money back' } }
    }
  }, { timeoutMs: 10000 });
  console.log(result.answers.refund.noul);
} finally {
  await engine.close();
}
```

## Opening

`Engine.open(model, options)` loads one model; `model` is a URL string or `{ url, sha256? }`. `Engine.openRouter(router, options)` takes `{ models, max_loaded?, auto? }`, where `models` maps route IDs to a URL or `{ url, sha256?, config? }` and a route's own `config` overrides the shared one key by key. `options` is optional and may be `null`:

```js
const router = await Engine.openRouter({
  models: {
    en: { url: '/models/Laya-Q8_0.gguf', sha256: 'c06528c5746d3bb8baa72a27938be95abbfd0b226f8471e8a9e365ed0bb066d2' },
    ml: { url: '/models/laya-multilingual-f16.gguf', sha256: '2742df1845b29bff811082916da331b34ca214816f4fa65d7f56dd09a8f942db',
          config: { context_tokens: 1024 } },
  },
  max_loaded: 2,
  auto: { english: 'en', default: 'ml' },
}, { maxModelBytes: 1200 * 1024 * 1024 });
```

The native settings go in `options.config`, with the native keys (`threads`, `context_tokens`, `overflow`, `temperatures`, `max_windows_per_call`); `max_loaded` and `auto` are the router's native keys too. The binding does not validate them: the core does, before any model is downloaded (it opens the same configuration over empty placeholder files first), and a bad value rejects with status 1. The only checks of the page are conversions and `config.threads`, which may only be 1 (or absent): the browser runs one thread. Options of the binding are camelCase:

| Option | Meaning |
|---|---|
| `config` | The native open config (a router applies it to every route): `threads` (1), `context_tokens` (32 up to the model's maximum, default 512), `overflow` (`reject` default, `truncate_end`, `truncate_start`, `scan`), `temperatures` (keyed like `choice`, `choice.3_5` or `noul.2`), `max_windows_per_call` (1–4096, default 256; shared config only) |
| `backend` | `wasm` (default, single-threaded CPU), `webgpu`, or `auto` (WebGPU, then WASM if WebGPU initialization fails; a model asset failure or an invalid config is not retried) |
| `maxPending` | Accepted calls, 1–1024, default 16 |
| `maxModelBytes` | Total model bytes fetched, default 1 GiB |
| `initializationTimeoutMs` | 1–86400000, default 120000 per backend attempt; expiry rejects with `status` 6 |
| `signal` | Aborts initialization (status 5) |
| `onDiagnostic` | Receives low-level runtime text, otherwise suppressed |
| `moduleUrl`, `wasmUrl`, `webgpuModuleUrl`, `webgpuWasmUrl`, `workerUrl` | Override asset locations |

Router requests name a route in `model`, or send `model: 'auto'` (with an optional `lang`) to choose by the state's language. The English (449 MB) and multilingual (658 MB) models together exceed the default `maxModelBytes`, so raise it to route between them. The build is wasm32 with a heap of at most 4 GiB: weights and inference buffers of every loaded model must fit, and inference memory grows with `context_tokens`.

`engine.capabilities` reports the selected backend and whether running CPU calls can be cancelled (only on cross-origin-isolated pages with the WASM backend).

## Executing

`engine.execute(request, options)` takes one request (object or JSON string) or an array of up to 32, and resolves to parsed results. Requests are strict; besides `state` and `questions` or `schema`, they may set `max_tokens`, `temperatures`, `overflow`, `max_windows`, `boolean_threshold` (with `schema`), and on a router `model` and `lang`. Request objects are serialized with `JSON.stringify`, which reorders integer-like keys and writes `149.0` as `149`; both can change the prompt and the results, so pass a JSON string when the literal text matters. Call options are `signal`, `timeoutMs` (0 for none, or any positive safe integer with no upper bound, counted from enqueue), `onEvent` (receives a `LayaEvent` `{ kind, requestId, status, elapsedMs }`), `requestId` (default 0) and `raw` (`true` resolves to the core's exact result text), as in the [Node package](https://www.npmjs.com/package/ducktape-laya). After an abort or deadline settles a call, its `onEvent` receives no more events. `await engine.info()` returns runtime and model diagnostics.

## Errors and closing

Errors the package raises are `LayaError` objects (exported, with `LayaLoadError` and `EngineClosedError`) carrying `status` (exported as `Status`) and sometimes a `code`. Argument errors, including a non-object `options` to `open`, `openRouter`, `execute` and `close` (`null` means the defaults), are `TypeError` or `RangeError` objects that carry `status` 1; queue and lifecycle failures carry `code`: `ERR_LAYA_QUEUE_FULL` (status 1), `ERR_LAYA_WORKER_EXITED` (status 3) and, as `EngineClosedError`, `ERR_LAYA_CLOSED` (status 1). `info()` never fails with `ERR_LAYA_QUEUE_FULL` and takes no queue slot, as `close()`. After a worker failure every call fails with `ERR_LAYA_WORKER_EXITED` ("Laya worker has exited; open a new engine"). A call is checked in this order: closed, then its arguments, then an already-cancelled `signal` (rejects with status 5 at once, sending nothing to the worker), then the queue limit, then the model. An invalid config rejects with status 1, a model asset that cannot be fetched or fails its `sha256` with status 2, assets over `maxModelBytes` with status 1, and an aborted `signal` with status 5; with `backend: 'auto'` these are not retried with WASM, while a WebGPU failure is. A runtime that cannot be loaded or has the wrong ABI fails with a `LayaLoadError`: status 7 (`Status.UNSUPPORTED_PLATFORM`) and code `ERR_LAYA_UNSUPPORTED` (every status-7 error is one), unless the browser could not reserve its WebAssembly memory, which is status 4. Strings that are not well-formed Unicode are rejected. A GPU device loss or a runtime abort fails pending work and closes that engine; open a new one.

`close()` drains accepted calls and terminates the worker; `close({ cancelPending: true })` cancels them first. Repeated calls return the same promise.

Deployment, failure semantics and validation are in the [browser guide](https://github.com/adrian729/ducktape-laya/blob/main/docs/BROWSER.md); the request and result format is in the [API guide](https://github.com/adrian729/ducktape-laya/blob/main/docs/API.md).
