# Publishing

Where each package is published, and what is still missing. Publication follows qualification (see [RELEASE_CHECKS.md](RELEASE_CHECKS.md)). The native tarball and the npm tarballs are uploaded exactly as the candidate qualified them. `cargo publish` cannot upload a prebuilt `.crate`: it packages the crates again from the tagged source, so the crates.io files are rebuilt from the qualified commit rather than copied, and their bytes are not checked against the inventory.

## Status

| Package | Channel | 0.1.0 | 0.2.0 (main) |
| --- | --- | --- | --- |
| Native tarball, SBOMs, inventories | GitHub Releases | Published, attested | Same process |
| `ducktape-laya` (Node.js) | npm | Published | Same process |
| `ducktape-laya-browser` | npm | Published | Same process |
| `ducktape-laya`, `ducktape-laya-sys` | crates.io | Published | Same process |
| `ducktape-laya` (Python wheel) | PyPI | — | `publish-release` workflow (trusted publishing) |
| `github.com/adrian729/ducktape-laya/bindings/go` | Go module proxy | — | No account needed; publishing is a tag. Tested against the release tarball |
| `io.github.adrian729:ducktape-laya` (Java) | Maven Central | — | `publish-release` workflow (signs at upload; token and key in the `release` environment) |
| `DucktapeLaya` (.NET) | NuGet | — | `publish-release` workflow (trusted publishing) |

All names above were unclaimed on 2026-10-19.

## Work before the next release

These are code changes; they need no accounts.

- Versions are already 0.2.0 in `release/compatibility.json` and every binding manifest. For later releases, bump them together: `check_record` rejects any mismatch, including the Python, Java and .NET versions.
- Write the release notes: give the unreleased section of [CHANGELOG.md](../CHANGELOG.md) its version and date, and use it for the GitHub release text.
- Python: the real wheel comes only from `scripts/python_wheel.py` (run by `scripts/package-linux.py`), which bundles the native library. Building `bindings/python` with its `pyproject.toml` alone (`pip wheel`, `python -m build`) produces a wheel without the library; never upload that.
- Java and .NET: `scripts/package-linux.py` builds `ducktape-laya-VERSION.jar`
  (library at `native/linux-x64/`), the sources and javadoc jars and the POM
  (`scripts/java_package.py`), and `DucktapeLaya.VERSION.nupkg` (library at
  `runtimes/linux-x64/native/`), all byte-reproducible.
  `tests/installed-packages.py` runs both, and a Go consumer built against the
  tarball, outside the checkout. A plain `dotnet pack` without
  `-p:DucktapeLayaNativeLibrary=...` produces a package without the library;
  never upload that. Maven Central requires the four Java files to be
  GPG-signed at upload.

## One-time maintainer setup

Each registry needs the maintainer's account. PyPI, NuGet and Maven Central are
published by the `publish-release` workflow from the GitHub environment
`release` (required reviewer: the maintainer; deployments only from `main` and
`v*` tags). The trusted publisher entries below must name exactly that
repository, workflow file and environment.

- **PyPI:** a pending trusted publisher (pypi.org, Account → Publishing) for
  project `ducktape-laya`, owner `adrian729`, repository `ducktape-laya`,
  workflow `publish-release.yml`, environment `release`. It creates the project
  at the first upload and does not reserve the name before that. PyPI requires
  2FA on the account.
- **NuGet:** a trusted publishing policy (nuget.org, user menu → Trusted
  Publishing) with owner `adrian729`, repository `ducktape-laya`, workflow
  `publish-release.yml`, environment `release`, and the nuget.org user name as
  the `NUGET_USER` variable of the `release` environment (not a secret). The
  policy becomes permanent at its first successful login; until then it expires
  after about a week and has to be re-activated.
- **Maven Central:** sign in to the Central Portal with GitHub, which verifies
  the `io.github.adrian729` namespace. Central has no OIDC publishing and
  requires a PGP signature on every file (the GitHub attestations do not replace
  it), so the `release` environment holds four secrets: `CENTRAL_USERNAME` and
  `CENTRAL_PASSWORD` (a Central Portal user token), `MAVEN_GPG_PRIVATE_KEY`
  (armored secret key) and `MAVEN_GPG_PASSPHRASE`. Publish the public key to
  `keyserver.ubuntu.com` so Central can verify the signatures. Set the secrets
  with `gh secret set NAME --env release`, which prompts for the value.
- **Go:** no account. Pushing the tag `bindings/go/vX.Y.Z` (subdirectory module
  tags carry the path prefix) makes the module available through the proxy and
  pkg.go.dev.
- **npm, crates.io:** already set up and published by the maintainer. Both also
  support trusted publishing and could move into `publish-release`; npm then no
  longer needs an OTP for each publish.

## The publish-release workflow

`publish-release` (run from `main`, input: the tag) builds nothing. Each job
downloads its files from the GitHub release with
`scripts/verify-release-assets.sh`, which checks them against `SHA256SUMS`, a
`release_ready` `gates.json` for the tagged commit and the `attest-release`
attestation, and then uploads exactly those bytes: the wheel to PyPI, the
`.nupkg` to NuGet, and the jar, sources jar, javadoc jar and POM to Maven
Central, signed and checksummed in the job. Each registry can be selected
separately, so a failed upload can be retried alone. Every job waits for
approval in the `release` environment.

## Release-day order

1. Qualify the candidate: all gates in `gates.json` pass (`scripts/check-candidate.py`).
2. Assemble the release assets from the qualified package directories:
   `python3 scripts/release-assets.py assemble --packages PACKAGES --browser-package BROWSER_PACKAGE --gates gates.json --output build/release-assets`.
   It refuses a `gates.json` that is not `release_ready` and package directories
   whose inventory is not the one `gates.json` qualified. The output is the full
   asset set: every artifact listed in the native inventory (native tarball, npm
   tarball, both `.crate` files, the Python wheel) and in the browser inventory
   (the browser npm tarball), the inventories as `native-inventory.json` and
   `npm-browser-inventory.json`, the SBOMs as `native-sbom.cdx.json` and
   `npm-browser-sbom.cdx.json`, `gates.json` and `SHA256SUMS`.
3. Tag `vX.Y.Z` (annotated) at the candidate and push it.
4. Create the GitHub release with exactly those files. The attestation step fails
   on any extra asset.
5. Run the `attest-release` workflow from `main` for the tag, then check one asset with
   `gh attestation verify` (see [SECURITY.md](../SECURITY.md)).
6. Run the `publish-release` workflow from `main` for the tag (PyPI, NuGet, Maven Central) and approve its jobs. Publish the npm tarballs from the release assets with `npm publish`, and the crates with `cargo publish` at the tag, `-sys` before the safe crate.
7. Push `bindings/go/vX.Y.Z` at the same commit.
8. Download every package from its registry into a clean directory, check its bytes against `SHA256SUMS` and its attestation (`scripts/verify-release-assets.sh` downloads and checks the release copies) (crates, which `cargo publish` rebuilds, against the tagged source instead), and run a real-model consumer.
9. Update the README status and requirements tables, and the version in the README's install snippets.

For the next release's rollback rehearsal, the published assets become the
last-known-good packages: `gh release download vX.Y.Z -D lkg-assets`, then
`python3 scripts/release-assets.py retain lkg-assets --output lkg-packages`
restores and verifies the native package directory that `tests/rollback.py`
takes as its first argument.

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
