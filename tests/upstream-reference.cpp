// Development-only reference: use upstream decision processing unchanged.
// No part of this executable is included in consumer packages.
#include "server-decision.h"
#include "llama.h"
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
static std::unique_ptr<llama_model, decltype(&llama_model_free)> model(nullptr, llama_model_free);
static std::unique_ptr<llama_context, decltype(&llama_free)> context(nullptr, llama_free);
static server_decision_context decision;
static std::string output;
extern "C" {
const char *reference_open(const char *path) {
    try {
        common_log_set_verbosity_thold(-100);
        llama_log_set([](ggml_log_level, const char *, void *) {}, nullptr);
        llama_backend_init();
        auto mp = llama_model_default_params();
#ifdef REFERENCE_WEBGPU
        mp.n_gpu_layers = 999;
#else
        mp.n_gpu_layers = 0;
#endif
        model.reset(llama_model_load_from_file(path, mp));
        if (!model) throw std::runtime_error("Reference model load failed");
        decision.init(model.get());
        if (decision.type != COMMON_DECISION_TYPE_LAYA) throw std::runtime_error("Expected Laya model");
        auto cp = llama_context_default_params();
        cp.n_ctx = cp.n_batch = cp.n_ubatch = 512; cp.n_seq_max = 1;
#ifdef __EMSCRIPTEN__
        cp.n_threads = cp.n_threads_batch = 1;
#else
        cp.n_threads = cp.n_threads_batch = 4;
#endif
        cp.embeddings = true; cp.pooling_type = LLAMA_POOLING_TYPE_NONE;
        cp.attention_type = LLAMA_ATTENTION_TYPE_NON_CAUSAL;
#ifdef REFERENCE_WEBGPU
        cp.offload_kqv = cp.op_offload = true;
#else
        cp.offload_kqv = cp.op_offload = false;
#endif
        context.reset(llama_init_from_model(model.get(), cp));
        if (!context) throw std::runtime_error("Reference context creation failed");
        return nullptr;
    } catch (const std::exception &e) { output = e.what(); return output.c_str(); }
}
const char *reference_execute(const char *request) {
    try {
        const auto body = json::parse(request);
        const auto questions = decision.parse_questions(body);
        std::vector<raw_buffer> files;
        const auto state = decision.parse_state(body, files);
        if (!files.empty()) throw std::runtime_error("Reference test accepts text only");
        json answers = json::object();
        size_t token_count = 0;
        for (const auto &question : questions) {
            std::vector<std::vector<float>> variants;
            for (size_t variant = 0; variant < decision.n_variants(question); ++variant) {
                server_task task;
                decision.fill_task(state, questions, question, variant, files, nullptr, {}, task);
                if (task.tokens.size() > 512) throw std::runtime_error("Reference input too long");
                token_count += task.tokens.size();
                if (auto memory = llama_get_memory(context.get())) llama_memory_clear(memory, true);
                auto batch = llama_batch_init(task.tokens.size(), 0, 1);
                batch.n_tokens = task.tokens.size();
                for (int i = 0; i < batch.n_tokens; ++i) {
                    batch.token[i] = task.tokens[i]; batch.pos[i] = i;
                    batch.n_seq_id[i] = 1; batch.seq_id[i][0] = 0; batch.logits[i] = 1;
                }
                const auto status = llama_encode(context.get(), batch);
                llama_batch_free(batch);
                if (status) throw std::runtime_error("Reference encode failed");
                std::vector<float> scores;
                for (auto marker : task.decision.markers) {
                    const float *row = llama_get_embeddings_ith(context.get(), marker);
                    if (!row) throw std::runtime_error("Missing reference embedding");
                    scores.push_back(row[task.decision.column]);
                }
                variants.push_back(std::move(scores));
            }
            answers[question.id] = decision.format_answer(question, variants);
        }
        output = json{{"answers", answers}, {"input_tokens", token_count}}.dump();
    } catch (const std::exception &e) { output = json{{"error", e.what()}}.dump(); }
    return output.c_str();
}
void reference_close() { context.reset(); model.reset(); }
}
#ifndef __EMSCRIPTEN__
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    if (const auto *error = reference_open(argv[1])) { std::cerr << error << '\n'; return 1; }
    std::string request((std::istreambuf_iterator<char>(std::cin)), {});
    std::cout << reference_execute(request.c_str()) << '\n';
    reference_close();
}
#else
int main() { return 0; }
#endif
