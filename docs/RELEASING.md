# Releasing

The one release procedure, from version bump to verified registries. A release agent follows it end to end ([AGENTS.md](../AGENTS.md) says when to start and what to watch for on the release host). How each check runs and what evidence it produces is in [RELEASE_CHECKS.md](RELEASE_CHECKS.md); which gates each platform needs is in [QUALIFICATION.md](QUALIFICATION.md).

## Channels

| Package | Channel | 0.1.0 | 0.2.0 |
| --- | --- | --- | --- |
| Native archive, SBOMs, inventories, `gates.json`, `SHA256SUMS` | GitHub Releases | Published, attested | Published, attested |
| `ducktape-laya` (Node.js) | npm | Published | Published |
| `ducktape-laya-browser` | npm | Published | Published |
| `ducktape-laya`, `ducktape-laya-sys` | crates.io | Published | Published |
| `ducktape-laya` (Python wheel) | PyPI | — | Published |
| `github.com/adrian729/ducktape-laya/bindings/go` | Go module proxy | — | Published |
| `io.github.adrian729:ducktape-laya` (Java) | Maven Central | — | Published |
| `DucktapeLaya` (.NET) | NuGet | — | Published |

0.1.0 and 0.2.0 carry Linux x86-64 only. From the next release every release target in `release/compatibility.json` is published (Linux x86-64 tier 1; Linux arm64 and Windows x64 tier 2); macOS arm64 is built in CI but not released.

| Channel | Linux x86-64 | Linux arm64 | Windows x64 |
| --- | --- | --- | --- |
| GitHub Releases | `-linux-x64.tar.gz` | `-linux-arm64.tar.gz` | `-windows-x64.zip` |
| npm | `native/linux-x64/` | `native/linux-arm64/` | `native/win32-x64/` |
| PyPI | `manylinux_2_38_x86_64` wheel | `manylinux_2_38_aarch64` wheel | `win_amd64` wheel |
| Maven Central | `native/linux-x64/` in the jar | `native/linux-arm64/` | `native/windows-x64/` |
| NuGet | `runtimes/linux-x64/native/` | `runtimes/linux-arm64/native/` | `runtimes/win-x64/native/` |
| crates.io, Go proxy | source only; the native library comes from the platform's archive | same | same (the zip's import library) |

The npm package, the jar and the NuGet package are one file each and carry every platform.

## 1. Prepare the release commit

- Bump the version in `release/compatibility.json` and every binding manifest together; `python3 scripts/check-release.py` (`check_record`) rejects any mismatch, including the Python, Java and .NET versions.
- Give the `Unreleased` section of [CHANGELOG.md](../CHANGELOG.md) its version and date; it becomes the GitHub release text.
- Update the user docs that name the current release: the README's status, install table (Go `@vX.Y.Z`, Maven `:X.Y.Z`, the C archive link) and requirements, the "from the next release" wording in the README, [PLATFORMS.md](PLATFORMS.md), [PACKAGING.md](PACKAGING.md#package-artifacts), [PERFORMANCE.md](PERFORMANCE.md#package-sizes) and the package READMEs, and the release table above. `check_record` (run by `check-release.py`, `validate.py`, `package-native.py` and `check-candidate.py`) refuses a `package_version` that these do not name:
  - [PACKAGING.md](PACKAGING.md): `find_package(ducktape_laya X.Y ...)` and "does not satisfy `X.Y`" (the CMake package accepts only its own minor version);
  - [SECURITY.md](../SECURITY.md): "(currently X.Y.Z)";
  - the README: "**Current release: X.Y.Z**", `bindings/go@vX.Y.Z`, `io.github.adrian729:ducktape-laya:X.Y.Z`, `ducktape-laya-X.Y.Z-linux-x64.tar.gz` and every `releases/tag/v...` link; the Go and Java READMEs' install lines; the `releases/tag/v...` links of the Go and Rust READMEs and [PERFORMANCE.md](PERFORMANCE.md#package-sizes) ("The X.Y.Z release files");
  - [COMPATIBILITY.md](COMPATIBILITY.md#versions): a row `| X.Y.Z | ABI | libducktape_laya.so.N |`, and a "Migrating from P (ABI a) to X.Y.Z (ABI b)" section when the ABI changes;
  - [API.md](API.md): "what changed since the X.Y.Z release" (its `CHANGELOG.md#unreleased` link needs an `Unreleased` heading, which `tests/docs-links.py` checks);
  - the lines of [QUALIFICATION.md](QUALIFICATION.md) ("passed every gate for their candidates"), [BROWSER.md](BROWSER.md) ("passed the browser release checks") and [ARCHITECTURE.md](ARCHITECTURE.md) ("passed the browser gates") that name the qualified releases.

  Then look for the remaining mentions of the previous version, for example `git grep -nE '0\.2(\.0)?\b' -- '*.md' ':!CHANGELOG.md'` when releasing 0.3.0, and update those that describe the current release.
- Run the model-free checks (`python3 scripts/validate.py --packages --output build-check-model-free`, which includes `tests/docs-links.py`).
- Land these edits before the long gates start: editing `release/compatibility.json` is a library input change and invalidates reuse of hours-long evidence.

Only `scripts/package-native.py` builds publishable packages. A wheel built from `bindings/python/pyproject.toml` alone (`pip wheel`, `python -m build`) or a plain `dotnet pack` without `-p:DucktapeLayaNativeLibrary=...` has no native library; never upload those.

## 2. Qualify the candidate

Qualify until `scripts/check-candidate.py` reports every gate of every release target passed. The release host builds only Linux x86-64; the packages of every target, and the Linux arm64 and Windows x64 (tier 2) evidence, come from one CI candidate run on the exact candidate commit.

1. Push the candidate: `main`'s head, or a `candidate/vX.Y.Z` branch at it (`gh workflow run` cannot target a SHA). Run `gh workflow run native.yml --ref BRANCH -f candidate=true`, wait with `gh run watch`, and rerun only failed jobs (`gh run rerun RUN --failed`), and only when the failure is environmental. Its artifacts are kept for 30 days.
2. Download and record it: `python3 -I scripts/fetch-ci-evidence.py --candidate COMMIT --output build-candidate/ci --merge build-candidate/evidence.json` ([details](RELEASE_CHECKS.md#release-targets-and-the-ci-candidate-run)).
3. Run the tier-1 gates on the release host ([the candidate check](RELEASE_CHECKS.md#the-candidate-check) lists every evidence name and its command). The long ones (soak, stress, sanitizers, fuzz, browser) can run while CI runs; run performance on an otherwise idle machine. The tier-1 gates need the primary model and both additional models (`--extra-model`).
4. As soon as both exist, compare `library_sha256` and `addon_sha256` in `build-candidate/native/report.json` (this host's `validate.py`) and `build-candidate/ci/evidence-linux-x64/report.json`. They must be equal: the shipped Linux x86-64 packages are CI's, bound to the local qualification by their bytes.
5. Run the package checks on the CI-assembled packages `build-candidate/ci/packages-assembled` (`installed_release`, `electron`, `userspaces`, `rollback`) and measure each tier-2 target's drift with `tests/compare-profiles.py` ([commands](RELEASE_CHECKS.md#release-targets-and-the-ci-candidate-run)). For the rollback rehearsal, make the last-known-good packages from the previous release: `gh release download vPREVIOUS -D build-candidate/lkg-assets` and `python3 scripts/release-assets.py retain build-candidate/lkg-assets --output build-candidate/lkg-packages`.
6. Write the security review ([format](RELEASE_CHECKS.md#security-review)), add every report to `build-candidate/evidence.json` and run `python3 scripts/check-candidate.py build-candidate/evidence.json --candidate COMMIT --report build-candidate/gates.json` until it is `release_ready`.

Rerun steps that failed for environmental reasons (machine load, a missing test dependency) and record why. Never loosen a check, budget or timeout to make a gate pass.

## 3. Assemble, tag and publish

1. Assemble the release assets and check them:

   ```sh
   python3 scripts/release-assets.py assemble --packages build-candidate/ci/packages-assembled \
     --browser-package BROWSER_PACKAGE --gates build-candidate/gates.json --output build/release-assets
   mkdir -p build/release-record && git archive COMMIT release | tar -x -C build/release-record
   scripts/check-release-assets.sh build/release-assets COMMIT vX.Y.Z build/release-record/release
   ```

   Assembly refuses a `gates.json` that is not `release_ready` or not schema 2, package directories whose inventory is not the one `gates.json` qualified or whose files differ from it, and native packages whose platforms are not the qualified targets. The output is the full asset set: every artifact in the native inventory (each target's archive and wheel, the multi-platform npm tarball, jar, sources and javadoc jars, POM and `.nupkg`, both `.crate` files) and in the browser inventory (the browser npm tarball), the inventories as `native-inventory.json` and `npm-browser-inventory.json`, the SBOMs as `native-sbom.cdx.json` and `npm-browser-sbom.cdx.json`, `gates.json` and `SHA256SUMS`.
2. Tag `vX.Y.Z` (annotated) at the candidate commit and push the tag.
3. Create the GitHub release with exactly those files and the changelog section as its text. The attestation step fails on any extra asset.
4. Run the `attest-release` workflow from `main` with the tag (`gh workflow run attest-release.yml --ref main -f tag=vX.Y.Z`). It refuses other refs and tags not of the form `vX.Y.Z`, resolves the tag through its tag ref (`scripts/tag-commit.sh`; an annotated tag is dereferenced, a branch of the same name is never used) and checks out that commit's `release/` directory. It runs `scripts/check-release-assets.sh` on the published assets: the tag names the version of the tagged commit's `release/compatibility.json`; every asset (hidden files included) is listed once in `SHA256SUMS` with matching bytes; `gates.json` passes `scripts/release-gates.jq` (release ready, produced by a clean checker for the tagged commit, and exactly the gates of the tagged commit's record and policy, all passed: for schema 2 the record's `required_release_gates` and every release target with its tier and `required_gates`, tier 2 being every `target_gates` entry of the policy, plus the candidate run `ci_run`; schema 1 only for 0.1.0 and 0.2.0); the published inventories are the ones `gates.json` qualified and were packaged from the tagged commit on a clean tree; every artifact they list is published with its hash; the native platforms are exactly the qualified targets. Any asset outside those inventories fails the run. `scripts/check-release-run.py` then requires the native inventory to be byte-identical to the `packages-assembled` inventory of the run `gates.json` names, which must be a successful `workflow_dispatch` of `native.yml` on the tagged commit with every artifact of a candidate run (the zip is checked against GitHub's artifact digest, so run it within the 30 days the artifacts are kept). It then signs a release-qualification attestation for each asset. Check one asset with the `gh attestation verify` command in [SECURITY.md](../SECURITY.md#verify-a-download).
5. Run the `publish-release` workflow from `main` with the tag (`gh workflow run publish-release.yml --ref main -f tag=vX.Y.Z`; the `npm`, `crates`, `pypi`, `nuget` and `maven` inputs select registries, all on by default). See [the workflow](#the-publish-release-workflow).
6. Push the Go module tag `bindings/go/vX.Y.Z` at the same commit.

## 4. Verify and report

1. Download every package from its registry into a clean directory and check its bytes against `SHA256SUMS` and its attestation (`scripts/verify-release-assets.sh TAG OUTPUT_DIR ASSET...` downloads and checks the release copies). crates.io's checksum of each crate must be the released `.crate`'s sha256 (`cargo publish` repackages the crates, byte-identical to the released files after the `reproduce` check); nuget.org adds a repository signature, so compare the NuGet package entry by entry, apart from `.signature.p7s` (`scripts/registry-state.py` does all of these). PyPI must serve every target's wheel. Run a real-model consumer from the registry packages. The release host can do this for Linux x86-64 only; registry installs on the tier-2 targets are not checked yet ([roadmap](ROADMAP.md)).
2. Report what was published, with the CI run, the `gates.json` hash, the attestation run and each registry's version.
3. The published assets become the next release's last-known-good packages for the rollback rehearsal (step 2.5 above).

## Stop rules

Stop and report instead of publishing when:

- a gate fails for a reason that is not clearly environmental;
- the CI Linux x86-64 library or Node addon differs from the release host's (not environmental: a toolchain or Node header difference on one side, or a build through a compiler cache whose `base_dir` made the source paths relative; the release scripts build without a cache and refuse binaries with unmapped source paths);
- the candidate run is not on the candidate commit, or its artifacts have expired (that needs a new candidate run);
- anything would need a decision the maintainer has not made.

Never ask the maintainer for secrets or tokens; the registries publish from the `release` environment.

## The publish-release workflow

`publish-release` (run from `main`, input: the tag) builds nothing. A first job, `reproduce`, checks the tag's form, resolves it through its tag ref, and repackages the crates from the tagged commit with the pinned toolchain, requiring them to be byte-identical to the released `.crate` files; every publish job waits for it, so a crate that does not reproduce stops the publication before any upload. Each job downloads its files from the GitHub release with `scripts/verify-release-assets.sh`, which checks them against `SHA256SUMS`, a `release_ready` `gates.json` for the tagged commit with exactly the gates and targets of that commit's release record and policy, all passed (`scripts/release-gates.jq`, as in `attest-release`; the tagged commit's `release/compatibility.json` and `release/qualification.json` are read through the contents API, and the tag must name the record's version), and the `attest-release` attestation, with the signer pinned exactly (the certificate identity `https://github.com/adrian729/ducktape-laya/.github/workflows/attest-release.yml@refs/heads/main`, its repository, `refs/heads/main`, a `workflow_dispatch` run on a GitHub-hosted runner). It then uploads exactly those bytes.

A failed run can be rerun with the same tag (`gh run rerun RUN --failed`, or a new dispatch). Before uploading, each job asks its registry what it holds for the version, skips a package that is there with the same contents, fails on one that is there with other contents, and uploads only what is missing (`scripts/registry-state.py`; PyPI: `scripts/pypi-missing-wheels.py`):

- **npm:** both tarballs. A version that exists must have the `dist.integrity` (sha512) and `dist.shasum` of the release tarball; npm serves uploaded tarballs unchanged.
- **PyPI:** every wheel the verified `native-inventory.json` lists (one per release target). The job does not use `skip-existing`, which would also pass over a wheel that is on PyPI with other bytes: `scripts/pypi-missing-wheels.py` asks PyPI's JSON API (`/pypi/ducktape-laya/VERSION/json`; 404 means nothing is published) what the version holds, skips a wheel that is there with the `SHA256SUMS` hash (the retry case), fails on a wheel there with another hash or on any other file of that version, and uploads only the missing wheels through trusted publishing.
- **NuGet:** the `.nupkg`. nuget.org adds its repository signature (`.signature.p7s`), so a published package never has the release file's hash: a version the flat-container index lists is downloaded and compared entry by entry, every entry but the signature equal. The push uses `--skip-duplicate`; when NuGet answers that the version exists although it was not listed yet (an earlier attempt's push still in validation), the job waits up to an hour for it to be listed and compares it the same way.
- **Maven Central:** the jar, sources jar, javadoc jar and POM, GPG-signed and checksummed in the job. A version whose four files `repo1.maven.org` serves with the release files' sha256 is skipped; some files missing or different fails. A version published but not yet on repo1 (Central can take up to an hour to sync) is uploaded again, which the Central Portal refuses without changing anything: rerun once repo1 serves it.
- **crates.io:** `cargo publish` cannot upload a prebuilt `.crate`, so the job repackages the crates from the tagged commit (as `reproduce` did), requires them to be byte-identical to the released `.crate` files, then publishes `-sys` first. A version that exists must have the released `.crate`'s sha256 as its crates.io checksum.

## Provenance

The attestation predicate (`release-qualification/v1`) records the release, the tag commit, the hash of `gates.json` and each target's tier. A valid attestation, signed by `attest-release.yml` as it is on `main`, proves that this workflow found, for this tag: every asset listed once in `SHA256SUMS` with matching bytes; a `gates.json` for the tag commit, from a clean checker, that reports as passed exactly the gates and targets which that commit's `release/compatibility.json` and `release/qualification.json` require; the published inventories to be the ones `gates.json` names, packaged from the tag commit, listing every other asset; and, from 0.3.0, the native inventory to be the `packages-assembled` inventory of the successful `native.yml` candidate run on the tag commit that `gates.json` names. It does not prove that the gates ran or that their evidence holds: `gates.json` is written by `scripts/check-candidate.py` on the maintainer's release host from the evidence there, and the workflow takes its gate results as written. Nor is it build provenance ([roadmap](ROADMAP.md#more-release-targets-and-release-provenance)).

The native files of 0.1.0 and 0.2.0 were built on the maintainer's release host, qualified there and attested as the qualified files. From the next release, every target's files are built on GitHub-hosted runners in the candidate run, and the release host qualifies those exact files: for Linux x86-64 the candidate run's library and Node addon must be byte-identical to the release host's validated build, which binds the shipped bytes to the local qualification. Linux arm64 and Windows x64 are qualified by the CI run alone, with fewer gates.

The `release` environment admits deployments from `main` only (a repository setting, not enforced by the workflow files). It has no required reviewer by the maintainer's choice: a release runs end to end once the maintainer asks for it. What stops unqualified bytes is then the checks `attest-release` makes before signing (which bind the published files to the qualification report and, for the native packages, to the CI candidate run, but take the report's gate results as written), the exact signer pin `publish-release` verifies before every upload, and the `main`-only environment; whoever can change `main` or produce a `gates.json` on the release host can pass them. The npm tarballs are uploaded exactly as attested. crates.io holds the released `.crate` files byte for byte: `publish-release` repackages them from the tag and requires byte-identical files before `cargo publish` (the 0.1.0 and 0.2.0 crates.io checksums also equal the released files).

## One-time registry setup

Every registry is published by `publish-release` from the GitHub environment `release`. The trusted publisher entries must name exactly repository `adrian729/ducktape-laya`, workflow `publish-release.yml` and environment `release`. All of this is in place for 0.2.0; it is recorded here for a new registry or account.

- **PyPI:** a trusted publisher (pypi.org, project settings → Publishing) for project `ducktape-laya`. PyPI requires 2FA on the account.
- **NuGet:** a trusted publishing policy (nuget.org, user menu → Trusted Publishing) with owner `adrian729`, and the nuget.org user name as the `NUGET_USER` variable of the `release` environment (not a secret).
- **Maven Central:** the Central Portal account, signed in with GitHub, verifies the `io.github.adrian729` namespace. Central has no OIDC publishing and requires a PGP signature on every file (the GitHub attestations do not replace it), so the `release` environment holds four secrets: `CENTRAL_USERNAME` and `CENTRAL_PASSWORD` (a Central Portal user token), `MAVEN_GPG_PRIVATE_KEY` (armored secret key) and `MAVEN_GPG_PASSPHRASE`. The public key is on `keyserver.ubuntu.com` so Central can verify the signatures. Secrets are set with `gh secret set NAME --env release`, which prompts for the value.
- **Go:** no account. Pushing the tag `bindings/go/vX.Y.Z` (subdirectory module tags carry the path prefix) makes the module available through the proxy and pkg.go.dev.
- **npm:** a trusted publisher (package settings → Trusted Publisher → GitHub Actions) on both `ducktape-laya` and `ducktape-laya-browser`. npm then needs no token or OTP.
- **crates.io:** a trusted publisher (crate settings → Trusted Publishing) on both `ducktape-laya-sys` and `ducktape-laya`. "Require trusted publishing for all new versions" can be enabled on both.

## Stable documentation paths

Published package pages (npm, PyPI, crates.io, NuGet, pkg.go.dev) are frozen at release time and link to `https://github.com/adrian729/ducktape-laya/blob/main/...` and to README anchors. Those targets must keep working: never rename or delete these files or headings, only change their content, and leave a short pointer if content moves.

- `README.md#requirements`
- `docs/API.md`, `docs/BROWSER.md`, `docs/COMPATIBILITY.md`, `docs/MODELS.md`, `docs/PACKAGING.md` (with `#package-artifacts` and the old anchor `#npm-rust-and-python-artifacts`), `docs/PLATFORMS.md`, `docs/PERFORMANCE.md`, `docs/BUILDING.md` (with `#build-the-library` and `#build-the-packages`)
- `include/ducktape_laya.h`

`tests/docs-links.py` (part of `validate.py`) checks that every such link in the repository resolves, including anchors.

## Converted models

The multilingual and typed-decisions GGUFs (`models/laya-multilingual.json`, `models/laya-typed-decisions.json`) have no download URL: users build them with `scripts/convert-model.py` ([MODELS.md](MODELS.md)). To make them downloadable, either ask the Laya authors or ggml-org to publish official GGUFs and switch the manifests to those files if they qualify, or upload the exact files the manifests pin (for example to a Hugging Face model repository under the maintainer's account, with the Apache-2.0 notice and a link to the source revision) and set each manifest's `url`.

## Leftovers from 0.1.0

- npm staged publishing left a `0.0.0-stage` placeholder on both npm packages. It is harmless (`latest` is 0.2.0); hide it with `npm deprecate`.
- The 0.1.0 artifacts include `release/compatibility.json` with `"status": "development"`. The field was removed afterwards; released files stay unchanged.
