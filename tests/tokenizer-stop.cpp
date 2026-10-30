// The tokenizer's work counter (tokenizer-stop-check.patch) on a vocabulary loaded without
// weights: it runs across llama_tokenize calls while one stop check stays installed and across a
// nested check swapped in and out (a cancellation callback calling into the library), and
// restarts only when a check is set where there was none. Short pieces, each well below the
// 1,024 units between checks, are tokenized one after another, as the library tokenizes a
// question's head between its mask tokens.
// Usage: laya-tokenizer-stop-tests VOCAB.gguf
#include "llama.h"
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

// Runtime patch tokenizer-stop-check.patch (library-internal).
extern "C" void dtl_llama_tokenize_swap_stop(bool (**stop)(void *), void **data);

namespace {
void expect(bool ok, const std::string &message) { if (!ok) throw std::runtime_error(message); }
bool count(void *calls) { ++*static_cast<int *>(calls); return false; }
struct check {
    bool (*stop)(void *) = nullptr;
    void *data = nullptr;
    void swap() { dtl_llama_tokenize_swap_stop(&stop, &data); }
};
}

int main(int argc, char **argv) {
    try {
        expect(argc == 2, "usage: laya-tokenizer-stop-tests VOCAB.gguf");
        llama_log_set([](ggml_log_level, const char *, void *) {}, nullptr);
        llama_backend_init();
        auto mp = llama_model_default_params();
        mp.vocab_only = true;
        std::unique_ptr<llama_model, decltype(&llama_model_free)> model(llama_model_load_from_file(argv[1], mp), llama_model_free);
        expect(model != nullptr, std::string("cannot load vocabulary ") + argv[1]);
        const llama_vocab *vocab = llama_model_get_vocab(model.get());
        const std::string piece = "Which queue handles a refund for order 1042?";
        std::vector<llama_token> tokens(256);
        const auto tokenize = [&] {
            expect(llama_tokenize(vocab, piece.data(), static_cast<int32_t>(piece.size()), tokens.data(),
                                  static_cast<int32_t>(tokens.size()), false, true) > 0, "tokenization failed");
        };
        constexpr int pieces = 100;
        // One check installed over every piece: the work is counted across them.
        int once = 0;
        check a{count, &once};
        a.swap();
        for (int i = 0; i < pieces; ++i) tokenize();
        a.swap();
        expect(a.stop == count && once > 0, "one check over " + std::to_string(pieces) + " pieces was never called");
        // A check set before each piece, where there was none: the count restarts each time, so
        // pieces this short are never checked. (The library installs its check once per question.)
        int each = 0;
        for (int i = 0; i < pieces; ++i) {
            check c{count, &each};
            c.swap();
            tokenize();
            c.swap();
        }
        expect(each == 0, "a piece reached the check on its own; the pieces are too long for this test");
        // A nested check swapped in and out after every piece keeps the count running: together
        // the two are called as often as the one check above.
        int outer = 0, inner = 0;
        check o{count, &outer};
        o.swap();
        for (int i = 0; i < pieces; ++i) {
            tokenize();
            check n{count, &inner};
            n.swap();
            tokenize();
            n.swap();
        }
        o.swap();
        int twice = 0;
        check t{count, &twice};
        t.swap();
        for (int i = 0; i < 2 * pieces; ++i) tokenize();
        t.swap();
        expect(outer + inner == twice, "a nested check restarted the count: " + std::to_string(outer + inner) + " checks, expected " +
               std::to_string(twice));
        std::cout << "tokenizer stop-check counting passed (" << once << " checks over " << pieces << " pieces)\n";
        return 0;
    } catch (const std::exception &e) { std::cerr << e.what() << '\n'; return 1; }
}
