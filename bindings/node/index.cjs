'use strict';
const { Worker } = require('node:worker_threads');
const path = require('node:path');
const resolveAddon = require('./resolve-addon.cjs');
class Laya {
  #worker; #pending = new Map(); #next = 0; #closing = false; #closePromise;
  #maxPending; #dead = false;
  constructor(token, worker, maxPending) {
    if (token !== internal) throw new TypeError('Use Laya.open()');
    this.#worker = worker; this.#maxPending = maxPending;
  }
  static runtimeInfo(options) {
    const { addonPath } = optionsObject(options);
    let addon;
    try { addon = require(resolveAddon(addonPath)); }
    catch (error) { throw nativeError(error.message, error.status ?? 7); }
    return JSON.parse(addon.info());
  }
  info() { return this.#send('info').then(JSON.parse); }
  static open(modelPath, options) { return Laya.#open(modelPath, options, false); }
  static async openRouter(config, options) {
    let text = config;
    if (typeof config !== 'string') text = wellFormedJson(config, 'router config');
    return Laya.#open(text, options, true);
  }
  static async #open(modelPath, options, router) {
    const { threads, contextTokens, overflow, temperatures, maxPending = 16, addonPath } = optionsObject(options);
    const what = router ? 'router config' : 'model path';
    if (typeof modelPath !== 'string' || modelPath.includes('\0') || Buffer.byteLength(modelPath) > 1048576) throw invalid(TypeError, `Invalid ${what}`);
    if (!modelPath.isWellFormed()) throw invalid(TypeError, `${what} is not valid Unicode`);
    const config = {};
    if (threads !== undefined) {
      if (!Number.isInteger(threads) || threads < 0 || threads > 256) throw invalid(RangeError, 'threads must be 0..256');
      config.threads = threads;
    }
    if (contextTokens !== undefined) {
      if (!Number.isSafeInteger(contextTokens) || contextTokens < 1) throw invalid(RangeError, 'contextTokens must be a positive integer');
      config.context_tokens = contextTokens;
    }
    if (overflow !== undefined) {
      if (!['reject', 'truncate_end', 'truncate_start', 'scan'].includes(overflow)) throw invalid(RangeError, 'overflow must be reject, truncate_end, truncate_start or scan');
      config.overflow = overflow;
    }
    if (temperatures !== undefined) {
      if (temperatures === null || typeof temperatures !== 'object' || Array.isArray(temperatures)) throw invalid(TypeError, 'temperatures must be an object');
      for (const value of Object.values(temperatures)) {
        if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) throw invalid(RangeError, 'temperatures must be finite positive numbers');
      }
      config.temperatures = temperatures;
    }
    if (!Number.isInteger(maxPending) || maxPending < 1 || maxPending > 1024) throw invalid(RangeError, 'maxPending must be 1..1024');
    let resolved;
    try { resolved = resolveAddon(addonPath); }
    catch (error) { throw nativeError(error.message, error.status ?? 7); }
    const worker = new Worker(path.join(__dirname, 'worker.cjs'), { workerData: {
      modelPath, config: Object.keys(config).length ? wellFormedJson(config, 'config') : null, router, addonPath: resolved
    }});
    const engine = new Laya(internal, worker, maxPending);
    await new Promise((resolve, reject) => {
      let ready = false;
      worker.on('message', (message) => {
        if (message.startupError) {
          engine.#dead = true;
          reject(nativeError(message.startupError, message.status));
          return;
        }
        if (message.ready) { ready = true; resolve(); return; }
        const pending = engine.#pending.get(message.id);
        if (!pending) return;
        if (message.event) {
          // Passive callbacks run on the host thread. Never block inference on
          // their promises or let a telemetry failure reject a decision.
          try { Promise.resolve(pending.onEvent?.(message.event)).catch(() => {}); } catch {}
          return;
        }
        engine.#pending.delete(message.id);
        pending.cleanup?.();
        if (message.error) pending.reject(nativeError(message.error, message.status));
        else pending.resolve(message.result);
      });
      const fail = (error) => {
        engine.#dead = true;
        if (!ready) reject(error);
        for (const pending of engine.#pending.values()) { pending.cleanup?.(); pending.reject(error); }
        engine.#pending.clear();
      };
      worker.on('error', fail);
      worker.on('exit', (code) => fail(exitedError(`Laya worker exited (${code})`)));
    });
    return engine;
  }
  #send(kind, request, onEvent, { signal, timeoutMs = 0, requestId } = {}) {
    // A closed engine reports ERR_LAYA_CLOSED even after its worker has exited.
    if (this.#closing && kind !== 'close') return Promise.reject(closedError());
    if (this.#dead) return Promise.reject(exitedError('Engine worker has exited'));
    // JSON.stringify can run application getters/toJSON methods that submit work
    // or close this engine. Check again at the actual enqueue boundary.
    if (kind !== 'close') {
      if (this.#pending.size >= this.#maxPending) return Promise.reject(queueFullError());
    }
    if (signal?.aborted) return Promise.reject(Object.assign(new Error('Request cancelled'), { status: 5 }));
    const id = ++this.#next;
    const cancellation = new Int32Array(new SharedArrayBuffer(4));
    const deadline = timeoutMs ? process.hrtime.bigint() + BigInt(timeoutMs) * 1000000n : 0n;
    return new Promise((resolve, reject) => {
      let timer;
      const cleanup = () => { clearTimeout(timer); signal?.removeEventListener('abort', abort); };
      const stop = status => {
        Atomics.compareExchange(cancellation, 0, 0, status);
        cleanup();
        entry.onEvent = undefined; // the promise settles now; drop late events
        reject(Object.assign(new Error(status === 6 ? 'Request deadline exceeded' : 'Request cancelled'), { status }));
      };
      const abort = () => stop(5);
      const entry = { resolve, reject, onEvent, cleanup, stop, kind };
      this.#pending.set(id, entry);
      signal?.addEventListener('abort', abort, { once: true });
      if (timeoutMs) timer = setTimeout(() => stop(6), timeoutMs);
      // Keep cancelled entries counted until the worker acknowledges them: an
      // abort storm must not bypass the bounded queue or race native shutdown.
      try { this.#worker.postMessage({ id, requestId: requestId ?? id, kind, request, observe: !!onEvent, cancellation, deadline }); }
      catch (error) { cleanup(); this.#pending.delete(id); reject(error); }
    });
  }
  /** Runs one request object (or JSON string) or an array of requests. */
  async execute(request, options) {
    const { onEvent, signal, timeoutMs = 0, requestId } = optionsObject(options);
    if (!Number.isInteger(timeoutMs) || timeoutMs < 0 || timeoutMs > 86400000) throw invalid(RangeError, 'timeoutMs must be 0..86400000');
    if (requestId !== undefined && (!Number.isSafeInteger(requestId) || requestId < 0)) throw invalid(RangeError, 'requestId must be a non-negative safe integer');
    if (signal !== undefined && (typeof signal?.addEventListener !== 'function' || typeof signal?.removeEventListener !== 'function')) throw invalid(TypeError, 'signal must be an AbortSignal');
    if (onEvent !== undefined && typeof onEvent !== 'function') throw invalid(TypeError, 'onEvent must be a function');
    if (this.#closing) throw closedError();
    if (this.#pending.size >= this.#maxPending) throw queueFullError();
    // A JSON string preserves the caller's literal key order, including numeric labels.
    const text = typeof request === 'string' ? request : wellFormedJson(request, 'request');
    if (typeof text === 'string' && !text.isWellFormed()) throw invalid(TypeError, 'request is not valid Unicode');
    if (typeof text !== 'string' || Buffer.byteLength(text) > 1048576 || text.includes('\0')) throw invalid(TypeError, 'Invalid or oversized request');
    return JSON.parse(await this.#send('execute', text, onEvent, { signal, timeoutMs, requestId }));
  }
  /**
   * Stops the worker thread and releases the model. Required: the worker keeps
   * the Node.js process alive until close() is called. Idempotent, and resolves
   * even if the worker has already crashed or exited.
   */
  close({ cancelPending = false } = {}) {
    if (cancelPending) for (const pending of this.#pending.values()) if (pending.kind !== 'close') pending.stop?.(5);
    if (!this.#closePromise) {
      this.#closing = true;
      // FIFO: drain accepted requests first. A worker that already died has
      // nothing left to close, so closing it succeeds and never masks the
      // original failure in `try { ... } finally { await engine.close(); }`.
      this.#closePromise = this.#send('close').catch(error => { if (!this.#dead) throw error; });
    }
    return this.#closePromise;
  }
}
const internal = Symbol('Laya');
// Options default to {}; null or a non-object is an argument error with status 1.
const optionsObject = options => {
  if (options === undefined) return {};
  if (options === null || typeof options !== 'object') throw invalid(TypeError, 'options must be an object');
  return options;
};
const closedError = () => Object.assign(new Error('Engine is closing or closed'), { code: 'ERR_LAYA_CLOSED' });
const queueFullError = () => Object.assign(new Error('Laya queue is full'), { code: 'ERR_LAYA_QUEUE_FULL' });
const exitedError = message => Object.assign(new Error(message), { code: 'ERR_LAYA_WORKER_EXITED' });
// Load and ABI failures (status 7) also carry a stable code.
const nativeError = (message, status) => Object.assign(new Error(message), { status }, status === 7 ? { code: 'ERR_LAYA_UNSUPPORTED' } : {});
const invalid = (Type, message) => Object.assign(new Type(message), { status: 1 });
// JSON.stringify escapes lone surrogates instead of failing, which the native
// side would then silently replace; reject them before serializing.
const wellFormedJson = (value, what) => {
  try {
    return JSON.stringify(value, (key, item) => {
      if (!key.isWellFormed() || (typeof item === 'string' && !item.isWellFormed())) throw invalid(TypeError, `${what} is not valid Unicode`);
      return item;
    });
  } catch (error) {
    // Circular structures, BigInt values and throwing toJSON/getters are argument errors.
    if (error && typeof error === 'object' && error.status === undefined) error.status = 1;
    throw error;
  }
};
const Status = Object.freeze({ OK: 0, INVALID_ARGUMENT: 1, MODEL_ERROR: 2, INFERENCE_ERROR: 3, OUT_OF_MEMORY: 4, CANCELLED: 5, DEADLINE_EXCEEDED: 6, UNSUPPORTED_PLATFORM: 7 });
module.exports = { Laya, Status };
