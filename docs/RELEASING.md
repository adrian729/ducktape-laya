# Releasing

The one release procedure, from version bump to verified registries. A release agent follows it end to end ([AGENTS.md](../AGENTS.md) says when to start and what to watch for on the release host). How each check runs and what evidence it produces is in [RELEASE_CHECKS.md](RELEASE_CHECKS.md); which gates each platform needs is in [QUALIFICATION.md](QUALIFICATION.md).

## Channels

| Package | Channel | 0.1.0 | 0.2.0 | 0.3.0 |
| --- | --- | --- | --- | --- |
| Native archive, SBOMs, inventories, `gates.json`, `SHA256SUMS` | GitHub Releases | Published, attested (all but `gates.json` and `SHA256SUMS`) | Published, attested (all but `gates.json` and `SHA256SUMS`) | Published, attested (all but `gates.json` and `SHA256SUMS`) |
| `ducktape-laya` (Node.js) | npm | Published | Published | Published |
| `ducktape-laya-browser` | npm | Published | Published | Published |
| `ducktape-laya`, `ducktape-laya-sys` | crates.io | Published | Published | Published |
| `ducktape-laya` (Python wheel) | PyPI | — | Published | Published |
| `github.com/adrian729/ducktape-laya/bindings/go` | Go module proxy | — | Published | Published |
| `io.github.adrian729:ducktape-laya` (Java) | Maven Central | — | Published | Published |
| `DucktapeLaya` (.NET) | NuGet | — | Published | Published |

0.1.0 and 0.2.0 carry Linux x86-64 only. From 0.3.0 every release target in `release/compatibility.json` is published (Linux x86-64 tier 1; Linux arm64 and Windows x64 tier 2). The macOS targets (`macos-arm64`, `macos-x64`, tier 2) are in the record and are published from the release after 0.3.0.

| Channel | Linux x86-64 | Linux arm64 | Windows x64 | macOS arm64 | macOS x64 |
| --- | --- | --- | --- | --- | --- |
| GitHub Releases | `-linux-x64.tar.gz` | `-linux-arm64.tar.gz` | `-windows-x64.zip` | `-macos-arm64.tar.gz` | `-macos-x64.tar.gz` |
| npm | `native/linux-x64/` | `native/linux-arm64/` | `native/win32-x64/` | `native/darwin-arm64/` | `native/darwin-x64/` |
| PyPI | `manylinux_2_38_x86_64` wheel | `manylinux_2_38_aarch64` wheel | `win_amd64` wheel | `macosx_14_0_arm64` wheel | `macosx_14_0_x86_64` wheel |
| Maven Central | `native/linux-x64/` in the jar | `native/linux-arm64/` | `native/windows-x64/` | `native/macos-arm64/` | `native/macos-x64/` |
| NuGet | `runtimes/linux-x64/native/` | `runtimes/linux-arm64/native/` | `runtimes/win-x64/native/` | `runtimes/osx-arm64/native/` | `runtimes/osx-x64/native/` |
| crates.io, Go proxy | source only; the native library comes from the platform's archive | same | same (the zip's import library) | same | same |

The npm package, the jar and the NuGet package are one file each and carry every platform.

## 1. Prepare the release commit

- Bump the version in `release/compatibility.json` and every binding manifest together; `python3 scripts/check-release.py` (`check_record`) rejects any mismatch, including the Python, Java and .NET versions.
- Give the `Unreleased` section of [CHANGELOG.md](../CHANGELOG.md) its version and date (`## X.Y.Z (YYYY-MM-DD)`); it becomes the GitHub release text (step 3.3). Leave an empty `## Unreleased` heading above it: the README, [API.md](API.md) and [COMPATIBILITY.md](COMPATIBILITY.md) link to `CHANGELOG.md#unreleased`, which `tests/docs-links.py` checks, and `tests/release-notes.py` uses the record version's section whenever it exists and `Unreleased` only while it has content. Point the section's migration link and [COMPATIBILITY.md](COMPATIBILITY.md)'s "Migrating from" section at the new version.
- Update the user docs that name the current release: the README's status, install table (Go `@vX.Y.Z`, Maven `:X.Y.Z`, the C archive link) and requirements, the "from the next release" wording in the README, [PLATFORMS.md](PLATFORMS.md), [PACKAGING.md](PACKAGING.md#package-artifacts), [PERFORMANCE.md](PERFORMANCE.md#package-sizes) and the package READMEs, and the release table above. `check_record` (run by `check-release.py`, `validate.py`, `package-native.py` and `check-candidate.py`) refuses a `package_version` that these do not name:
  - [PACKAGING.md](PACKAGING.md): `find_package(ducktape_laya X.Y ...)` and "does not satisfy `X.Y`" (the CMake package accepts only its own minor version);
  - [SECURITY.md](../SECURITY.md): "(currently X.Y.Z)";
  - the README: "**Current release: X.Y.Z**", `bindings/go@vX.Y.Z`, `io.github.adrian729:ducktape-laya:X.Y.Z`, `ducktape-laya-X.Y.Z-linux-x64.tar.gz` and every `releases/tag/v...` link; the Go and Java READMEs' install lines; the `releases/tag/v...` links of the Go and Rust READMEs and [PERFORMANCE.md](PERFORMANCE.md#package-sizes) ("The X.Y.Z release files");
  - [COMPATIBILITY.md](COMPATIBILITY.md#versions): a row `| X.Y.Z | ABI | libducktape_laya.so.N |`, and a "Migrating from P (ABI a) to X.Y.Z (ABI b)" section when the ABI changes;
  - [API.md](API.md): "what changed since the X.Y.Z release" (its `CHANGELOG.md#unreleased` link needs the empty `Unreleased` heading kept in step 1, which `tests/docs-links.py` checks);
  - the lines of [QUALIFICATION.md](QUALIFICATION.md) ("passed every gate for their candidates"), [BROWSER.md](BROWSER.md) ("passed the browser release checks") and [ARCHITECTURE.md](ARCHITECTURE.md) ("passed the browser gates") that name the qualified releases.

  Then look for the remaining mentions of the previous version and of the release being prepared, and update those that describe the current release: `git grep -nE '0\.2(\.0)?\b' -- '*.md' ':!CHANGELOG.md'` and `git grep -niE 'next release|next version|unreleased' -- '*.md' ':!CHANGELOG.md'` when releasing 0.3.0. These include the README's "Unreleased on main" bullet (remove it), the Linux arm64 and Windows x64 "from the next release" wording in the README, the package READMEs, [BUILDING.md](BUILDING.md), [PLATFORMS.md](PLATFORMS.md) and [COMPATIBILITY.md](COMPATIBILITY.md), the Chrome version that "the next release's" browser checks run on ([BROWSER.md](BROWSER.md), [PLATFORMS.md](PLATFORMS.md), [ARCHITECTURE.md](ARCHITECTURE.md), the browser package README), and the estimates in [ROADMAP.md](ROADMAP.md).
  - Re-read every measured number that names a candidate from the final candidate's evidence: the tier-2 drift (`build-candidate/drift-*.json`; in the changelog, [PLATFORMS.md](PLATFORMS.md#numerical-results-across-platforms), [QUALIFICATION.md](QUALIFICATION.md#release-targets-and-tiers)), the Windows known deviation ([COMPATIBILITY.md](COMPATIBILITY.md#known-deviations)) and the package sizes ([PERFORMANCE.md](PERFORMANCE.md#package-sizes), measured on the assembled packages).
- Run the model-free checks: `python3 scripts/validate.py --policy all --packages --output build-check-model-free` (the generic native checks, the packages and every policy check, `tests/docs-links.py` and `tests/release-notes.py` included). Before the candidate, the tree should also have passed `python3 scripts/verify.py --ci --all` ([AGENTS.md](../AGENTS.md#development-workflow)).
- Land these edits before the long gates start: editing `release/compatibility.json` is a library input change and invalidates reuse of hours-long evidence.

Only `scripts/package-native.py` builds publishable packages. A wheel built from `bindings/python/pyproject.toml` alone (`pip wheel`, `python -m build`) or a plain `dotnet pack` without `-p:DucktapeLayaNativeLibrary=...` has no native library; never upload those.

## 2. Qualify the candidate

Qualify until `scripts/check-candidate.py` reports every gate of every release target passed. The release host builds only Linux x86-64. Two CI dispatches on the exact candidate commit produce the rest: the candidate run (the packages of every target and the tier-2 evidence of Linux arm64, Windows x64, macOS arm64 and macOS x64) and the qualify run (sanitizers, the one-hour fuzz and the runtime-upgrade rehearsal). `scripts/qualify-host.py` runs this host's gates. `COMMIT` is the candidate's full SHA, `BRANCH` a branch whose head it is, `MODEL`, `ML` and `TD` the three model files ([AGENTS.md](../AGENTS.md#this-release-host) gives their paths on the release host).

**Cheap checks first.** Everything a long run needs and a short check can decide runs before the first long step; the 0.3.0 qualification found three such problems only after hours (a qualify run that rehearsed the pin against itself, a runtime patch that failed to configure at the chosen revision, a performance budget that failed 35 minutes into a 3.5 hour run). In this order, each in seconds to a minute or two:

- **Choose `RUNTIME`** (step 2 says how) and check the rehearsal axis: it is not the pin. The dispatch below refuses the pin, no value or a value that is not a full 40-hex SHA in seconds, and `qualify-host.py` refuses them too.
- **Configure-only rehearsal** at `RUNTIME`, on the host, before any dispatch: `python3 scripts/check-upgrade.py --runtime RUNTIME --model MODEL --configure-only --output /tmp/rehearsal-configure` (the runtime patches apply to a copy of that revision and `cmake/runtime_sources.cmake` covers every patched file; nothing is built, about a minute). A failure here is a change to `patches/` or `cmake/`, which is a library input: land it before the candidate is cut.
- **Dispatch the qualify run with the runtime** (step 2) as soon as the candidate's build jobs finish; do not start the host gates before it is running.
- **`qualify-host.py --dry-run --runtime RUNTIME`** repeats the plan check (`check-candidate.py --plan`), the runtime-axis check and the configure-only rehearsal against the candidate, with the other preflight checks.

1. **Candidate run.** Push the candidate: a `candidate/vX.Y.Z` branch at it, or `main`'s head (`gh workflow run` cannot target a SHA). Prefer the branch: a candidate dispatched on `main` runs beside that push's own full push profile, and the runners of the first jobs then took 38 to 126 s to start (candidate runs 40259848379 and 40253038726, 12.0 and 13.4 minutes) against 2 to 5 s for a run on a branch (40251619567, 10.5 minutes). Dispatch it and find its run:

   ```sh
   gh workflow run native.yml --ref BRANCH -f mode=candidate
   gh run list --workflow native.yml --branch BRANCH --event workflow_dispatch --limit 1   # RUN
   gh run view RUN --json jobs --jq '.jobs[] | select(.name | startswith("build-")) | "\(.name) \(.status) \(.conclusion)"'
   ```

   It takes about 10 to 15 minutes ([what it runs](RELEASE_CHECKS.md#release-targets-and-the-ci-candidate-run)). Rerun only failed jobs (`gh run rerun RUN --failed`), and only when the failure is environmental (an `evidence-<P>` job that reports "image changed: rerun" is, but rerunning only that job fails again on the same artifacts: rerun the whole run with `gh run rerun RUN`, or dispatch a new candidate run). Its evidence artifacts are kept for 30 days, its build tarballs 7.
2. **Qualify run.** Once the candidate's four `build-*` jobs have finished, dispatch the qualify run, so the candidate's shards get the runners first (GitHub runs at most 20 jobs at once):

   ```sh
   gh workflow run native.yml --ref BRANCH -f mode=qualify -f runtime=RUNTIME
   ```

   The plan step refuses, in seconds, a `runtime` that is missing, equal to the pin or not a full 40-hex SHA (`-f replay_baseline=true` replays the baseline on purpose and is never release evidence). `RUNTIME` is the runtime revision (full 40-hex SHA) the upgrade rehearsal (`qualify-upgrade`) moves to. It must differ from the pinned runtime: the `upgrade_rollback` gate requires a rehearsed runtime axis, and a replay of the pin fails that gate. Choose a recent upstream llama.cpp revision past the pin on which every runtime patch applies (`git -C third_party/llama.cpp fetch origin master`, then `git apply --check` of each patch in `release/compatibility.json` on a checkout of the revision; take the newest such revision on upstream's first-parent history). The rehearsal builds the candidate with the patches as recorded and compares its results with the pin's. Alternatively, run `scripts/check-upgrade.py --runtime RUNTIME` on the host ([upgrades](UPGRADES.md#checking-a-candidate)) and name its report as the `upgrade` evidence. The run takes about 62 minutes (61 minutes 24 seconds for the 0.3.0 candidate, the one-hour fuzz campaign being the longest job; [what it runs](RELEASE_CHECKS.md#long-and-instrumented-gates)).
3. **Prepare the host.** A clean checkout of the candidate (`git switch --detach COMMIT`, `git submodule update --init`; `--reference` fails against a shallow checkout), no host outputs in `build-candidate/` (it holds only the CI download of step 4, which may come before or after this step; `qualify-host.py` refuses existing outputs of its own), nothing else running. Have ready the pinned Emscripten SDK ([building](BUILDING.md#build-the-browser-package)), the Ubuntu root filesystems (`python3 tests/download-userspaces.py --output build-platforms`), and the last-known-good packages of the previous release: `gh release download vPREVIOUS -D build-candidate-lkg/assets` and `python3 scripts/release-assets.py retain build-candidate-lkg/assets --output build-candidate-lkg/packages`. The two npm test trees (`tests/typescript`, `tests/browser-automation`) need no step: the `qualify-host.py` preflight installs them with `npm ci --ignore-scripts`.
4. **Download the candidate run** once it has succeeded: `python3 -I scripts/fetch-ci-evidence.py --candidate COMMIT --output build-candidate/ci --merge build-candidate/evidence.json` ([details](RELEASE_CHECKS.md#release-targets-and-the-ci-candidate-run)).
5. **Run the host gates**, with the primary model and both additional models:

   ```sh
   python3 scripts/qualify-host.py --candidate COMMIT --dry-run --runtime RUNTIME --emsdk EMSDK --lkg-packages build-candidate-lkg/packages --rootfs build-platforms/ubuntu-24.04.5/rootfs --rootfs build-platforms/ubuntu-26.04.1/rootfs
   python3 scripts/qualify-host.py --candidate COMMIT --runtime RUNTIME --emsdk EMSDK --lkg-packages build-candidate-lkg/packages --rootfs build-platforms/ubuntu-24.04.5/rootfs --rootfs build-platforms/ubuntu-26.04.1/rootfs
   ```

   `--runtime RUNTIME` (the commit of the qualify dispatch) is required until the qualify run is downloaded, else the plan check fails with "no rehearsal runtime"; after that it is read from the upgrade report and the two must agree. Before any step, a preflight checks what the hours need, cheapest first, and `--dry-run` runs the same one: (1) in seconds the inputs below and `check-candidate.py --plan` (every evidence key the gates read is produced by the plan, the CI download or a later step, the reuse table is complete, the model axis exists), (2) in seconds the runtime axis (not the pin, present in `third_party/llama.cpp`), (3) in about a minute the configure-only rehearsal at that runtime (`check-upgrade.py --configure-only`, nothing built); a stage that fails stops the later ones. The inputs: the runtime submodule at the compatibility record's commit, the three models, `--emsdk` at its pin, `--lkg-packages` (with its `inventory.json`), every `--rootfs` and the downloaded CI run, each resolved to an absolute path and required explicitly (no silent fallback), and both npm trees (installed in a real run, reported by `--dry-run`). It also refuses a checkout that is not clean and at the candidate, and existing outputs: nothing is overwritten, and `--replace` moves the outputs of an earlier attempt, evidence included, to `build-candidate/superseded/STAMP/` and reruns. It runs, in order: the gcc evidence build and `validate.py --full` alone (about 30 minutes); performance on the idle machine (about 25 minutes; each benchmark first waits, for at most 15 minutes, until the CPU package is at most 75 C and the load at most 1, and its report records the wait and the CPU's temperature, frequency, throttle counts and load before, after each workload and after the run); then the three-hour soak (at least 1,000 rounds) pinned to its own physical cores, and on the other cores reproducibility, `model_upgrade`, the browser builds and both browser pages, `installed_release`, `userspaces`, `rollback` and the cross-CPU comparison. On this host the soak's rounds floor binds, so the whole run takes about 4.5 to 5 hours. It writes their keys into `build-candidate/evidence.json` (`native`, `native_matrix`, `performance`, `performance_<model id>`, `soak`, `stress`, `reproducibility`, `model_upgrade`, `browser_package`, `browser`, `browser_isolated`, `installed_release`, `userspaces`, `rollback`, `cross_cpu`) and exits non-zero if any step failed. A failing step prints a `***** FAILED: STEP` banner at once (stdout and stderr) and `build-candidate/qualify-status.json` (state, per-step state and seconds, `failed`, `stopped_because`; rewritten atomically) lets a watcher poll the run; a failed `native` build or a performance budget that still fails after its one re-measure ([rule](RELEASE_CHECKS.md#performance-budget-re-measure)) stops the run; rerun a failed environmental step by hand with the command `--dry-run` printed. `--reuse EVIDENCE` lets hours-long reports of an earlier candidate stand where the reuse rules allow.
6. **Compare the bytes** as soon as step 1 of `qualify-host.py` (`build-candidate/native/report.json`) and the CI evidence exist: `library_sha256` and `addon_sha256` in `build-candidate/native/report.json` and `build-candidate/ci/evidence-linux-x64/report.json` must be equal. The shipped Linux x86-64 packages are CI's, bound to the local qualification by their bytes. If they differ, dispatch the candidate run once more with `-f cache=off` (no compiler cache in any build): if its bytes match, the cache was at fault; record it, download that run instead (a new `--output`) and rerun `installed_release`, `userspaces`, `rollback` and the cross-CPU comparison on its packages ([commands](RELEASE_CHECKS.md#release-targets-and-the-ci-candidate-run)). If they still differ, stop ([stop rules](#stop-rules)).
7. **Add the qualify run** once it and `qualify-host.py` have finished. Every CI file must lie in the one downloaded directory that `ci-run.json` lists, and `--qualify` adds the qualify run's files to the step 4 download in place:

   ```sh
   python3 -I scripts/fetch-ci-evidence.py --candidate COMMIT --qualify --output build-candidate/ci --merge build-candidate/evidence.json
   ```

   It requires `build-candidate/ci` to be a download of the same candidate run and commit (it refuses another run, another commit or a file that changed), downloads only the qualify artifacts into `build-candidate/ci/qualify`, adds `sanitizers`, `fuzz` and `upgrade` (recorded under `qualify_run` in `ci-run.json`) and leaves the candidate files, and so the host's references into them, as they were. Repeating it with the same qualify run changes nothing; another qualify run needs a new `--output`.
8. **Drift.** Measure each tier-2 target's drift and add `linux-arm64:drift`, `windows-x64:drift`, `macos-arm64:drift` and `macos-x64:drift` to `build-candidate/evidence.json`:

   ```sh
   python3 tests/compare-profiles.py build-candidate/native/outputs.json build-candidate/ci/evidence-linux-arm64/outputs.json --target-platform linux-arm64 --report build-candidate/drift-linux-arm64.json
   python3 tests/compare-profiles.py build-candidate/native/outputs.json build-candidate/ci/evidence-windows-x64/outputs.json --target-platform windows-x64 --report build-candidate/drift-windows-x64.json
   python3 tests/compare-profiles.py build-candidate/native/outputs.json build-candidate/ci/evidence-macos-arm64/outputs.json --target-platform macos-arm64 --report build-candidate/drift-macos-arm64.json
   python3 tests/compare-profiles.py build-candidate/native/outputs.json build-candidate/ci/evidence-macos-x64/outputs.json --target-platform macos-x64 --report build-candidate/drift-macos-x64.json
   ```
9. **Check.** Write the security review ([format](RELEASE_CHECKS.md#security-review)), add it to `build-candidate/evidence.json` as `security_review`, and run `python3 scripts/check-candidate.py build-candidate/evidence.json --candidate COMMIT --report build-candidate/gates.json` until it is `release_ready`. Run it from the clean checkout of the candidate commit itself: `gates.json` records `checker_commit`, and a checker at any other commit (older or newer) makes the report not `release_ready`, and `scripts/release-gates.jq` refuses it. It reads the versioned documentation from the candidate's tree, not the checkout's. A gate also fails when a required test was skipped (a registered test that reports itself skipped, or a `SKIPPED:` line inside a passing one, recorded as `<test> (partly)`, such as the non-ASCII path check of `tests/native.cpp`), for every release target; rerun on a host where every check runs.

Rerun steps that failed for environmental reasons (machine load, a missing test dependency, a runner image change) and record why. Never loosen a check, budget or timeout to make a gate pass.

## 3. Assemble, tag and publish

1. Assemble the release assets and check them:

   ```sh
   python3 scripts/release-assets.py assemble --packages build-candidate/ci/packages-assembled \
     --browser-package BROWSER_PACKAGE --gates build-candidate/gates.json --output build/release-assets
   mkdir -p build/release-record && git archive COMMIT release | tar -x -C build/release-record
   scripts/check-release-assets.sh build/release-assets COMMIT vX.Y.Z build/release-record/release
   ```

   COMMIT is the full 40-hex SHA of the candidate commit (`git rev-parse HEAD` in the candidate checkout; the tag does not exist yet), not an abbreviation or a branch name.

   Assembly refuses a `gates.json` that is not `release_ready` or not schema 2, package directories whose inventory is not the one `gates.json` qualified or whose files differ from it, and native packages whose platforms are not the qualified targets. The output is the full asset set: every artifact in the native inventory (each target's archive and wheel, the multi-platform npm tarball, jar, sources and javadoc jars, POM and `.nupkg`, both `.crate` files) and in the browser inventory (the browser npm tarball), the inventories as `native-inventory.json` and `npm-browser-inventory.json`, the SBOMs as `native-sbom.cdx.json` and `npm-browser-sbom.cdx.json`, `gates.json` and `SHA256SUMS`.
2. Tag `vX.Y.Z` (annotated) at the candidate commit and push the tag.
3. Render the release text and create the GitHub release with exactly those files. The attestation step fails on any extra asset.

   ```sh
   python3 scripts/release-notes.py --commit vX.Y.Z --version X.Y.Z --output build/release-notes.md
   gh release create vX.Y.Z --verify-tag --title vX.Y.Z --notes-file build/release-notes.md build/release-assets/*
   ```

   `scripts/release-notes.py` writes an install table naming the version and every target's archive, the platforms by tier, which assets are attested and how to verify them, and then the version's changelog section with its relative links rewritten to `https://github.com/adrian729/ducktape-laya/blob/vX.Y.Z/...`, so they keep pointing at the released docs. `--commit vX.Y.Z` reads `CHANGELOG.md` and the record from the tag with `git show`, so the text is the tagged commit's whatever is checked out. It refuses a version other than the record's. `--section Unreleased` previews the text before the release commit (with the record's current version).
4. Run the `attest-release` workflow from `main` with the tag (`gh workflow run attest-release.yml --ref main -f tag=vX.Y.Z`). It refuses other refs and tags not of the form `vX.Y.Z`, resolves the tag through its tag ref (`scripts/tag-commit.sh`; an annotated tag is dereferenced, a branch of the same name is never used) and checks out that commit's `release/` directory. It runs `scripts/check-release-assets.sh` on the published assets: the tag names the version of the tagged commit's `release/compatibility.json`; every asset (hidden files included) is listed once in `SHA256SUMS` with matching bytes; `gates.json` passes `scripts/release-gates.jq` (release ready, produced by a clean checker at the tagged commit (`checker_commit` equals the tag commit), and exactly the gates of the tagged commit's record and policy, all passed: for schema 2 the record's `required_release_gates` and every release target with its tier and `required_gates`, tier 2 being every `target_gates` entry of the policy, plus the candidate run `ci_run`; schema 1 only for 0.1.0 and 0.2.0); the published inventories are the ones `gates.json` qualified and were packaged from the tagged commit on a clean tree; every artifact they list is published with its hash; the native platforms are exactly the qualified targets. Any asset outside those inventories fails the run. `scripts/check-release-run.py` then requires the native inventory to be byte-identical to the `packages-assembled` inventory of the run `gates.json` names, which must be a successful `workflow_dispatch` of `native.yml` on the tagged commit with every artifact of a candidate run (the zip is checked against GitHub's artifact digest, so run it within the 30 days the artifacts are kept). It then signs a release-qualification attestation for each asset. Check one asset with the `gh attestation verify` command in [SECURITY.md](../SECURITY.md#verify-a-download).
5. Run the `publish-release` workflow from `main` with the tag (`gh workflow run publish-release.yml --ref main -f tag=vX.Y.Z`; the `npm`, `crates`, `pypi`, `nuget` and `maven` inputs select registries, all on by default). See [the workflow](#the-publish-release-workflow).
6. Push the Go module tag `bindings/go/vX.Y.Z` at the same commit.

## 4. Verify and report

1. Download every package from its registry into a clean directory and check its bytes against `SHA256SUMS` and its attestation (`scripts/verify-release-assets.sh TAG OUTPUT_DIR ASSET...` downloads and checks the release copies). crates.io's checksum of each crate must be the released `.crate`'s sha256 (`cargo publish` repackages the crates, byte-identical to the released files after the `reproduce` check); nuget.org adds a repository signature, so compare the NuGet package entry by entry, apart from `.signature.p7s` (`scripts/registry-state.py` does all of these). PyPI must serve every target's wheel. Run a real-model consumer from the registry packages. The release host can do this for Linux x86-64 only; registry installs on the tier-2 targets are not checked yet ([roadmap](ROADMAP.md)).
2. Report what was published, with the CI run, the `gates.json` hash, the attestation run and each registry's version.
3. The published assets become the next release's last-known-good packages for the rollback rehearsal (step 2.3 above).

## Stop rules

Stop and report instead of publishing when:

- a gate fails for a reason that is not clearly environmental;
- the CI Linux x86-64 library or Node addon differs from the release host's even after one uncached redispatch of the candidate run (`-f cache=off`, step 2.6); a match in the uncached run identifies a compiler-cache fault, which is recorded and does not stop the release. A remaining difference is not environmental: a toolchain or Node header difference on one side (the scripts run ccache without `base_dir` and refuse binaries with unmapped source paths);
- the candidate run is not on the candidate commit, or its artifacts have expired (that needs a new candidate run);
- anything would need a decision the maintainer has not made.

Never ask the maintainer for secrets or tokens; the registries publish from the `release` environment.

## The publish-release workflow

`publish-release` (run from `main`, input: the tag) builds nothing. A first job, `reproduce`, checks the tag's form, resolves it through its tag ref, and repackages the crates from the tagged commit with the pinned toolchain, requiring them to be byte-identical to the released `.crate` files; every publish job waits for it, so a crate that does not reproduce stops the publication before any upload. Each job downloads its files from the GitHub release with `scripts/verify-release-assets.sh`, which checks them against `SHA256SUMS`, a `release_ready` `gates.json` for the tagged commit with exactly the gates and targets of that commit's release record and policy, all passed (`scripts/release-gates.jq`, as in `attest-release`; the tagged commit's `release/compatibility.json` and `release/qualification.json` are read through the contents API, and the tag must name the record's version), and the `attest-release` attestation, with the signer pinned exactly (the certificate identity `https://github.com/adrian729/ducktape-laya/.github/workflows/attest-release.yml@refs/heads/main`, its repository, `refs/heads/main`, a `workflow_dispatch` run on a GitHub-hosted runner). It then uploads exactly those bytes.

A failed run can be rerun with the same tag (`gh run rerun RUN --failed`, or a new dispatch). Before uploading, each job asks its registry what it holds for the version, skips a package that is there with the same contents, fails on one that is there with other contents, and uploads only what is missing (`scripts/registry-state.py`; PyPI: `scripts/pypi-missing-wheels.py`):

- **npm:** both tarballs. A version that exists must have the `dist.integrity` (sha512) and `dist.shasum` of the release tarball; npm serves uploaded tarballs unchanged.
- **PyPI:** every wheel the verified `native-inventory.json` lists (one per release target). The job does not use `skip-existing`, which would also pass over a wheel that is on PyPI with other bytes: `scripts/pypi-missing-wheels.py` asks PyPI's JSON API (`/pypi/ducktape-laya/VERSION/json`; 404 means nothing is published) what the version holds, skips a wheel that is there with the hash of the verified wheel (the retry case; `SHA256SUMS` is not attested, so it is not the reference), fails on a wheel there with another hash or on any other file of that version, and uploads only the missing wheels through trusted publishing.
- **NuGet:** the `.nupkg`. nuget.org adds its repository signature (`.signature.p7s`), so a published package never has the release file's hash: a version the flat-container index lists is downloaded and compared entry by entry, every entry but the signature equal. The push uses `--skip-duplicate`; when NuGet answers that the version exists although it was not listed yet (an earlier attempt's push still in validation), the job waits up to an hour for it to be listed and compares it the same way.
- **Maven Central:** the jar, sources jar, javadoc jar and POM, GPG-signed and checksummed in the job. A version whose four files `repo1.maven.org` serves with the release files' sha256 is skipped; some files missing or different fails. A version published but not yet on repo1 (Central can take up to an hour to sync) is uploaded again, which the Central Portal refuses without changing anything: rerun once repo1 serves it.
- **crates.io:** `cargo publish` cannot upload a prebuilt `.crate`, so the job repackages the crates from the tagged commit (as `reproduce` did), requires them to be byte-identical to the released `.crate` files, then publishes `-sys` first. A version that exists must have the released `.crate`'s sha256 as its crates.io checksum.

## Provenance

The attestation predicate (`release-qualification/v1`) records the release, the tag commit, the hash of `gates.json` and each target's tier. A valid attestation, signed by `attest-release.yml` as it is on `main`, proves that this workflow found, for this tag: every asset listed once in `SHA256SUMS` with matching bytes; a `gates.json` for the tag commit, from a clean checker, that reports as passed exactly the gates and targets which that commit's `release/compatibility.json` and `release/qualification.json` require; the published inventories to be the ones `gates.json` names, packaged from the tag commit, listing every other asset; and, from 0.3.0, the native inventory to be the `packages-assembled` inventory of the successful `native.yml` candidate run on the tag commit that `gates.json` names. It does not prove that the gates ran or that their evidence holds: `gates.json` is written by `scripts/check-candidate.py` on the maintainer's release host from the evidence there, and the workflow takes its gate results as written. Nor is it build provenance ([roadmap](ROADMAP.md#more-release-targets-and-release-provenance)).

The native files of 0.1.0 and 0.2.0 were built on the maintainer's release host, qualified there and attested as the qualified files. From 0.3.0, every target's files are built on GitHub-hosted runners in the candidate run, and the release host qualifies those exact files: for Linux x86-64 the candidate run's library and Node addon must be byte-identical to the release host's validated build, which binds the shipped bytes to the local qualification. Linux arm64, Windows x64, macOS arm64 and macOS x64 are qualified by the CI run alone, with fewer gates.

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

The multilingual and typed-decisions GGUFs (`models/laya-multilingual.json`, `models/laya-typed-decisions.json`) have no download URL: users build them with `scripts/convert-model.py` ([MODELS.md](MODELS.md)). To make them downloadable, either switch the manifests to official GGUFs if the Laya authors or ggml-org publish them and they qualify, or upload the exact files the manifests pin (for example to a Hugging Face model repository under the maintainer's account, with the Apache-2.0 notice and a link to the source revision) and set each manifest's `url`.

## Leftovers from 0.1.0

- npm staged publishing left a `0.0.0-stage` placeholder on both npm packages. It is harmless (`latest` points at the newest release); hide it with `npm deprecate`.
- The 0.1.0 artifacts include `release/compatibility.json` with `"status": "development"`. The field was removed afterwards; released files stay unchanged.
