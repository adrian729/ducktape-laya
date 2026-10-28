const { createInterface }: typeof import('node:readline') = require('node:readline');
const { Engine }: typeof import('../bindings/node') = require('../bindings/node');

(async () => {
  const model = process.argv[2] || process.env.LAYA_MODEL;
  if (!model) throw Error('Use ./demo ts or supply a model path.');
  const form = createInterface({ input: process.stdin, output: process.stdout });
  const lines = form[Symbol.asyncIterator]();
  const ask = async (label: string, fallback: string) => {
    process.stdout.write(`${label} [${fallback}]: `);
    const line = await lines.next();
    if (line.done) throw Error('Input ended before the form was complete.');
    return line.value.trim() || fallback;
  };
  let request: import('../bindings/node').Request;
  try {
    console.log('Press Enter to use the example. Separate answer choices with |.');
    const state = await ask('Text to classify', 'I was charged twice. Please refund me.');
    const question = await ask('Question', 'Which department should handle this message?');
    const labels = (await ask('Possible answers', 'billing | technical | sales')).split('|').map(s => s.trim());
    if (labels.some(s => !s) || new Set(labels).size !== labels.length) throw Error('Answer choices must be nonempty and unique.');
    if (labels.length < 2 || labels.length > 8) throw Error('Enter 2 to 8 possible answers.');
    request = { state, questions: { decision: {
      type: 'choice', instructions: question, criteria: Object.fromEntries(labels.map(s => [s, s]))
    }}};
  } finally { form.close(); }
  console.log('Running Laya...');
  const engine = await Engine.open(model, { config: { threads: 2 } });
  try {
    const { answers: { decision } } = await engine.execute(request);
    if (decision.type !== 'choice') throw Error('Expected a choice response.');
    console.log(`\nBest answer: ${decision.choice}\nProbabilities:`);
    for (const [label, probability] of Object.entries(decision.probabilities))
      console.log(`  ${(probability * 100).toFixed(2)}%  ${label}`);
  } finally { await engine.close(); }
})().catch(error => { console.error(`Error: ${error.message}`); process.exitCode = 1; });
