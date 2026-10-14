// Prepare identical tokenized rows for a native ONNX CPU probe (no inference).
// node tests/export-probe-inputs.mjs UPSTREAM_DIST TOKENIZER OUR_NODE_RESULTS
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const [dist, tokenizer, samples] = process.argv.slice(2);
const { parseTokenizerJson, encodeWithData } = await import(pathToFileURL(path.resolve(dist, 'tokenizer.js')));
const { buildSequence, toInternal } = await import(pathToFileURL(path.resolve(dist, 'index.js')));
const data = parseTokenizerJson(JSON.parse(fs.readFileSync(tokenizer)));
if (!data) throw new Error('Unsupported tokenizer');
const tok = { clsId: data.ids.cls, sepId: data.ids.sep, maskId: data.ids.mask,
  padId: data.ids.pad, maskToken: data.maskToken, encode: text => encodeWithData(data, text) };
const questions = JSON.parse(fs.readFileSync(new URL('./fixtures/primitive.json', import.meta.url))).questions;
const rows = JSON.parse(fs.readFileSync(samples)).results.map(({ state }) => ({ state,
  questions: Object.entries(questions).map(([id, definition]) => {
    const q = toInternal(definition);
    const seq = buildSequence(tok, state, q);
    return { id, definition, ...seq, qtype: { choice: 0, score: 1, noul: 2 }[q.t] };
  }),
}));
console.log(JSON.stringify(rows));
