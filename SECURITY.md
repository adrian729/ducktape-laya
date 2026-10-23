# Security

## Reporting a vulnerability

Report suspected vulnerabilities in the library or its packages privately through [GitHub's vulnerability reporting form](https://github.com/adrian729/ducktape-laya/security/advisories/new). Reports go to the maintainer, @adrian729. Do not include private inputs, model files, credentials or exploit payloads in public issues. For a problem specific to an application that embeds this library, use that application's own security channel.

## Supported versions

Fixes go into the next release; released files are never replaced. Before 1.0 only the latest release (currently 0.2.0) receives fixes, and an affected release is named in the [changelog](CHANGELOG.md) with the version that fixes it.

## Verify a download

Every file of a GitHub release is listed in its `SHA256SUMS` and has a GitHub (Sigstore) attestation that binds it to the release and its qualification report. Verify a file with the GitHub CLI, pinning the workflow that signs releases:

```sh
gh attestation verify FILE -R adrian729/ducktape-laya \
  --predicate-type https://github.com/adrian729/ducktape-laya/release-qualification/v1 \
  --cert-identity https://github.com/adrian729/ducktape-laya/.github/workflows/attest-release.yml@refs/heads/main
```

`--cert-identity` requires exactly that workflow, run from `main` (from 0.2.0 on it signs only from `main`); `--signer-workflow` alone is a prefix match and not enough. The npm tarballs, wheels and Maven files are uploaded to their registries byte for byte as attested, so the same command verifies a copy downloaded from there. nuget.org adds its own repository signature to the package, and crates.io rebuilds crates from the tagged source, so compare those with the GitHub release copy or the tagged source instead. The attestation says that the published files are the ones that passed qualification; it is not a record of how they were built ([details](docs/RELEASING.md#provenance)).

Model files are not release files: check them against the SHA-256 in their manifest ([models](docs/MODELS.md)).

## Trust model

- **Models are code.** Treat GGUF files, their embedded templates and the native and WebAssembly binaries as trusted, code-like assets. The model parser is not a sandbox for attacker-supplied model files; pin the files you ship and verify their hashes before loading. The open-time checks prove format compatibility, not that a file is benign.
- **Requests are bounded, not trusted.** Request size, nesting, question count and other limits ([API guide](docs/API.md#batches)) bound the work one request can cause. Tokenization of a large request cannot be interrupted by a deadline, so applications that accept untrusted input and need short worst-case latency should cap the request size themselves (the library accepts up to 1 MiB per call).
- **No network.** The native library opens no network connections. The browser package fetches only the asset URLs the host gives it. There is no telemetry, no automatic update of the library or model, no shell execution, remote routing, HTTP service or plugin loading.
- **Callbacks are application code.** Observer and cancellation callbacks run inside your process and must protect their own data. Browser workers separate inference from the page's UI thread but do not replace the browser's origin and content security policies.
- **Memory failures.** Allocation failures are reported as status 4 and leave the engine usable, except for some small allocations inside ggml, where upstream aborts the process ([API guide](docs/API.md#ownership-admission-and-errors)). Run the engine in a separate process if your application must survive those.

## How reports are handled

Confirmed defects are triaged by impact and by the affected package, runtime and model. A fix is reproduced against the last release, gets a regression test without private content, goes into the shared core where possible, and passes the affected release checks; the changelog names the affected versions and any migration. Upstream llama.cpp, model and toolchain advisories are reviewed before each release candidate; dependency updates never reach installed applications automatically.
