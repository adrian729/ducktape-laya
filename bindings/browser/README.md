# ducktape-laya-browser

Local [Laya](https://github.com/NandhaKishorM/laya) decisions in the browser: ask named questions about a text (`choice` picks one of several options, `noul` gives the probability that a statement is true, `score` picks a level on an ordered scale) and get calibrated probabilities, using the same C++ core and llama.cpp runtime as the native library, compiled to WebAssembly with an optional WebGPU build. Inference runs in a module worker. **Experimental:** it passed the browser release checks on Chromium 152 only, and its results differ slightly from native builds ([platforms](https://github.com/adrian729/ducktape-laya/blob/main/docs/PLATFORMS.md#numerical-results-across-platforms)). 0.1.0 had a different API (see [the migration notes](https://github.com/adrian729/ducktape-laya/blob/main/docs/COMPATIBILITY.md)).

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
import { Laya } from '/vendor/laya/index.mjs';

const engine = await Laya.open({
  modelUrl: '/models/Laya-Q8_0.gguf',
  sha256: 'c06528c5746d3bb8baa72a27938be95abbfd0b226f8471e8a9e365ed0bb066d2',
  backend: 'auto',
});
try {
  const result = await engine.execute({
    state: 'Please refund the duplicate charge.',
    questions: { refund: { type: 'noul', instructions: 'Does the customer request a refund?' } }
  }, { timeoutMs: 10000 });
  console.log(result.answers.refund.noul);
} finally {
  await engine.close();
}
```

## Opening

`Laya.open(options)` takes the model asset and the options together. `Laya.openRouter(models, options)` maps route IDs to assets, `{ url, sha256, contextTokens, overflow, temperatures }`, where a model's own settings override the shared options for that route.

| Option | Meaning |
|---|---|
| `modelUrl`, `sha256` | `open` only: the model URL and its optional SHA-256 |
| `backend` | `wasm` (default, single-threaded CPU), `webgpu`, or `auto` (WebGPU, then WASM if initialization fails) |
| `contextTokens` | Prompt capacity: 32 up to the model's trained length, default 512 |
| `overflow` | Default policy for prompts over the limit: `reject` (default), `truncate_end`, `truncate_start` or `scan` |
| `temperatures` | Calibration overrides keyed like `choice`, `choice.3_5` or `noul.2`; finite and above 0 |
| `maxPending` | Accepted calls, 1–1024, default 16 |
| `maxModelBytes` | Total model bytes fetched, default 1 GiB |
| `maxLoaded` | Router only: models kept loaded, 1–4, default 1 |
| `auto` | Router only: `{ english, default }` routes for `model: 'auto'` |
| `initializationTimeoutMs` | 1–86400000, default 120000 per backend attempt; expiry rejects with `status` 6 |
| `signal` | Aborts initialization |
| `onDiagnostic` | Receives low-level runtime text, otherwise suppressed |
| `moduleUrl`, `wasmUrl`, `webgpuModuleUrl`, `webgpuWasmUrl`, `workerUrl` | Override asset locations |

Router requests name a route in `model`, or send `model: 'auto'` (with an optional `lang`) to choose by the state's language. The English (449 MB) and multilingual (658 MB) models together exceed the default `maxModelBytes`, so raise it to route between them. The build is wasm32 with a heap of at most 4 GiB: weights and inference buffers of every loaded model must fit, and inference memory grows with `contextTokens`.

`engine.capabilities` reports the selected backend and whether running CPU calls can be cancelled (only on cross-origin-isolated pages with the WASM backend).

## Executing

`engine.execute(request, options)` takes one request (object or JSON string) or an array of up to 32, and resolves to parsed results. Requests are strict; besides `state` and `questions` or `schema`, they may set `max_tokens`, `temperatures`, `overflow`, `max_windows`, `boolean_threshold` (with `schema`), and on a router `model` and `lang`. Request objects are serialized with `JSON.stringify`, which reorders integer-like keys and writes `149.0` as `149`; both can change the prompt and the results, so pass a JSON string when the literal text matters. Call options are `signal`, `timeoutMs` (0–86400000, counted from enqueue), `onEvent` and `requestId`, as in the [Node package](https://www.npmjs.com/package/ducktape-laya). After an abort or deadline settles a call, its `onEvent` receives no more events. `await engine.info()` returns runtime and model diagnostics.

## Errors and closing

Native failures carry `status` (exported as `Status`); argument errors carry `status` 1; queue and lifecycle failures carry `code` (`ERR_LAYA_QUEUE_FULL`, `ERR_LAYA_CLOSED`, `ERR_LAYA_WORKER_EXITED`); a runtime that cannot be loaded or has the wrong ABI fails with status 7 (`Status.UNSUPPORTED_PLATFORM`) and code `ERR_LAYA_UNSUPPORTED`. Strings that are not well-formed Unicode are rejected. A GPU device loss or a runtime abort fails pending work and closes that engine; open a new one.

`close()` drains accepted calls and terminates the worker; `close({ cancelPending: true })` cancels them first. Repeated calls return the same promise.

Deployment, failure semantics and validation are in the [browser guide](https://github.com/adrian729/ducktape-laya/blob/main/docs/BROWSER.md); the request and result format is in the [API guide](https://github.com/adrian729/ducktape-laya/blob/main/docs/API.md).
