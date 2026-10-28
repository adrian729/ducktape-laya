const internal = Symbol('Engine');
const bytes = text => new TextEncoder().encode(text).byteLength;
const absolute = url => new URL(url, globalThis.location?.href).href;
/** Every error this package raises. `status` is a `Status` value; `code` is a stable string where there is one. */
export class LayaError extends Error {
  constructor(message, status, code) {
    super(message);
    this.status = status;
    if (code !== undefined) this.code = code;
  }
}
LayaError.prototype.name = 'LayaError';
/** The WASM runtime could not be loaded or is incompatible (status 7). */
export class LayaLoadError extends LayaError {
  constructor(message) { super(message, 7, 'ERR_LAYA_UNSUPPORTED'); }
}
LayaLoadError.prototype.name = 'LayaLoadError';
/** A call on a closing or closed engine (status 1). */
export class EngineClosedError extends LayaError {
  constructor(message = 'Engine is closing or closed') { super(message, 1, 'ERR_LAYA_CLOSED'); }
}
EngineClosedError.prototype.name = 'EngineClosedError';
// Every error this package raises carries a status: lifecycle and queue errors as the other bindings
// report a closed engine (1, INVALID_ARGUMENT), a failed worker as an inference failure (3).
const queueFullError = () => new LayaError('Laya queue is full', 1, 'ERR_LAYA_QUEUE_FULL');
const exitedError = message => new LayaError(message, 3, 'ERR_LAYA_WORKER_EXITED');
// Cancelled initialization, like a cancelled request: status 5.
const abortedError = () => new LayaError('Initialization cancelled', 5);
// Failures a WebGPU attempt shares with WASM (fetching, hashing or sizing a model asset, an invalid config): backend 'auto'
// does not retry them with WASM.
const final = new WeakSet();
// Load and ABI failures (status 7) are LayaLoadError with a stable code.
const nativeError = (message, status) => status === 7 ? new LayaLoadError(message) : new LayaError(message, status);
const invalid = (Type, message) => Object.assign(new Type(message), { status: 1 });
// Options default to {}; undefined and null mean the defaults, any other non-object is an argument error with status 1.
const optionsObject = options => {
  if (options === undefined || options === null) return {};
  if (typeof options !== 'object') throw invalid(TypeError, 'options must be an object');
  return options;
};
// JSON.stringify escapes lone surrogates instead of failing, which the native
// side would then silently replace, and writes non-finite numbers as null;
// reject both before serializing.
// A model: a URL or { url, sha256?, config? } (config only in a router).
const modelAsset = (model, what) => {
  if (typeof model === 'string') model = { url: model };
  if (!model || typeof model !== 'object' || typeof model.url !== 'string' || !model.url) throw invalid(TypeError, `${what} requires a URL`);
  if (model.sha256 !== undefined && !/^[a-fA-F0-9]{64}$/.test(model.sha256)) throw invalid(TypeError, 'sha256 must be 64 hexadecimal characters');
  return model;
};
const wellFormedJson = (value, what) => {
  try {
    return JSON.stringify(value, (key, item) => {
      if (!key.isWellFormed() || (typeof item === 'string' && !item.isWellFormed())) throw invalid(TypeError, `${what} is not valid Unicode`);
      if (typeof item === 'number' && !Number.isFinite(item)) throw invalid(TypeError, `${what} contains a non-finite number`);
      return item;
    });
  } catch (error) {
    // Circular structures, BigInt values and throwing toJSON/getters are argument errors.
    if (error && typeof error === 'object' && error.status === undefined) error.status = 1;
    throw error;
  }
};
export const Status = Object.freeze({ OK: 0, INVALID_ARGUMENT: 1, MODEL_ERROR: 2, INFERENCE_ERROR: 3, OUT_OF_MEMORY: 4, CANCELLED: 5, DEADLINE_EXCEEDED: 6, UNSUPPORTED_PLATFORM: 7 });
export class Engine {
  #worker; #pending = new Map(); #next = 0; #closing = false; #dead = false; #failed = false; #limit; #close; #diagnostic;
  capabilities;
  constructor(token, worker, limit, diagnostic) {
    if (token !== internal) throw new TypeError('Use Engine.open() or Engine.openRouter()');
    this.#worker = worker; this.#limit = limit; this.#diagnostic = diagnostic;
    worker.onmessage = ({ data }) => {
      if (data.fatal) { this.#fail(exitedError(data.fatal)); return; }
      if (data.diagnostic) { try { Promise.resolve(this.#diagnostic?.(data.diagnostic)).catch(() => {}); } catch {} return; }
      const pending = this.#pending.get(data.id);
      if (!pending) return;
      if (data.event) { try { Promise.resolve(pending.onEvent?.(data.event)).catch(() => {}); } catch {} return; }
      this.#pending.delete(data.id);
      pending.cleanup?.();
      if (data.error) {
        const error = nativeError(data.error, data.status);
        if (data.final) final.add(error);
        pending.reject(error);
      }
      else pending.resolve(data.result);
    };
    worker.onerror = event => this.#fail(exitedError(event.message || 'Laya worker failed'));
    worker.onmessageerror = () => this.#fail(exitedError('Laya worker message could not be decoded'));
  }
  #fail(error) {
    this.#dead = this.#failed = true;
    for (const entry of this.#pending.values()) { entry.cleanup?.(); entry.reject(error); }
    this.#pending.clear(); this.#worker.terminate();
  }
  /** `model`: a URL or `{ url, sha256? }`. */
  static async open(model, options) {
    const asset = modelAsset(model, 'model');
    if (asset.config !== undefined) throw invalid(TypeError, 'model has no config: pass it in options.config');
    return Engine.#open({ model: asset }, optionsObject(options));
  }
  /** `router`: `{ models: { id: url | { url, sha256?, config? } }, max_loaded?, auto? }`. */
  static async openRouter(router, options) {
    if (router === null || typeof router !== 'object' || router.models === null || typeof router.models !== 'object') throw invalid(TypeError, 'router must be an object with models');
    const { models, max_loaded: maxLoaded, auto, ...rest } = router;
    if (Object.keys(rest).length) throw invalid(TypeError, `Unknown router key: ${Object.keys(rest)[0]}`);
    return Engine.#open({ models: Object.fromEntries(Object.entries(models).map(([id, model]) => [id, modelAsset(model, 'Each model')])), maxLoaded, auto }, optionsObject(options));
  }
  static async #open(target, options) {
    const { maxPending = 16, maxModelBytes = 1073741824, initializationTimeoutMs = 120000, signal, backend = 'wasm', onDiagnostic, config } = options;
    if (!Number.isInteger(maxPending) || maxPending < 1 || maxPending > 1024) throw invalid(RangeError, 'maxPending must be 1..1024');
    if (!Number.isSafeInteger(maxModelBytes) || maxModelBytes < 1) throw invalid(RangeError, 'maxModelBytes must be positive');
    if (!Number.isInteger(initializationTimeoutMs) || initializationTimeoutMs < 1 || initializationTimeoutMs > 86400000) throw invalid(RangeError, 'initializationTimeoutMs must be 1..86400000');
    if (!['wasm','webgpu','auto'].includes(backend)) throw invalid(TypeError, 'Invalid backend');
    if (onDiagnostic !== undefined && typeof onDiagnostic !== 'function') throw invalid(TypeError, 'onDiagnostic must be a function');
    if (signal !== undefined && (typeof signal?.addEventListener !== 'function' || typeof signal?.removeEventListener !== 'function')) throw invalid(TypeError, 'signal must be an AbortSignal');
    // Conversions only: the config goes to the core, which validates every value (before any download, see worker.mjs).
    const configOf = (value, what) => {
      if (value === undefined || value === null) return undefined;
      if (typeof value !== 'object' || Array.isArray(value)) throw invalid(TypeError, `${what} must be an object`);
      if (value.threads !== undefined && value.threads !== 1) throw invalid(RangeError, `${what}.threads must be 1: the browser runs one thread`);
      return JSON.parse(wellFormedJson(value, what));
    };
    const routed = !!target.models;
    const entries = routed ? Object.entries(target.models) : [['model', target.model]];
    const assets = Object.create(null);
    for (const [id, model] of entries) assets[id] = { url: absolute(model.url), sha256: model.sha256, config: configOf(model.config, 'route config') };
    const sharedConfig = configOf(config, 'config');
    wellFormedJson([entries.map(([id, model]) => [id, model.url, model.sha256]), target.maxLoaded], 'router config');
    const auto = target.auto === undefined ? undefined : JSON.parse(wellFormedJson(target.auto, 'router config'));
    if (signal?.aborted) throw abortedError();  // after the conversions: closed, arguments, then cancellation
    const attempts = backend === 'auto' ? ['webgpu','wasm'] : [backend];
    let last;
    for (const selected of attempts) {
      const moduleUrl = selected === 'webgpu' ? (options.webgpuModuleUrl || new URL('./webgpu/laya.mjs', import.meta.url).href) : (options.moduleUrl || new URL('./wasm/laya.mjs', import.meta.url).href);
      if (!moduleUrl) throw invalid(TypeError, `Missing ${selected} module URL`);
      const worker = new Worker(options.workerUrl || new URL('./worker.mjs', import.meta.url), { type: 'module' });
      const engine = new Engine(internal, worker, maxPending, onDiagnostic);
      const abort = () => engine.#fail(abortedError());
      // status 6 (DEADLINE_EXCEEDED), like request deadlines; browser errors otherwise use codes only for lifecycle failures.
      const timer = setTimeout(() => engine.#fail(new LayaError('Laya initialization timed out', 6)), initializationTimeoutMs);
      try {
        signal?.addEventListener('abort', abort, { once: true });
        // Option getters/URL coercion can run caller code before registration.
        // AbortSignal does not replay an abort that already happened.
        if (signal?.aborted) throw abortedError();
        engine.capabilities = await engine.#send('init', undefined, undefined, {
          routed, assets, config: sharedConfig, maxLoaded: target.maxLoaded, auto,
          diagnostics: !!onDiagnostic, maxModelBytes, backend: selected, moduleUrl: absolute(moduleUrl),
          wasmUrl: absolute(selected === 'webgpu' ? (options.webgpuWasmUrl || new URL('laya.wasm', absolute(moduleUrl))) : (options.wasmUrl || new URL('laya.wasm', absolute(moduleUrl)))) });
        if (signal?.aborted) throw abortedError();
        return engine;
      } catch (error) {
        engine.#fail(error); last = error;
        if (signal?.aborted || final.has(error)) throw error;
      } finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); }
    }
    throw last;
  }
  #send(kind, request, onEvent, options, { signal, timeoutMs = 0, requestId } = {}) {
    // Order: closed, dead, an already-cancelled signal (no worker message), then admission.
    if (this.#closing && kind !== 'close') return Promise.reject(new EngineClosedError());
    if (this.#dead) return Promise.reject(exitedError('Laya is closing or closed'));
    if (signal?.aborted) return Promise.reject(new LayaError('Request cancelled', 5));
    if (kind !== 'close' && this.#pending.size >= this.#limit) return Promise.reject(queueFullError());
    const id = ++this.#next;
    // Shared cancellation is optional: ordinary pages still run without isolation.
    const flag = globalThis.crossOriginIsolated && typeof SharedArrayBuffer === 'function' ? new Int32Array(new SharedArrayBuffer(4)) : undefined;
    // Wall-clock milliseconds: the page and worker realms have different performance.now() origins.
    const deadline = timeoutMs ? Date.now() + timeoutMs : 0;
    return new Promise((resolve, reject) => {
      let timer;
      const stop = status => {
        if (flag) Atomics.compareExchange(flag, 0, 0, status);
        cleanup();
        entry.onEvent = undefined; // the promise settles now; drop late events
        this.#worker.postMessage({ kind: 'cancel', id, status });
        reject(new LayaError(status === 6 ? 'Request deadline exceeded' : 'Request cancelled', status));
      };
      const abort = () => stop(5);
      const cleanup = () => { clearTimeout(timer); signal?.removeEventListener('abort', abort); };
      const entry = { resolve, reject, onEvent, cleanup, stop, kind };
      this.#pending.set(id, entry);
      if (timeoutMs) timer = setTimeout(() => stop(6), timeoutMs);
      signal?.addEventListener('abort', abort, { once: true });
      try { this.#worker.postMessage({ id, requestId, kind, request, observe: !!onEvent, options, flag, deadline }); }
      catch (error) {
        this.#pending.delete(id); cleanup();
        // Only init sends options; a value the worker cannot receive (a function, for example) is an argument error.
        reject(kind === 'init' ? invalid(TypeError, `options cannot be sent to the worker: ${error?.message ?? error}`) : exitedError(`Laya worker unreachable: ${error?.message ?? error}`));
      }
    });
  }
  async execute(request, options) {
    // Order: closed, then conversions, then an already-cancelled signal, then admission, then the worker.
    if (this.#closing) throw new EngineClosedError();
    if (this.#dead) throw exitedError('Laya is closing or closed');
    const { onEvent, signal, timeoutMs = 0, requestId } = optionsObject(options);
    if (!Number.isInteger(timeoutMs) || timeoutMs < 0 || timeoutMs > 86400000) throw invalid(RangeError, 'timeoutMs must be 0..86400000');
    if (requestId !== undefined && (!Number.isSafeInteger(requestId) || requestId < 0)) throw invalid(RangeError, 'requestId must be a non-negative safe integer');
    if (signal !== undefined && (typeof signal?.addEventListener !== 'function' || typeof signal?.removeEventListener !== 'function')) throw invalid(TypeError, 'signal must be an AbortSignal');
    if (onEvent !== undefined && typeof onEvent !== 'function') throw invalid(TypeError, 'onEvent must be a function');
    const text = typeof request === 'string' ? request : wellFormedJson(request, 'request');
    if (typeof text === 'string' && !text.isWellFormed()) throw invalid(TypeError, 'request is not valid Unicode');
    if (typeof text !== 'string' || bytes(text) > 1048576 || text.includes('\0')) throw invalid(TypeError, 'Invalid or oversized request');
    return JSON.parse(await this.#send('execute', text, onEvent, undefined, { signal, timeoutMs, requestId }));
  }
  info() { return this.#send('info').then(JSON.parse); }
  /**
   * Terminates the worker and releases the model. Call it when done: the
   * worker holds the model memory until close() runs. Idempotent, and resolves
   * even if the worker has already failed (crash, WebGPU device loss, abort).
   */
  close(options) {
    let cancelPending;
    try { ({ cancelPending = false } = optionsObject(options)); }
    catch (error) { return Promise.reject(error); }
    if (cancelPending) for (const entry of this.#pending.values()) if (entry.kind !== 'close') entry.stop(5);
    if (!this.#close) { this.#closing = true; this.#close = (this.#failed ? Promise.resolve() : this.#send('close')).catch(error => { if (!this.#failed) throw error; }).finally(() => { this.#dead = true; this.#worker.terminate(); }); }
    return this.#close;
  }
}
