# Implementation audit — 2026-10-14

Scope: shared native request/schema/routing logic, model adaptation and calibration,
C/C++/Rust ownership and ABI boundaries, Node/browser worker lifecycle and
cancellation, artifact identity, validation tooling and the first hosted CI run.
This is a source review with targeted regressions and runtime checks, not an
exhaustive security assessment or a new dependency-advisory survey.

## Confirmed findings and fixes

| Finding | Impact | Fix and verification |
| --- | --- | --- |
| Sanitized C consumer linked with the C driver | Clang 18 could not resolve C++ UBSan vptr runtime symbols, preventing the sanitizer CI job from running. | Keep source compilation in C and select the C++ link driver for in-tree C consumers. The repaired hosted job passed all three model-free contracts and 40,000 mutation cases without disabling sanitizers. |
| Browser initialization missed aborts around option processing and ready delivery | `open()` could resolve with a worker after initialization was aborted, including a worker already marked dead. | Recheck the signal after listener registration and before returning the engine. Regression tests cover both timings, termination and prevention of automatic fallback. |
| Browser initialization accepted overflowing timers and could leak a worker when a diagnostics getter threw | Large timer values could become immediate timeouts; repeated option access after worker creation could escape cleanup. | Bound initialization timeouts to integer milliseconds in 1..86400000, reject invalid signals early, and retain the validated diagnostics callback. Getter-failure tests verify worker cleanup. |
| Approved-runtime validation inherited candidate CMake cache overrides | Tests could run against a candidate while the report's top-level runtime named the approved pin. | Explicitly configure the approved source and clear the revision override, then compare built diagnostics with the release record before accepting test gates. Tested with an intentionally stale revision override; mismatched/missing diagnostic fields are rejected. |

Fix commits: `9243476`, `b09ae77`, `4d78c62`, `5d56d5b`.
No model pin, inference arithmetic or public C ABI changed.

## Verification

- 10 artifact/runtime identity tests and 9 browser backend/lifecycle tests passed.
- Native model-free contracts, Rust/Node checks and the 16-function ABI export
  check passed through the validation runner.
- Primitive, structured and batch fixture outputs agreed exactly across C, C++,
  Rust and Node with the pinned real model.
- Local strict ASan/UBSan passed three model-free contracts and 20,000 request
  mutations plus 20,000 router-config mutations. These are deterministic mutation
  checks, not a coverage-guided fuzzing campaign.
- [The repaired hosted CI run](https://github.com/adrian729/ducktape-laya/actions/runs/38567447167)
  passed the GCC/Node 26, Clang/Node 24 and sanitizer jobs. That run verified the
  sanitizer link fix; later browser/validation changes have their own local
  regression evidence and a subsequent hosted run.
- Native and both browser package targets rebuilt successfully. Both packagers
  accepted their current source fingerprints and generated artifacts. Installed
  C/C++, pkg-config, npm and Rust consumers passed real-model agreement checks
  outside the repository.

Machine-readable evidence is retained in
[evidence/audit-20261014.json](evidence/audit-20261014.json).
The existing cross-backend browser numeric tolerance failure and other unrun
release gates remain unchanged. This audit does not declare release readiness.
