// No C++ exception leaves the llama.cpp C functions the library calls (c-api-no-throw.patch,
// llama-encode-no-throw.patch, tokenizer-stop-check.patch). The Windows build (clang-cl /EHsc)
// assumes that a function declared extern "C" does not throw, so an exception leaving one is
// undefined behaviour there; on Linux it unwinds normally and the library's own catch would
// report it as DTL_OUT_OF_MEMORY, which hides it. This test calls the functions directly, fails
// one C++ allocation at a time (every point up to 16, then every 1.5x), and fails if anything
// is caught around a call: each must return its own failure value instead (NULL, a negative
// status or llama_tokenize's sentinels). The functions the library calls without a failure
// value must not allocate at all. It cannot see an exception that a C function inside
// llama.cpp lets out and llama.cpp catches further up; the patch's audit covers those.
// Usage: laya-c-api-fault-tests MODEL.gguf (Linux, uninstrumented builds)
#include "llama.h"
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <new>
#include <stdexcept>
#include <string>
#include <vector>

// Counted and failed on the calling thread only: ggml's worker threads do not allocate with new,
// and no other thread's allocation is attributed to the call under test.
extern "C" void *__libc_malloc(size_t);
static thread_local long fail_after = -1, allocations = 0, failed = 0;
void *operator new(std::size_t size) {
    ++allocations;
    if (fail_after >= 0 && fail_after-- == 0) { ++failed; throw std::bad_alloc(); }
    if (void *p = __libc_malloc(size ? size : 1)) return p;
    throw std::bad_alloc();
}
void *operator new[](std::size_t size) { return ::operator new(size); }
void operator delete(void *p) noexcept { std::free(p); }
void operator delete(void *p, std::size_t) noexcept { std::free(p); }
void operator delete[](void *p) noexcept { std::free(p); }
void operator delete[](void *p, std::size_t) noexcept { std::free(p); }

namespace {
void expect(bool ok, const std::string &message) { if (!ok) throw std::runtime_error(message); }

// Calls fn with the nth C++ allocation failing (n < 0: none, and failed keeps the count of the
// last injected call); anything caught fails the test.
template <class F> auto injected(const char *name, long n, F fn) -> decltype(fn()) {
    if (n >= 0) failed = 0;
    fail_after = n;
    try {
        auto result = fn();
        fail_after = -1;
        return result;
    } catch (const std::exception &e) {
        fail_after = -1;
        throw std::runtime_error(std::string(name) + ": allocation failure " + std::to_string(n) + " threw " + e.what());
    } catch (...) {
        fail_after = -1;
        throw std::runtime_error(std::string(name) + ": allocation failure " + std::to_string(n) + " threw a non-standard exception");
    }
}

// Fails each allocation point of call (which returns true once it succeeded) until a call
// succeeds without a failed allocation; ok checks the failure value. Returns the points tried.
template <class Call> long sweep(const char *name, Call call) {
    long points = 0;
    for (long n = 0;; n = n < 16 ? n + 1 : n * 3 / 2) {
        ++points;
        const bool succeeded = call(n);
        if (succeeded && !failed) break;
        expect(succeeded || failed, std::string(name) + ": failed at point " + std::to_string(n) + " without a failed allocation");
    }
    std::cerr << name << ": " << points << " allocation failure points, no exception\n";
    return points;
}

// The functions the library calls without a failure value: none may allocate.
template <class F> auto no_allocation(const char *name, F fn) -> decltype(fn()) {
    const long before = allocations;
    auto result = injected(name, -1, fn);
    const long made = allocations - before; // before building the message, which allocates
    expect(made == 0, std::string(name) + " allocated " + std::to_string(made) + " time(s)");
    return result;
}

llama_batch batch_of(std::vector<llama_token> &tokens, std::vector<llama_pos> &positions, std::vector<int32_t> &counts,
                     std::vector<llama_seq_id *> &ids, std::vector<int8_t> &outputs, llama_seq_id &sequence) {
    const size_t n = tokens.size();
    positions.resize(n); counts.assign(n, 1); ids.assign(n, &sequence); outputs.assign(n, 1);
    for (size_t i = 0; i < n; ++i) positions[i] = static_cast<llama_pos>(i);
    return llama_batch{static_cast<int32_t>(n), tokens.data(), nullptr, positions.data(), counts.data(), ids.data(), outputs.data()};
}
}

int main(int argc, char **argv) {
    try {
        expect(argc == 2, "usage: laya-c-api-fault-tests MODEL.gguf");
        const char *path = argv[1];
        // As the library does, once per process; the backend registry's first construction aborts on
        // allocation failure by design (c-api-no-throw.patch), so it is not injected.
        llama_log_set([](ggml_log_level, const char *, void *) {}, nullptr);
        llama_backend_init();
        auto mp = llama_model_default_params();
        mp.n_gpu_layers = 0;

        // Model loading: NULL on failure (the library reports status 2).
        sweep("llama_model_load_from_file", [&](long n) {
            llama_model *m = injected("llama_model_load_from_file", n, [&] { return llama_model_load_from_file(path, mp); });
            injected("llama_model_free", -1, [&] { llama_model_free(m); return 0; });
            return m != nullptr;
        });
        llama_model *model = llama_model_load_from_file(path, mp);
        expect(model != nullptr, "model load");

        // Metadata, vocabulary and token lookups the library makes while opening an engine.
        const int32_t count = no_allocation("llama_model_meta_count", [&] { return llama_model_meta_count(model); });
        expect(count > 0, "no metadata");
        for (int32_t i = 0; i < count; ++i) {
            char key[512], value[64];
            expect(no_allocation("llama_model_meta_key_by_index", [&] { return llama_model_meta_key_by_index(model, i, key, sizeof key); }) >= 0, "key");
            expect(no_allocation("llama_model_meta_val_str_by_index", [&] { return llama_model_meta_val_str_by_index(model, i, value, sizeof value); }) >= 0, "value");
        }
        no_allocation("llama_model_n_embd_out", [&] { return llama_model_n_embd_out(model); });
        no_allocation("llama_model_n_ctx_train", [&] { return llama_model_n_ctx_train(model); });
        const llama_vocab *vocab = no_allocation("llama_model_get_vocab", [&] { return llama_model_get_vocab(model); });
        const llama_token marker = no_allocation("llama_vocab_mask", [&] { return llama_vocab_mask(vocab); });
        const llama_token sep = no_allocation("llama_vocab_sep", [&] { return llama_vocab_sep(vocab); });
        expect(marker != LLAMA_TOKEN_NULL && sep != LLAMA_TOKEN_NULL, "Laya marker tokens");
        char piece[256];
        expect(no_allocation("llama_token_to_piece", [&] { return llama_token_to_piece(vocab, marker, piece, sizeof piece, 0, true); }) > 0, "mask piece");

        // Tokenization: tokens, or INT32_MIN + 2 (allocation) / + 3 (other) on failure.
        const std::string text = "The customer asked for a refund because the parcel arrived late.  Twice.\nLater again.";
        std::vector<llama_token> tokens(text.size() + 2);
        sweep("llama_tokenize", [&](long n) {
            const int32_t r = injected("llama_tokenize", n, [&] {
                return llama_tokenize(vocab, text.data(), static_cast<int32_t>(text.size()), tokens.data(), static_cast<int32_t>(tokens.size()), false, true);
            });
            constexpr int32_t low = std::numeric_limits<int32_t>::min();
            expect(r > 0 || r == low + 2 || r == low + 3, "llama_tokenize returned " + std::to_string(r));
            if (r > 0) tokens.resize(static_cast<size_t>(r));
            return r > 0;
        });

        // Context creation with the library's parameters: NULL on failure (status 2).
        auto cp = llama_context_default_params();
        cp.n_ctx = cp.n_batch = cp.n_ubatch = 128;
        cp.n_seq_max = 1;
        cp.n_threads = cp.n_threads_batch = 2;
        cp.embeddings = true;
        cp.pooling_type = LLAMA_POOLING_TYPE_NONE;
        cp.attention_type = LLAMA_ATTENTION_TYPE_NON_CAUSAL;
        cp.offload_kqv = false;
        cp.op_offload = false;
        sweep("llama_init_from_model", [&](long n) {
            llama_context *c = injected("llama_init_from_model", n, [&] { return llama_init_from_model(model, cp); });
            injected("llama_free", -1, [&] { llama_free(c); return 0; });
            return c != nullptr;
        });
        llama_context *ctx = llama_init_from_model(model, cp);
        expect(ctx != nullptr, "context");

        // A forward pass: 0, or -2 (allocation) / -3 (other) on failure (status 4 / 3); the context
        // stays usable. The prompt grows from one token so that the output buffer is reallocated.
        std::vector<llama_token> prompt{sep};
        prompt.insert(prompt.end(), tokens.begin(), tokens.end());
        prompt.push_back(marker);
        prompt.push_back(sep);
        std::vector<llama_pos> positions; std::vector<int32_t> counts; std::vector<llama_seq_id *> ids; std::vector<int8_t> outputs;
        llama_seq_id sequence = 0;
        std::vector<llama_token> first{sep};
        auto small = batch_of(first, positions, counts, ids, outputs, sequence);
        expect(llama_encode(ctx, small) == 0, "first pass");
        auto batch = batch_of(prompt, positions, counts, ids, outputs, sequence);
        sweep("llama_encode", [&](long n) {
            no_allocation("llama_get_memory", [&] { return llama_get_memory(ctx); });
            no_allocation("llama_set_abort_callback", [&] { llama_set_abort_callback(ctx, [](void *) { return false; }, nullptr); return 0; });
            const int status = injected("llama_encode", n, [&] { return llama_encode(ctx, batch); });
            no_allocation("llama_set_abort_callback", [&] { llama_set_abort_callback(ctx, nullptr, nullptr); return 0; });
            expect(status == 0 || status == -2 || status == -3, "llama_encode returned " + std::to_string(status));
            if (status != 0) return false;
            const float *row = no_allocation("llama_get_embeddings_ith", [&] { return llama_get_embeddings_ith(ctx, static_cast<int32_t>(prompt.size() - 2)); });
            expect(row != nullptr, "missing output row");
            return true;
        });

        injected("llama_free", -1, [&] { llama_free(ctx); return 0; });
        injected("llama_model_free", -1, [&] { llama_model_free(model); return 0; });
        std::cout << "C API fault tests passed\n";
        return 0;
    } catch (const std::exception &e) { std::cerr << e.what() << '\n'; return 1; }
}
