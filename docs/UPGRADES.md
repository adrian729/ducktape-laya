# Runtime and model upgrades

Updating llama.cpp or a Laya model is a repeatable maintenance operation, not a rewrite: the runtime revision and patches, each model artifact and the package versions have separate identities, and one tool builds and compares a candidate against the approved release.

## Principles

- **Separate identities.** `release/compatibility.json` is the one machine-readable record linking the runtime pin, the adapter, the model manifests, the package versions and the release targets; it is cross-checked against the submodule and the built artifacts (`scripts/check-release.py`), so pins are not copied by hand into bindings or build scripts. Each model has an immutable manifest: source checkpoint and revision, GGUF repository, revision and hash, and for converted models the converter revision, settings and source file hashes.
- **Independent updates.** Runtime and model updates are allowed independently. A new upstream commit does not imply a model replacement or a binding API change; an unknown combination is unvalidated, not automatically compatible.
- **Never overwrite the known-good release.** Candidates build in isolated checkouts from immutable inputs, previous artifacts and manifests are kept for rollback, and expected outputs are never regenerated silently.
- **Hold one axis fixed.** For a runtime update, hold the model fixed; for a model update, hold the runtime fixed when compatible; test the combination separately when both change.
- **Report, do not widen.** Each backend has a predeclared tolerance; exact-equality paths stay exact. A changed model may change answers: report that drift and the agreement with the original and upstream references, and do not treat the old model's answers as universal truth. Task-quality claims are outside this library.
- **Promotion is a reviewed commit** that updates pins, manifests, versions and notes together, after every required release gate has passed. Dependency monitoring may propose candidates; it never upgrades a runtime or model inside an installed application.

## Checking a candidate

Run `python3 scripts/check-release.py` to verify the release record, runtime checkout/gitlink and package/model identities. Fetch a candidate commit into the runtime Git object database explicitly; the check never changes the working submodule or fetches a moving branch itself.

```sh
git -C third_party/llama.cpp fetch origin COMMIT
python3 scripts/check-upgrade.py --runtime COMMIT --model /path/to/pinned.gguf \
  --output build-upgrade-COMMIT --compiler-launcher ccache
```

Tracked source changes must be committed first. The tool archives that exact source revision and each runtime into a new output directory. It verifies model bytes, builds both configurations, runs native/Rust/Node real-model suites and shared conformance, checks exports, and compares ordered results at the predeclared tolerance in `release/compatibility.json`. Commands, logs, build identities, relevant upstream diffs and a JSON report stay in that directory. Existing output directories are rejected; the approved checkout is untouched. Builds use Ninja and, by default, as many jobs as the CPUs and memory allow (`--jobs`). Pairs with the same runtime revision share one build: a later pair reconfigures the first pair's build directory, which recompiles nothing, and is recorded as `reused_build`. `--compiler-launcher ccache` compiles through ccache: the rehearsal builds are never shipped (the default is none).

For a release, the runtime-upgrade rehearsal runs in CI: the `qualify-upgrade` job of a `native.yml` dispatch with `mode=qualify` (`-f runtime=COMMIT`, default the pinned runtime) runs this command on a GitHub runner and keeps its report as the `upgrade` artifact ([RELEASE_CHECKS.md](RELEASE_CHECKS.md#long-and-instrumented-gates)). The model-upgrade rehearsal (`--candidate-model`) runs on the release host through `scripts/qualify-host.py`, because the typed-decisions model exists only there.

Use `--runtime` equal to the approved pin to replay a baseline. For a model candidate, additionally supply `--candidate-model` and `--candidate-manifest`. When both model and runtime change, the tool also runs each independent axis. Changed model answers require upstream/reference evaluation; a previous model's answers are not universal goldens. No expected outputs are overwritten.

The report distinguishes passed, failed and not-run gates. `release_ready` stays false: this entry point currently automates adapter/native checks, not the whole release matrix. Reference, packaged artifact, sanitizer/stress, browser, security and platform checks remain required before promotion. A success exit means the implemented checks passed; it does not authorize promotion. Promotion is a separate reviewed commit updating pins/manifests/versions together. Keeping the previous commit and artifacts provides rollback without mutating installed apps.

## Runtime patches and candidate identity

Runtime patches have an explicit hash-checked record. CMake applies them to build-directory source copies with zero fuzz, and refuses patch/context/source layout mismatches. The submodule is never edited by configuration. Runtime introspection includes the applied patch list; isolated candidate archives get their actual revision through `DUCKTAPE_LLAMA_REVISION`. A custom source directory without an explicit identity reports `unverified-custom-source`.

`release/compatibility.json` lists the patches with their hashes; [patches/README.md](../patches/README.md) explains each one and its removal condition.

On an upgrade, a patch that no longer applies fails configuration; inspect the upstream change and remove or adapt the patch explicitly. Patch applicability alone does not establish candidate compilation or compatibility. Every candidate must run the checks for its actual patched build.

The approved-runtime runner (`scripts/validate.py`) resets candidate source/revision overrides in reused CMake caches and verifies built diagnostics against the release record. Candidate evaluation uses `scripts/check-upgrade.py` instead. Retain run-specific reports as PR/CI artifacts or ignored local build outputs, not source documents. Commit the accepted pins, manifests and any migration instructions.
