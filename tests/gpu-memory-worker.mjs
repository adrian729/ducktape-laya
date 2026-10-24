// Test-only WebGPU allocation-failure injection, outside the shipped worker and package.
// A message { kind: 'cancel', id: -1, testFailBuffer: { label, skip } } arms one failure:
// after skipping `skip` buffers whose label starts with `label`, the next one is created
// with a size above the device's maxBufferSize. The browser then returns an invalid
// buffer and raises a validation error, as it returns an invalid buffer with an
// out-of-memory error when a buffer does not fit; the runtime handles both alike.
import '/browser/worker.mjs';
let armed = null;
const createBuffer = GPUDevice.prototype.createBuffer;
GPUDevice.prototype.createBuffer = function(descriptor) {
  if (armed && String(descriptor.label ?? '').startsWith(armed.label) && armed.skip-- === 0) {
    armed = null;
    postMessage({ diagnostic: `test: failed buffer ${descriptor.label} (${descriptor.size} bytes)` });
    return createBuffer.call(this, { ...descriptor, size: this.limits.maxBufferSize + 4 });
  }
  return createBuffer.call(this, descriptor);
};
self.addEventListener('message', event => {
  if (event.data.testFailBuffer) { event.stopImmediatePropagation(); armed = { ...event.data.testFailBuffer }; }
});
