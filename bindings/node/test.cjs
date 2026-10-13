const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { Laya } = require('./index.cjs');
test('missing model rejects asynchronously', async () => {
  await assert.rejects(Laya.open('/nonexistent/ducktape-laya.gguf'), error => error.status === 2);
});
test('invalid options reject', async () => {
  await assert.rejects(Laya.open('x', { threads: -1 }), /threads/);
  await assert.rejects(Laya.open('x', { maxPending: 0 }), /maxPending/);
});
test('inference, queue bound and drain on close', { skip: !process.env.LAYA_MODEL }, async () => {
  const engine = await Laya.open(process.env.LAYA_MODEL, { threads: 2, maxPending: 1 });
  const request = fs.readFileSync(path.join(__dirname, '../../examples/request.json'), 'utf8');
  try {
    await assert.rejects(engine.decide('{'), error => error.status === 1);
    const first = engine.decide(request);
    await assert.rejects(engine.decide(request), /queue is full/);
    const close = engine.close();
    await assert.rejects(engine.decide(request), /closing or closed/);
    const result = await first;
    assert.equal(result.answers.department.choice, 'billing');
    assert.ok(result.answers.refund_requested.noul > 0.5);
    assert.ok(result.answers.urgency.score >= 0 && result.answers.urgency.score <= 2);
    await close;
    await engine.close();
  } finally { await engine.close(); }
});
