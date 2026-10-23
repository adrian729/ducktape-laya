# Security and maintenance

The first release, 0.1.0, is published on
[GitHub Releases](https://github.com/adrian729/ducktape-laya/releases/tag/v0.1.0)
with SHA-256 checksums. Each artifact has a GitHub (Sigstore) attestation binding it
to the release and its qualification report; verify one with
`gh attestation verify FILE -R adrian729/ducktape-laya --predicate-type
https://github.com/adrian729/ducktape-laya/release-qualification/v1`.
Add `--cert-identity https://github.com/adrian729/ducktape-laya/.github/workflows/attest-release.yml@refs/heads/main`
to require exactly that workflow, run from `main`, as the signer (from 0.2.0 the
workflow signs only from `main`); `--signer-workflow` is a prefix match that does
not name the ref, so it is not enough on its own. `--source-ref refs/heads/main`
pins the source ref too, and `scripts/verify-release-assets.sh`, which the
publish jobs run, also checks the certificate's repository, workflow ref,
`workflow_dispatch` trigger and GitHub-hosted runner. The `release` environment
should additionally restrict deployment branches to `main` and require a reviewer
(a repository setting, not enforced by these files).
The npm tarballs are uploaded exactly as attested, so the same command verifies
the copies npm serves. The crates are not: `cargo publish` packages them again
from the tagged source, so a crates.io file verifies only if its bytes happen to
equal the attested `.crate`. For 0.1.0 the crates.io checksums do equal the
released files; later releases are not guaranteed to, so check crates against
the tagged source instead (see [docs/PUBLISHING.md](docs/PUBLISHING.md)).
Provenance differs by release. The native artifacts of 0.1.0 and 0.2.0 were built locally on the maintainer's release host, qualified there, and attested as the qualified files. From the next release, the artifacts of every target are built on GitHub-hosted runners in the release candidate run (`native.yml`, dispatched on the candidate commit), and the release host qualifies those exact files: for Linux x86-64 the candidate run's library and Node addon must be byte-identical to the release host's validated build, which binds the shipped bytes to the local qualification. Linux arm64 and Windows x64 (tier 2) are qualified by that CI run alone, with fewer gates ([docs/QUALIFICATION.md](docs/QUALIFICATION.md#release-targets-and-tiers)). The attestation predicate (`release-qualification/v1`) records the release, the tag commit, the hash of the qualification report (`gates.json`) and each target's tier. It states that the published assets are the qualified ones; it is not build provenance (see [docs/ROADMAP.md](docs/ROADMAP.md)).
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
loading. Passive observer callbacks are application code and must protect their own data.

Triage confirmed defects by impact and affected package/runtime/model identities.
Reproduce on the last-known-good pin, add a regression fixture without private
content, fix the shared implementation where possible, run affected release gates,
and document affected versions and migration. Keep the previous artifacts for
rollback. Review upstream runtime, model and toolchain advisories and dependency
changes before selecting candidates; updates never propagate automatically.
