'use strict';
const { parentPort, workerData } = require('node:worker_threads');
const { loadMessage } = require('./resolve-addon.cjs');
let native, handle;
// The core's event JSON as the public LayaEvent; one that does not parse is dropped.
const layaEvent = text => {
  try { const event = JSON.parse(text); return { kind: event.event, requestId: event.request_id, status: event.status, elapsedMs: event.elapsed_ms }; }
  catch { return undefined; }
};
try {
  try { native = require(workerData.addonPath); }
  catch (error) { throw Object.assign(new Error(loadMessage(error)), { status: 7 }); }
  handle = native.open(workerData.modelPath, workerData.config, workerData.router);
} catch (error) {
  parentPort.postMessage({ startupError: error.message, status: error.status });
  parentPort.close();
}
if (handle) {
parentPort.postMessage({ ready: true });
parentPort.on('message', ({ id, requestId, kind, request, observe, cancellation, deadline }) => {
  try {
    if (kind === 'info') { parentPort.postMessage({ id, result: native.info(handle) }); }
    else if (kind === 'close') {
      native.close(handle);
      parentPort.postMessage({ id });
      parentPort.close();
    } else {
      let timeoutMs = 0;
      if (deadline) {
        const remaining = deadline - process.hrtime.bigint();
        // Already expired in the queue: fail at once, with no native call and no events.
        if (remaining <= 0n) throw Object.assign(new Error('Request deadline exceeded'), { status: 6 });
        timeoutMs = Number((remaining + 999999n) / 1000000n);
      }
      parentPort.postMessage({ id, result: native.execute(handle, request, requestId, observe ? text => { const event = layaEvent(text); if (event) parentPort.postMessage({ id, event }); } : undefined, cancellation, timeoutMs) });
    }
  } catch (error) { parentPort.postMessage({ id, error: error.message, status: error.status }); }
});
}
