# Agent instructions

## Releases

A release starts only when the maintainer asks for one in the current session. Once asked, carry it out end to end without asking for confirmation at each step, following [docs/RELEASE_CHECKS.md](docs/RELEASE_CHECKS.md) and the release-day order in [docs/PUBLISHING.md](docs/PUBLISHING.md):

1. Qualify the candidate until `scripts/check-candidate.py` reports every gate of every release target in `release/compatibility.json` passed. This host builds only Linux x86-64; the packages for every target, and the Linux arm64 and Windows x64 (tier 2) evidence, come from one CI candidate run on the exact candidate commit:
   - Push the candidate (`main`'s head, or a `candidate/vX.Y.Z` branch at it), run `gh workflow run native.yml --ref BRANCH -f candidate=true`, wait with `gh run watch`, and rerun only failed jobs (`gh run rerun RUN --failed`) when the failure is environmental.
   - `python3 -I scripts/fetch-ci-evidence.py --candidate COMMIT --output build-candidate/ci --merge build-candidate/evidence.json` downloads and records it.
   - As soon as both exist, compare `library_sha256` and `addon_sha256` in `build-candidate/native/report.json` (this host's `validate.py`) and `build-candidate/ci/evidence-linux-x64/report.json`. They must be equal; the shipped Linux x86-64 packages are CI's.
   - Run the tier-1 gates on this host as before (the long ones can run while CI runs). The checks that take packages use the CI-assembled `build-candidate/ci/packages-assembled`; the drift of each tier-2 target is measured with `tests/compare-profiles.py`.
   - Rerun steps that failed for environmental reasons (load, missing test dependencies) and record why; never loosen a check, budget or timeout to make a gate pass.
2. Assemble the assets from `build-candidate/ci/packages-assembled` and the browser package, check them with `scripts/check-release-assets.sh`, tag `vX.Y.Z` at the candidate, create the GitHub release, run `attest-release`, run `publish-release` for every registry (PyPI receives one wheel per target), and push `bindings/go/vX.Y.Z`.
3. Verify each registry serves the qualified bytes, update the README, and report what was published.

Stop and report instead of publishing when a gate fails for a reason that is not clearly environmental, or when anything would need a decision the maintainer has not made. In particular, stop when the CI Linux x86-64 library or Node addon differs from this host's (not environmental: a toolchain or Node header difference), when the candidate run is not on the candidate commit, or when its artifacts have expired. Never ask the maintainer for secrets or tokens.

Pitfalls on this host: the shell exports `CC=clang`, so set `CC=gcc CXX=g++` for gcc builds; heavy parallel load makes the real-model ctest time out, so run the long gates (soak, browser) apart from the timing-sensitive ones and run performance on an idle machine.
