# Changelog

## 0.2.0 (unreleased)

C ABI 2 (`libducktape_laya.so.1`). Breaking before 1.0, hence the minor version. Migration notes are in [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md#migrating-from-010-abi-1-to-020-abi-2).

### Breaking changes

- **C ABI 2.** One `dtl_engine` handle for a single model or a router; `dtl_open(path, config_json)` and `dtl_open_router(router_json, config_json)` take a JSON config instead of `dtl_options`; `dtl_execute(engine, request, call_options)` is the only call, with the observer (`request_id`, `on_event`) inside `dtl_call_options`. `dtl_router*`, `dtl_decide*`, `dtl_execute_with_options`, `dtl_observer` and `dtl_options` are removed. SONAME `libducktape_laya.so.1`.
- **Strict requests only.** The lenient legacy `decide`, which ignored unknown top-level fields, is removed from every binding.
- **C++:** `ducktape::engine::open` and `open_router` replace `ducktape::laya` and `ducktape::router`; `execute` takes a `ducktape::call_options`.
- **Rust:** `Engine::open(path, &Config)` and `Engine::open_router(json, &Config)`; `execute` and `execute_with(json, CallOptions)` replace `decide*`, `execute_observed` and `execute_with_options`.
- **Node and browser:** `decide`, `decideStructured` and `decideBatch` are removed; `execute` takes one request or an array.
- **Confidence fields.** Every answer reports `answer_confidence` (the probability of the reported answer) and `confidence` (`1 − H(p)/log k`); the old `choice`/`score` `confidence` formulas are replaced.
- **Diagnostics.** Model info reports `context_tokens`, `trained_context_tokens`, `temperatures` and `metadata_temperatures` instead of `max_tokens`; the runtime limit `tokens_per_question` is now `default_context_tokens`.
- **Stored temperatures are bounded to [0.5, 5]**, as in the original Laya. With the English model this changes `choice` probabilities for 11 or more options (its stored `choice.11` is 0.1006).
- **Structured values are serialized as the original Laya does** (Python `json.dumps`: full float precision, `0`/`false` descriptions kept), so prompts for structured input change.
- **`"auto"` is reserved** and cannot be a router route ID.

### New features

- **Open config:** `threads`, `context_tokens` (prompt capacity, 32 up to the model's trained length, default 512, replacing the fixed 512), `overflow` and `temperatures`, for an engine, for every route of a router, and per route. Requests may set `max_tokens`, `temperatures`, `overflow` and `max_windows`.
- **Long input:** overflow policies `reject` (default), `truncate_end`, `truncate_start` and `scan`. Except under `reject`, over-long instructions and options are shortened as llama.cpp's decision server does. `usage` reports `state_tokens`, `state_tokens_dropped` and `truncated_questions`; scanned answers carry `window`. `"aggregate": "all"` makes a scanned `noul` question take the minimum over windows.
- **Choices:** `choice` criteria may be a list of labels; a single-option `choice` (and a one-value schema `enum`) is answered without inference.
- **Models:** the multilingual (F16) and typed-decisions (Q8_0) checkpoints, built with `scripts/convert-model.py` from `models/laya-multilingual.json` and `models/laya-typed-decisions.json` (no download URL yet).
- **Routing by language:** a router's `"auto": {"english", "default"}` and requests with `"model": "auto"` and an optional `"lang"`; `usage.routing` reports the reason.
- **Runtime info:** features `language_routing` and `overflow_policies`, an `overflow_policies` list, and limits `minimum_context_tokens` and `scan_windows`.
- **Bindings:** Python, Go, Java/Kotlin and .NET (not yet published to their registries). Node `execute` and the browser take a `requestId`; the browser `openRouter` takes `auto` and per-model `contextTokens`, `overflow` and `temperatures`.
- **Examples:** request presets in `examples/presets/` (support triage, email triage, prompt-injection screening, moderation).

### Fixes

- The text between mask tokens is tokenized separately, as the models were trained; the multilingual vocabulary otherwise dropped whitespace before a mask token.
- A question whose instructions and options alone exceed the prompt limit is rejected under every overflow policy. With an empty state it used to overrun the context and abort the process.
- The state is tokenized once per request instead of once per question.
- Routes sharing a loaded model keep their own default overflow policy.
- Temperatures must be finite and positive after conversion to a 32-bit float (`1e-50` and `1e39` are rejected).
- Bindings reject strings that are not well-formed Unicode instead of replacing them.
- Node and the browser drop a call's `onEvent` handler once an abort or deadline has settled it.
- Python `timeout=0` means no deadline.
- The Rust, Go and Node bindings check the library's ABI version at run time, as Python, Java and .NET do.
- The browser binding's queue-full error no longer recurses.

### Release tooling

- New `original_reference` gate: the original Laya's recorded results, at each checkpoint's trained length.
- `validate.py --extra-model` qualifies the additional models against the original and upstream references; `check-candidate.py` requires them.
- `validate.py --packages` works without a model (`tests/package-smoke.py`), and CI runs it.
- The rollback rehearsal handles an ABI break by building each side's consumer from its own source commit.
- The `attest-release` workflow checks the qualified commit and the published inventories.

## 0.1.0

First release, from tag `v0.1.0`, on GitHub Releases (native tarball, SBOMs, inventories, attested), npm (`ducktape-laya`, `ducktape-laya-browser`) and crates.io (`ducktape-laya`, `ducktape-laya-sys`).

- C ABI 1 (`libducktape_laya.so.0`) with C, C++, Rust, Node.js/Electron and experimental browser (WASM and WebGPU) bindings.
- The pinned English Laya Q8_0 GGUF on Linux x86-64 (AVX2-era CPUs, glibc 2.38+).
- `choice`, `noul` and `score` questions, structured decisions, batches, explicit local routing, passive events, deadlines and cooperative cancellation.
- Prompts limited to 512 tokens; over-long input rejected.
