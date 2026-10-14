# Validation and release checks

Run the native checks with one command. Output directories must be new so earlier
reports are not overwritten. Keep local outputs under ignored `build*/` paths
and attach relevant results to the commit, PR or CI run; do not commit run reports.
Models are supplied explicitly and checked against the pinned
manifest before real inference; the runner never downloads one.

```sh
python3 scripts/validate.py --output build-check-model-free
python3 scripts/validate.py --model /path/to/Laya-Q8_0.gguf \
  --packages --output build-check-native
python3 scripts/check-reproducibility.py --output build-repro --jobs 2
python3 tests/benchmark.py /path/to/Laya-Q8_0.gguf --report build/benchmark.json
```

The native runner configures/builds the library and addon, checks required CTest
registration, runs native/Rust/Node suites, and checks public exports. It also
runs artifact-identity and browser lifecycle/backend regressions. Reused candidate
source/revision overrides are cleared, and built runtime diagnostics must match
the approved compatibility record before any test gate is accepted. With a model,
it also compares C/C++/Rust/Node fixture outputs. `--packages` builds actual artifacts
and exercises installed consumers outside the checkout. `--reproducibility` adds two
independent builds; it is intentionally optional because it recompiles the runtime.

Every run retains commands, logs, exit codes and source/runtime/model/fixture/binary
identities. `report.json` distinguishes selected checks from release readiness.
Without a model, native real-model and binding-conformance gates stay `not_run`.
Sanitizer, stress, upstream-reference, browser and platform evidence remain separate;
the runner does not silently transplant historical results to a different binary.
`--require-release` exits unsuccessfully if any required gate is failed or unrun.
This runner currently covers a subset of release gates and cannot approve a release
by itself. A successful normal exit means its selected checks passed.

The GitHub workflow defines Ubuntu 24.04 model-free checks for Clang/Node 24 and
GCC/Node 26. Manual dispatch can explicitly enable the pinned model download and
installed-package checks. Workflow dependencies are pinned by commit; Rust and Node
versions are explicit. Consult the relevant GitHub Actions run for results;
workflow definitions alone are not release evidence. CI does not publish packages
or update dependencies.

A separate model-free CI job instruments the shared library with ASan/UBSan and
runs `tests/input-fuzz.cpp`: deterministic request/schema/config mutations, nesting
and size boundaries. It does not replace real-model sanitizer runs or claim
coverage-guided fuzzing. No sanitizer recovery or suppression is enabled.

The benchmark records model open, first request, repeated-request samples,
sequential request rate, close and peak process RSS. It verifies repeat outputs
and retains model/library/fixture/configuration identities. Run without competing
builds/inference; record the host conditions. A handful of laptop samples is an
exploratory measurement, not a universal latency or throughput promise. Store
measurements as CI/PR artifacts, not dated source documents.

Independent reproducibility compares the bytes, permission bits and symlink targets
of all installed native and Node files from a single captured working-tree snapshot
compiled in two separate source/build/install roots. Runtime source is archived from
its recorded commit and patches are hash checked. The report identifies dirty source
and hashes the actual snapshot. The guarantee is for the same toolchain and options;
it does not assert identical output across compilers or architectures. Packaging
determinism is a separate check for identical prebuilt inputs.

Before publication, require all applicable recorded gates, review current evidence
and numeric-profile limits, verify the security contact and release hosting, and
retain the prior artifacts for rollback. Publication and signing are explicit
maintainer actions. Browser/native probability equivalence currently fails its
original tolerance and must not be described as a passing gate.
