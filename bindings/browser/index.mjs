const internal = Symbol('Laya');
const overflows = ['reject', 'truncate_end', 'truncate_start', 'scan'];
const bytes = text => new TextEncoder().encode(text).byteLength;
const absolute = url => new URL(url, globalThis.location?.href).href;
// Every error this package raises carries a status: lifecycle and queue errors as the other bindings
// report a closed engine (1, INVALID_ARGUMENT), a failed worker as an inference failure (3).
const queueFullError = () => Object.assign(new Error('Laya queue is full'), { status: 1, code: 'ERR_LAYA_QUEUE_FULL' });
const exitedError = message => Object.assign(new Error(message), { status: 3, code: 'ERR_LAYA_WORKER_EXITED' });
// Cancelled initialization, like a cancelled request: status 5.
const abortedError = () => Object.assign(new Error('Initialization cancelled'), { status: 5 });
// Failures a WebGPU attempt shares with WASM (fetching, hashing or sizing a model asset): backend 'auto'
// does not retry them with WASM.
const final = new WeakSet();
// Load and ABI failures (status 7) also carry a stable code.
const nativeError = (message, status) => Object.assign(new Error(message), { status }, status === 7 ? { code: 'ERR_LAYA_UNSUPPORTED' } : {});
const invalid = (Type, message) => Object.assign(new Type(message), { status: 1 });
// Options default to {}; null or a non-object is an argument error with status 1.
const optionsObject = options => {
  if (options === undefined) return {};
  if (options === null || typeof options !== 'object') throw invalid(TypeError, 'options must be an object');
  return options;
};
// JSON.stringify escapes lone surrogates instead of failing, which the native
// side would then silently replace, and writes non-finite numbers as null;
// reject both before serializing.
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
const closedError = () => Object.assign(new Error('Laya is closing or closed'), { status: 1, code: 'ERR_LAYA_CLOSED' });
export class Laya {
  #worker; #pending = new Map(); #next = 0; #closing = false; #dead = false; #failed = false; #limit; #close; #diagnostic;
  capabilities;
  constructor(token, worker, limit, diagnostic) {
    if (token !== internal) throw new TypeError('Use Laya.open() or Laya.openRouter()');
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
  static async open(options) { return Laya.#open(optionsObject(options)); }
  static async openRouter(models, options) { return Laya.#open({ ...optionsObject(options), models }); }
  static async #open(options) {
    const { maxPending = 16, maxModelBytes = 1073741824, initializationTimeoutMs = 120000, signal, backend = 'wasm', onDiagnostic } = options;
    if (!Number.isInteger(maxPending) || maxPending < 1 || maxPending > 1024) throw invalid(RangeError, 'maxPending must be 1..1024');
    if (!Number.isSafeInteger(maxModelBytes) || maxModelBytes < 1) throw invalid(RangeError, 'maxModelBytes must be positive');
    if (!Number.isInteger(initializationTimeoutMs) || initializationTimeoutMs < 1 || initializationTimeoutMs > 86400000) throw invalid(RangeError, 'initializationTimeoutMs must be 1..86400000');
    if (options.contextTokens !== undefined && (!Number.isSafeInteger(options.contextTokens) || options.contextTokens < 1)) throw invalid(RangeError, 'contextTokens must be a positive integer');
    if (options.overflow !== undefined && !overflows.includes(options.overflow)) throw invalid(RangeError, 'overflow must be reject, truncate_end, truncate_start or scan');
    const temperatureError = value => value !== undefined && (value === null || typeof value !== 'object' || Array.isArray(value) ||
      Object.values(value).some(t => typeof t !== 'number' || !Number.isFinite(t) || t <= 0));
    if (temperatureError(options.temperatures)) throw invalid(TypeError, 'temperatures must map names to finite positive numbers');
    if (!['wasm','webgpu','auto'].includes(backend)) throw invalid(TypeError, 'Invalid backend');
    if (onDiagnostic !== undefined && typeof onDiagnostic !== 'function') throw invalid(TypeError, 'onDiagnostic must be a function');
    if (signal !== undefined && (typeof signal?.addEventListener !== 'function' || typeof signal?.removeEventListener !== 'function')) throw invalid(TypeError, 'signal must be an AbortSignal');
    if (signal?.aborted) throw abortedError();
    wellFormedJson([options.models, options.modelUrl, options.temperatures, options.auto], 'config');
    const models = options.models && Object.fromEntries(Object.entries(options.models).map(([id, model]) => {
      if (!model || typeof model.url !== 'string' || !model.url) throw invalid(TypeError, 'Each model requires a URL');
      if (model.overflow !== undefined && !overflows.includes(model.overflow)) throw invalid(RangeError, 'overflow must be reject, truncate_end, truncate_start or scan');
      return [id, { ...model, url: absolute(model.url) }];
    }));
    if (models && (Object.keys(models).length < 1 || Object.keys(models).length > 16)) throw invalid(RangeError, 'models must contain 1..16 entries');
    for (const asset of models ? Object.values(models) : [{ url: options.modelUrl, sha256: options.sha256 }]) {
      if (typeof asset.url !== 'string' || !asset.url) throw invalid(TypeError, 'Model URL is required');
      if (asset.sha256 !== undefined && !/^[a-fA-F0-9]{64}$/.test(asset.sha256)) throw invalid(TypeError, 'sha256 must be 64 hexadecimal characters');
    }
    const attempts = backend === 'auto' ? ['webgpu','wasm'] : [backend];
    let last;
    for (const selected of attempts) {
      const moduleUrl = selected === 'webgpu' ? (options.webgpuModuleUrl || new URL('./webgpu/laya.mjs', import.meta.url).href) : (options.moduleUrl || new URL('./wasm/laya.mjs', import.meta.url).href);
      if (!moduleUrl) throw invalid(TypeError, `Missing ${selected} module URL`);
      const worker = new Worker(options.workerUrl || new URL('./worker.mjs', import.meta.url), { type: 'module' });
      const engine = new Laya(internal, worker, maxPending, onDiagnostic);
      const abort = () => engine.#fail(abortedError());
      // status 6 (DEADLINE_EXCEEDED), like request deadlines; browser errors otherwise use codes only for lifecycle failures.
      const timer = setTimeout(() => engine.#fail(Object.assign(new Error('Laya initialization timed out'), { status: 6 })), initializationTimeoutMs);
      try {
        signal?.addEventListener('abort', abort, { once: true });
        // Option getters/URL coercion can run caller code before registration.
        // AbortSignal does not replay an abort that already happened.
        if (signal?.aborted) throw abortedError();
        engine.capabilities = await engine.#send('init', undefined, undefined, { ...options, signal: undefined, onDiagnostic: undefined, diagnostics: !!onDiagnostic,
          models, modelUrl: models ? undefined : absolute(options.modelUrl), maxModelBytes, backend: selected,
          moduleUrl: absolute(moduleUrl), wasmUrl: absolute(selected === 'webgpu' ? (options.webgpuWasmUrl || new URL('laya.wasm', absolute(moduleUrl))) : (options.wasmUrl || new URL('laya.wasm', absolute(moduleUrl)))) });
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
    if (this.#closing && kind !== 'close') return Promise.reject(closedError());
    if (this.#dead) return Promise.reject(exitedError('Laya is closing or closed'));
    if (kind !== 'close' && this.#pending.size >= this.#limit) return Promise.reject(queueFullError());
    if (signal?.aborted) return Promise.reject(Object.assign(new Error('Request cancelled'), { status: 5 }));
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
        reject(Object.assign(new Error(status === 6 ? 'Request deadline exceeded' : 'Request cancelled'), { status }));
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
    const { onEvent, signal, timeoutMs = 0, requestId } = optionsObject(options);
    if (!Number.isInteger(timeoutMs) || timeoutMs < 0 || timeoutMs > 86400000) throw invalid(RangeError, 'timeoutMs must be 0..86400000');
    if (requestId !== undefined && (!Number.isSafeInteger(requestId) || requestId < 0)) throw invalid(RangeError, 'requestId must be a non-negative safe integer');
    if (signal !== undefined && (typeof signal?.addEventListener !== 'function' || typeof signal?.removeEventListener !== 'function')) throw invalid(TypeError, 'signal must be an AbortSignal');
    if (signal?.aborted) throw Object.assign(new Error('Request cancelled'), { status: 5 });
    if (onEvent !== undefined && typeof onEvent !== 'function') throw invalid(TypeError, 'onEvent must be a function');
    if (this.#closing) throw closedError();
    if (this.#dead) throw exitedError('Laya is closing or closed');
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
