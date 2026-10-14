# Security and maintenance

This library is a public development project with no published package release.
Report suspected library vulnerabilities privately through
[GitHub's vulnerability reporting form](https://github.com/adrian729/ducktape-laya/security/advisories/new).
Reports are delivered to the repository maintainer, @adrian729. For issues specific
to an application embedding this library, use that application's established
private security channel. Avoid public reports that include private inputs,
model assets, credentials or exploit payloads.

Treat GGUF files, embedded templates and native/WASM binaries as trusted code-like
assets. The runtime parser is not a sandbox for attacker-supplied model files.
Pin their identity and verify release provenance and checksums before loading.
Request bounds limit supported input; they do not turn malicious models into
trusted ones. Browser workers isolate lifecycle/resources from UI execution but
are not a substitute for the browser's origin/CSP policies.

Runtime networking is absent from native bindings. Browser fetching uses explicit
host asset locations. There is no telemetry, implicit model/runtime update, shell
execution, remote routing, inference HTTP service or arbitrary callback plugin
loading. Passive host hooks are application code and must protect their own data.

Triage confirmed defects by impact and affected package/runtime/model identities.
Reproduce on the last-known-good pin, add a regression fixture without private
content, fix the shared implementation where possible, run affected release gates,
and document affected versions and migration. Keep the previous artifacts for
rollback. Review upstream runtime, model and toolchain advisories and dependency
changes before selecting candidates; updates never propagate automatically.
