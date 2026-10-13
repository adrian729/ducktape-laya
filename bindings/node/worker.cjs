'use strict';
const { parentPort, workerData } = require('node:worker_threads');
let native, handle;
try {
  native = require(workerData.addonPath);
  handle = native.open(workerData.modelPath, workerData.threads, workerData.router);
} catch (error) {
  parentPort.postMessage({ startupError: error.message, status: error.status });
  parentPort.close();
}
if (handle) {
parentPort.postMessage({ ready: true });
parentPort.on('message', ({ id, kind, request, observe }) => {
  try {
    if (kind === 'close') {
      native.close(handle);
      parentPort.postMessage({ id });
      parentPort.close();
    } else {
      parentPort.postMessage({ id, result: native.decide(handle, request, id, observe ? event => parentPort.postMessage({ id, event: JSON.parse(event) }) : undefined) });
    }
  } catch (error) { parentPort.postMessage({ id, error: error.message, status: error.status }); }
});
}
