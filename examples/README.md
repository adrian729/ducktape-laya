# Examples

Ready-made requests and small programs. The request files are plain JSON in the format of the [API guide](../docs/API.md); every binding accepts them unchanged. A model-free test (`tests/examples-smoke.py`) checks that each request file is valid and that the C and C++ programs compile.

## Requests

| File | What it shows |
| --- | --- |
| [request.json](request.json) | One of each question type about a support message: `choice` (department), `noul` (refund requested) and `score` (urgency) |
| [structured.json](structured.json) | A [structured decision](../docs/API.md#structured-decisions): a small JSON Schema whose properties are filled from the answers |
| [batch.json](batch.json) | A [batch](../docs/API.md#batches) of two requests in one call |
| [presets/support-triage.json](presets/support-triage.json) | Customer support: intent, urgency, frustration and churn risk |
| [presets/email-triage.json](presets/email-triage.json) | Email: category, whether it needs a reply, phishing and urgency |
| [presets/prompt-guard.json](presets/prompt-guard.json) | Text sent to an LLM: prompt injection, data exfiltration and overall risk |
| [presets/moderation.json](presets/moderation.json) | User content: harassment, threats and severity |

The presets are starting points, not tuned policies, and none has been measured on real data: replace the `state` with your text, adapt the questions and options, and choose your own thresholds on `answer_confidence` with labelled examples ([using Laya](../docs/USAGE.md)). The prompt-guard preset is a rough filter, not a safety control: the models react to which words a text contains, so rephrasing evades it.

## Running a request

Every binding's `execute` takes the file's text as the request, for example in Node.js `await engine.execute(fs.readFileSync('examples/request.json', 'utf8'))` or in Python `engine.execute(pathlib.Path('examples/request.json').read_text())`.

These programs read one request from standard input and print the result; they run from a checkout ([building](../docs/BUILDING.md)):

- C and C++: [decide.c](decide.c) and [decide.cpp](decide.cpp), built as `laya-decide` and `laya-decide-cpp` with `-DDUCKTAPE_BUILD_EXAMPLES=ON`: `build/laya-decide /path/to/Laya-Q8_0.gguf < examples/request.json`.
- Rust: `cargo run --example decide -- /path/to/Laya-Q8_0.gguf < ../../../examples/request.json` in `bindings/rust/ducktape-laya`, with `DUCKTAPE_LAYA_LIB_DIR` set.
- Node.js: `node bindings/node/decide.cjs /path/to/Laya-Q8_0.gguf < examples/request.json`, with `DUCKTAPE_LAYA_LIBRARY` naming the built addon (an absolute path).

## Interactive demos

`./demo [c|cpp|rust|js|ts] [model.gguf]` in the repository root builds and runs a small form ([demo.c](demo.c), [demo.cpp](demo.cpp), [demo.cjs](demo.cjs), [demo.cts](demo.cts) and the Rust `demo.rs`) that asks for a text, a question and its possible answers. The demos accept 2 to 8 answers; the library accepts 1 to 255 options. They are developer tools, not part of any package.

To reduce the influence of option order on a `choice`, see the rotation recipe in the [API guide](../docs/API.md#option-order).
