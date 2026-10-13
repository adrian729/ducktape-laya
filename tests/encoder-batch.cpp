// Developer-only experiment. Never used by the public library.
#include "llama.h"
#include <nlohmann/json.hpp>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <vector>
using json = nlohmann::ordered_json;
using Clock = std::chrono::steady_clock;
int main(int argc, char **argv) {
    try {
        if (argc != 3) throw std::runtime_error("Usage: laya-encoder-probe MODEL TOKEN_FIXTURE");
        llama_log_set([](ggml_log_level, const char *, void *) {}, nullptr);
        llama_backend_init();
        auto mp = llama_model_default_params(); mp.n_gpu_layers = 0;
        std::unique_ptr<llama_model, decltype(&llama_model_free)> model(llama_model_load_from_file(argv[1], mp), llama_model_free);
        if (!model) throw std::runtime_error("model load failed");
        auto cp = llama_context_default_params();
        cp.n_ctx = cp.n_batch = cp.n_ubatch = 512; cp.n_seq_max = 8; cp.kv_unified = true;
        cp.n_threads = cp.n_threads_batch = 2; cp.embeddings = true;
        cp.pooling_type = LLAMA_POOLING_TYPE_NONE; cp.attention_type = LLAMA_ATTENTION_TYPE_NON_CAUSAL;
        cp.offload_kqv = cp.op_offload = false;
        std::unique_ptr<llama_context, decltype(&llama_free)> ctx(llama_init_from_model(model.get(), cp), llama_free);
        if (!ctx) throw std::runtime_error("context load failed");
        std::ifstream file(argv[2]); json fixtures; file >> fixtures;
        auto run = [&](const std::vector<size_t> &indices) {
            size_t count = 0; for (auto i : indices) count += fixtures.at(i).at("ids").size();
            if (indices.empty() || indices.size() > 8 || count > 512) throw std::runtime_error("probe budget exceeded");
            auto batch = llama_batch_init(count, 0, 1);
            struct guard { llama_batch &b; ~guard() { llama_batch_free(b); } } cleanup{batch};
            batch.n_tokens = count; size_t row = 0;
            for (size_t seq = 0; seq < indices.size(); ++seq) {
                auto ids = fixtures[indices[seq]]["ids"].get<std::vector<llama_token>>();
                for (size_t pos = 0; pos < ids.size(); ++pos, ++row) {
                    batch.token[row] = ids[pos]; batch.pos[row] = pos; batch.n_seq_id[row] = 1;
                    batch.seq_id[row][0] = seq; batch.logits[row] = 1;
                }
            }
            if (llama_encode(ctx.get(), batch)) throw std::runtime_error("encode failed");
            json result = json::array(); size_t offset = 0;
            for (auto i : indices) {
                const auto &q = fixtures[i]; int column = q["qtype"];
                std::vector<double> scores;
                for (size_t marker : q["markers"].get<std::vector<size_t>>()) {
                    auto values = llama_get_embeddings_ith(ctx.get(), offset + marker);
                    if (!values || !std::isfinite(values[column])) throw std::runtime_error("missing/nonfinite embeddings");
                    scores.push_back(values[column]);
                }
                auto n = scores.size(); const char *types[] = {"choice", "score", "noul"};
                std::string key = std::string("modern-bert.decision.temperature.") + types[column] + (n <= 2 ? ".2" : n <= 5 ? ".3_5" : n <= 10 ? ".6_10" : ".11");
                char text[64]; int len = llama_model_meta_val_str(model.get(), key.c_str(), text, sizeof(text));
                double temperature = len > 0 ? std::stod(text) : 1;
                double largest = *std::max_element(scores.begin(), scores.end()), sum = 0;
                for (auto &s : scores) { s = std::exp((s - largest) / temperature); sum += s; }
                for (auto &s : scores) s /= sum;
                result.push_back(scores); offset += q["ids"].size();
            }
            return result;
        };
        auto start = Clock::now(); json singles = json::array();
        for (size_t i = 0; i < fixtures.size(); ++i) singles.push_back(run({i})[0]);
        double single_ms = std::chrono::duration<double,std::milli>(Clock::now()-start).count();
        std::vector<size_t> indices;
        for (size_t i = 0; i < fixtures.size(); ++i) indices.push_back(i);
        start = Clock::now(); auto batch = run(indices);
        double batch_ms = std::chrono::duration<double,std::milli>(Clock::now()-start).count();
        std::reverse(indices.begin(), indices.end()); auto reversed = run(indices);
        double delta = 0, reverse_delta = 0; size_t winners = 0;
        for (size_t i = 0; i < singles.size(); ++i) {
            auto a = singles[i].get<std::vector<double>>(), b = batch[i].get<std::vector<double>>();
            winners += std::max_element(a.begin(),a.end())-a.begin() == std::max_element(b.begin(),b.end())-b.begin();
            for (size_t j = 0; j < a.size(); ++j) {
                delta = std::max(delta, std::fabs(a[j]-b[j]));
                reverse_delta = std::max(reverse_delta, std::fabs(b[j]-reversed[singles.size()-1-i][j].get<double>()));
            }
        }
        std::cout << json({{"questions",singles.size()},{"max_probability_delta",delta},
            {"max_reverse_order_delta",reverse_delta},{"same_winners",winners},
            {"sequential_ms",single_ms},{"simultaneous_ms",batch_ms},
            {"sequential",singles},{"simultaneous",batch}}).dump(2) << '\n';
        return 0;
    } catch (const std::exception &e) { std::cerr << e.what() << '\n'; return 1; }
}
