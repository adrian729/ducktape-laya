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
  static async open(modelPath, { threads = 0, maxPending = 16, addonPath } = {}) {
    if (typeof modelPath !== 'string' || modelPath.includes('\0')) throw new TypeError('Invalid model path');
    if (!Number.isInteger(threads) || threads < 0 || threads > 256) throw new RangeError('threads must be 0..256');
    if (!Number.isInteger(maxPending) || maxPending < 1 || maxPending > 1024) throw new RangeError('maxPending must be 1..1024');
    const worker = new Worker(path.join(__dirname, 'worker.cjs'), { workerData: {
      modelPath, threads, addonPath: path.resolve(addonPath || process.env.DUCKTAPE_LAYA_ADDON || path.join(__dirname, '../../build/ducktape_laya.node'))
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
  #send(kind, request) {
    if (this.#dead) return Promise.reject(new Error('Engine worker has exited'));
    const id = ++this.#next;
    return new Promise((resolve, reject) => {
      this.#pending.set(id, { resolve, reject });
      try { this.#worker.postMessage({ id, kind, request }); }
      catch (error) { this.#pending.delete(id); reject(error); }
    });
  }
  async decide(request) {
    if (this.#closing) throw new Error('Engine is closing or closed');
    if (this.#pending.size >= this.#maxPending) throw new Error('Laya queue is full');
    // A JSON string preserves the caller's literal key order, including numeric labels.
    const text = typeof request === 'string' ? request : JSON.stringify(request);
    if (typeof text !== 'string' || Buffer.byteLength(text) > 1048576 || text.includes('\0')) throw new TypeError('Invalid or oversized request');
    return JSON.parse(await this.#send('decide', text));
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
