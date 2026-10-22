# Agent instructions

## Releases

A release starts only when the maintainer asks for one in the current session.
Once asked, carry it out end to end without asking for confirmation at each
step, following [docs/RELEASE_CHECKS.md](docs/RELEASE_CHECKS.md) and the
release-day order in [docs/PUBLISHING.md](docs/PUBLISHING.md):

1. Qualify the candidate on this host until `scripts/check-candidate.py` reports
   every gate passed. Rerun steps that failed for environmental reasons (load,
   missing test dependencies) and record why; never loosen a check, budget or
   timeout to make a gate pass.
2. Assemble the assets, tag `vX.Y.Z` at the candidate, create the GitHub release,
   run `attest-release`, run `publish-release` for every registry, and push
   `bindings/go/vX.Y.Z`.
3. Verify each registry serves the qualified bytes, update the README, and
   report what was published.

Stop and report instead of publishing when a gate fails for a reason that is not
clearly environmental, or when anything would need a decision the maintainer has
not made. Never ask the maintainer for secrets or tokens.

Pitfalls on this host: the shell exports `CC=clang`, so set `CC=gcc CXX=g++` for
gcc builds; heavy parallel load makes the real-model ctest time out, so run the
long gates (soak, browser) apart from the timing-sensitive ones and run
performance on an idle machine.
