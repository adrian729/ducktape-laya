const internal = Symbol('Laya');
const bytes = text => new TextEncoder().encode(text).byteLength;
const absolute = url => new URL(url, globalThis.location?.href).href;
export class Laya {
  #worker; #pending = new Map(); #next = 0; #closing = false; #dead = false; #limit; #close; #diagnostic;
  capabilities;
  constructor(token, worker, limit, diagnostic) {
    if (token !== internal) throw new TypeError('Use Laya.open() or Laya.openRouter()');
    this.#worker = worker; this.#limit = limit; this.#diagnostic = diagnostic;
    worker.onmessage = ({ data }) => {
      if (data.fatal) { this.#fail(Object.assign(new Error(data.fatal), { status: 3 })); return; }
      if (data.diagnostic) { try { Promise.resolve(this.#diagnostic?.(data.diagnostic)).catch(() => {}); } catch {} return; }
      const pending = this.#pending.get(data.id);
      if (!pending) return;
      if (data.event) { try { Promise.resolve(pending.onEvent?.(data.event)).catch(() => {}); } catch {} return; }
      this.#pending.delete(data.id);
      pending.cleanup?.();
      if (data.error) pending.reject(Object.assign(new Error(data.error), { status: data.status }));
      else pending.resolve(data.result);
    };
    worker.onerror = event => this.#fail(new Error(event.message || 'Laya worker failed'));
    worker.onmessageerror = () => this.#fail(new Error('Laya worker message could not be decoded'));
  }
  #fail(error) {
    this.#dead = true;
    for (const entry of this.#pending.values()) { entry.cleanup?.(); entry.reject(error); }
    this.#pending.clear(); this.#worker.terminate();
  }
  static open(options) { return Laya.#open(options); }
  static openRouter(models, options) { return Laya.#open({ ...options, models }); }
  static async #open(options = {}) {
    const { maxPending = 16, maxModelBytes = 1073741824, initializationTimeoutMs = 120000, signal, backend = 'wasm' } = options;
    if (!Number.isInteger(maxPending) || maxPending < 1 || maxPending > 1024) throw new RangeError('maxPending must be 1..1024');
    if (!Number.isSafeInteger(maxModelBytes) || maxModelBytes < 1) throw new RangeError('maxModelBytes must be positive');
    if (!Number.isFinite(initializationTimeoutMs) || initializationTimeoutMs <= 0) throw new RangeError('initializationTimeoutMs must be positive');
    if (!['wasm','webgpu','auto'].includes(backend)) throw new TypeError('Invalid backend');
    if (options.onDiagnostic !== undefined && typeof options.onDiagnostic !== 'function') throw new TypeError('onDiagnostic must be a function');
    if (signal && (typeof signal.addEventListener !== 'function' || typeof signal.removeEventListener !== 'function')) throw new TypeError('signal must be an AbortSignal');
    if (signal?.aborted) throw new DOMException('Initialization aborted', 'AbortError');
    const models = options.models && Object.fromEntries(Object.entries(options.models).map(([id, model]) => {
      if (!model || typeof model.url !== 'string' || !model.url) throw new TypeError('Each model requires a URL');
      return [id, { ...model, url: absolute(model.url) }];
    }));
    if (models && (Object.keys(models).length < 1 || Object.keys(models).length > 16)) throw new RangeError('models must contain 1..16 entries');
    for (const asset of models ? Object.values(models) : [{ url: options.modelUrl, sha256: options.sha256 }]) {
      if (typeof asset.url !== 'string' || !asset.url) throw new TypeError('Model URL is required');
      if (asset.sha256 !== undefined && !/^[a-fA-F0-9]{64}$/.test(asset.sha256)) throw new TypeError('sha256 must be 64 hexadecimal characters');
    }
    const attempts = backend === 'auto' ? ['webgpu','wasm'] : [backend];
    let last;
    for (const selected of attempts) {
      const moduleUrl = selected === 'webgpu' ? (options.webgpuModuleUrl || new URL('./webgpu/laya.mjs', import.meta.url).href) : (options.moduleUrl || new URL('./wasm/laya.mjs', import.meta.url).href);
      if (!moduleUrl) throw new TypeError(`Missing ${selected} module URL`);
      const worker = new Worker(options.workerUrl || new URL('./worker.mjs', import.meta.url), { type: 'module' });
      const engine = new Laya(internal, worker, maxPending, options.onDiagnostic);
      const abort = () => engine.#fail(new DOMException('Initialization aborted', 'AbortError'));
      const timer = setTimeout(() => engine.#fail(new Error('Laya initialization timed out')), initializationTimeoutMs);
      signal?.addEventListener('abort', abort, { once: true });
      try {
        engine.capabilities = await engine.#send('init', undefined, undefined, { ...options, signal: undefined, onDiagnostic: undefined, diagnostics: !!options.onDiagnostic,
          models, modelUrl: models ? undefined : absolute(options.modelUrl), maxModelBytes, backend: selected,
          moduleUrl: absolute(moduleUrl), wasmUrl: absolute(selected === 'webgpu' ? (options.webgpuWasmUrl || new URL('laya.wasm', absolute(moduleUrl))) : (options.wasmUrl || new URL('laya.wasm', absolute(moduleUrl)))) });
        return engine;
      } catch (error) {
        engine.#fail(error); last = error;
        if (signal?.aborted) throw error;
      } finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); }
    }
    throw last;
  }
  #send(kind, request, onEvent, options, { signal, timeoutMs = 0 } = {}) {
    if (this.#dead || (this.#closing && kind !== 'close')) return Promise.reject(new Error('Laya is closing or closed'));
    if (kind !== 'close' && this.#pending.size >= this.#limit) return Promise.reject(new Error('Laya queue is full'));
    if (signal?.aborted) return Promise.reject(Object.assign(new Error('Request cancelled'), { status: 5 }));
    const id = ++this.#next;
    // Shared cancellation is optional: ordinary pages still run without isolation.
    const flag = globalThis.crossOriginIsolated && typeof SharedArrayBuffer === 'function' ? new Int32Array(new SharedArrayBuffer(4)) : undefined;
    const deadline = timeoutMs ? performance.timeOrigin + performance.now() + timeoutMs : 0;
    return new Promise((resolve, reject) => {
      let timer;
      const stop = status => {
        if (flag) Atomics.compareExchange(flag, 0, 0, status);
        cleanup();
        this.#worker.postMessage({ kind: 'cancel', id, status });
        reject(Object.assign(new Error(status === 6 ? 'Request deadline exceeded' : 'Request cancelled'), { status }));
      };
      const abort = () => stop(5);
      const cleanup = () => { clearTimeout(timer); signal?.removeEventListener('abort', abort); };
      this.#pending.set(id, { resolve, reject, onEvent, cleanup, stop, kind });
      if (timeoutMs) timer = setTimeout(() => stop(6), timeoutMs);
      signal?.addEventListener('abort', abort, { once: true });
      try { this.#worker.postMessage({ id, kind, request, observe: !!onEvent, options, flag, deadline }); }
      catch (error) { this.#pending.delete(id); cleanup(); reject(error); }
    });
  }
  async #execute(kind, request, { onEvent, signal, timeoutMs = 0 } = {}) {
    if (!Number.isInteger(timeoutMs) || timeoutMs < 0 || timeoutMs > 86400000) throw new RangeError('timeoutMs must be 0..86400000');
    if (signal && (typeof signal.addEventListener !== 'function' || typeof signal.removeEventListener !== 'function')) throw new TypeError('signal must be an AbortSignal');
    if (signal?.aborted) throw Object.assign(new Error('Request cancelled'), { status: 5 });
    if (onEvent !== undefined && typeof onEvent !== 'function') throw new TypeError('onEvent must be a function');
    if (this.#closing || this.#dead) throw new Error('Laya is closing or closed');
    const text = typeof request === 'string' ? request : JSON.stringify(request);
    if (typeof text !== 'string' || bytes(text) > 1048576 || text.includes('\0')) throw new TypeError('Invalid or oversized request');
    return JSON.parse(await this.#send(kind, text, onEvent, undefined, { signal, timeoutMs }));
  }
  info() { return this.#send('info').then(JSON.parse); }
  execute(request, options) { return this.#execute('execute', request, options); }
  decide(request, options) { return this.#execute('decide', request, options); }
  decideStructured(request, options) { return this.execute(request, options); }
  decideBatch(requests, options) {
    if (!Array.isArray(requests)) return Promise.reject(new TypeError('Batch must be an array'));
    return this.execute(requests, options);
  }
  close({ cancelPending = false } = {}) {
    if (cancelPending) for (const entry of this.#pending.values()) if (entry.kind !== 'close') entry.stop(5);
    if (!this.#close) { this.#closing = true; this.#close = this.#send('close').finally(() => { this.#dead = true; this.#worker.terminate(); }); }
    return this.#close;
  }
}
