'use strict';
const { parentPort, workerData } = require('node:worker_threads');
let native, handle;
try {
  native = require(workerData.addonPath);
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
        if (remaining <= 0n) Atomics.compareExchange(cancellation, 0, 0, 6);
        else timeoutMs = Number((remaining + 999999n) / 1000000n);
      }
      parentPort.postMessage({ id, result: native.execute(handle, request, requestId, observe ? event => parentPort.postMessage({ id, event: JSON.parse(event) }) : undefined, cancellation, timeoutMs) });
    }
  } catch (error) { parentPort.postMessage({ id, error: error.message, status: error.status }); }
});
}
