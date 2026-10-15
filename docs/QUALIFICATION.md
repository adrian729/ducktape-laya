# Release qualification contract

`release/qualification.json` defines the full-product qualification target and
maps every required gate to implementation, reusable checks and acceptance
criteria. `release/compatibility.json` owns the required gate list; maintainer
checks reject drift between the two. These records are requirements, not a
claim that the listed platforms or gates have passed.

The first target is Linux x86-64 CPU with the declared AVX2-era ISA, Ubuntu 24.04
as the minimum userspace and Ubuntu 26.04 as the newer userspace. Native release
binaries must stay within the declared GLIBC/GLIBCXX floors. The pinned Rust,
Node, Electron and compiler versions must be tested against actual installed
packages. Chrome on Linux must qualify both WASM and WebGPU on the listed GPU;
experimental browser launch flags do not establish default-browser support.
Additional hardware, versions and platforms need their own qualification.

Record source/build, runtime/patch, model, fixture, toolchain, configuration,
platform and artifact identities for every gate, together with the command,
exit code and passed/failed/not-run status. Diagnostic/sanitized builds must be
explicitly linked to the same source and relevant configuration as shipped
artifacts. Missing evidence remains not run. A successful native runner alone
does not qualify the browser, platform matrix, security or publication.

The shared fixtures under `tests/fixtures` are the conformance and drift corpus for
every binding, backend and upstream comparison. They must cover each decision type
and calibration bucket, close probabilities, structured and text inputs, Unicode,
literal mask text, integer-like keys, a prompt near the token budget and every
supported schema projection. Reports identify the corpus by fixture hashes. Budget
overflow rejection is checked by focused tests, not inferred from it. Exact score
ties cannot be produced reliably through a model, so the tie rule (first option or
level), calibration bucket selection and confidence formulas are checked by the
model-free `decision-rules` test against the arithmetic the backend uses.

Routine stress lasts at least one hour; candidate soak lasts at least eight
hours. Fuzz each selected target for at least one hour in qualification runs.
Use at least three independent baseline runs, then freeze profile-specific
performance/resource thresholds before candidate evaluation. An unfilled budget
is an unmet gate. Fix correctness before tuning performance. Preserve exact
same-native-binary binding results with identical model/settings and the declared
same-backend upstream tolerances. Native ISA, WASM and WebGPU are distinct
numerical profiles. Cross-profile comparisons must report numeric and discrete
decision drift while retaining strict shapes, ordering and metadata; they do not
assert probability equivalence. Record runtime/model/compiler/settings/device and
artifact identities in addition to the diagnostic profile label.

All qualification gates must pass for the full product. Publication additionally
requires signed/attested artifact identity, verification through public consumer
channels and retained rollback artifacts. Do not equate artifact qualification
with successful publication. Run reports belong in CI/PR artifacts or ignored
local build directories; accepted contracts and reusable checks belong in source.

See [RELEASE_CHECKS.md](RELEASE_CHECKS.md) for current runners,
[COMPATIBILITY.md](COMPATIBILITY.md) for versioning, and
[SECURITY.md](../SECURITY.md) for trust boundaries and vulnerability reporting.
