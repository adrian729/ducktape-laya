# Validation and release checks

The complete target matrix, gate mapping and measurement requirements are in
[QUALIFICATION.md](QUALIFICATION.md) and `release/qualification.json`. Those are
acceptance requirements; the runners below do not yet cover every required gate.

Run the native checks with one command. Output directories must be new so earlier
reports are not overwritten. Keep local outputs under ignored `build*/` paths
and attach relevant results to the commit, PR or CI run; do not commit run reports.
Models are supplied explicitly and checked against the pinned
manifest before real inference; the runner never downloads one.

```sh
npm ci --ignore-scripts --prefix tests/typescript
python3 scripts/validate.py --output build-check-model-free
python3 scripts/validate.py --model /path/to/Laya-Q8_0.gguf \
  --packages --upstream-reference --output build-check-native
python3 scripts/check-reproducibility.py --output build-repro --jobs 2
python3 tests/benchmark.py /path/to/Laya-Q8_0.gguf --report build/benchmark.json
```

The native runner configures/builds the library and addon, checks required CTest
registration, runs native/Rust/Node suites, and checks public exports. It also
runs compiled C/C++/Rust ABI-layout checks, TypeScript consumers in CommonJS,
ESM and bundler modes, artifact-identity and browser lifecycle/backend regressions.
TypeScript test tools are development-only and installed explicitly with the
locked command above. `tests/typescript/check.py --node-package NPM_TARBALL
--browser-package BROWSER_TARBALL` additionally checks extracted package declarations.
Reused candidate
source/revision overrides are cleared, and built runtime diagnostics must match
the approved compatibility record before any test gate is accepted. With a model,
it also compares C/C++/Rust/Node fixture outputs. `--packages` builds actual artifacts
and exercises installed consumers outside the checkout. `--reproducibility` adds two
independent builds; it is intentionally optional because it recompiles the runtime.

Every run retains commands, logs, exit codes and source/runtime/model/fixture/binary
identities. `report.json` distinguishes selected checks from release readiness.
Without a model, native real-model and binding-conformance gates stay `not_run`.
`--upstream-reference` builds a separate test executable using upstream primitive
processing and compares primitive fixtures, including batch members, without an
HTTP server. It never links that processor into the product library. Structured
projection remains covered by the shared-core contracts. Without this option,
the upstream-reference gate stays not run.
Sanitizer, stress, browser and platform evidence remain separate;
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

The native stress target supports a bounded round smoke test and elapsed-time
qualification runs. Configure first, then explicitly build this optional target:

```sh
cmake -S . -B build -DDUCKTAPE_BUILD_TESTS=ON
cmake --build build --target laya-stress --parallel 2
build/laya-stress /path/to/Laya-Q8_0.gguf 10
build/laya-stress /path/to/Laya-Q8_0.gguf --seconds 3600
# Candidate soak uses --seconds 28800 instead.
```

Each duration run completes at least ten rounds, then runs the concurrent-caller
check. It exercises open/use/close, malformed input, deadline recovery and mixed
batches with two inference threads. After every close, descriptors/threads must
return to baseline and RSS must stay within the declared 128 MiB allocator
retention allowance. Progress goes to stderr; the final JSON on stdout includes
elapsed time and resource measurements. A short smoke run does not satisfy the
one-hour or eight-hour gates, and this native test does not cover browser memory.

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
maintainer actions. Each numerical profile must pass strict same-backend upstream
checks. Cross-profile comparisons report probability and decision drift and must
not be described as probability equivalence.
