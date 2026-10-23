# Release qualification contract

`release/qualification.json` defines the full-product qualification target and maps every required gate to implementation, reusable checks and acceptance criteria. `release/compatibility.json` owns the required gate list; maintainer checks reject drift between the two. These records are requirements, not a claim that the listed platforms or gates have passed. 0.1.0 and 0.2.0 passed every gate for their candidates (`gates.json` on each GitHub release; they predate targets, so they cover Linux x86-64 only); changes on main after 0.2.0, including the Linux arm64 and Windows x64 targets, are not qualified until the next release.

Tier 1 is Linux x86-64 CPU with the declared AVX2-era ISA, Ubuntu 24.04 as the minimum userspace and Ubuntu 26.04 as the newer userspace. Native release binaries must stay within the declared GLIBC/GLIBCXX floors. The pinned Rust, Node, Electron and compiler versions must be tested against actual installed packages. Chrome on Linux must qualify both WASM and WebGPU on the listed GPU; experimental browser launch flags do not establish default-browser support. Additional hardware, versions and platforms need their own qualification. Every model in `release/compatibility.json` must pass: the primary English model and each entry of `additional_models` (the multilingual and typed-decisions models) must pass the `original_reference` and `upstream_reference` gates.

## Release targets and tiers

`release/compatibility.json` lists the release `targets` (schema 2). Each names its platform, tier, CPU baseline and required CPU features, numerical profile (runtime ID, compiler, math library), models, build (where it is qualified, the workflow and runner that build it, the Node headers), known deviations, whether Electron is tested, and its required gates. Only targets in this record are release targets; a platform that merely builds is not one.

| Target | Tier | Qualified on | Runner | Compiler, math library |
| --- | --- | --- | --- | --- |
| `linux-x64` | 1 | the release host, with CI's byte-identical build | `ubuntu-24.04` | gcc, glibc |
| `linux-arm64` | 2 | CI | `ubuntu-24.04-arm` | gcc, glibc |
| `windows-x64` | 2 | CI | `windows-2025` | clang-cl, UCRT |

**Tier 1** (`linux-x64`) must pass every gate in `required_release_gates`, with the primary model and both additional models.

**Tier 2** is the set `release/qualification.json` `target_gates` defines, and a tier-2 target passes all of it in the CI candidate run: `native_contracts`, `binding_conformance`, `upstream_reference`, `original_reference`, `abi`, `installed_packages` (the target's own packages and the assembled release packages, Electron included), `artifact_integrity`, `reproducibility`, `short_stress` and `cross_profile_drift`. The differences from tier 1:

- Primary (English) model only. The multilingual and typed-decisions models are qualified on tier 1 only.
- `short_stress` runs `budgets.short_stress_seconds` (600 s) with the tier-1 resource limits, instead of the one-hour stress and eight-hour soak.
- No sanitizer, userspace-matrix, browser, performance-budget, security-review or rollback gates: ASan/TSan builds, the Ubuntu userspaces and the QEMU CPU guard are Linux x86-64 mechanisms, and there are no performance budgets for these targets yet.
- Drift against the tier-1 profile is reported with `tests/compare-profiles.py`, and any changed decision fails. Measured on the fixture corpus: Linux arm64 0.0334 maximum probability drift, Windows x64 0.0865, no changed decisions.
- `original_reference` allows only the deviations the target lists under `known_deviations`, each within its own cap (Windows: `json_escaping/spam`, 0.06; see [COMPATIBILITY.md](COMPATIBILITY.md#known-deviations)).

**Evidence is produced by CI.** One candidate run (`native.yml` dispatched with `candidate=true`) on the exact candidate commit validates, packages, assembles and installs every target and runs the tier-2 stress and reproducibility checks. `scripts/fetch-ci-evidence.py` downloads it, checks every artifact against GitHub's digest and records every file's hash in `ci-run.json`; `scripts/check-candidate.py` then checks each target offline and writes `gates.json` (schema 2), whose `targets` section gives each target's tier, gate statuses and CI run. For tier 1, the release host still runs its own gates, and the CI Linux x86-64 library and Node addon must be byte-identical to the host's validated build, so the shipped Linux x86-64 packages are CI's and bound to the local qualification by their bytes. Procedure: [RELEASE_CHECKS.md](RELEASE_CHECKS.md#release-targets-and-the-ci-candidate-run).

**Promotion.** A tier-2 target becomes tier 1 after one release in which it passes all tier-1 gates. Windows needs a tier-1 gate set defined first, because TSan, the Ubuntu userspaces and the QEMU guard do not apply to it. macOS arm64 is built and tested in CI but is not a target yet; it would enter as tier 2 ([ROADMAP.md](ROADMAP.md)).

Record source/build, runtime/patch, model, fixture, toolchain, configuration, platform and artifact identities for every gate, together with the command, exit code and passed/failed/not-run status. Diagnostic/sanitized builds must be explicitly linked to the same source and relevant configuration as shipped artifacts. Missing evidence remains not run. A successful native runner alone does not qualify the browser, platform matrix, security or publication.

The shared fixtures under `tests/fixtures` are the conformance and drift corpus for every binding, backend and upstream comparison. They must cover each decision type and calibration bucket, close probabilities, structured and text inputs, Unicode, literal mask text, integer-like keys, a prompt near the token budget and every supported schema projection. Reports identify the corpus by fixture hashes. Overflow handling (rejection by default, truncation, scan windows and the head budget) is checked by focused tests, not inferred from it. Exact score ties cannot be produced reliably through a model, so the tie rule (first option or level), calibration bucket selection and confidence formulas are checked by the model-free `decision-rules` test against the arithmetic the backend uses.

Routine stress lasts at least one hour; candidate soak lasts at least eight hours. Fuzz each selected target for at least one hour in qualification runs. Use at least three independent baseline runs, then freeze profile-specific performance/resource thresholds before candidate evaluation. An unfilled budget is an unmet gate. Fix correctness before tuning performance. Preserve exact same-native-binary binding results with identical model/settings and the declared same-backend upstream tolerances. Native ISA, WASM and WebGPU are distinct numerical profiles. Cross-profile comparisons must report numeric and discrete decision drift while retaining strict shapes, ordering and metadata; they do not assert probability equivalence. Record runtime/model/compiler/settings/device and artifact identities in addition to the diagnostic profile label.

All qualification gates must pass for the full product. Publication additionally requires signed/attested artifact identity, verification through public consumer channels and retained rollback artifacts. Do not equate artifact qualification with successful publication. Run reports belong in CI/PR artifacts or ignored local build directories; accepted contracts and reusable checks belong in source.

See [RELEASE_CHECKS.md](RELEASE_CHECKS.md) for current runners, [COMPATIBILITY.md](COMPATIBILITY.md) for versioning, and [SECURITY.md](../SECURITY.md) for trust boundaries and vulnerability reporting.
