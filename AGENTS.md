# Agent instructions

## Releases

A release starts only when the maintainer asks for one in the current session. Once asked, carry it out end to end without asking for confirmation at each step, following [docs/RELEASING.md](docs/RELEASING.md): prepare the release commit, qualify the candidate until `scripts/check-candidate.py` reports every gate of every release target passed, assemble and check the assets, tag, create the GitHub release, run `attest-release` and `publish-release`, push `bindings/go/vX.Y.Z`, verify every registry and report. How each check runs is in [docs/RELEASE_CHECKS.md](docs/RELEASE_CHECKS.md).

Stop and report instead of publishing when a gate fails for a reason that is not clearly environmental, when the CI Linux x86-64 library or Node addon differs from this host's, when the candidate run is not on the candidate commit or its artifacts have expired, or when anything would need a decision the maintainer has not made ([stop rules](docs/RELEASING.md#stop-rules)). Rerun environmental failures and record why; never loosen a check, budget or timeout. Never ask the maintainer for secrets or tokens.

## This release host

- **Models.** The primary model is `~/.cache/huggingface/hub/models--ggml-org--Laya-GGUF/snapshots/22265007700297ba9e128297e82540cf28c5d7d4/Laya-Q8_0.gguf`; the additional ones are `laya-multilingual-f16.gguf` and `laya-typed-decisions-q8_0.gguf` in `build-models/` of the main checkout (untracked). If one is missing or fails its manifest hash, rebuild it, never edit a manifest: `python3 scripts/convert-model.py models/laya-multilingual.json build-models/laya-multilingual-f16.gguf --python PYTHON` (and `models/laya-typed-decisions.json build-models/laya-typed-decisions-q8_0.gguf --python PYTHON --quantize LLAMA_QUANTIZE`). `PYTHON` needs `third_party/llama.cpp/requirements/requirements-convert_hf_to_gguf.txt` in a throwaway venv, for example `uv venv --python /usr/bin/python3.12` (the host's default `python3` is 3.15, where the pinned torch did not install); `LLAMA_QUANTIZE` is `llama-quantize` built from `third_party/llama.cpp` with `CC=gcc CXX=g++`. About 2 minutes each.
- **Compilers.** The shell exports `CC=clang`; set `CC=gcc CXX=g++` for gcc builds (the release build is gcc, and its library must match CI's byte for byte).
- **Load.** Heavy parallel load makes the real-model ctest time out: run the long gates (soak, browser) apart from the timing-sensitive ones, and run performance on an idle machine.
- **Scope.** This host builds only Linux x86-64; every other target's packages and evidence come from the CI candidate run.
