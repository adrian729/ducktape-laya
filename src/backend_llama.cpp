// Laya prompt layout and result formulas adapted from llama.cpp b11540
// tools/server/server-decision.cpp (MIT). See THIRD_PARTY_NOTICES.md.
#include "backend.hpp"
#include "errors.hpp"
#include "llama.h"
#include "jinja/parser.h"
#include "json.h"
#include <algorithm>
#include <cmath>
#include <map>
#include <mutex>
#include <thread>

namespace dtl {
namespace {
std::string meta(llama_model *m, const std::string &key) {
    char buf[256];
    int n = llama_model_meta_val_str(m, key.c_str(), buf, sizeof(buf));
    if (n < 0 || n >= static_cast<int>(sizeof(buf)))
        throw failure(DTL_MODEL_ERROR, "Missing/invalid model metadata: " + key);
    return std::string(buf, n);
}
void sanitize(json &value, const std::string &marker) {
    if (value.is_string()) {
        auto s = value.get<std::string>();
        size_t at = 0;
        while ((at = s.find(marker, at)) != std::string::npos) {
            s.replace(at, marker.size(), " ");
            ++at; // Progress even if a custom model's mask token is a space.
        }
        value = s;
    } else if (value.is_structured()) {
        for (auto &child : value) sanitize(child, marker);
    }
}
}
struct prepared_question {
    int column = 0;
    std::vector<llama_token> tokens;
    std::vector<size_t> markers;
};
question::question(std::string id_, std::string type_, json instructions_, json options_)
    : id(std::move(id_)), type(std::move(type_)), instructions(std::move(instructions_)),
      options(std::move(options_)) {}
question::~question() = default;
question::question(question &&) noexcept = default;
question &question::operator=(question &&) noexcept = default;
size_t question::token_count() const { return prepared ? prepared->tokens.size() : 0; }

struct backend::impl {
    std::unique_ptr<llama_model, decltype(&llama_model_free)> model{nullptr, llama_model_free};
    std::unique_ptr<llama_context, decltype(&llama_free)> ctx{nullptr, llama_free};
    const llama_vocab *vocab = nullptr;
    std::string source, marker_text;
    jinja::program program;
    llama_token marker, sep;
    size_t max_head;
    std::map<std::string, float> temperatures;

    impl(const char *path, uint32_t threads) {
        static std::once_flag once;
        std::call_once(once, [] {
            // This build statically embeds a private libllama. Keep diagnostics
            // out of the host application's stdout/stderr; use per-call errors.
            llama_log_set([](ggml_log_level, const char *, void *) {}, nullptr);
            llama_backend_init();
        });
        auto mp = llama_model_default_params();
        mp.n_gpu_layers = 0;
        model.reset(llama_model_load_from_file(path, mp));
        if (!model) throw failure(DTL_MODEL_ERROR, "Could not load local GGUF model");
        auto prefix = meta(model.get(), "general.architecture") + ".decision.";
        if (meta(model.get(), prefix + "type") != "laya")
            throw failure(DTL_MODEL_ERROR, "Only Laya decision models are supported");
        if (llama_model_n_embd_out(model.get()) != 3)
            throw failure(DTL_MODEL_ERROR, "Expected three Laya decision output columns");
        const char *tmpl = llama_model_chat_template(model.get(), "systemone");
        if (!tmpl) throw failure(DTL_MODEL_ERROR, "Missing systemone template");
        jinja::lexer lexer;
        auto lexed = lexer.tokenize(tmpl);
        source = lexed.source;
        program = jinja::parse_from_tokens(lexed);
        vocab = llama_model_get_vocab(model.get());
        marker = llama_vocab_mask(vocab); sep = llama_vocab_sep(vocab);
        if (marker == LLAMA_TOKEN_NULL || sep == LLAMA_TOKEN_NULL)
            throw failure(DTL_MODEL_ERROR, "Missing Laya marker tokens");
        char piece[256];
        int n = llama_token_to_piece(vocab, marker, piece, sizeof(piece), 0, true);
        if (n <= 0 || n > static_cast<int>(sizeof(piece)))
            throw failure(DTL_MODEL_ERROR, "Invalid mask token");
        marker_text.assign(piece, n);
        max_head = std::stoul(meta(model.get(), prefix + "max_head_tokens"));
        if (!max_head || max_head > 512) throw failure(DTL_MODEL_ERROR, "Invalid head token budget");
        prefix += "temperature.";
        for (int32_t i = 0; i < llama_model_meta_count(model.get()); ++i) {
            char key[256];
            int len = llama_model_meta_key_by_index(model.get(), i, key, sizeof(key));
            if (len < 0 || len >= static_cast<int>(sizeof(key))) continue;
            if (std::string(key).compare(0, prefix.size(), prefix) != 0) continue;
            float t = std::stof(meta(model.get(), key));
            if (!(t > 0) || !std::isfinite(t)) throw failure(DTL_MODEL_ERROR, "Invalid temperature");
            temperatures[std::string(key).substr(prefix.size())] = t;
        }
        auto cp = llama_context_default_params();
        cp.n_ctx = 512; cp.n_batch = 512; cp.n_ubatch = 512; cp.n_seq_max = 1;
        cp.n_threads = cp.n_threads_batch = threads ? threads : std::max(1u, std::min(4u, std::thread::hardware_concurrency()));
        cp.embeddings = true; cp.pooling_type = LLAMA_POOLING_TYPE_NONE;
        cp.attention_type = LLAMA_ATTENTION_TYPE_NON_CAUSAL;
        cp.offload_kqv = false; cp.op_offload = false;
        ctx.reset(llama_init_from_model(model.get(), cp));
        if (!ctx) throw failure(DTL_MODEL_ERROR, "Could not create CPU inference context");
    }
    void prepare(question &q, const json &state) {
        q.prepared = std::make_unique<prepared_question>();
        auto &prepared = *q.prepared;
        prepared.column = q.type == "score" ? 1 : q.type == "noul" ? 2 : 0;
        json input = {{"id",q.id}, {"type",q.type}, {"instructions",q.instructions},
                      {"state",state}, {"options",q.options}, {"images",json::array()}};
        sanitize(input, marker_text);
        jinja::context jc(source);
        jinja::global_from_json(jc, common_json::parse(input.dump()), false);
        jinja::runtime runtime(jc);
        auto rendered = jinja::runtime::gather_string_parts(runtime.execute(program))->as_string().str();
        require(rendered.size() <= max_request * 2, "Rendered prompt is too large");
        int n = llama_tokenize(vocab, rendered.data(), static_cast<int32_t>(rendered.size()), nullptr, 0, false, true);
        require(n < 0 && -n <= 512, q.id + ": prompt exceeds the 512-token budget");
        prepared.tokens.resize(-n);
        n = llama_tokenize(vocab, rendered.data(), static_cast<int32_t>(rendered.size()), prepared.tokens.data(), prepared.tokens.size(), false, true);
        if (n < 0) throw failure(DTL_INFERENCE_ERROR, "Tokenization failed");
        prepared.tokens.resize(n);
        for (size_t i = 0; i < prepared.tokens.size(); ++i) if (prepared.tokens[i] == marker) prepared.markers.push_back(i);
        require(prepared.markers.size() == q.options.size() && !prepared.markers.empty() && prepared.markers[0] >= 2 &&
                prepared.tokens[prepared.markers[0]-1] == sep && prepared.tokens.back() == sep, "Invalid decision prompt layout");
        size_t opts_end = std::find(prepared.tokens.begin() + prepared.markers.back(), prepared.tokens.end(), sep) - prepared.tokens.begin();
        require(opts_end + 1 < prepared.tokens.size(), "Invalid option separator");
        size_t total = opts_end - prepared.markers[0];
        size_t cap = total + 16 > max_head ? std::max(size_t(4), (max_head - std::min(max_head, size_t(16))) / q.options.size()) : 49;
        // Reject where upstream would truncate; otherwise the prompt is identical.
        for (size_t i = 0; i < prepared.markers.size(); ++i) {
            size_t end = i + 1 < prepared.markers.size() ? prepared.markers[i+1] : opts_end;
            require(end - prepared.markers[i] <= std::min(size_t(49), cap), q.id + ": option text exceeds the model's head budget");
        }
        size_t head_length = prepared.markers[0] - 2;
        require(head_length <= std::max(size_t(8), max_head - std::min(max_head, total)),
                q.id + ": instructions exceed the model's head budget");
    }
    json run(const question &q) {
        const auto &prepared = *q.prepared;
        if (auto memory = llama_get_memory(ctx.get())) llama_memory_clear(memory, true);
        auto batch = llama_batch_init(prepared.tokens.size(), 0, 1);
        struct cleanup { llama_batch &b; ~cleanup() { llama_batch_free(b); } } guard{batch};
        if (!batch.token || !batch.pos || !batch.n_seq_id || !batch.seq_id || !batch.logits) throw std::bad_alloc();
        batch.n_tokens = prepared.tokens.size();
        for (int i = 0; i < batch.n_tokens; ++i) {
            batch.token[i] = prepared.tokens[i]; batch.pos[i] = i; batch.n_seq_id[i] = 1;
            batch.seq_id[i][0] = 0; batch.logits[i] = 1;
        }
        int status = llama_encode(ctx.get(), batch);
        if (status != 0) throw failure(DTL_INFERENCE_ERROR, "Laya forward pass failed: " + std::to_string(status));
        std::vector<float> scores;
        for (size_t index : prepared.markers) {
            const float *v = llama_get_embeddings_ith(ctx.get(), index);
            if (!v || !std::isfinite(v[prepared.column])) throw failure(DTL_INFERENCE_ERROR, "Missing/nonfinite decision output");
            scores.push_back(v[prepared.column]);
        }
        size_t n = scores.size();
        std::string bucket = n <= 2 ? "2" : n <= 5 ? "3_5" : n <= 10 ? "6_10" : "11";
        auto it = temperatures.find(q.type + "." + bucket);
        if (it == temperatures.end()) it = temperatures.find(q.type);
        float temperature = it == temperatures.end() ? 1.0f : it->second;
        float largest = *std::max_element(scores.begin(), scores.end());
        std::vector<double> p(n);
        double sum = 0;
        for (size_t i = 0; i < n; ++i) { p[i] = std::exp(double(scores[i] - largest) / temperature); sum += p[i]; }
        for (double &v : p) v /= sum;
        json answer = {{"type", q.type}};
        if (q.type == "noul") { answer["noul"] = p[1]; return answer; }
        json probabilities = json::object(), legend = json::object();
        size_t best = std::max_element(p.begin(), p.end()) - p.begin();
        double expected = 0, distance = 0, uniform_distance = 0;
        for (size_t i = 0; i < n; ++i) {
            auto key = q.options[i]["key"].get<std::string>();
            probabilities[key] = p[i]; legend[key] = q.options[i]["description"];
            expected += i * p[i]; distance += p[i] * std::fabs(double(i) - double(best));
            uniform_distance += std::fabs(double(i) - (n - 1) / 2.0) / n;
        }
        if (q.type == "choice") {
            answer["choice"] = q.options[best]["key"];
            answer["probabilities"] = probabilities;
            answer["confidence"] = std::max(0.0, (p[best] - 1.0/n) / (1.0 - 1.0/n));
        } else {
            answer["score"] = expected; answer["legend"] = legend;
            answer["probabilities"] = probabilities;
            answer["confidence"] = std::max(0.0, 1.0 - distance / uniform_distance);
        }
        return answer;
    }
};
backend::backend(const char *path, uint32_t threads) : impl_(std::make_unique<impl>(path, threads)) {}
backend::~backend() = default;
void backend::prepare(question &q, const json &state) { impl_->prepare(q, state); }
json backend::run(const question &q) { return impl_->run(q); }
} // namespace dtl
