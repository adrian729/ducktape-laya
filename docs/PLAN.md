# Ducktape Laya implementation plan

## Reviewed architecture

One C++ inference implementation, a versioned C ABI, a C++ ownership wrapper,
a safe Rust crate over that ABI, and a Node-API addon with TypeScript declarations.
Electron consumes the Node addon behind application-owned IPC. No HTTP or child
inference process is part of the library. Model files are application resources.

Review findings that change the original plan:

1. libllama does not expose a complete typed-decision API. The template, marker,
   temperature, and result code lives in tools/server/server-decision.cpp. Adapt
   only Laya's path into a small adapter against a pinned upstream commit. Avoid
   linking the HTTP server and unrelated model implementations.
2. Start with the exact existing English Q8_0 GGUF. Its backbone context metadata
   is not a promise of useful long-context decisions. Explicitly enforce a
   conservative 512-token combined request budget in the first adapter and reject
   overflow rather than silently truncating.
3. Preserve question and option insertion order. JSON libraries that sort keys
   can change model answers. Return ordered results across every binding.
4. Define ownership, errors and concurrency before shipping bindings: opaque
   handles, explicit buffer release, fixed-width/versioned options, per-call error
   output, and exceptions caught inside the ABI. A handle serializes inference;
   destruction must not race with active calls. Wrappers enforce that lifecycle.
5. CPU is the first tested backend. GPU acceleration, new models and context
   extensions each require their own validation and are later milestones.
6. Node-API provides a stable addon ABI, not universal OS/CPU binary portability.
   Publish separate builds and test Electron packaging outside ASAR archives.
7. Parity against the pinned native server checks implementation correctness, not
   task accuracy or probability calibration on an application's data.

## Milestones and acceptance

### M1: native proof and C ABI

- Pin llama.cpp as a git submodule; record model revision, checksum, provenance.
- Load a local model, evaluate choice/noul/score directly, unload safely.
- No network calls, listening ports, executable launches or model downloads.
- CMake build, plain-C example, RAII C++ wrapper.
- Validate bad inputs and missing/corrupt/unsupported models.
- Check all three question types against the same model/runtime server.

### M2: language interfaces

- Rust sys bindings and safe Engine with Result errors and deterministic cleanup.
- Node-API addon; asynchronous load/decide/close; bounded outstanding work.
- TypeScript request/result types; preserve order and consistent errors.
- Demonstrate identical fixture results through C, C++, Rust and Node.

### M3: distribution and Electron

- Linux x86-64 CPU first; Windows x86-64 and macOS ARM64 build/test matrix next.
- Electron example with context isolation and a narrow decision IPC interface.
- CPU portable builds (avoid compiling only for the build machine's ISA).
- Model packaged separately from library; checksum and upstream notices included.
- Clean-machine installer test without Python, Ollama or llama.cpp installed.
- Native dependencies, runtime search paths, signing and resource paths verified.

### M4: robustness and performance

- Sanitizer checks, repeated open/decide/close, concurrent requests and queue limits.
- Token overflow, Unicode, malformed JSON, missing models and invalid schemas.
- Startup, resident memory and latency measurements on representative hardware.
- GPU variants and cancellation only after backend-specific correctness checks.

## Non-goals for the first release

Chat generation, HTTP serving, model training, implicit downloads, automatic model
updates, mobile/web-browser binaries and a universal runtime for arbitrary models.

## Initial implementation status (2026-10-13)

The CPU core, versioned C ABI, C++ wrapper, Rust crates and asynchronous Node
interface are implemented. Real inference covers all three question types.
The four interfaces and a source-built upstream server agree exactly on the
initial fixture. Error handling, concurrent native calls, Node queue bounds and
draining on close are tested. Linux staging has been smoke-tested.

This is the first implementation of M1/M2, not a completed distribution release.
Broader compatibility, stress/sanitizer checks, other operating systems, prebuilt
packages and Electron installer testing remain. The detailed evidence and limits
are recorded in [VALIDATION.md](VALIDATION.md).

## Sources

- https://github.com/ggml-org/llama.cpp/pull/29818
- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/server-decision.cpp
- https://huggingface.co/convaiinnovations/laya
- https://huggingface.co/ggml-org/Laya-GGUF
- https://nodejs.org/api/n-api.html
- https://www.electronjs.org/docs/latest/tutorial/using-native-node-modules
