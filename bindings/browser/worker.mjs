let runtime, handle = 0, routed = false, files = [], chain = Promise.resolve();
const CALL_OPTIONS_SIZE = 40;
let observer = 0, cancellation = 0, active, closing = false;
const jobs = new Map();
const fail = (message, status) => Object.assign(new Error(message), { status });
// A model asset failure (fetch: status 2, hash: 2, size: 1) is the same with any backend: the page does
// not retry it with another one.
const assetError = (message, status) => Object.assign(fail(message, status), { final: true });
async function asset(url, limit, expectedHash) {
  let response;
  try { response = await fetch(url); }
  catch (error) { throw assetError(`Model fetch failed: ${error?.message ?? error}`, 2); }
  if (!response.ok || !response.body) throw assetError(`Model fetch failed: HTTP ${response.status}`, 2);
  // A Content-Length within the budget sizes the buffer up front, so the model is held once rather than as
  // chunks plus their concatenation. One above the budget is rejected before any byte is read, but only
  // when the response has no Content-Encoding: with one, the length is the compressed size, which is not a
  // lower bound of the decoded size (incompressible data grows by about 0.03%), so only the streaming
  // check below enforces the budget. The body may also differ from the declared length; then the buffer
  // grows or is trimmed.
  const declared = Number(response.headers.get('content-length') ?? NaN);
  const hasDeclared = Number.isSafeInteger(declared) && declared > 0;
  const encoded = !!response.headers.get('content-encoding') && response.headers.get('content-encoding').toLowerCase() !== 'identity';
  if (hasDeclared && !encoded && declared > limit) {
    await response.body.cancel().catch(() => {});
    throw assetError('Model assets exceed maxModelBytes', 1);
  }
  // A buffer that cannot be allocated is out of memory (status 4), not a fetch failure: never retried with another backend.
  const allocate = length => {
    try { return new Uint8Array(length); }
    catch (error) {
      if (error instanceof RangeError) throw assetError('Out of memory for the model assets', 4);
      throw error;
    }
  };
  let bytes, size = 0;
  const reader = response.body.getReader();
  try {
    bytes = allocate(hasDeclared ? Math.min(declared, limit) : 0);
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (size + value.byteLength > limit) throw assetError('Model assets exceed maxModelBytes', 1);
      if (size + value.byteLength > bytes.length) {
        const grown = allocate(Math.min(limit, Math.max(size + value.byteLength, bytes.length * 2, 1 << 20)));
        grown.set(bytes.subarray(0, size));
        bytes = grown;
      }
      bytes.set(value, size);
      size += value.byteLength;
    }
  } catch (error) {
    await reader.cancel().catch(() => {});
    throw error?.final ? error : assetError(`Model fetch failed: ${error?.message ?? error}`, 2);
  }
  // A copy of exactly the bytes read when the buffer is larger, so the oversized one can be released and
  // FS.writeFile(..., { canOwn: true }) keeps no more than the model.
  if (bytes.length !== size) bytes = bytes.slice(0, size);
  if (expectedHash) {
    const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), v => v.toString(16).padStart(2, '0')).join('');
    if (hash !== expectedHash.toLowerCase()) throw assetError('Model SHA-256 mismatch', 2);
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
      const remaining = job.deadline ? Math.ceil(job.deadline - Date.now()) : 0;
      // Already expired in the queue: fail at once, with no native call and no events.
      if (job.deadline && remaining <= 0) throw fail('Request deadline exceeded', 6);
      const requestId = job.requestId ?? 0;
      runtime.HEAPU32.set([CALL_OPTIONS_SIZE, 0, requestId >>> 0, Math.floor(requestId / 4294967296), remaining >>> 0, Math.floor(remaining / 4294967296),
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
// The browser could not reserve the WebAssembly memory (status 4, OUT_OF_MEMORY); any other failure to load
// or instantiate the runtime is status 7. Browsers report the former as a RangeError such as "could not
// allocate memory" (Chromium) or "out of memory" (Firefox, Safari), possibly inside Emscripten's abort text.
function instantiationError(error) {
  const message = String(error?.message ?? error);
  if (/out of memory|(could not|cannot|failed to) allocate/i.test(message)) return fail(`Not enough memory for the Laya WASM runtime: ${message}`, 4);
  return fail(`Cannot load the Laya WASM runtime: ${message}`, 7);
}
async function init(options) {
  if (options.backend === 'webgpu') {
    if (!navigator.gpu || !await navigator.gpu.requestAdapter()) throw fail('WebGPU adapter unavailable', 7);
    // Monitor the runtime's device in this private worker realm.
    const requestDevice = GPUAdapter.prototype.requestDevice;
    GPUAdapter.prototype.requestDevice = async function(...args) {
      const device = await requestDevice.apply(this, args);
      device.lost.then(info => { if (!closing) postMessage({ fatal: `WebGPU device lost (${info.reason})` }); });
      return device;
    };
  }
  // An abort while the runtime instantiates rejects create(); only a later abort ends a running engine.
  let instantiating = true;
  try {
  const create = (await import(options.moduleUrl)).default;
  runtime = await create({ onAbort: () => { if (!closing && !instantiating) postMessage({ fatal: 'Laya WASM runtime aborted' }); }, locateFile: name => name.endsWith('.wasm') ? options.wasmUrl : new URL(name, options.moduleUrl).href,
    print: options.diagnostics ? text => postMessage({ diagnostic: String(text) }) : () => {}, printErr: options.diagnostics ? text => postMessage({ diagnostic: String(text) }) : () => {} });
  } catch (error) { throw instantiationError(error); }
  finally { instantiating = false; }
  if (runtime._dtl_abi_version() !== 2) throw fail('Incompatible Laya WASM ABI', 7);
  const diagnostics = JSON.parse(await call('dtl_info', null, {}));
  const expectedBackend = options.backend === 'webgpu' ? 'webgpu' : 'wasm-cpu';
  if (diagnostics.backend !== expectedBackend) throw fail(`Laya backend mismatch: expected ${expectedBackend}, got ${diagnostics.backend}`, 7);
  {
    // Verify the wasm32 struct layout the worker writes against the runtime's own defaults.
    const probe = runtime._malloc(CALL_OPTIONS_SIZE);
    if (!probe) throw fail('Out of WASM memory', 4);
    try {
      runtime._dtl_default_call_options(probe);
      if (runtime.HEAPU32[probe >>> 2] !== CALL_OPTIONS_SIZE) throw fail('Incompatible Laya WASM ABI', 7);
    } finally { runtime._free(probe); }
  }
  observer = runtime.addFunction((text, user) => {
    // Calls serialize, so the active job owns every event. Route by job id; the reported request id is the caller's.
    try {
      const event = JSON.parse(runtime.UTF8ToString(text));
      if (active) postMessage({ id: active.id, event: { kind: event.event, requestId: event.request_id, status: event.status, elapsedMs: event.elapsed_ms } });
    } catch {}
  }, 'vii');
  cancellation = runtime.addFunction(() => active ? ((active.flag ? Atomics.load(active.flag, 0) : 0) || active.status || 0) : 0, 'ii');
  routed = options.routed;
  const assets = options.assets;
  // Single-threaded worker; no COOP/COEP requirement. The page has already refused a threads value other than 1.
  const config = JSON.stringify({ ...options.config, threads: 1 });
  // The router JSON for the given model paths: every route keeps its own config, passed through.
  const routerJson = pathOf => {
    const routes = Object.create(null);
    for (const [id, descriptor] of Object.entries(assets)) routes[id] = descriptor.config ? { path: pathOf(descriptor), config: descriptor.config } : pathOf(descriptor);
    return JSON.stringify({ models: routes, ...(options.maxLoaded !== undefined ? { max_loaded: options.maxLoaded } : {}),
      ...(options.auto !== undefined ? { auto: options.auto } : {}) });
  };
  // Calls dtl_open or dtl_open_router. `keep` stores the engine as the handle; otherwise it is closed again.
  const open = async (kind, input, keep) => {
    const out = runtime._malloc(4), error = runtime._malloc(4);
    if (!out || !error) { if (out) runtime._free(out); if (error) runtime._free(error); throw fail('Out of WASM memory', 4); }
    try {
      runtime.HEAPU32[out >>> 2] = runtime.HEAPU32[error >>> 2] = 0;
      const status = await runtime.ccall(kind, 'number', ['string','string','number','number'], [input, config, out, error], { async: true });
      const opened = runtime.HEAPU32[out >>> 2];
      const message = runtime.HEAPU32[error >>> 2];
      try {
        if (status) {
          // An invalid config or route (status 1) is invalid on every backend: backend 'auto' does not retry it.
          throw (status === 1 ? assetError : fail)(message ? runtime.UTF8ToString(message) : 'Model loading failed', status);
        }
        if (keep) handle = opened;
        else if (opened) await runtime.ccall('dtl_close', null, ['number'], [opened], { async: true });
      } finally { runtime._dtl_free(message); }
    } finally { runtime._free(out); runtime._free(error); }
  };
  // The core validates every config and route key before it loads a model, so a bad config fails here, before any
  // download: open the same router over 0-byte placeholder files (routes load lazily), then close it.
  {
    const placeholders = new Map(), written = [];
    try {
      const placeholder = descriptor => {
        const identity = JSON.stringify([descriptor.url, descriptor.sha256 || null]);
        if (!placeholders.has(identity)) {
          const path = `/validate-${placeholders.size}.gguf`;
          runtime.FS.writeFile(path, new Uint8Array(0)); written.push(path); placeholders.set(identity, path);
        }
        return placeholders.get(identity);
      };
      await open('dtl_open_router', routerJson(placeholder), false);
    } finally { for (const path of written) runtime.FS.unlink(path); }
  }
  let remaining = options.maxModelBytes;
  const paths = new Map();
  for (const descriptor of Object.values(assets)) {
    const identity = JSON.stringify([descriptor.url, descriptor.sha256 || null]);
    if (paths.has(identity)) continue;
    const bytes = await asset(descriptor.url, remaining, descriptor.sha256);
    remaining -= bytes.length;
    const path = `/model-${files.length}.gguf`;
    runtime.FS.writeFile(path, bytes, { canOwn: true });
    files.push(path); paths.set(identity, path);
  }
  const pathOf = descriptor => paths.get(JSON.stringify([descriptor.url, descriptor.sha256 || null]));
  await open(routed ? 'dtl_open_router' : 'dtl_open', routed ? routerJson(pathOf) : pathOf(assets.model), true);
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
    } catch (error) {
      // Every error carries a status: unexpected ones fail model loading (2) during init, otherwise inference (3).
      postMessage({ id, error: error?.message ?? String(error), status: error?.status ?? (kind === 'init' ? 2 : 3), final: error?.final === true });
    }
    finally { jobs.delete(id); }
  });
};
