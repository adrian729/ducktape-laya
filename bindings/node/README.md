# ducktape-laya

Local [Laya](https://github.com/NandhaKishorM/laya) decisions for Node.js and Electron: ask named questions about a text and get calibrated probabilities. A `choice` question picks one of several options, a `noul` question gives the probability that a statement is true, and a `score` question picks a level on an ordered scale. Inference runs on the CPU in a worker thread of your process: no server, network access or implicit model download. 0.1.0 had a different API ([migration notes](https://github.com/adrian729/ducktape-laya/blob/main/docs/COMPATIBILITY.md)).

## Requirements

- Node.js 20+ (tested on 24 and 26) or Electron (tested with 44). The native addon is prebuilt; no compiler is needed.
- Linux x86-64 with AVX, AVX2, FMA, F16C, BMI2 and SSE4.2 and glibc 2.38+ (Ubuntu 24.04+). From 0.3.0 the package also carries Linux arm64 (Armv8.2-A with dot product and FP16), macOS arm64 and x64 (macOS 14+; arm64 needs dot product and FP16) and Windows x64 (which needs the Microsoft Visual C++ Redistributable), and picks the addon for the running platform. musl (Alpine) is not supported: loading fails with `ERR_LAYA_UNSUPPORTED`. Full matrix: [platforms](https://github.com/adrian729/ducktape-laya/blob/main/docs/PLATFORMS.md) and the [repository README](https://github.com/adrian729/ducktape-laya#requirements).
- A Laya GGUF model, downloaded separately. `python3 node_modules/ducktape-laya/download-model.py resources` downloads the pinned English model into `resources/` and verifies its hash ([models](https://github.com/adrian729/ducktape-laya/blob/main/docs/MODELS.md)).

On another platform, build the addon from a checkout ([building](https://github.com/adrian729/ducktape-laya/blob/main/docs/BUILDING.md)) and set `DUCKTAPE_LAYA_LIBRARY` to the addon's absolute path (or `DUCKTAPE_LAYA_LIB_DIR` to the absolute path of the directory holding `ducktape_laya.node`, or of an installed libdir with it in `ducktape-laya/node`; a relative path is a load error); on Windows keep `ducktape_laya.dll` next to `ducktape_laya.node`.

```sh
npm install ducktape-laya
```

## Usage

```js
const { Engine } = require('ducktape-laya');

async function main() {
  const engine = await Engine.open('/absolute/path/to/Laya-Q8_0.gguf', { config: { threads: 2 } });
  try {
    const result = await engine.execute({
      state: 'Please refund the duplicate charge.',
      questions: {
        refund: { type: 'noul', instructions: 'Does the customer request a refund?' }
      }
    });
    console.log(result.answers.refund.noul, result.answers.refund.answer_confidence);
  } finally {
    await engine.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
```

From an ES module, `import { Engine } from 'ducktape-laya';` works too, and top-level `await` is then allowed.

`close()` is required: the worker keeps the process alive until it is closed.

## Opening

`Engine.open(modelPath, options)` loads one model; `modelPath` is a string, a `file:` `URL`, or a `Buffer` or `Uint8Array` holding the path's bytes (a POSIX path need not be UTF-8). `Engine.openRouter(router, options)` opens a lazy registry of local models; `router` is an object or JSON string:

```js
const router = await Engine.openRouter({
  models: {
    en: '/app/resources/Laya-Q8_0.gguf',
    ml: { path: '/app/resources/laya-multilingual-f16.gguf', config: { context_tokens: 1024 } }
  },
  max_loaded: 2,
  auto: { english: 'en', default: 'ml' }
}, { config: { threads: 2 } });
await router.execute({ ...request, model: 'auto' }); // or model: 'en'
```

Router requests name a route in `model`, or send `model: 'auto'` (with an optional `lang`) to choose by the state's language when `auto` is configured. `auto` cannot be a route ID. `max_loaded` (1–4, default 1) bounds the loaded models.

`options` is optional and may be `null`. The native settings sit in a nested `config` object with the native keys (the same names as the C API); the binding does not validate them, the core rejects a bad value with status 1. Options of the binding itself are camelCase beside it:

| Option | Meaning |
|---|---|
| `config.threads` | 0 (automatic: at most 4 and at most the CPUs the process may use) or 1–256 |
| `config.context_tokens` | Prompt capacity: 32 up to the model's maximum (`trained_context_tokens`, 8,192 for the shipped files), default 512 |
| `config.overflow` | Default policy for prompts over the limit: `reject` (default), `truncate_end`, `truncate_start` or `scan` |
| `config.temperatures` | Calibration overrides keyed like `choice`, `choice.3_5` or `noul.2`; finite and above 0 |
| `config.max_windows_per_call` | The most forward passes one call may plan: 1–4096, default 256. For a router it applies to the whole call, so it belongs in the shared `config` and a route's own `config` cannot set it |
| `maxPending` | Accepted calls (each call or whole batch is one), 1–1024, default 16 |

For a router, `config` applies to every route, and a route's own `config` (`threads`, `context_tokens`, `overflow`, `temperatures`) overrides it key by key.

To use a build of the addon of your own, set `DUCKTAPE_LAYA_LIBRARY` to the addon's absolute path, or `DUCKTAPE_LAYA_LIB_DIR` to the absolute path of the directory with `ducktape_laya.node` (or of an installed libdir, which keeps it in `ducktape-laya/node`). Both are read from the environment only; an empty one is unset, and a relative path is a `LayaLoadError`.

## Executing

`engine.execute(request, options)` takes one request (object or JSON string) or an array of up to 32, and resolves to parsed results in the same shape. Requests are strict; besides `state` and `questions` or `schema`, they may set `max_tokens`, `temperatures`, `overflow`, `max_windows`, `boolean_threshold` (with `schema`), and on a router `model` and `lang`. Request objects are serialized with `JSON.stringify`, so integer-like keys are reordered and a number such as `149.0` is written as `149`; both can change the prompt and the results compared with the same request as a raw JSON string (or from Python). Pass a JSON string when the literal text matters.

| Call option | Meaning |
|---|---|
| `signal` | An `AbortSignal`; aborting rejects with `status` 5 |
| `timeoutMs` | 0 (none) or 1–86400000, counted from enqueue; expiry rejects with `status` 6 |
| `onEvent` | Receives `start`, then `end` or `error` events on the host thread |
| `requestId` | Non-negative safe integer reported as `request_id` in events; generated when omitted |

Cancellation is cooperative; a rejected call returns no partial result. After an abort or deadline settles a call, its `onEvent` receives no more events.

`Engine.runtimeInfo()` returns the runtime's identity, features and limits without loading a model; `await engine.info()` adds the model's metadata for a single-model engine.

## Errors and closing

Errors the package raises are `LayaError` objects (exported, with `LayaLoadError` and `EngineClosedError`) carrying `status`, one of the C ABI statuses also exported as `Status`, and sometimes a `code`. Argument errors (types, units, NUL, strings that are not well-formed Unicode, non-finite numbers, binding options) are `TypeError` or `RangeError` objects that also carry `status` 1. Lifecycle failures carry `code`: `ERR_LAYA_QUEUE_FULL` (status 1), `ERR_LAYA_WORKER_EXITED` (status 3) and, as `EngineClosedError`, `ERR_LAYA_CLOSED` (status 1). A missing, unloadable or wrong-ABI addon (including a glibc addon on a musl system), or an addon whose library is not the one it was built with, fails with a `LayaLoadError`: status 7 (`Status.UNSUPPORTED_PLATFORM`) and code `ERR_LAYA_UNSUPPORTED`, as does every other status-7 error, such as a CPU without the required features; an addon that exists but cannot be loaded is reported as found, with the loader's reason. After `close()`, every call (including `info()`) rejects with `EngineClosedError`. A call is checked in this order: closed, then its arguments, then an already-cancelled `signal` (rejects with status 5 at once, without reaching the worker), then the queue limit, then the model. A `null` options argument means the defaults; any other non-object (including for `close()`) is a status-1 `TypeError`. On Linux the addon loads the library next to it even when another `libducktape_laya.so.1` is on `LD_LIBRARY_PATH`.

`close()` stops admission and drains accepted calls; `close({ cancelPending: true })` cancels them first. Repeated calls return the same promise, and closing succeeds even if the worker already exited.

## Electron

Open the engine in the main process. Unpack the whole `node_modules/ducktape-laya` package and the model from ASAR, for example with electron-builder's `asarUnpack: ["**/node_modules/ducktape-laya/**"]`: the worker script (`worker.cjs`) is started from the package directory, and the native addon and library (`ducktape_laya.node` and `ducktape_laya.dll` on Windows) must be real files. The package resolves its `app.asar` native path to `app.asar.unpacked`. Electron 44 is tested as a directory and from ASAR.

The full request and result format is in the [API guide](https://github.com/adrian729/ducktape-laya/blob/main/docs/API.md); deployment details are in [linking and deployment](https://github.com/adrian729/ducktape-laya/blob/main/docs/PACKAGING.md).
