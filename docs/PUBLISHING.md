# Publishing

Where each package is published, and what is still missing. Publication follows qualification (see [RELEASE_CHECKS.md](RELEASE_CHECKS.md)). The native tarball and the npm tarballs are uploaded exactly as the candidate qualified them. `cargo publish` cannot upload a prebuilt `.crate`: it packages the crates again from the tagged source, so the crates.io files are rebuilt from the qualified commit rather than copied, and their bytes are not checked against the inventory.

## Status

| Package | Channel | 0.1.0 | 0.2.0 (main) |
| --- | --- | --- | --- |
| Native tarball, SBOMs, inventories | GitHub Releases | Published, attested | Same process |
| `ducktape-laya` (Node.js) | npm | Published | Same process |
| `ducktape-laya-browser` | npm | Published | Same process |
| `ducktape-laya`, `ducktape-laya-sys` | crates.io | Published | Same process |
| `ducktape-laya` (Python wheel) | PyPI | — | Wheel is built and tested; PyPI setup missing |
| `github.com/adrian729/ducktape-laya/bindings/go` | Go module proxy | — | Needs only a tag |
| `io.github.adrian729:ducktape-laya` (Java) | Maven Central | — | Packaging and Central setup missing |
| `DucktapeLaya` (.NET) | NuGet | — | Packaging and NuGet setup missing |

All names above were unclaimed on 2026-10-19.

## Work before the next release

These are code changes; they need no accounts.

- Versions are already 0.2.0 in `release/compatibility.json` and every binding manifest. For later releases, bump them together: `check_record` rejects any mismatch, including the Python, Java and .NET versions.
- Write the release notes: move the `0.2.0 (unreleased)` section of [CHANGELOG.md](../CHANGELOG.md) under the release version and date, and use it for the GitHub release text.
- Python: the real wheel comes only from `scripts/python_wheel.py` (run by `scripts/package-linux.py`), which bundles the native library. Building `bindings/python` with its `pyproject.toml` alone (`pip wheel`, `python -m build`) produces a wheel without the library; never upload that.
- Java: build the release jar in `scripts/package-linux.py` with the native
  library at `native/linux-x64/libducktape_laya.so` (the loader already looks
  there). Maven Central also requires a sources jar, a javadoc jar, and `pom.xml`
  metadata: `url`, `licenses`, `developers` and `scm`. Add the jar to
  `tests/installed-packages.py`.
- .NET: build the `.nupkg` in `scripts/package-linux.py` with `dotnet pack -p:DucktapeLayaNativeLibrary=...`, which places the library at `runtimes/linux-x64/native/`. Without that property, `dotnet pack` succeeds but the package has no native library. Add a consumer that installs it from a local
  feed to `tests/installed-packages.py`. Add NuGet metadata: license expression,
  project URL, readme and description.
- Go: add a consumer test that builds against the release tarball's `include`
  and `lib` directories, not the checkout.
- Set `rust-version` in the crates; 0.1.0 declares none and was tested with 1.98.

## One-time maintainer setup

Each registry needs the maintainer's account. Prefer trusted publishing
(GitHub OIDC) over long-lived tokens where the registry supports it.

- **PyPI:** create the project through a pending trusted publisher for
  `adrian729/ducktape-laya` and the publishing workflow, or upload once with a
  scoped API token. PyPI requires 2FA on the account.
- **Maven Central:** register at the Central Portal and verify the
  `io.github.adrian729` namespace, which is automatic for a GitHub login. Central
  requires PGP signatures on every file, so create and publish a signing key.
  This is the only channel that requires signatures; the GitHub attestations do
  not replace them.
- **NuGet:** create a nuget.org account. Then either add a trusted publishing
  policy for the repository and workflow, or create an API key scoped to
  `DucktapeLaya`.
- **Go:** no account. Pushing the tag `bindings/go/vX.Y.Z` (subdirectory module
  tags carry the path prefix) makes the module available through the proxy and
  pkg.go.dev.
- **npm, crates.io:** already set up. Both also support trusted publishing; npm
  then no longer needs an OTP for each publish.

## Recommended: publish from CI

Add a `publish-release` workflow like `attest-release`. It would download the
assets of a published GitHub release, check them against `SHA256SUMS` and a
`release_ready` `gates.json`, then upload those exact files with trusted
publishing: npm and PyPI files as they are, the `.nupkg` as built, and the crates through `cargo publish` from the tagged source (see above). The registries then serve the attested bytes, no maintainer token or OTP
is handled locally, and every upload is logged in Actions.

## Release-day order

1. Qualify the candidate: all gates in `gates.json` pass.
2. Tag `vX.Y.Z` (annotated) at the candidate and push it.
3. Create the GitHub release with every artifact, `SHA256SUMS` and `gates.json`.
4. Run the `attest-release` workflow for the tag, then check one asset with
   `gh attestation verify`.
5. Publish to each registry from the release assets; publish the crates with `cargo publish` at the tag, `-sys` before the safe crate.
6. Push `bindings/go/vX.Y.Z` at the same commit.
7. Download every package from its registry into a clean directory, check its bytes against `SHA256SUMS` and its attestation (crates, which `cargo publish` rebuilds, against the tagged source instead), and run a real-model consumer.
8. Update the README status and requirements tables, and the version in the README's install snippets.

## Converted models

The multilingual and typed-decisions GGUFs (`models/laya-multilingual.json`,
`models/laya-typed-decisions.json`) have no download URL: users build them with
`scripts/convert-model.py`. To make them downloadable, either:

- ask the Laya authors or ggml-org to publish official GGUFs, then switch the
  manifests to those files if they qualify; or
- upload the exact files the manifests pin (for example to a Hugging Face model
  repository under the maintainer's account, with the Apache-2.0 notice and a
  link to the source revision) and set each manifest's `url`.

## Leftovers from 0.1.0

- npm staged publishing left a `0.0.0-stage` placeholder on both npm packages.
  It is harmless (`latest` is 0.1.0); hide it with `npm deprecate`.
- The 0.1.0 artifacts include `release/compatibility.json` with
  `"status": "development"`. The field was removed afterwards; released files
  stay unchanged.
