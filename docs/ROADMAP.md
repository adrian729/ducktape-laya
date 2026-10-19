# Roadmap

Work that is wanted but not scheduled, open research questions, and features
deliberately not planned, with the reasons. Scheduled work lives in working
plans; when a plan picks an item up, remove it from here.

## Wanted later

### GPU backends

CPU comes first only because it runs everywhere, is the simplest to qualify, and
the model is already fast enough on it. GPUs matter most for long documents
(8,192 tokens), large batches and high-throughput servers. Start once the CPU
platforms are fully qualified.

| Backend | Platforms | Notes |
|---|---|---|
| Metal | macOS arm64 | Built into ggml; the obvious first GPU target |
| CUDA | Linux and Windows x64 with NVIDIA GPUs | Fastest server option; a large toolkit dependency, so ship it as a separate package variant |
| Vulkan | Linux and Windows (any vendor), Android later | One portable backend across GPU vendors |
| WebGPU | Browser | Already exists (experimental) |

Each backend needs:
- a build option and runtime selection, with CPU fallback when no device is
  found and the active backend reported in `info()`;
- its own numerical profile, held to the CPU bar: the upstream reference on that
  backend, cross-profile drift against CPU, and no changed decisions on the
  fixtures;
- device-loss and out-of-memory handling and tests (the WebGPU work is the
  pattern);
- packaging variants, so CPU users don't download GPU runtimes;
- qualification, starting at tier 2 (CI-tested).

### Higher-precision model variants

`ggml-org/Laya-GGUF` also has `Laya-BF16.gguf`: the same English model at roughly
the original Laya's precision, 844 MB against 449 MB for the Q8_0 file we ship.
- It should remove most of the 0.010 mean probability difference from the
  original (measured 2026-10-20), at about twice the memory and slower CPU
  inference.
- Supporting it means pinning its hash, the same qualification as Q8_0, and
  documenting it as the accuracy-first option next to the memory-first Q8_0.
- The same applies to BF16 versions of other supported Laya models.

### Larger Laya models

The English model (ModernBERT-large, about 421M parameters) is the largest Laya
checkpoint published as of 2026-10-20. GGUF and llama.cpp run this architecture
at any size, so a larger release would be supported the same way: convert or
download, pin, qualify. Check memory per engine and the context capacity.

### Mobile, and the languages that depend on it

iOS and Android need their own packaging (XCFramework, AAR), toolchains and
memory testing. The 0.5 GB model fits on phones.
- **Swift for iOS:** extends a Swift package for macOS.
- **Kotlin on Android:** needs a JNI binding, because Android lacks Java's FFM
  API.
- **Dart/Flutter:** a `dart:ffi` binding across mobile and desktop.

### More language bindings on request

Ruby (`ffi` gem) and PHP (`FFI`) would each be a small wrapper like the Python
binding, for Rails and Laravel backends. Add one when a user asks.

## Research questions

- **The original Laya's `act_probability`:** a second "act" output alongside
  the decisions. The GGUF and llama.cpp path don't carry it; supporting it would
  need converter and llama.cpp changes. First find out what it means and
  whether the authors treat it as public.
- **Loading a model from memory:** llama.cpp has no public load-from-buffer API.
  Workarounds (memfd, temporary files) add little real protection for encrypted
  assets. Revisit with mobile.

## Not planned

Features the original Laya has that ducktape-laya deliberately leaves out.

| Feature | Why |
|---|---|
| `min_confidence` abstention in the library | A one-line check on `answer_confidence` in the caller; thresholds are application policy |
| Hooks that rewrite questions or budgets | Observers stay passive, so planning is deterministic and nothing reenters it |
| Presets and option-order rotations as API | They are request content: ship as example request files and a documented recipe |
| Email cleaner, embedding shortlist | Application helpers; the shortlist needs a second model |
| Temperature fitting, fine-tuning, evaluation harnesses | Training tooling: use the original's tools, then convert the model or pass the fitted temperatures |
| HTTP server, MCP server, LangChain/LlamaIndex/CrewAI integrations | In-process embedding is a project goal; integrations could later sit on top of the Python binding |
| Intel Macs | Apple stopped selling them in 2023 and CI runners are being retired; they still build from source |
