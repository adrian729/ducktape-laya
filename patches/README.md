# Runtime patches

The source submodule remains clean at its declared commit. Patches are listed with SHA-256 in `release/compatibility.json`; diagnostics and artifact inventories report them. CMake copies only the affected source files into the build directory, applies each patch with zero fuzz, and replaces those target sources. Patched vendored headers are placed ahead of upstream's include directory; the library fails to compile if it sees an unpatched header. Hash, context or target-layout mismatches fail configuration. `patch` is a build-time tool; consumers do not need it. Candidate upgrade builds apply the same record.

## typed-cpu-callbacks.patch

The pinned ggml CPU code has incompatible indirect-call prototypes for Q8 row dequantization, FP16/BF16 conversion and FP16/BF16 vector dot products. These violate function-type sanitizer checks. The patch supplies typed callback bridges for Q8 dequantization and the CPU F32/F16/BF16/I32 conversion/dot entries. It preserves the called functions and arithmetic. It does not disable sanitizers, alter model probabilities or claim to audit every other GGML type.

Removal condition: an upstream revision fixes these callback signatures and passes the same strict ASan/UBSan real-model suite and unchanged-output checks. On an update conflict, inspect the upstream fix and remove/adapt the patch explicitly; never suppress or force an incompatible patch. Upstream submission is a separate action and has not been performed.

## wasm-q8-round-to-nearest.patch

Browser builds use ggml's WASM SIMD CPU kernels (`EMSCRIPTEN_SYSTEM_PROCESSOR=wasm32`); Emscripten's default `x86` processor makes ggml fall back to generic kernels, about three times slower on the shared fixtures. The pinned WASM SIMD `quantize_row_q8_0` and `quantize_row_q8_1` convert scaled activations with truncation toward zero, while the scalar reference, x86 and the same file's `q8_K` path round to nearest. The patch rounds to nearest (`wasm_f32x4_nearest`, ties to even as on x86) before the existing saturating conversion. It changes browser WASM CPU arithmetic only; native builds do not compile this file. Unpatched WASM kernels matched unchanged upstream exactly but drifted up to 0.120 from native with a changed decision on a close fixture; with the patch, drift on the same corpus is within the generic build's range with no changed decisions. Same-backend upstream comparisons build the reference from the same patched source, so they check our integration, not this arithmetic choice.

Removal condition: an upstream revision rounds these quantizers to nearest (or otherwise matches the reference); verify same-backend agreement and drift, then remove. Upstream submission is a separate action and has not been performed.

## nlohmann-nothrow-destroy.patch

The vendored nlohmann/json destructor flattens nested values onto a heap-allocated stack to avoid deep recursion. That allocation can throw `std::bad_alloc` inside a `noexcept` destructor, which terminates the host process (found by the fault suite's allocation-failure injection). The patch catches that allocation failure and lets the remaining children be destroyed recursively, as they would be without the flattening. Request nesting is already bounded. It adds a marker macro that the library requires at compile time. No arithmetic or output changes.

Removal condition: the vendored nlohmann/json no longer allocates in destruction (or handles failure there) and `real-faults` passes unchanged.

## common-propagate-allocation-failure.patch

llama.cpp's JSON wrapper and Jinja renderer rewrap every exception as a generic error, and five Jinja `catch (...)` fallbacks would absorb an allocation failure and continue with a different value. The patch rethrows `std::bad_alloc` unchanged at those sites, so callers receive `DTL_OUT_OF_MEMORY` instead of an inference error and a render never continues after a failed allocation. Other exceptions keep their upstream handling. No arithmetic or output changes.

Removal condition: upstream propagates allocation failure from these sites and `real-faults` passes unchanged. Upstream submission has not been performed.

## ggml-check-allocations.patch

Several ggml allocations are used without a NULL check: the CPU thread pool that every graph computation creates, the graph allocator's chunk and dynamic-allocator records, and the backend scheduler's arrays (found by the fault suite's C allocation injection). Natively a failure there is a segmentation fault; in WebAssembly, where `malloc` returns NULL when memory cannot grow and address 0 is valid memory, it would silently write into low memory. The thread pool now returns NULL and `ggml_graph_compute` reports `GGML_STATUS_ALLOC_FAILED`, which llama.cpp returns from `llama_encode` and the library maps to `DTL_OUT_OF_MEMORY`. The other sites assert, matching ggml's existing handling of small allocation failure (`ggml_malloc` and `ggml_init` abort). No arithmetic or output changes.

Removal condition: upstream checks these allocations and `real-faults` passes unchanged. Upstream submission has not been performed.

## tokenizer-linear-partition.patch

Before tokenizing, llama.cpp splits text at every control, user-defined and unknown token (`tokenizer_st_partition`). For each match it found the fragment's position with `std::distance` from the list's start and erased it with `std::next` from the start again, so the split took time quadratic in the number of matches. The Laya vocabularies make ordinary text match often: the English and typed-decisions vocabularies have user-defined tokens for runs of 2 to 24 spaces, and the multilingual one for every newline. 64 KiB of `"x  "` took about 31 s, 1 MiB of log lines on the multilingual model minutes, and tokenization cannot be cancelled. The patch keeps the list element before the current fragment, so each split erases in constant time; the order of operations and the resulting fragments are the same. `tests/tokenizer.cpp` checks that token sequences equal those recorded with the unpatched runtime (`tests/fixtures/tokenizer-partition.txt`; upstream's vocabulary-only test files and the Laya models) and that time grows linearly. No arithmetic or output changes.

Removal condition: upstream makes the partition linear; `tokenizer-partition` and `real-tokenizer` pass unchanged. Upstream master (e1e3b2c40, 2026-10-23) still has the quadratic code. Upstream submission has not been performed.

## ggml-cpu-work-buffer.patch

The CPU backend keeps a work buffer between graph computations and grows it with `delete[]` followed by `new[]`. When the `new[]` threw, the backend kept the freed pointer and its old size: the next smaller computation wrote into freed memory, and a later larger one or closing the engine corrupted the heap (found by the audit with an injected allocation failure; realistic under `RLIMIT_AS` or the 4 GiB WebAssembly ceiling). The patch clears the pointer and size first and allocates with `new (std::nothrow)`, so the existing `GGML_STATUS_ALLOC_FAILED` path runs; llama.cpp returns it from `llama_encode` and the library reports `DTL_OUT_OF_MEMORY` (4). No arithmetic or output changes.

Removal condition: upstream handles the failed allocation; `ggml-faults` and `real-runtime-faults` pass unchanged. Upstream master (e1e3b2c40) still has the code. Upstream submission has not been performed.

## ggml-thread-creation-failure.patch

ggml creates the CPU worker threads for every graph computation (the library attaches no persistent thread pool) and asserted that `pthread_create` succeeded, so a refused thread (`RLIMIT_NPROC`, a container's pids limit) aborted the host process. The patch stops and joins the workers already started, frees the pool and returns NULL, which `ggml_graph_compute` already reports as `GGML_STATUS_ALLOC_FAILED` (`ggml-check-allocations.patch`); the caller gets status 4 and the engine stays usable. No arithmetic or output changes.

A persistent thread pool per engine (`ggml_threadpool_new` and `llama_attach_threadpool` at open) would report the failure at open and stop creating threads for every pass, but it was not adopted: its workers would not exist in a child process after `fork()`, so a host that forks after opening an engine (Python `multiprocessing` with the fork start method, the fault suite) would hang on the next call; every loaded router model would keep `threads - 1` idle threads; and it changes the threading the qualified results were measured with.

Removal condition: upstream handles thread creation failure; `ggml-faults` and `real-runtime-faults` pass unchanged. Upstream master (e1e3b2c40) still asserts. Upstream submission has not been performed.

## llama-embeddings-no-logits-buffer.patch

`llama_context::output_reserve` always reserved `n_vocab` logits per output token and filled them with zeros, although an embeddings context whose graph has no logits tensor never reads them. The Laya decision graph has none (its scores are read as embeddings) and every token is an output, so an engine kept `n_vocab × tokens × 4` bytes it never used: about 98 MiB per 512 tokens with the English and typed-decisions vocabularies and about 500 MiB per 512 tokens with the multilingual one (the audit measured 479 MiB less resident memory on the multilingual near-limit fixture). Upstream fixed this after the pinned revision in ed019cd8b (#30255) with a new `llama_context` member; the patch derives the same condition from the graph reserved at context creation instead, because a patched `llama-context.h` would change the class layout seen by every other llama.cpp source. Contexts that produce logits, and every context before its graph is reserved, keep the buffer as before. `real-features` checks that a near-limit request does not grow the resident set by anything close to the buffer's size. No arithmetic or output changes.

Removal condition: the pinned revision contains ed019cd8b; drop the patch and check that `real-features` passes unchanged.

## laya-selected-decision-head.patch

The ModernBERT decision graph evaluates the decision head (the last layers and the scorer) once for each of the three question types and concatenates the scores, because the question type is not a graph input; the library reads one column, so two of the three heads are wasted work. The patch adds a thread-local selection, set through `dtl_laya_select_decision_type` (library-internal: not in the exported symbols of any platform). With a type selected, the graph evaluates only that type's head and repeats its scores in all three columns, so the output keeps its `[3, n]` shape and `n_embd_out`; the selection is registered as a graph input without tensors whose `can_reuse` holds only for the same selection, so a graph built for one type is never reused for another. The library selects the question's type right before `llama_encode` and resets it to -1 afterwards; -1, the default and the selection when the context reserves its graph at open, evaluates every type as upstream does, so compute buffers are sized for the full graph and never grow. Scores are bit-identical: `real-decision-head` compares every column of each selected pass with the full graph's while switching types on one context, and the parity, upstream-reference and original-reference suites cover the results. The audit measured each pass about 12% faster with the English and typed-decisions models and 21 to 25% faster with the multilingual one, on a busy desktop. The browser builds compile the same patched source.

Removal condition: upstream makes the question type a graph input (or otherwise evaluates only the needed head) with identical scores; `real-decision-head` and the reference comparisons pass. Upstream submission has not been performed.

## tokenizer-stop-check.patch

Tokenizing 1 MiB of text takes seconds, and nothing in it checked the caller's cancellation or deadline. The patch adds a thread-local stop check, set through `dtl_llama_tokenize_set_stop` (library-internal: not in the exported symbols of any platform). When one is set, the BPE tokenizer calls it once every 1024 words, bigram insertions and merges, and the special-token split once for each special token and once every 1024 matches; when it reports a stop, tokenization throws `std::runtime_error`, which `llama_tokenize` propagates. Every tokenizer structure is local to the call, so nothing is left half-updated. The library sets the check only around its own tokenization of a call with a cancellation callback or deadline, reports the latched `DTL_CANCELLED` or `DTL_DEADLINE_EXCEEDED`, and keeps no partial tokens. The regular-expression pre-split and each word's symbol setup are not checked. Without a check, or while it reports no stop, tokens are identical. `real-control` checks that a deadline or cancellation at a tenth of a 1 MiB state's full tokenization time ends the call with its own status within half of that time, and that the engine is usable afterwards. No arithmetic or output changes.

Removal condition: upstream offers a way to interrupt tokenization; switch to it and check that `real-control` passes. Upstream submission has not been performed.
