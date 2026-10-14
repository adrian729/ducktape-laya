# Compatibility and release policy

The current 0.1.0 tree is unreleased development work. A passing demo or a model-less
test does not establish release readiness. Runtime/model combinations are identified
by `release/compatibility.json`, immutable model manifests and applied patch hashes.

The C ABI owns shared behavior. Additive functions/status codes can remain ABI 1;
changes to existing layouts, ownership or semantics require a new ABI and SONAME.
Bindings follow package semantic versions, and released artifacts must be immutable.
Before 1.0, breaking changes require a minor version and migration notes; after 1.0,
a major version. Do not silently replace artifacts under an existing version.

Existing status numbers, ordering, primitive probability interpretation, overflow
rejection, no-partial-result behavior and JSON field meanings are compatibility
contracts. Introspection may add fields; consumers ignore unknown fields there.
Strict execution requests reject unsupported fields, while the documented legacy
primitive API preserves its metadata behavior. New model capabilities require
validation and explicit adapter support, not just an unchanged file extension.

A release identifies its architecture, CPU features, system-library floor, tested
Linux userspaces, Node/Electron/browser versions and backend. The same model may
have numeric differences between CPU/GPU/ISA profiles; bindings using the same
native binary and settings must agree exactly. Each backend must agree with
upstream processing within its declared strict tolerance. Cross-profile probability
and discrete-decision drift is reported, without an interchangeability guarantee.
Browser delivery remains experimental pending full qualification. Unvalidated platforms must not be
advertised as supported merely because the source can compile there.

Release acceptance includes actual artifact installation, model integrity, ABI and
cross-binding checks, upstream-reference agreement, sanitizer/resource regressions,
platform coverage, license/provenance inventory and reproducibility. Failed and
unrun gates remain visible. Public publication, signing keys, security contact and
release hosting are separate deployment decisions, not silently inferred from
local implementation work. See [SECURITY.md](../SECURITY.md) and [PLAN.md](PLAN.md) for boundaries.
