# ducktape-laya

Local [Laya](https://github.com/NandhaKishorM/laya) decisions for Node.js and Electron: ask named questions about a text and get calibrated probabilities. A `choice` question picks one of several options, a `noul` question gives the probability that a statement is true, and a `score` question picks a level on an ordered scale. Inference runs on the CPU in a worker thread of your process: no server, network access or implicit model download. 0.1.0 had a different API ([migration notes](https://github.com/adrian729/ducktape-laya/blob/main/docs/COMPATIBILITY.md)).

## Requirements

- Node.js 20+ (tested on 24 and 26) or Electron (tested with 44). The native addon is prebuilt; no compiler is needed.
- Linux x86-64 with AVX2, FMA, F16C, BMI2 and SSE4.2 and glibc 2.38+ (Ubuntu 24.04+). The 0.2.0 package carries Linux x86-64 only; from the next release it also carries Linux arm64 (Armv8.2-A with dot product and FP16) and Windows x64 (which needs the Microsoft Visual C++ Redistributable), and picks the addon for the running platform. musl (Alpine) is not supported: loading fails with `ERR_LAYA_UNSUPPORTED`. Full matrix: [platforms](https://github.com/adrian729/ducktape-laya/blob/main/docs/PLATFORMS.md) and the [repository README](https://github.com/adrian729/ducktape-laya#requirements).
- A Laya GGUF model, downloaded separately. `python3 node_modules/ducktape-laya/download-model.py resources` downloads the pinned English model into `resources/` and verifies its hash ([models](https://github.com/adrian729/ducktape-laya/blob/main/docs/MODELS.md)).

On another platform, build the addon from a checkout ([building](https://github.com/adrian729/ducktape-laya/blob/main/docs/BUILDING.md)) and pass `addonPath` or set `DUCKTAPE_LAYA_ADDON`; on Windows keep `ducktape_laya.dll` next to `ducktape_laya.node`.

```sh
npm install ducktape-laya
```

## Usage

```js
const { Laya } = require('ducktape-laya');

async function main() {
  const engine = await Laya.open('/absolute/path/to/Laya-Q8_0.gguf', { threads: 2 });
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

From an ES module, `import { Laya } from 'ducktape-laya';` works too, and top-level `await` is then allowed.

`close()` is required: the worker keeps the process alive until it is closed.

## Opening

`Laya.open(modelPath, options)` loads one model. `Laya.openRouter(config, options)` opens a lazy registry of local models; `config` is an object or JSON string:

```js
const router = await Laya.openRouter({
  models: {
    en: '/app/resources/Laya-Q8_0.gguf',
    ml: { path: '/app/resources/laya-multilingual-f16.gguf', config: { context_tokens: 1024 } }
  },
  max_loaded: 2,
  auto: { english: 'en', default: 'ml' }
});
await router.execute({ ...request, model: 'auto' }); // or model: 'en'
```

Router requests name a route in `model`, or send `model: 'auto'` (with an optional `lang`) to choose by the state's language when `auto` is configured. `auto` cannot be a route ID. `max_loaded` (1–4, default 1) bounds the loaded models.

Options for both:

| Option | Meaning |
|---|---|
| `threads` | 0 (automatic, at most 4) or 1–256 |
| `contextTokens` | Prompt capacity: 32 up to the model's trained length, default 512 |
| `overflow` | Default policy for prompts over the limit: `reject` (default), `truncate_end`, `truncate_start` or `scan` |
| `temperatures` | Calibration overrides keyed like `choice`, `choice.3_5` or `noul.2`; finite and above 0 |
| `maxPending` | Accepted calls (each call or whole batch is one), 1–1024, default 16 |
| `addonPath` | A specific `ducktape_laya.node`; `DUCKTAPE_LAYA_ADDON` does the same |

For a router, the options apply to every route, and a route's own `config` (`threads`, `context_tokens`, `overflow`, `temperatures`) overrides them.

## Executing

`engine.execute(request, options)` takes one request (object or JSON string) or an array of up to 32, and resolves to parsed results in the same shape. Requests are strict; besides `state` and `questions` or `schema`, they may set `max_tokens`, `temperatures`, `overflow`, `max_windows`, `boolean_threshold` (with `schema`), and on a router `model` and `lang`. Request objects are serialized with `JSON.stringify`, so integer-like keys are reordered and a number such as `149.0` is written as `149`; both can change the prompt and the results compared with the same request as a raw JSON string (or from Python). Pass a JSON string when the literal text matters.

| Call option | Meaning |
|---|---|
| `signal` | An `AbortSignal`; aborting rejects with `status` 5 |
| `timeoutMs` | 0 (none) or 1–86400000, counted from enqueue; expiry rejects with `status` 6 |
| `onEvent` | Receives `start`, then `end` or `error` events on the host thread |
| `requestId` | Non-negative safe integer reported as `request_id` in events; generated when omitted |

Cancellation is cooperative; a rejected call returns no partial result. After an abort or deadline settles a call, its `onEvent` receives no more events.

`Laya.runtimeInfo()` returns the runtime's identity, features and limits without loading a model; `await engine.info()` adds the model's metadata for a single-model engine.

## Errors and closing

Errors are `Error` objects and all carry `status` (the C ABI status, also exported as `Status`); argument errors carry `status` 1. Queue and lifecycle failures also carry `code`: `ERR_LAYA_QUEUE_FULL` and `ERR_LAYA_CLOSED` (status 1) or `ERR_LAYA_WORKER_EXITED` (status 3). A missing, unloadable or wrong-ABI addon (including a glibc addon on a musl system), or an addon whose library is not the one it was built with, fails with status 7 (`Status.UNSUPPORTED_PLATFORM`) and code `ERR_LAYA_UNSUPPORTED`; an addon that exists but cannot be loaded is reported as found, with the loader's reason. After `close()`, every call (including `info()`) rejects with `ERR_LAYA_CLOSED`. A `null` or non-object options argument (including to `close()`) and an `addonPath` that is not a non-empty string are status-1 `TypeError`s. On Linux the addon loads the library next to it even when another `libducktape_laya.so.1` is on `LD_LIBRARY_PATH`. Strings that are not well-formed Unicode are rejected.

`close()` stops admission and drains accepted calls; `close({ cancelPending: true })` cancels them first. Repeated calls return the same promise, and closing succeeds even if the worker already exited.

## Electron

Open the engine in the main process. Unpack the whole `node_modules/ducktape-laya` package and the model from ASAR, for example with electron-builder's `asarUnpack: ["**/node_modules/ducktape-laya/**"]`: the worker script (`worker.cjs`) is started from the package directory, and the native addon and library (`ducktape_laya.node` and `ducktape_laya.dll` on Windows) must be real files. The package resolves its `app.asar` native path to `app.asar.unpacked`. Electron 44 is tested as a directory and from ASAR.

The full request and result format is in the [API guide](https://github.com/adrian729/ducktape-laya/blob/main/docs/API.md); deployment details are in [linking and deployment](https://github.com/adrian729/ducktape-laya/blob/main/docs/PACKAGING.md).
