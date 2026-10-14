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
  static runtimeInfo({ addonPath } = {}) { return JSON.parse(require(resolveAddon(addonPath)).info()); }
  info() { return this.#send('info').then(JSON.parse); }
  static open(modelPath, options) { return Laya.#open(modelPath, options, false); }
  static async openRouter(config, options) {
    return Laya.#open(typeof config === 'string' ? config : JSON.stringify(config), options, true);
  }
  static async #open(modelPath, { threads = 0, maxPending = 16, addonPath } = {}, router) {
    if (typeof modelPath !== 'string' || modelPath.includes('\0') || Buffer.byteLength(modelPath) > 1048576) throw new TypeError('Invalid model path');
    if (!Number.isInteger(threads) || threads < 0 || threads > 256) throw new RangeError('threads must be 0..256');
    if (!Number.isInteger(maxPending) || maxPending < 1 || maxPending > 1024) throw new RangeError('maxPending must be 1..1024');
    const worker = new Worker(path.join(__dirname, 'worker.cjs'), { workerData: {
      modelPath, threads, router, addonPath: resolveAddon(addonPath)
    }});
    const engine = new Laya(internal, worker, maxPending);
    await new Promise((resolve, reject) => {
      let ready = false;
      worker.on('message', (message) => {
        if (message.startupError) {
          engine.#dead = true;
          reject(Object.assign(new Error(message.startupError), { status: message.status }));
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
        if (message.error) pending.reject(Object.assign(new Error(message.error), { status: message.status }));
        else pending.resolve(message.result);
      });
      const fail = (error) => {
        engine.#dead = true;
        if (!ready) reject(error);
        for (const pending of engine.#pending.values()) { pending.cleanup?.(); pending.reject(error); }
        engine.#pending.clear();
      };
      worker.on('error', fail);
      worker.on('exit', (code) => fail(new Error(`Laya worker exited (${code})`)));
    });
    return engine;
  }
  #send(kind, request, onEvent, { signal, timeoutMs = 0 } = {}) {
    if (this.#dead) return Promise.reject(new Error('Engine worker has exited'));
    // JSON.stringify can run application getters/toJSON methods that submit work
    // or close this engine. Check again at the actual enqueue boundary.
    if (kind !== 'close') {
      if (this.#closing) return Promise.reject(new Error('Engine is closing or closed'));
      if (this.#pending.size >= this.#maxPending) return Promise.reject(new Error('Laya queue is full'));
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
        reject(Object.assign(new Error(status === 6 ? 'Request deadline exceeded' : 'Request cancelled'), { status }));
      };
      const abort = () => stop(5);
      this.#pending.set(id, { resolve, reject, onEvent, cleanup, stop, kind });
      signal?.addEventListener('abort', abort, { once: true });
      if (timeoutMs) timer = setTimeout(() => stop(6), timeoutMs);
      // Keep cancelled entries counted until the worker acknowledges them: an
      // abort storm must not bypass the bounded queue or race native shutdown.
      try { this.#worker.postMessage({ id, kind, request, observe: !!onEvent, cancellation, deadline }); }
      catch (error) { cleanup(); this.#pending.delete(id); reject(error); }
    });
  }
  decide(request, options) { return this.#execute('decide', request, options); }
  decideStructured(request, options) { return this.execute(request, options); }
  decideBatch(requests, options) {
    if (!Array.isArray(requests)) return Promise.reject(new TypeError('Batch must be an array'));
    return this.execute(requests, options);
  }
  execute(request, options) { return this.#execute('execute', request, options); }
  async #execute(kind, request, { onEvent, signal, timeoutMs = 0 } = {}) {
    if (!Number.isInteger(timeoutMs) || timeoutMs < 0 || timeoutMs > 86400000) throw new RangeError('timeoutMs must be 0..86400000');
    if (signal !== undefined && (typeof signal?.addEventListener !== 'function' || typeof signal?.removeEventListener !== 'function')) throw new TypeError('signal must be an AbortSignal');
    if (onEvent !== undefined && typeof onEvent !== 'function') throw new TypeError('onEvent must be a function');
    if (this.#closing) throw new Error('Engine is closing or closed');
    if (this.#pending.size >= this.#maxPending) throw new Error('Laya queue is full');
    // A JSON string preserves the caller's literal key order, including numeric labels.
    const text = typeof request === 'string' ? request : JSON.stringify(request);
    if (typeof text !== 'string' || Buffer.byteLength(text) > 1048576 || text.includes('\0')) throw new TypeError('Invalid or oversized request');
    return JSON.parse(await this.#send(kind, text, onEvent, { signal, timeoutMs }));
  }
  close({ cancelPending = false } = {}) {
    if (cancelPending) for (const pending of this.#pending.values()) if (pending.kind !== 'close') pending.stop?.(5);
    if (!this.#closePromise) {
      this.#closing = true;
      this.#closePromise = this.#send('close'); // FIFO: drain accepted requests first.
    }
    return this.#closePromise;
  }
}
const internal = Symbol('Laya');
module.exports = { Laya };
