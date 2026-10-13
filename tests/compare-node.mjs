// Developer comparison harness; upstream is a separate checkout, not a dependency.
// node tests/compare-node.mjs ours|laya-ts MODEL [UPSTREAM_DIST_INDEX]
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { performance } from 'node:perf_hooks';
const [kind, model, upstream] = process.argv.slice(2);
if (!['ours', 'laya-ts'].includes(kind) || !model || (kind === 'laya-ts' && !upstream)) {
  throw new Error('Usage: compare-node.mjs ours|laya-ts MODEL [UPSTREAM_DIST_INDEX]');
}
const fixture = JSON.parse(fs.readFileSync(new URL('../examples/request.json', import.meta.url)));
const states = [fixture.state,
  'The app crashes every time I open settings. Can you help?',
  'How much does your enterprise plan cost for twenty users?',
  'URGENT: production is down for every customer. Please fix it immediately.',
  'Thanks for the help. Everything is working now.',
  'I do not want a refund. Please explain the duplicate charge.',
  'Please cancel my subscription and return the last payment.',
  { subject: 'Invoice', body: 'Please send a receipt for my last payment.' },
];
const start = performance.now();
let engine, predict, close;
if (kind === 'ours') {
  const { Laya } = await import('../bindings/node/index.cjs');
  engine = await Laya.open(model, { threads: 2 });
  predict = state => engine.decide({ ...fixture, state });
  close = () => engine.close();
} else {
  const { Agent } = await import(pathToFileURL(path.resolve(upstream)));
  engine = await Agent.load(path.resolve(model), { numThreads: 2 });
  predict = state => engine.predict(state, fixture.questions);
  if (process.env.LAYA_SERIAL_QUESTIONS === '1') {
    predict = async state => {
      const answers = {};
      let input_tokens = 0;
      for (const [id, question] of Object.entries(fixture.questions)) {
        const result = await engine.predict(state, { [id]: question });
        Object.assign(answers, result.answers);
        input_tokens += result.usage.input_tokens;
      }
      return { answers, usage: { input_tokens, output_tokens: 0 } };
    };
  }
  close = async () => {}; // Upstream has no public close/release method.
}
const loadMs = performance.now() - start;
const results = [];
let maxTimerDelayMs = 0;
let tick = performance.now();
const timer = setInterval(() => {
  const now = performance.now();
  maxTimerDelayMs = Math.max(maxTimerDelayMs, now - tick - 10);
  tick = now;
}, 10);
try {
  await predict(states[0]); // untimed warmup
  for (const state of states) {
    const t = performance.now();
    const result = await predict(state);
    results.push({ state, ms: performance.now() - t, result });
  }
  let batch = null;
  if (kind === 'laya-ts') {
    const t = performance.now();
    try {
      const outputs = await engine.predictBatch(states.slice(0, 2), fixture.questions, { batchSize: 2 });
      batch = { ms: performance.now() - t, outputs };
    } catch (error) { batch = { error: String(error) }; }
  }
  await new Promise(resolve => setTimeout(resolve, 20));
  const linuxHighWaterKiB = process.platform === 'linux'
    ? Number(fs.readFileSync('/proc/self/status', 'utf8').match(/^VmHWM:\s+(\d+)/m)?.[1]) : null;
  console.log(JSON.stringify({ kind, model, node: process.version, requestedThreads: 2,
    serialQuestions: process.env.LAYA_SERIAL_QUESTIONS === '1',
    loadMs, linuxHighWaterKiB, rssBytes: process.memoryUsage().rss,
    maxTimerDelayMs, results, batch }, null, 2));
} finally { clearInterval(timer); await close(); }
