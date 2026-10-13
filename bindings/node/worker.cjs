'use strict';
const { parentPort, workerData } = require('node:worker_threads');
let native, handle;
try {
  native = require(workerData.addonPath);
  handle = native.open(workerData.modelPath, workerData.threads);
} catch (error) {
  parentPort.postMessage({ startupError: error.message, status: error.status });
  parentPort.close();
}
if (handle) {
parentPort.postMessage({ ready: true });
parentPort.on('message', ({ id, kind, request }) => {
  try {
    if (kind === 'close') {
      native.close(handle);
      parentPort.postMessage({ id });
      parentPort.close();
    } else {
      parentPort.postMessage({ id, result: native.decide(handle, request) });
    }
  } catch (error) { parentPort.postMessage({ id, error: error.message, status: error.status }); }
});
}
