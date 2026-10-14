# Runtime and model upgrades

Run `python3 scripts/check-release.py` to verify the release record, runtime
checkout/gitlink and package/model identities. Fetch a candidate commit into the
runtime Git object database explicitly; the check never changes the working
submodule or fetches a moving branch itself.

```sh
git -C third_party/llama.cpp fetch origin COMMIT
python3 scripts/check-upgrade.py --runtime COMMIT --model /path/to/pinned.gguf \
  --output build-upgrade-COMMIT --jobs 2
```

Tracked source changes must be committed first. The tool archives that exact
source revision and each runtime into a new output directory. It verifies model
bytes, builds both configurations, runs native/Rust/Node real-model suites and
shared conformance, checks exports, and compares ordered results at the
predeclared tolerance in `release/compatibility.json`. Commands, logs, build
identities, relevant upstream diffs and a JSON report stay in that directory.
Existing output directories are rejected; the approved checkout is untouched.

Use `--runtime` equal to the approved pin to replay a baseline. For a model
candidate, additionally supply `--candidate-model` and `--candidate-manifest`.
When both model and runtime change, the tool also runs each independent axis.
Changed model answers require upstream/reference evaluation; a previous model's
answers are not universal goldens. No expected outputs are overwritten.

The report distinguishes passed, failed and not-run gates. `release_ready` stays
false: this entry point currently automates adapter/native checks, not the whole
release matrix. Reference, packaged artifact, sanitizer/stress, browser, security
and platform checks remain required before promotion. A success exit means the
implemented checks passed; it does not authorize promotion. Promotion is a
separate reviewed commit updating pins/manifests/versions together. Keeping the
previous commit and artifacts provides rollback without mutating installed apps.

## Validated experiment

On 2026-10-14 the approved runtime `1e6f04a75e91da4bb66b5248f3fd4bdeff169d41`
and candidate `df18bee11e96557b07aa7b12db0976f3c7ac9165` were built from the
same immutable library source with the pinned English Q8_0 artifact. Native,
Rust and Node tests and all shared fixtures passed for both; public exports
matched, and maximum output difference was **zero**. No language binding edits
or pin promotion were needed. See [retained report](evidence/upgrade-20261014.json).

The relevant upstream diff introduces score-bias metadata and other decision
models. The existing GGUF is unchanged; this experiment does not approve future
model artifacts or imply every future upstream revision is compatible.

Runtime patches now have an explicit hash-checked record. CMake applies them to
build-directory source copies with zero fuzz, and refuses patch/context/source
layout mismatches. The submodule is never edited by configuration. Runtime
introspection includes the applied patch list; isolated candidate archives get
their actual revision through `DUCKTAPE_LLAMA_REVISION`. A custom source directory
without an explicit identity reports `unverified-custom-source`.

The current [callback-signature patch](../patches/README.md) addresses a strict
UBSan finding in the pinned runtime. A future upstream fix should remove this
patch after the same sanitizer and unchanged-output checks. The initial upgrade
report predates it; that historical report is not approval of a patched candidate.

The typed-callback patch also applies with zero fuzz to the previously evaluated
candidate `df18bee11e96557b07aa7b12db0976f3c7ac9165`; upstream line offsets are
reported in [the patch probe](evidence/candidate-patch-20261014.json). This only
checks patch context, not candidate compilation, arithmetic or sanitizer results.
The earlier unpatched candidate report does not approve a patched release.
