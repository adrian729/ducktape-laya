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
  const circular = {}; circular.self = circular;
  await assert.rejects(Laya.openRouter(circular), /circular/i);
  await assert.rejects(Laya.openRouter({ models: {} }), e => e.status === 1);
});
test('serialization reentry cannot bypass queue or close boundaries', async () => {
  // Lazy routing permits validation tests without loading a model.
  const config = { models: { unused: __filename } };
  const engine = await Laya.openRouter(config, { maxPending: 1 });
  try {
    let nested;
    await assert.rejects(engine.execute({ toJSON() {
      nested = assert.rejects(engine.execute('{}'), e => e.status === 1);
      return {};
    }}), /queue is full/);
    await nested;
  } finally { await engine.close(); }
  const closing = await Laya.openRouter(config);
  try {
    await assert.rejects(closing.execute({ toJSON() {
      closing.close();
      return {};
    }}), /closing or closed/);
  } finally { await closing.close(); }
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

test('batch, structured projection, hooks and worker responsiveness', { skip: !process.env.LAYA_MODEL }, async () => {
  const engine = await Laya.open(process.env.LAYA_MODEL, { threads: 2 });
  const request = JSON.parse(fs.readFileSync(path.join(__dirname, '../../examples/request.json'), 'utf8'));
  const structured = { state: request.state, schema: { type: 'object', properties: {
    refund: { type: 'boolean', description: 'Does the customer request a refund?' },
    priority: { type: 'integer', minimum: 1, maximum: 3, description: 'How urgent is this?' },
    team: { type: 'string', enum: ['billing', 'technical'], description: 'Which team should handle this?' }
  }, required: ['refund'], additionalProperties: false } };
  try {
    const single = await engine.decide(request);
    const legacyEvents = [];
    const tagged = { ...request, metadata: 'caller tag' };
    assert.deepEqual(await engine.decide(tagged, { onEvent: e => { legacyEvents.push(e.event); } }), single);
    assert.deepEqual(legacyEvents, ['start', 'end']);
    await assert.rejects(engine.execute(tagged), e => e.status === 1);
    const projected = await engine.decideStructured(structured);
    assert.equal(projected.values.refund, projected.answers.refund.noul >= 0.5);
    assert.equal(projected.values.team, projected.answers.team.choice);
    assert.ok(Number.isInteger(projected.values.priority) && projected.values.priority >= 1 && projected.values.priority <= 3);
    const events = [];
    let ticks = 0;
    const timer = setInterval(() => { ticks++; }, 10);
    let batch;
    try { batch = await engine.decideBatch([request, structured, request], { onEvent: event => { events.push(event); } }); }
    finally { clearInterval(timer); }
    assert.deepEqual(batch, [single, projected, single]);
    assert.ok(ticks > 5, 'inference must leave the host event loop running');
    assert.deepEqual(events.map(e => e.event), ['start', 'end']);
    assert.equal(events[0].request_id, events[1].request_id);
    assert.ok(events[1].elapsed_ms > 0);
    const errors = [];
    await assert.rejects(engine.execute('{}', { onEvent: e => { errors.push(e); throw Error('telemetry'); } }), e => e.status === 1);
    assert.deepEqual(errors.map(e => e.event), ['start', 'error']);
    await assert.rejects(engine.decideBatch([]), e => e.status === 1);
    await assert.rejects(engine.decideBatch(Array(33).fill(request)), e => e.status === 1);
    await assert.rejects(engine.decide(request, { onEvent: 3 }), /onEvent/);
    // Rejected observer promises are contained. Reentry is queued behind this call.
    let nested;
    const observed = await engine.decide(request, { onEvent: async e => {
      if (e.event === 'start') nested = engine.decide(request);
      throw Error('ignored asynchronous telemetry failure');
    }});
    assert.deepEqual(await nested, observed);
  } finally { await engine.close(); }
});

test('router shares native model selection and drains accepted batch on close', { skip: !process.env.LAYA_MODEL }, async () => {
  const router = await Laya.openRouter({ models: { english: process.env.LAYA_MODEL, alias: process.env.LAYA_MODEL }, max_loaded: 1 }, { threads: 2 });
  const request = { state: 'Please refund me.', questions: { q: { type: 'noul', instructions: 'Is a refund requested?' } } };
  try {
    await assert.rejects(router.decide(request), e => e.status === 1);
    await assert.rejects(router.decide({ ...request, model: 'missing' }), e => e.status === 1);
    const pending = router.decideBatch([{ ...request, model: 'english' }, { ...request, model: 'alias' }]);
    const close = router.close();
    const [a, b] = await pending;
    assert.equal(a.model, 'english'); assert.equal(b.model, 'alias');
    assert.deepEqual(a.answers, b.answers);
    await close;
  } finally { await router.close(); }
});
