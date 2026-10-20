let runtime, handle = 0, routed = false, files = [], chain = Promise.resolve();
const CALL_OPTIONS_SIZE = 40;
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
async function call(name, request, job) {
  const out = runtime._malloc(4), error = runtime._malloc(4);
  let control = 0, text = 0;
  if (!out || !error) { if (out) runtime._free(out); if (error) runtime._free(error); throw fail('Out of WASM memory', 4); }
  runtime.HEAPU32[out >>> 2] = runtime.HEAPU32[error >>> 2] = 0;
  try {
    const info = name === 'dtl_info';
    if (!info) {
      // wasm32 dtl_call_options: u32 size@0, u32 reserved@4, u64 request_id@8, u64 timeout_ms@16,
      // on_event@24, event_user_data@28, should_cancel@32, cancel_user_data@36 (40 bytes).
      control = runtime._malloc(CALL_OPTIONS_SIZE);
      if (!control) throw fail('Out of WASM memory', 4);
      // Requests (up to 1 MiB) go on the heap: a ccall string argument would take
      // half of the 2 MiB stack the native call itself needs.
      const length = runtime.lengthBytesUTF8(request) + 1;
      text = runtime._malloc(length);
      if (!text) throw fail('Out of WASM memory', 4);
      runtime.stringToUTF8(request, text, length);
      active = job;
      const remaining = job.deadline ? Math.max(0, Math.ceil(job.deadline - performance.timeOrigin - performance.now())) : 0;
      if (job.deadline && !remaining && !job.status) job.status = 6;
      const requestId = job.requestId ?? job.id;
      runtime.HEAPU32.set([CALL_OPTIONS_SIZE, 0, requestId >>> 0, Math.floor(requestId / 4294967296), remaining, 0,
        job.observe ? observer : 0, 0, cancellation, 0], control >>> 2);
    }
    const status = await runtime.ccall(name, 'number', info ? ['number','number','number'] : ['number','number','number','number','number'],
      info ? [handle, out, error] : [handle, text, control, out, error], { async: !info });
    const result = runtime.HEAPU32[out >>> 2], message = runtime.HEAPU32[error >>> 2];
    try {
      if (status) throw fail(message ? runtime.UTF8ToString(message) : 'Laya operation failed', status);
      return runtime.UTF8ToString(result);
    } finally { runtime._dtl_free(result); runtime._dtl_free(message); }
  } finally { active = undefined; if (control) runtime._free(control); if (text) runtime._free(text); runtime._free(out); runtime._free(error); }
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
  if (runtime._dtl_abi_version() !== 2) throw new Error('Incompatible Laya WASM ABI');
  const diagnostics = JSON.parse(await call('dtl_info', null, {}));
  const expectedBackend = options.backend === 'webgpu' ? 'webgpu' : 'wasm-cpu';
  if (diagnostics.backend !== expectedBackend) throw new Error(`Laya backend mismatch: expected ${expectedBackend}, got ${diagnostics.backend}`);
  {
    // Verify the wasm32 struct layout the worker writes against the runtime's own defaults.
    const probe = runtime._malloc(CALL_OPTIONS_SIZE);
    if (!probe) throw fail('Out of WASM memory', 4);
    try {
      runtime._dtl_default_call_options(probe);
      if (runtime.HEAPU32[probe >>> 2] !== CALL_OPTIONS_SIZE) throw new Error('Incompatible Laya WASM ABI');
    } finally { runtime._free(probe); }
  }
  observer = runtime.addFunction((text, user) => {
    // Calls serialize, so the active job owns every event. Route by job id; the reported request_id may be caller-chosen.
    try { const event = JSON.parse(runtime.UTF8ToString(text)); if (active) postMessage({ id: active.id, event }); } catch {}
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
  const out = runtime._malloc(4), error = runtime._malloc(4);
  if (!out || !error) throw fail('Out of WASM memory', 4);
  try {
    runtime.HEAPU32[out >>> 2] = runtime.HEAPU32[error >>> 2] = 0;
    // Single-threaded worker; no COOP/COEP requirement.
    const config = { threads: 1 };
    if (options.contextTokens !== undefined) config.context_tokens = options.contextTokens;
    if (options.overflow !== undefined) config.overflow = options.overflow;
    if (options.temperatures !== undefined) config.temperatures = options.temperatures;
    const routes = Object.create(null);
    for (const [id, path] of Object.entries(models)) {
      const route = {};
      if (assets[id]?.contextTokens !== undefined) route.context_tokens = assets[id].contextTokens;
      if (assets[id]?.overflow !== undefined) route.overflow = assets[id].overflow;
      if (assets[id]?.temperatures !== undefined) route.temperatures = assets[id].temperatures;
      routes[id] = Object.keys(route).length ? { path, config: route } : path;
    }
    const input = routed ? JSON.stringify({ models: routes, max_loaded: options.maxLoaded ?? 1, ...(options.auto ? { auto: options.auto } : {}) }) : files[0];
    const status = await runtime.ccall(routed ? 'dtl_open_router' : 'dtl_open', 'number', ['string','string','number','number'], [input, JSON.stringify(config), out, error], { async: true });
    handle = runtime.HEAPU32[out >>> 2];
    const message = runtime.HEAPU32[error >>> 2];
    try { if (status) throw fail(message ? runtime.UTF8ToString(message) : 'Model loading failed', status); }
    finally { runtime._dtl_free(message); }
  } finally { runtime._free(out); runtime._free(error); }
  return { backend: options.backend, abi: 2, threads: 1, runningCancellation: globalThis.crossOriginIsolated === true && options.backend === 'wasm', deadlines: true };
}
onmessage = ({ data }) => {
  if (data.kind === 'cancel') { const job = jobs.get(data.id); if (job && !job.status) job.status = data.status; return; }
  jobs.set(data.id, data);
  chain = chain.then(async () => {
    // Give queued cancellation messages a chance to arrive before the next call.
    await new Promise(resolve => setTimeout(resolve, 0));
    const { id, kind, request } = data;
    try {
      if (kind === 'init') { postMessage({ id, result: await init(data.options) }); return; }
      if (kind === 'close') {
        closing = true;
        if (handle) await runtime.ccall('dtl_close', null, ['number'], [handle], { async: true });
        handle = 0;
        if (observer) runtime.removeFunction(observer);
        if (cancellation) runtime.removeFunction(cancellation);
        for (const path of files) runtime.FS.unlink(path);
        files = [];
        postMessage({ id, result: null }); self.close(); return;
      }
      postMessage({ id, result: await call(kind === 'info' ? 'dtl_info' : 'dtl_execute', request, data) });
    } catch (error) { postMessage({ id, error: error.message, status: error.status }); }
    finally { jobs.delete(id); }
  });
};
