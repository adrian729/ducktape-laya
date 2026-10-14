onmessage = async ({ data: { backend, requests } }) => {
  try {
    const base = backend === 'webgpu' ? '/webgpu/' : '/wasm/';
    const create = (await import(base + 'laya-reference.mjs')).default;
    const runtime = await create({ locateFile: name => base + name, print: () => {}, printErr: () => {} });
    const model = new Uint8Array(await (await fetch('/model.gguf')).arrayBuffer());
    runtime.FS.writeFile('/model.gguf', model, { canOwn: true });
    const error = await runtime.ccall('reference_open', 'string', ['string'], ['/model.gguf'], { async: true });
    if (error) throw new Error(error);
    const results = {};
    for (const [name, request] of Object.entries(requests)) {
      const result = JSON.parse(await runtime.ccall('reference_execute', 'string', ['string'], [JSON.stringify(request)], { async: true }));
      if (result.error) throw new Error(result.error);
      results[name] = result;
      postMessage({ progress: name });
    }
    await runtime.ccall('reference_close', null, [], [], { async: true });
    postMessage({ status: 'passed', backend, results });
  } catch (error) { postMessage({ status: 'failed', error: error.stack }); }
  finally { self.close(); }
};
