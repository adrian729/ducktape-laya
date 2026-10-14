# Product and release plan

This is the current product contract. It supersedes the scope and priorities in
[historical evaluations](EVALUATION.md). [API.md](API.md) describes implemented
behavior; [VALIDATION.md](VALIDATION.md) records actual evidence. Requirements
below are not claims that the prototype already satisfies them.

## Fixed product requirements

| Area | Requirement |
|---|---|
| Inference | llama.cpp |
| Model format | GGUF |
| Native interfaces | Shared C ABI, thin C++, Rust and Node/TypeScript bindings |
| Browser execution | WebGPU/WASM path, reusing the shared implementation wherever feasible |
| Higher-level features | Routing, batching, hooks and structured decisions |
| Supported operating system for now | Linux; advertise only tested CPU architectures, distributions/runtime baselines and execution backends |

One shared implementation owns common behavior. Bindings adapt language types,
ownership, errors, async scheduling and platform integration. Reuse upstream
functionality before implementing it ourselves. Duplicate code only when a
specific platform constraint or demonstrated benefit justifies it; record why,
what is duplicated, how it stays equivalent, and when it can be removed.

Demos are disposable, optional developer tools. They must never drive product
architecture, APIs, dependencies, scope, priorities or release acceptance. Build
and package the library independently; exclude demo-only dependencies and assets
from consumer artifacts. Package integration tests are automated consumers of
the public API, not demo applications or UI requirements.

No Windows/macOS delivery commitment until suitable test environments exist.
Linux browser testing is in scope now. Linux native GPU support is a separately
validated capability, not implied by browser WebGPU support.

## Present state and gaps

- Implemented: Linux CPU GGUF adapter, C/C++/Rust/Node interfaces, bounded
  sequential batching, a flat structured-decision subset, passive hooks, and
  explicit local routing with a model-count-bounded cache.
- Checked: pinned English Q8_0 inference, interface parity, selected lifecycle
  and error cases. Existing reviews and tests do not establish broad platform
  support, application accuracy, or production reliability.
- Missing: browser runtime, supported release packages, automated compatibility
  and upgrade tooling, broad Linux coverage, sustained reliability validation,
  and explicit cancellation/deadline behavior.
- Architectural debt: model-specific preparation and result processing are
  adapted into our core from upstream server code. Review that boundary before
  extending it. Existing demos are also built by the default CMake configuration;
  move them behind an explicit development option.

## Architecture and upstream boundary

1. Inventory each responsibility against the pinned llama.cpp and Laya sources:
   model loading, template rendering, tokenization, score extraction, calibration,
   result construction, and our higher-level features. Distinguish model data,
   upstream algorithms, necessary adapter code, and binding-only behavior.
2. Prefer a reusable upstream decision-processing component if it can be linked
   in-process without bringing in an HTTP server or changing GGUF inference.
   Investigate extracting/reusing the existing upstream implementation rather
   than growing a parallel implementation. Prove equivalence before replacing
   working code; reuse alone does not justify extra runtime dependencies.
3. Where upstream exposes no suitable library boundary, keep the smallest
   necessary Laya adapter isolated behind an internal backend interface. Record
   its source revision, source locations, intentional deviations and comparison
   fixtures. Keep llama.cpp-specific types and headers out of the public ABI.
4. Keep shared routing/planning/batch/result semantics above that boundary.
   Native and browser adapters should select backends and manage resources, not
   independently implement the same Laya protocol.
5. Keep upstream source pins unchanged by default. Any necessary patch must be
   small, separately recorded, reproducible and justified, with a removal plan
   when upstream supports the requirement. No silent local vendor edits or
   long-lived broad fork. Consider upstreaming reusable work as a separate task.
6. Isolate model capabilities from public language APIs. Expose supported limits,
   decision types and model/runtime identity so consumers can detect capabilities
   without depending on a particular upstream revision.

Acceptance: a responsibility map, documented reuse/extraction decision, a narrow
internal backend boundary, and an upgrade experiment showing that an ordinary
compatible runtime update does not require edits in every language binding.
No claim that all future upstream changes can be absorbed without adapter work.

## Professional release requirements

### Packaging and installation

- Reproducible, pinned build recipes and toolchain/container definitions. Record
  compiler, dependency revisions, architecture, CPU ISA and build flags in each
  release. Verify reproducibility and document any remaining nondeterminism.
- Linux C/C++ headers and shared library with exported CMake targets and pkg-config
  metadata; versioned SONAME, intentional exported symbols and working installed
  include/library paths. No checkout-specific absolute paths in shipped files.
- Versioned Rust sys/safe crates that work outside this monorepo, with a documented
  native-library discovery/build policy. Consumer installation must not depend on
  an unpublished sibling path, our development machine or a research checkout.
- Versioned npm package with declarations, worker and Node-API addon, selecting
  supported Linux artifacts and producing useful errors on unsupported systems.
  Test Node and Linux Electron loading/resource paths, including ASAR handling.
  Do not impose an application architecture beyond necessary integration rules.
- Browser package containing its loader, worker, WASM and any backend assets, with
  an explicit asset-location contract usable in ordinary bundlers and offline
  deployments. Keep native-only imports out of browser entry points.
- Keep model weights separately versioned from library packages. Document how a
  consuming app bundles the approved GGUF in its own installer/resources so end
  users install only that app. No implicit runtime model download or update.
- Publish artifact checksums, dependency/license inventory (SBOM), provenance and
  release integrity verification. Include required notices for runtime, model
  and any reused code. Public publication/signing is a separate release action.
- Clean Linux environment tests must install the actual package artifacts and
  call public APIs without this repository, global development dependencies,
  Ollama, Python or a separately installed inference server. Build-time tools
  must not accidentally become runtime requirements.

### Reliability and resource management

- Specify handle/thread ownership, concurrent calls, shutdown, callback reentry,
  partial-failure semantics and error categories consistently across interfaces.
  Preserve the existing strict bounds and documented legacy API behavior.
- Define queue, request, token, batch, loaded-model and memory budgets. Model-count
  limits are not byte-level RAM guarantees; measure actual peaks and use an
  explicit policy for admission, loading failures and cache eviction.
- Add sustained open/use/close and mixed-workload tests, concurrent engines,
  queue saturation, cancellation during shutdown, failed model loads, malformed
  requests, corrupt/incompatible assets, allocation failures where testable,
  and worker/backend failures. Include representative large valid inputs.
- Run native sanitizer and parser/schema fuzz checks, leak checks, race checks
  where supported, and memory/descriptor/thread measurements over long runs.
  Define measurable acceptance thresholds before declaring a release stable.
- Define cancellation and deadlines honestly: cancel queued work, check shared
  cancellation at safe boundaries, and use backend interruption only where
  verified. A timeout must not imply an unsafe thread kill or immediate GPU/CPU
  interruption; document what happens to running work, results and resources.
- Provide version/capability diagnostics and opt-in timing/resource events with
  stable error codes. No state/prompt/result logging or telemetry by default;
  hosts own telemetry destinations. Logging must not change inference behavior.
- Benchmark startup, latency distribution, throughput, peak memory and shutdown
  with recorded hardware/configuration. Establish regression budgets from repeatable
  baselines, not laptop anecdotes. Simultaneous batching remains gated on output
  isolation/parity and measured benefit; a sequential batch is still a valid API.

### Linux platform coverage

- Publish an explicit support matrix: architecture, minimum CPU ISA, glibc/libstdc++
  baseline, compiler/runtime versions, Node/Electron versions and execution backend.
  Current x86-64 AVX2-era flags are not a generic Linux compatibility guarantee.
- Choose a portable baseline or explicit ISA-specific packages with safe selection.
  Detect unsupported CPUs before execution; validate dispatch/fallback where used.
- Test artifact installation on a chosen minimum Linux baseline and representative
  newer distributions. Containers validate userspace compatibility, not different
  physical CPUs, GPU drivers or a different host kernel.
- Add hardware coverage when available. Linux ARM64, native GPU paths and other
  untested combinations remain explicitly unsupported/experimental until tested;
  a successful cross-compile is not sufficient evidence of support.
- Run browser validation on Linux in the browser/backend combinations we advertise.
  Keep tested, experimental and unsupported capabilities visibly separate.

### Public API, security and maintenance

- Define semantic versioning, ABI compatibility, JSON schema/result stability,
  deprecation and migration policies. Add ABI/export checks and compilation tests
  for existing consumers. Check Rust declarations/TS types against the C contract;
  generate mechanical bindings where that reduces drift without obscuring safety.
- Document ownership, blocking/async behavior, ordering, overflow, calibration and
  probability conventions, packaging and supported models. Distinguish promises
  from implementation details and known limitations.
- Validate untrusted request sizes and formats. Define the trust boundary for
  GGUF/template assets; verify distributed model identities before use and fail
  clearly on incompatible metadata. Checksums identify expected bytes; release
  provenance/signatures establish who supplied them.
- Maintain vulnerability triage, dependency monitoring, a security reporting path,
  release notes and reproducible bug-report guidance. Do not silently inherit a
  newly changed dependency or require applications to update on every upstream
  commit. Keep runtime network access opt-in to the host's asset policy.
- Validate our integration against upstream/reference outputs. Model task quality,
  application-specific decision thresholds and business fallback behavior belong
  to Laya and consuming applications; they are not generic library release gates.

## Browser delivery

The browser is a required product path, not an instruction to redirect consumers
to a different SDK. Preserve llama.cpp/GGUF and share as much native implementation
as feasible through WASM.

1. Establish actual support for the pinned Laya model's operations in the chosen
   llama.cpp WASM/WebGPU build. Produce a capability report and identify missing
   operations, memory limits or initialization constraints. Do not assume a
   runtime's advertised WebGPU support covers this model.
2. Run the same core in a Web Worker where feasible. Share schema planning,
   calibration, routing policy and result construction; isolate browser-specific
   memory, asset loading, scheduling and backend selection.
3. Define WebGPU initialization, adapter/device limits and device-loss handling,
   with an explicitly tested WASM fallback. Define WASM threading requirements,
   cross-origin isolation headers and behavior without those capabilities.
4. Test app-supplied GGUF/worker/WASM URLs, bundler/base-path deployment, optional
   persistence/cache ownership, offline reuse, missing/corrupt assets, cancelled
   initialization, memory pressure and deterministic release of resources.
5. Use identical fixtures and declared numeric tolerances across native CPU,
   WASM and WebGPU. Require bounded event-loop impact, ordered results, lifecycle
   correctness and usable diagnostics on the real supported browser matrix.

If platform constraints require duplicate implementation, document the measured
reason and maintain shared conformance fixtures. Do not silently replace the
agreed inference engine/model format or move shared logic into independent SDKs.
A browser blocker must remain visible; it must not be relabeled as completed
support merely because a loader builds or WASM instantiates.

## Easy, controlled llama.cpp and Laya upgrades

Updating these dependencies must be a repeatable maintenance operation. Build
this workflow early, before adding more model-specific code or backends.

### Separate identities and compatibility

- Pin the llama.cpp source revision and any patches. Lock build/toolchain inputs
  and language dependencies. Preserve the existing release while testing a candidate.
- Give each supported model artifact its own immutable manifest: upstream Laya
  source/checkpoint revision, GGUF repository/revision/hash, tokenizer/template and
  calibration identity, format/capabilities/limits, and license/provenance. If we
  convert a model, also record exporter revision, conversion/quantization settings,
  source hashes and build tools. Prefer an approved upstream GGUF when suitable.
- Maintain one machine-readable release/compatibility record linking runtime pins,
  adapter revision, model manifests, package versions and validated backends.
  Cross-check it against the submodule and artifacts; avoid independently edited
  copies of pins scattered through bindings/build scripts. Audit current hardcoded
  pins as part of this work. Historical evidence remains immutable.
- Permit compatible model and runtime updates independently. Do not equate a new
  upstream commit with a required model replacement or a binding API change.
  Unknown combinations are unvalidated, not automatically compatible.

### Candidate upgrade workflow to implement

1. A maintainer selects a candidate runtime revision, model revision, or both.
   Tooling creates an isolated checkout/build against immutable inputs and a
   verified local/CI model cache. It never overwrites the known-good release.
2. Inspect upstream change summaries and diffs affecting public runtime APIs,
   Laya processing, tokenizer/template/calibration data and model conversion.
   Check whether our adapter can shrink as upstream adds reusable functionality.
   Surface compatibility risks and patch conflicts in the report.
3. Build the old and candidate configurations with recorded flags. For a runtime
   update, hold the model fixed; for a model update, hold the runtime fixed when
   compatible. Test the combined pair separately when both must change. Retain
   supported older combinations required by the published compatibility policy.
4. Run shared contract fixtures: tokenization/prompt boundaries and output
   interpretation where we own them; choice/noul/score probabilities; schema
   projection; overflow/errors; routing and lifecycle; batch order/isolation;
   hooks; and cross-binding results. Compare with the corresponding upstream
   reference for the same artifact/configuration where available.
5. Use predeclared numeric tolerances for each backend. Require exact equality
   for paths promising it; justify numeric/backend changes rather than widening
   thresholds merely to pass. An intentionally changed model can change answers:
   report that drift and reference agreement rather than treating the old model's
   answers as universal truth. Task-quality claims remain outside this library.
6. Run ABI/API, installed-package, Linux/browser, reliability, license/security
   and performance checks applicable to the release. No model-less green suite
   may stand in for required real-model gates. Report skipped/unavailable checks.
7. Produce a reviewable report with changed pins/artifacts, capabilities, result
   differences, memory/performance, compatibility, migration needs and failures.
   Promote an accepted candidate by updating the release record/lockfiles,
   package versions and notes together. Never regenerate expected outputs silently.
8. Preserve previous artifacts/manifests and a tested rollback/re-pin path.
   Dependency monitoring may propose candidates; it never silently upgrades the
   runtime or a model inside an installed consumer application.

Acceptance: one documented automated entry point builds and compares a selected
candidate and produces the report; a maintainer is not manually editing every
binding or assembling a test checklist. A new incompatible capability may still
require shared-core work and deliberate API evolution. This tooling is planned,
not implemented yet.

## Implementation sequence and release gates

| Stage | Deliverable | Completion gate |
|---|---|---|
| 1. Architecture and maintenance foundation | Upstream responsibility/reuse audit, isolated backend boundary, release/model compatibility records, shared fixtures and candidate-upgrade tooling; demos isolated from product builds | A baseline replay and a real candidate update produce reviewable reports; runtime internals do not leak into bindings; library builds without demos |
| 2. Linux native release | Installed C/C++/Rust/npm artifacts, API/ABI policy, portable/declared CPU targets, package metadata and provenance; reliability/cancellation/resource work | Real-model conformance, ABI and fault/stress checks pass; clean Linux consumers load the actual artifacts; advertised platform matrix and limits are documented |
| 3. Browser release | Shared-core WASM/WebGPU implementation, worker/assets/package, tested fallback and browser lifecycle | Real Linux-browser WebGPU and WASM runs meet conformance, memory and responsiveness gates; unsupported combinations fail clearly |
| 4. Ongoing maintenance | Candidate monitoring, regression/performance history, security triage, release notes and rollback | Every promoted runtime/model/package update has the required compatibility report and retained last-known-good artifacts |

Investigate browser backend feasibility during stage 1 so native architecture
choices do not obstruct the required browser path. Linux-native packages can be
released with their scope clearly labeled before the browser package is ready;
the overall agreed feature set is not complete until both paths meet their gates.

Chat generation, HTTP serving, model training, implicit downloads/updates and
untested OS support are outside the current product scope. Demo enhancements
have no priority in this sequence.
