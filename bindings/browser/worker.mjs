let runtime, handle = 0, routed = false, files = [], chain = Promise.resolve();
let observer = 0, cancellation = 0, active, closing = false;
const jobs = new Map();
const fail = (message, status) => Object.assign(new Error(message), { status });
async function asset(url, limit, expectedHash) {
  const response = await fetch(url);
  if (!response.ok || !response.body) throw new Error(`Model fetch failed: HTTP ${response.status}`);
  const reader = response.body.getReader(), chunks = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) throw new Error('Model assets exceed maxModelBytes');
      chunks.push(value);
    }
  } catch (error) { await reader.cancel().catch(() => {}); throw error; }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  if (expectedHash) {
    const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), v => v.toString(16).padStart(2, '0')).join('');
    if (hash !== expectedHash.toLowerCase()) throw new Error('Model SHA-256 mismatch');
  }
  return bytes;
}
async function call(name, request, eventId, observe, job) {
  const out = runtime._malloc(4), error = runtime._malloc(4);
  let hook = 0, control = 0;
  if (!out || !error) { if (out) runtime._free(out); if (error) runtime._free(error); throw fail('Out of WASM memory', 4); }
  runtime.HEAPU32[out >>> 2] = runtime.HEAPU32[error >>> 2] = 0;
  try {
    if (observe) {
      hook = runtime._malloc(16);
      if (!hook) throw fail('Out of WASM memory', 4);
      runtime.HEAPU32[hook >>> 2] = eventId >>> 0;
      runtime.HEAPU32[(hook + 4) >>> 2] = Math.floor(eventId / 4294967296);
      runtime.HEAPU32[(hook + 8) >>> 2] = observer;
      runtime.HEAPU32[(hook + 12) >>> 2] = 0;
    }
    control = runtime._malloc(24);
    if (!control) throw fail('Out of WASM memory', 4);
    active = job;
    const remaining = job.deadline ? Math.max(0, Math.ceil(job.deadline - performance.timeOrigin - performance.now())) : 0;
    if (job.deadline && !remaining && !job.status) job.status = 6;
    runtime.HEAPU32.set([24, 0, remaining, 0, cancellation, 0], control >>> 2);
    const info = name === 'dtl_info';
    const status = await runtime.ccall(name, 'number', info ? ['number','number','number'] : ['number','string','number','number','number','number'],
      info ? [routed ? 0 : handle, out, error] : [handle, request, hook, control, out, error], { async: !info });
    const result = runtime.HEAPU32[out >>> 2], message = runtime.HEAPU32[error >>> 2];
    try {
      if (status) throw fail(message ? runtime.UTF8ToString(message) : 'Laya operation failed', status);
      return runtime.UTF8ToString(result);
    } finally { runtime._dtl_free(result); runtime._dtl_free(message); }
  } finally { active = undefined; if (control) runtime._free(control); if (hook) runtime._free(hook); runtime._free(out); runtime._free(error); }
}
async function init(options) {
  if (options.backend === 'webgpu') {
    if (!navigator.gpu || !await navigator.gpu.requestAdapter()) throw new Error('WebGPU adapter unavailable');
    // Monitor the runtime's device in this private worker realm.
    const requestDevice = GPUAdapter.prototype.requestDevice;
    GPUAdapter.prototype.requestDevice = async function(...args) {
      const device = await requestDevice.apply(this, args);
      device.lost.then(info => { if (!closing) postMessage({ fatal: `WebGPU device lost (${info.reason})` }); });
      return device;
    };
  }
  const create = (await import(options.moduleUrl)).default;
  runtime = await create({ onAbort: () => { if (!closing) postMessage({ fatal: 'Laya WASM runtime aborted' }); }, locateFile: name => name.endsWith('.wasm') ? options.wasmUrl : new URL(name, options.moduleUrl).href,
    print: options.diagnostics ? text => postMessage({ diagnostic: String(text) }) : () => {}, printErr: options.diagnostics ? text => postMessage({ diagnostic: String(text) }) : () => {} });
  if (runtime._dtl_abi_version() !== 1) throw new Error('Incompatible Laya WASM ABI');
  observer = runtime.addFunction(text => {
    try { const event = JSON.parse(runtime.UTF8ToString(text)); postMessage({ id: event.request_id, event }); } catch {}
  }, 'vii');
  cancellation = runtime.addFunction(() => active ? ((active.flag ? Atomics.load(active.flag, 0) : 0) || active.status || 0) : 0, 'ii');
  routed = !!options.models;
  const assets = options.models || { model: { url: options.modelUrl, sha256: options.sha256 } };
  let remaining = options.maxModelBytes;
  const models = Object.create(null), loaded = new Map();
  for (const [id, descriptor] of Object.entries(assets)) {
    const identity = JSON.stringify([descriptor.url, descriptor.sha256 || null]);
    if (loaded.has(identity)) { models[id] = loaded.get(identity); continue; }
    const bytes = await asset(descriptor.url, remaining, descriptor.sha256);
    remaining -= bytes.length;
    const path = `/model-${files.length}.gguf`;
    runtime.FS.writeFile(path, bytes, { canOwn: true });
    files.push(path); models[id] = path; loaded.set(identity, path);
  }
  const opts = runtime._malloc(16), out = runtime._malloc(4), error = runtime._malloc(4);
  if (!opts || !out || !error) throw fail('Out of WASM memory', 4);
  try {
    runtime.HEAPU32.set([16, 1, 1, 0], opts >>> 2); // single-threaded worker; no COOP/COEP requirement
    runtime.HEAPU32[out >>> 2] = runtime.HEAPU32[error >>> 2] = 0;
    const input = routed ? JSON.stringify({ models, max_loaded: options.maxLoaded ?? 1 }) : files[0];
    const status = await runtime.ccall(routed ? 'dtl_router_open' : 'dtl_open', 'number', ['string','number','number','number'], [input, opts, out, error], { async: true });
    handle = runtime.HEAPU32[out >>> 2];
    const message = runtime.HEAPU32[error >>> 2];
    try { if (status) throw fail(message ? runtime.UTF8ToString(message) : 'Model loading failed', status); }
    finally { runtime._dtl_free(message); }
  } finally { runtime._free(opts); runtime._free(out); runtime._free(error); }
  return { backend: options.backend, abi: 1, threads: 1, runningCancellation: globalThis.crossOriginIsolated === true && options.backend === 'wasm', deadlines: true };
}
onmessage = ({ data }) => {
  if (data.kind === 'cancel') { const job = jobs.get(data.id); if (job && !job.status) job.status = data.status; return; }
  jobs.set(data.id, data);
  chain = chain.then(async () => {
    // Give queued cancellation messages a chance to arrive before the next call.
    await new Promise(resolve => setTimeout(resolve, 0));
    const { id, kind, request, observe } = data;
    try {
      if (kind === 'init') { postMessage({ id, result: await init(data.options) }); return; }
      if (kind === 'close') {
        closing = true;
        if (handle) await runtime.ccall(routed ? 'dtl_router_close' : 'dtl_close', null, ['number'], [handle], { async: true });
        handle = 0;
        if (observer) runtime.removeFunction(observer);
        if (cancellation) runtime.removeFunction(cancellation);
        for (const path of files) runtime.FS.unlink(path);
        files = [];
        postMessage({ id, result: null }); self.close(); return;
      }
      const name = kind === 'info' ? 'dtl_info' : routed ? 'dtl_router_execute_with_options' : kind === 'decide' ? 'dtl_decide_with_options' : 'dtl_execute_with_options';
      postMessage({ id, result: await call(name, request, id, observe, data) });
    } catch (error) { postMessage({ id, error: error.message, status: error.status }); }
    finally { jobs.delete(id); }
  });
};
