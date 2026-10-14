# Third-party components

- llama.cpp / ggml: MIT, copyright 2023–2026 The ggml authors.
  Pinned source: `third_party/llama.cpp`, commit
  `1e6f04a75e91da4bb66b5248f3fd4bdeff169d41` (b11540).
  [License](licenses/llama.cpp-MIT.txt). `src/backend_llama.cpp` adapts Laya's prompt
  preparation, temperature lookup and answer formatting from
  `tools/server/server-decision.cpp`. Jinja, Unicode and JSON support are compiled
  directly from the pinned source. Additional copyright notices remain in the
  vendored files; preserve them when redistributing source.
- nlohmann JSON: MIT, Niels Lohmann. Included through llama.cpp's vendor directory.
  [License](licenses/nlohmann-json-MIT.txt).
- Laya model: the upstream model card declares Apache-2.0.
  [Model source](https://huggingface.co/convaiinnovations/laya),
  [GGUF conversion](https://huggingface.co/ggml-org/Laya-GGUF),
  [license text](licenses/Apache-2.0.txt). Weights are not committed or downloaded
  by the runtime. `models/manifest.json` pins the tested conversion. Model
  attribution and any applicable upstream NOTICE must accompany a distributed
  app's model assets; finish this artifact audit before publishing installers.

Ducktape Laya is an independent integration, not an upstream Laya release.
