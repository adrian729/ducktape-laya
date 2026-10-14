// Test-only fault injection, outside the shipped worker and package.
import '/browser/worker.mjs';
let device;
const original = GPUAdapter.prototype.requestDevice;
GPUAdapter.prototype.requestDevice = async function(...args) { device = await original.apply(this, args); return device; };
self.addEventListener('message', event => {
  if (event.data.testDestroy) { event.stopImmediatePropagation(); device.destroy(); }
});

