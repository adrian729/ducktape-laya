# Plan review — 2026-10-14

Two sequential review passes by the implementing agent, before implementation.
These are plan reviews, not independent code reviews or production certification.

## Pass 1: architecture and scope

- Retain llama.cpp/GGUF, a shared C ABI and thin native bindings. Keep the browser
  as a required delivery path. No new consumer application or demo scope.
- Inspect the pinned upstream decision API before selecting an adapter boundary.
  `server_decision_context` depends on server tasks, common chat and multimodal
  types; there is no standalone Laya decision library target at this pin. Reuse
  the existing tokenizer, Jinja engine and model inference; isolate the adapted
  preparation/scoring code instead of linking a server or expanding a fork.
- Keep prepared tokens/output-column selection private to that adapter. Shared
  request validation, routing, batching, hooks and schema projection stay above
  it. Do not add a public backend abstraction to solve a private maintenance task.
- Start browser feasibility now: inspect the ModernBERT decision graph and the
  WebGPU operation support, then require an actual model run. Do not confuse
  source-level operation coverage with validated tensor shapes or GPU support.

Result: no product decision needs the user. Record the responsibility map and
reuse decision; implement the private seam first.

## Pass 2: sequencing and evidence

- Existing parity checks consume example executables and an example fixture.
  Replace that dependency with automated API consumers and an owned conformance
  corpus. Keep demonstrations behind an opt-in build option.
- Record outputs from the pre-refactor library before rebuilding, then require
  exact equality from the refactored core across C, C++, Rust and Node. Existing
  lifecycle, error, routing and hook tests supplement the successful fixtures.
- Split stage 1 into smaller increments. Foundation checks cannot claim the
  whole stage complete before baseline/candidate reports and browser feasibility.
- Specify failed/not-run gates distinctly in upgrade reports and prohibit
  release promotion with missing required evidence. Declare model/fixture/build
  identities and numeric tolerances before comparing candidates.
- Preserve separate meanings for binding equality, runtime-update drift and
  upstream agreement. Unchanged answers alone do not validate calibration.
- Keep source-checkpoint provenance gaps explicit. A GGUF repository revision
  does not prove which original source checkpoint produced it.

Result: revise sequencing/report requirements, then implement the first increment.
Packaging, cancellation, platform coverage and browser delivery remain scheduled
work; this increment does not establish production readiness.
