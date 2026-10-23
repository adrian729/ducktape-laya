# Runtime and model upgrades

Run `python3 scripts/check-release.py` to verify the release record, runtime checkout/gitlink and package/model identities. Fetch a candidate commit into the runtime Git object database explicitly; the check never changes the working submodule or fetches a moving branch itself.

```sh
git -C third_party/llama.cpp fetch origin COMMIT
python3 scripts/check-upgrade.py --runtime COMMIT --model /path/to/pinned.gguf \
  --output build-upgrade-COMMIT --jobs 2
```

Tracked source changes must be committed first. The tool archives that exact source revision and each runtime into a new output directory. It verifies model bytes, builds both configurations, runs native/Rust/Node real-model suites and shared conformance, checks exports, and compares ordered results at the predeclared tolerance in `release/compatibility.json`. Commands, logs, build identities, relevant upstream diffs and a JSON report stay in that directory. Existing output directories are rejected; the approved checkout is untouched.

Use `--runtime` equal to the approved pin to replay a baseline. For a model candidate, additionally supply `--candidate-model` and `--candidate-manifest`. When both model and runtime change, the tool also runs each independent axis. Changed model answers require upstream/reference evaluation; a previous model's answers are not universal goldens. No expected outputs are overwritten.

The report distinguishes passed, failed and not-run gates. `release_ready` stays false: this entry point currently automates adapter/native checks, not the whole release matrix. Reference, packaged artifact, sanitizer/stress, browser, security and platform checks remain required before promotion. A success exit means the implemented checks passed; it does not authorize promotion. Promotion is a separate reviewed commit updating pins/manifests/versions together. Keeping the previous commit and artifacts provides rollback without mutating installed apps.

## Runtime patches and candidate identity

Runtime patches have an explicit hash-checked record. CMake applies them to build-directory source copies with zero fuzz, and refuses patch/context/source layout mismatches. The submodule is never edited by configuration. Runtime introspection includes the applied patch list; isolated candidate archives get their actual revision through `DUCKTAPE_LLAMA_REVISION`. A custom source directory without an explicit identity reports `unverified-custom-source`.

`release/compatibility.json` lists eight patches; [patches/README.md](../patches/README.md) explains each and its removal condition:

- `typed-cpu-callbacks.patch`: typed bridges for ggml CPU callbacks whose prototypes fail function-type sanitizer checks. Remove when upstream fixes the signatures and the strict sanitizer and unchanged-output checks pass.
- `wasm-q8-round-to-nearest.patch`: the WASM SIMD Q8_0/Q8_1 activation quantizers round to nearest like the reference and x86 paths (browser arithmetic only). Remove when upstream rounds these to nearest, after re-checking same-backend agreement and drift.
- `nlohmann-nothrow-destroy.patch`: JSON destruction no longer terminates the process when its stack allocation fails. Remove when the vendored nlohmann/json handles that and `real-faults` passes.
- `common-propagate-allocation-failure.patch`: JSON parsing and Jinja rendering rethrow `std::bad_alloc`, so the caller sees status 4. Remove when upstream propagates it and `real-faults` passes.
- `ggml-check-allocations.patch`: unchecked ggml allocations report allocation failure (thread pool) or abort (graph allocator, scheduler) instead of using NULL. Remove when upstream checks them and `real-faults` passes.
- `tokenizer-linear-partition.patch`: splitting text at special tokens takes linear instead of quadratic time, with identical tokens. Remove when upstream makes it linear and `tokenizer-partition` and `real-tokenizer` pass.
- `ggml-cpu-work-buffer.patch`: a failed CPU work-buffer allocation reports status 4 instead of leaving a freed buffer in use. Remove when upstream handles it and `ggml-faults` and `real-runtime-faults` pass.
- `ggml-thread-creation-failure.patch`: a refused CPU worker thread reports status 4 instead of aborting the process. Remove when upstream handles it and `ggml-faults` and `real-runtime-faults` pass.

On an upgrade, a patch that no longer applies fails configuration; inspect the upstream change and remove or adapt the patch explicitly. Patch applicability alone does not establish candidate compilation or compatibility. Every candidate must run the checks for its actual patched build.

The approved-runtime runner (`scripts/validate.py`) resets candidate source/revision overrides in reused CMake caches and verifies built diagnostics against the release record. Candidate evaluation uses `scripts/check-upgrade.py` instead. Retain run-specific reports as PR/CI artifacts or ignored local build outputs, not source documents. Commit the accepted pins, manifests and any migration instructions.
