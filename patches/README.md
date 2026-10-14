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
