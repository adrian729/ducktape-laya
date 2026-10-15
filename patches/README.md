# Runtime patches

The source submodule remains clean at its declared commit. Patches are listed
with SHA-256 in `release/compatibility.json`; diagnostics and artifact inventories
report them. CMake copies only the affected source files into the build directory,
applies each patch with zero fuzz, and replaces those target sources. Hash,
context or target-layout mismatches fail configuration. `patch` is a build-time
tool; consumers do not need it. Candidate upgrade builds apply the same record.

## typed-cpu-callbacks.patch

The pinned ggml CPU code has incompatible indirect-call prototypes for Q8 row
dequantization, FP16/BF16 conversion and FP16/BF16 vector dot products. These
violate function-type sanitizer checks. The patch supplies typed
callback bridges for Q8 dequantization and the CPU F32/F16/BF16/I32 conversion/dot
entries. It preserves the called functions and arithmetic. It does not disable
sanitizers, alter model probabilities or claim to audit every other GGML type.

Removal condition: an upstream revision fixes these callback signatures and
passes the same strict ASan/UBSan real-model suite and unchanged-output checks.
On an update conflict, inspect the upstream fix and remove/adapt the patch
explicitly; never suppress or force an incompatible patch. Upstream submission
is a separate action and has not been performed.

## wasm-q8-round-to-nearest.patch

Browser builds use ggml's WASM SIMD CPU kernels (`EMSCRIPTEN_SYSTEM_PROCESSOR=wasm32`);
Emscripten's default `x86` processor makes ggml fall back to generic kernels, about
three times slower on the shared fixtures. The pinned WASM SIMD `quantize_row_q8_0`
and `quantize_row_q8_1` convert scaled activations with truncation toward zero,
while the scalar reference, x86 and the same file's `q8_K` path round to nearest.
The patch rounds to nearest (`wasm_f32x4_nearest`, ties to even as on x86) before
the existing saturating conversion. It changes browser WASM CPU arithmetic only;
native builds do not compile this file. Unpatched WASM kernels matched unchanged
upstream exactly but drifted up to 0.120 from native with a changed decision on a
close fixture; with the patch, drift on the same corpus is within the generic
build's range with no changed decisions. Same-backend upstream comparisons build
the reference from the same patched source, so they check our integration, not
this arithmetic choice.

Removal condition: an upstream revision rounds these quantizers to nearest (or
otherwise matches the reference); verify same-backend agreement and drift, then
remove. Upstream submission is a separate action and has not been performed.
