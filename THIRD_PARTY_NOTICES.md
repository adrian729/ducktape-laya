# Third-party components

- llama.cpp / ggml: MIT, copyright 2023–2026 The ggml authors.
  Pinned source: `third_party/llama.cpp`, commit
  `1e6f04a75e91da4bb66b5248f3fd4bdeff169d41` (b11540).
  [License](licenses/llama.cpp-MIT.txt). `src/backend_llama.cpp` adapts Laya's prompt
  preparation, temperature lookup and answer formatting from
  `tools/server/server-decision.cpp`. Jinja, Unicode and JSON support are compiled
  directly from the pinned source. Additional copyright notices remain in the
  vendored files; preserve them when redistributing source. Builds compile modified
  copies of some upstream files, including the vendored nlohmann/json header, using
  the recorded patches in `patches/llama.cpp` (see `patches/README.md`).
- Laya (original implementation, https://github.com/NandhaKishorM/laya): Apache-2.0,
  copyright the Laya authors. [License](licenses/Apache-2.0.txt). The word lists and
  thresholds in `src/language.cpp` are taken from its `laya/lang.py`; the test cases in
  `tests/original-reference` are its recorded results.
- llamafile CPU kernels incorporated by ggml: MIT, Copyright 2024 Mozilla
  Foundation. [Full notice](licenses/llamafile-MIT.txt).
- nlohmann JSON: MIT, Niels Lohmann. Included through llama.cpp's vendor directory.
  [License](licenses/nlohmann-json-MIT.txt).
- Laya models: the upstream model card declares Apache-2.0 for all three checkpoints in [convaiinnovations/laya](https://huggingface.co/convaiinnovations/laya) ([license text](licenses/Apache-2.0.txt)).
  - English: fine-tuned from [ModernBERT-large](https://huggingface.co/answerdotai/ModernBERT-large) (Apache-2.0). Used through the [ggml-org GGUF conversion](https://huggingface.co/ggml-org/Laya-GGUF), pinned by `models/manifest.json`.
  - Multilingual: fine-tuned from [jhu-clsp/mmBERT-base](https://huggingface.co/jhu-clsp/mmBERT-base) (MIT). Pinned by `models/laya-multilingual.json`.
  - Typed decisions: fine-tuned from ModernBERT-large (Apache-2.0). Pinned by `models/laya-typed-decisions.json`.

  No official GGUF exists for the multilingual and typed-decisions checkpoints; users build them with `scripts/convert-model.py` from the source revision and file hashes in those manifests. Neither the Laya models, the GGUF conversion nor ModernBERT-large publishes a NOTICE file. Weights are not committed or downloaded by the runtime. An app distributing a model must include the Apache-2.0 text and this attribution with it, and for the multilingual model also credit mmBERT-base, whose model card declares MIT (its repository publishes no license file or copyright line).

Ducktape Laya is an independent integration, not an upstream Laya release.

Browser artifacts additionally incorporate code from the pinned Emscripten SDK
(`release/browser-toolchain.json`): Emscripten runtime glue (MIT or NCSA), musl
(MIT and the notices in its COPYRIGHT), libc++/libc++abi/compiler-rt (LLVM license
texts including applicable exceptions), and Dawn WebGPU bindings (BSD notices).
Their complete texts are included under `licenses/`. The browser's GPU driver
and browser implementation are supplied by the host, not redistributed here.
Test-only Electron, ASAR and upstream server-reference dependencies are excluded
from consumer packages. Build/toolchain licenses do not replace the separate
license obligations of an application's bundled model assets.
