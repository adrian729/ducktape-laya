# Models

ducktape-laya runs the three checkpoints the original [Laya](https://github.com/NandhaKishorM/laya) publishes, as GGUF files (the llama.cpp model format). The library never downloads a model: your application supplies the file path (or, in the browser, its URL). Each supported file is pinned by a manifest in `models/` with its exact size and SHA-256, and qualified against the original Laya's recorded results and against upstream llama.cpp.

| Model | Manifest | File | Size | Base model, languages | Trained prompt length |
| --- | --- | --- | --- | --- | --- |
| English | [models/manifest.json](../models/manifest.json) | `Laya-Q8_0.gguf` (Q8_0) | 449,397,600 B | ModernBERT-large; English | 512 tokens |
| Multilingual | [models/laya-multilingual.json](../models/laya-multilingual.json) | `laya-multilingual-f16.gguf` (F16) | 658,414,400 B | mmBERT-base; 100+ languages | 1,024 tokens |
| Typed decisions | [models/laya-typed-decisions.json](../models/laya-typed-decisions.json) | `laya-typed-decisions-q8_0.gguf` (Q8_0) | 449,397,664 B | ModernBERT-large; English | 1,024 tokens |

Q8_0 and F16 are the weight precisions: 8-bit quantized and 16-bit floating point. The multilingual model ships as F16 because a Q8_0 conversion moved probabilities up to 0.056 from the original's, over the 0.05 limit the qualification allows.

## Choosing a model

- **English** is the default and the only one with an official GGUF download.
- **Multilingual** reads any of its 100+ languages, English included, and is faster per question than the English model but uses about twice the memory ([performance](PERFORMANCE.md)). To send English text to the English model and everything else to the multilingual one automatically, open both in a router with `"auto"` routes ([API guide](API.md#choosing-the-model-by-language)).
- **Typed decisions** is the original Laya's checkpoint for its typed decision workflows. Use it by opening its file directly or as a named route; the library does not detect those workflows from question names ([roadmap](ROADMAP.md#not-planned)).

## Downloading the English model

```sh
curl -fL -o Laya-Q8_0.gguf https://huggingface.co/ggml-org/Laya-GGUF/resolve/22265007700297ba9e128297e82540cf28c5d7d4/Laya-Q8_0.gguf
echo 'c06528c5746d3bb8baa72a27938be95abbfd0b226f8471e8a9e365ed0bb066d2  Laya-Q8_0.gguf' | sha256sum --check
```

Alternatively, `download-model.py DIRECTORY` downloads the file into `DIRECTORY` and verifies it before putting it in place. It needs only Python 3 and ships in the npm package (`node_modules/ducktape-laya/download-model.py`), in the native archive (`share/ducktape-laya/download-model.py`) and in a checkout (`scripts/download-model.py`).

## Building the multilingual and typed-decisions models

No official GGUF exists for these two checkpoints yet. `scripts/convert-model.py` rebuilds the exact pinned file from a checkout: it downloads the official checkpoint at the revision the manifest pins, checks every source file's hash, converts it with the pinned llama.cpp converter, quantizes when the manifest asks for it, and checks the result's size and SHA-256 against the manifest. It needs Python 3.11+ and a clone with submodules ([building](BUILDING.md#get-the-source)); each conversion takes a few minutes.

```sh
python3 -m venv .venv-convert
.venv-convert/bin/pip install -r third_party/llama.cpp/requirements/requirements-convert_hf_to_gguf.txt
python3 scripts/convert-model.py models/laya-multilingual.json resources/laya-multilingual-f16.gguf \
  --python .venv-convert/bin/python

# The typed-decisions model is quantized, so it also needs llama-quantize from the pinned llama.cpp:
cmake -S third_party/llama.cpp -B build-llama-tools -DGGML_OPENMP=OFF -DBUILD_SHARED_LIBS=OFF
cmake --build build-llama-tools --target llama-quantize -j 3
python3 scripts/convert-model.py models/laya-typed-decisions.json resources/laya-typed-decisions-q8_0.gguf \
  --python .venv-convert/bin/python --quantize build-llama-tools/bin/llama-quantize
```

The pinned converter's dependencies may not install on the newest Python release; if `pip install` fails, create the virtual environment with an older Python (3.12 is known to work). `.venv-convert` and `build-*` directories are ignored by Git.

## Prompt length

Each model was trained with prompts up to its trained length (table above). Open the multilingual and typed-decisions models with `context_tokens` 1024 to match the original Laya; the default is 512. The files accept up to 8,192 tokens, but longer prompts are not checked for accuracy, compute time grows faster than the prompt length, and the memory reserved grows with `context_tokens`. Text longer than the limit is rejected by default; it can instead be truncated or read in windows ([long input](API.md#long-input)).

## Verifying a model you ship

`scripts/verify-model.py MODEL [--manifest MANIFEST]` checks a file against a manifest (from a checkout, Python 3.11+). At open, the library checks that a file is a compatible Laya GGUF (metadata, output layout, template, calibration), which proves format compatibility, not the file's identity or quality: check the hash when you bundle a model, and treat model files as trusted code ([SECURITY.md](../SECURITY.md)).

## Other models

Only the three pinned files are qualified. Another Laya-format GGUF may open, but it needs its own tokenizer, calibration and decision checks before you rely on it. A higher-precision English variant (BF16) and larger checkpoints are on the [roadmap](ROADMAP.md#higher-precision-model-variants).

## Other Laya runtimes

Other projects run Laya models too; they are independent of ducktape-laya and not tested here.

- [Official Laya / laya-ts](https://github.com/NandhaKishorM/laya/tree/main/laya-ts): richer TypeScript workflows and an ONNX browser path.
- [LibLayaX](https://github.com/DaragonTech/LibLayaX), [RLaya](https://github.com/DaragonTech/RLaya), [laya.cpp](https://github.com/lkarlslund/laya.cpp): native C, Rust and C++ runtimes.
- [laya-rust (Candle)](https://github.com/aovestdipaperino/laya-rust), [laya-rust (GGUF)](https://github.com/zerodegress/laya-rust), [receptron/laya](https://github.com/receptron/laya): Rust inference and another Node/ONNX integration.

## Licences

The upstream model card declares Apache-2.0 for all three checkpoints; the multilingual model is fine-tuned from mmBERT-base, which its model card declares MIT. An application that distributes a model must include the Apache-2.0 text and the attribution in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
