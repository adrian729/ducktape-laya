'use strict';
const { Worker } = require('node:worker_threads');
const path = require('node:path');
class Laya {
  #worker; #pending = new Map(); #next = 0; #closing = false; #closePromise;
  #maxPending; #dead = false;
  constructor(token, worker, maxPending) {
    if (token !== internal) throw new TypeError('Use Laya.open()');
    this.#worker = worker; this.#maxPending = maxPending;
  }
  static open(modelPath, options) { return Laya.#open(modelPath, options, false); }
  static async openRouter(config, options) {
    return Laya.#open(typeof config === 'string' ? config : JSON.stringify(config), options, true);
  }
  static async #open(modelPath, { threads = 0, maxPending = 16, addonPath } = {}, router) {
    if (typeof modelPath !== 'string' || modelPath.includes('\0') || Buffer.byteLength(modelPath) > 1048576) throw new TypeError('Invalid model path');
    if (!Number.isInteger(threads) || threads < 0 || threads > 256) throw new RangeError('threads must be 0..256');
    if (!Number.isInteger(maxPending) || maxPending < 1 || maxPending > 1024) throw new RangeError('maxPending must be 1..1024');
    const worker = new Worker(path.join(__dirname, 'worker.cjs'), { workerData: {
      modelPath, threads, router, addonPath: path.resolve(addonPath || process.env.DUCKTAPE_LAYA_ADDON || path.join(__dirname, '../../build/ducktape_laya.node'))
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
        if (message.error) pending.reject(Object.assign(new Error(message.error), { status: message.status }));
        else pending.resolve(message.result);
      });
      const fail = (error) => {
        engine.#dead = true;
        if (!ready) reject(error);
        for (const { reject } of engine.#pending.values()) reject(error);
        engine.#pending.clear();
      };
      worker.on('error', fail);
      worker.on('exit', (code) => fail(new Error(`Laya worker exited (${code})`)));
    });
    return engine;
  }
  #send(kind, request, onEvent) {
    if (this.#dead) return Promise.reject(new Error('Engine worker has exited'));
    // JSON.stringify can run application getters/toJSON methods that submit work
    // or close this engine. Check again at the actual enqueue boundary.
    if (kind !== 'close') {
      if (this.#closing) return Promise.reject(new Error('Engine is closing or closed'));
      if (this.#pending.size >= this.#maxPending) return Promise.reject(new Error('Laya queue is full'));
    }
    const id = ++this.#next;
    return new Promise((resolve, reject) => {
      this.#pending.set(id, { resolve, reject, onEvent });
      try { this.#worker.postMessage({ id, kind, request, observe: !!onEvent }); }
      catch (error) { this.#pending.delete(id); reject(error); }
    });
  }
  decide(request, options) { return this.#execute('decide', request, options); }
  decideStructured(request, options) { return this.execute(request, options); }
  decideBatch(requests, options) {
    if (!Array.isArray(requests)) return Promise.reject(new TypeError('Batch must be an array'));
    return this.execute(requests, options);
  }
  execute(request, options) { return this.#execute('execute', request, options); }
  async #execute(kind, request, { onEvent } = {}) {
    if (onEvent !== undefined && typeof onEvent !== 'function') throw new TypeError('onEvent must be a function');
    if (this.#closing) throw new Error('Engine is closing or closed');
    if (this.#pending.size >= this.#maxPending) throw new Error('Laya queue is full');
    // A JSON string preserves the caller's literal key order, including numeric labels.
    const text = typeof request === 'string' ? request : JSON.stringify(request);
    if (typeof text !== 'string' || Buffer.byteLength(text) > 1048576 || text.includes('\0')) throw new TypeError('Invalid or oversized request');
    return JSON.parse(await this.#send(kind, text, onEvent));
  }
  close() {
    if (!this.#closePromise) {
      this.#closing = true;
      this.#closePromise = this.#send('close'); // FIFO: drain accepted requests first.
    }
    return this.#closePromise;
  }
}
const internal = Symbol('Laya');
module.exports = { Laya };
