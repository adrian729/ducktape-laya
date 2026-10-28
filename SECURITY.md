# Security

## Reporting a vulnerability

Report suspected vulnerabilities in the library or its packages privately through [GitHub's vulnerability reporting form](https://github.com/adrian729/ducktape-laya/security/advisories/new). Reports go to the maintainer, @adrian729. Do not include private inputs, model files, credentials or exploit payloads in public issues. For a problem specific to an application that embeds this library, use that application's own security channel.

## Supported versions

Fixes go into the next release; released files are never replaced. Before 1.0 only the latest release (currently 0.3.0) receives fixes, and an affected release is named in the [changelog](CHANGELOG.md) with the version that fixes it.

## Verify a download

Every other file of a GitHub release is listed in its `SHA256SUMS`, and every listed file except the qualification report `gates.json` has a GitHub (Sigstore) attestation that binds it to the release and to that report (the attestation records the hash of `gates.json`). Verify a file with the GitHub CLI, pinning the workflow that signs releases:

```sh
gh attestation verify FILE -R adrian729/ducktape-laya \
  --predicate-type https://github.com/adrian729/ducktape-laya/release-qualification/v1 \
  --cert-identity https://github.com/adrian729/ducktape-laya/.github/workflows/attest-release.yml@refs/heads/main
```

`--cert-identity` requires exactly that workflow, run from `main` (from 0.2.0 on it signs only from `main`); `--signer-workflow` alone is a prefix match and not enough. That command checks only the digest and the signer, so an older release's attested file would also pass: require the attestation's release to be the tag you expect, by adding `--format json` and piping to `jq -e --arg tag vX.Y.Z 'any(.[]; .verificationResult.statement.predicate.release == $tag)'`, or run `scripts/verify-release-assets.sh`, which checks it and the rest of the attestation for every file. The npm tarballs, wheels and Maven files are uploaded to their registries byte for byte as attested, so the same command verifies a copy downloaded from there. nuget.org adds its own repository signature to the package, so compare its entries (all but `.signature.p7s`) with the GitHub release copy. Crates are uploaded with `cargo publish`, which packages them again from the tagged source; the release requires that packaging to be byte-identical to the attested `.crate` files, so crates.io's checksum of each crate is the attested file's SHA-256. The attestation says that the release workflow on `main` found each file listed in `SHA256SUMS` and in the package inventories the release's `gates.json` names, and that `gates.json`, written for the tagged commit, reports as passed exactly the gates and release targets that commit's release record requires (from 0.3.0 also that the native packages are those of the CI candidate run on that commit). The workflow does not rerun the gates: their results come from the maintainer's release host. It is not a record of how the files were built ([details](docs/RELEASING.md#provenance)).

Model files are not release files: check them against the SHA-256 in their manifest ([models](docs/MODELS.md)).

## Trust model

- **Models are code.** Treat GGUF files, their embedded templates and the native and WebAssembly binaries as trusted, code-like assets. The model parser is not a sandbox for attacker-supplied model files; pin the files you ship and verify their hashes before loading. The open-time checks prove format compatibility, not that a file is benign.
- **Requests are bounded, not trusted.** Request size, nesting, question count and other limits ([API guide](docs/API.md#batches)) bound the work one request can cause. Deadlines and cancellation also stop tokenization, except its regular-expression pre-split into words and each word's symbol setup, which run to their end once started ([cancellation](docs/API.md#cancellation-and-deadlines)); applications that accept untrusted input and need short worst-case latency should still cap the request size themselves, lower `max_windows_per_call` (default 256 planned forward passes per call), set a `timeout_ms` on each call, and filter requests' `overflow` and `max_windows` when they want no `scan` at all (the library accepts up to 1 MiB per call). With `"model": "auto"` routing, requests that alternate between routes force a model load each when `max_loaded` is below the number of routes; for untrusted input set `max_loaded` to at least the number of routes (up to 4, [API guide](docs/API.md#local-routing)).
- **Native code locations are trusted inputs.** `DUCKTAPE_LAYA_LIBRARY` and `DUCKTAPE_LAYA_LIB_DIR` (Node, Python, Java, .NET) and `-Dducktape.laya.tmpdir` (Java) select the native code that runs in your process, so only an administrator or the application may set them; never take them from untrusted input or an untrusted environment. On Windows without the Visual C++ Redistributable, the .NET and Node loaders can resolve `vcruntime140.dll` and `msvcp140.dll` through the standard DLL search order, so a planted copy in a searched directory would run; install the Redistributable. Python restricts its fallback search to `LOAD_LIBRARY_SEARCH_DEFAULT_DIRS`.
- **No network.** The native library opens no network connections. The browser package fetches only the asset URLs the host gives it. There is no telemetry, no automatic update of the library or model, no shell execution, remote routing, HTTP service or plugin loading.
- **Callbacks are application code.** Observer and cancellation callbacks run inside your process and must protect their own data. Browser workers separate inference from the page's UI thread but do not replace the browser's origin and content security policies.
- **Memory failures.** Allocation failures are reported as status 4 and leave the engine usable, except for some small allocations inside ggml, where upstream aborts the process ([API guide](docs/API.md#ownership-admission-and-errors)). Run the engine in a separate process if your application must survive those.

## How reports are handled

Confirmed defects are triaged by impact and by the affected package, runtime and model. A fix is reproduced against the last release, gets a regression test without private content, goes into the shared core where possible, and passes the affected release checks; the changelog names the affected versions and any migration. Upstream llama.cpp, model and toolchain advisories are reviewed before each release candidate; dependency updates never reach installed applications automatically.
