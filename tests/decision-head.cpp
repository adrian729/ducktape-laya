// Selected decision head (laya-selected-decision-head.patch): with a question type selected,
// the graph evaluates only that type's head and repeats its scores in all three columns. This
// test encodes one prompt with every type evaluated (the upstream graph) and then with each
// type selected, switching types between passes on one context, and requires every column of
// a selected pass to equal that type's column of the full pass bit for bit.
// Usage: laya-decision-head-tests MODEL.gguf
#include "llama.h"
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

extern "C" void dtl_laya_select_decision_type(int type);

namespace {
void expect(bool ok, const std::string &message) { if (!ok) throw std::runtime_error(message); }

std::vector<llama_token> tokenize(const llama_vocab *vocab, const std::string &text) {
    std::vector<llama_token> out(text.size() + 2);
    const int n = llama_tokenize(vocab, text.data(), static_cast<int32_t>(text.size()), out.data(),
                                 static_cast<int32_t>(out.size()), false, true);
    expect(n >= 0, "tokenize");
    out.resize(static_cast<size_t>(n));
    return out;
}

// One forward pass with type selected (-1: every type); n_tokens x 3 scores.
std::vector<float> encode(llama_context *ctx, const std::vector<llama_token> &prompt, int type) {
    if (auto memory = llama_get_memory(ctx)) llama_memory_clear(memory, true);
    const size_t n = prompt.size();
    std::vector<llama_token> tokens(prompt);
    std::vector<llama_pos> positions(n);
    std::vector<int32_t> seq_counts(n, 1);
    llama_seq_id sequence = 0;
    std::vector<llama_seq_id *> seq_ids(n, &sequence);
    std::vector<int8_t> outputs(n, 1);
    for (size_t i = 0; i < n; ++i) positions[i] = static_cast<llama_pos>(i);
    llama_batch batch{static_cast<int32_t>(n), tokens.data(), nullptr, positions.data(), seq_counts.data(),
                      seq_ids.data(), outputs.data()};
    dtl_laya_select_decision_type(type);
    const int status = llama_encode(ctx, batch);
    dtl_laya_select_decision_type(-1);
    expect(status == 0, "llama_encode failed: " + std::to_string(status));
    std::vector<float> scores;
    for (size_t i = 0; i < n; ++i) {
        const float *row = llama_get_embeddings_ith(ctx, static_cast<int32_t>(i));
        expect(row != nullptr, "missing output row");
        scores.insert(scores.end(), row, row + 3);
    }
    return scores;
}
}

int main(int argc, char **argv) {
    try {
        expect(argc == 2, "usage: laya-decision-head-tests MODEL.gguf");
        llama_log_set([](ggml_log_level, const char *, void *) {}, nullptr);
        llama_backend_init();
        auto mp = llama_model_default_params();
        mp.n_gpu_layers = 0;
        llama_model *model = llama_model_load_from_file(argv[1], mp);
        expect(model != nullptr, "model load");
        expect(llama_model_n_embd_out(model) == 3, "three decision columns");
        auto cp = llama_context_default_params();
        cp.n_ctx = cp.n_batch = cp.n_ubatch = 128;
        cp.n_seq_max = 1;
        cp.n_threads = cp.n_threads_batch = 2;
        cp.embeddings = true;
        cp.pooling_type = LLAMA_POOLING_TYPE_NONE;
        cp.attention_type = LLAMA_ATTENTION_TYPE_NON_CAUSAL;
        cp.offload_kqv = false;
        cp.op_offload = false;
        llama_context *ctx = llama_init_from_model(model, cp);
        expect(ctx != nullptr, "context");
        const llama_vocab *vocab = llama_model_get_vocab(model);
        std::vector<llama_token> prompt{llama_vocab_bos(vocab) == LLAMA_TOKEN_NULL ? llama_vocab_sep(vocab) : llama_vocab_bos(vocab)};
        for (const auto &t : tokenize(vocab, "Does the customer request a refund for the duplicate payment?")) prompt.push_back(t);
        prompt.push_back(llama_vocab_sep(vocab));
        prompt.push_back(llama_vocab_mask(vocab));
        for (const auto &t : tokenize(vocab, "Yes, the customer asks for the money back.")) prompt.push_back(t);
        prompt.push_back(llama_vocab_mask(vocab));
        for (const auto &t : tokenize(vocab, "No refund is requested.")) prompt.push_back(t);
        prompt.push_back(llama_vocab_sep(vocab));
        for (const auto &t : tokenize(vocab, "I was charged twice for my subscription this month, please give me my money back.")) prompt.push_back(t);
        prompt.push_back(llama_vocab_sep(vocab));
        expect(prompt.size() <= 128, "prompt fits the context");
        const auto full = encode(ctx, prompt, -1);
        bool columns_differ = false;
        for (size_t i = 0; i < prompt.size(); ++i)
            columns_differ |= full[i * 3] != full[i * 3 + 1] || full[i * 3 + 1] != full[i * 3 + 2];
        expect(columns_differ, "the full graph evaluates three different heads");
        // Switch types in every order, repeat one, and return to the full graph in between.
        for (int type : {0, 1, 2, 2, 0, -1, 1, 0, 2, 1, -1, 2}) {
            const auto scores = encode(ctx, prompt, type);
            expect(scores.size() == full.size(), "output shape");
            for (size_t i = 0; i < prompt.size(); ++i)
                for (int column = 0; column < 3; ++column) {
                    const float expected = full[i * 3 + (type < 0 ? column : type)];
                    const float actual = scores[i * 3 + column];
                    expect(std::memcmp(&expected, &actual, sizeof(float)) == 0,
                           "type " + std::to_string(type) + " row " + std::to_string(i) + " column " + std::to_string(column) +
                           " differs from the full graph");
                }
        }
        llama_free(ctx);
        llama_model_free(model);
        llama_backend_free();
        std::cout << "decision head tests passed\n";
        return 0;
    } catch (const std::exception &e) { std::cerr << e.what() << '\n'; return 1; }
}
