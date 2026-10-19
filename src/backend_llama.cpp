// Laya prompt layout and result formulas adapted from llama.cpp b11540
// tools/server/server-decision.cpp (MIT). See THIRD_PARTY_NOTICES.md.
#include "backend.hpp"
#include "python_json.hpp"
#include "decision.hpp"
#include "errors.hpp"
#include "execution.hpp"
#include "platform.hpp"
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
    size_t max_head, capacity, trained;
    temperature_map temperatures;

    impl(const char *path, const engine_config &config) {
        static std::once_flag once;
        ensure_cpu_supported();
        std::call_once(once, [] {
            // This build statically embeds a private libllama. Keep diagnostics
            // out of the host application's stdout/stderr; use per-call errors.
            llama_log_set([](ggml_log_level, const char *, void *) {}, nullptr);
            llama_backend_init();
        });
        auto mp = llama_model_default_params();
#ifdef DTL_BROWSER_WEBGPU
        mp.n_gpu_layers = 999;
#else
        mp.n_gpu_layers = 0;
#endif
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
        trained = static_cast<size_t>(std::max<int32_t>(0, llama_model_n_ctx_train(model.get())));
        if (!trained) throw failure(DTL_MODEL_ERROR, "Missing trained context length");
        max_head = std::stoul(meta(model.get(), prefix + "max_head_tokens"));
        if (!max_head || max_head > trained) throw failure(DTL_MODEL_ERROR, "Invalid head token budget");
        capacity = config.context_tokens ? config.context_tokens : std::min<size_t>(512, trained);
        require(capacity >= 32 && capacity <= trained,
                "context_tokens must be 32.." + std::to_string(trained) + " for this model");
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
        for (const auto &[key, t] : config.temperatures) temperatures[key] = t;
        auto cp = llama_context_default_params();
        cp.n_ctx = cp.n_batch = cp.n_ubatch = static_cast<uint32_t>(capacity); cp.n_seq_max = 1;
        const uint32_t threads = config.threads;
        cp.n_threads = cp.n_threads_batch = threads ? threads : std::max(1u, std::min(4u, std::thread::hardware_concurrency()));
        cp.embeddings = true; cp.pooling_type = LLAMA_POOLING_TYPE_NONE;
        cp.attention_type = LLAMA_ATTENTION_TYPE_NON_CAUSAL;
#ifdef DTL_BROWSER_WEBGPU
        cp.offload_kqv = true; cp.op_offload = true;
#else
        cp.offload_kqv = false; cp.op_offload = false;
#endif
        ctx.reset(llama_init_from_model(model.get(), cp));
        if (!ctx) throw failure(DTL_MODEL_ERROR, "Could not create CPU inference context");
    }
    void prepare(question &q, const json &state) {
        if (q.options.size() == 1) return; // answered without inference; uses no tokens
        q.prepared = std::make_unique<prepared_question>();
        auto &prepared = *q.prepared;
        prepared.column = q.type == "score" ? 1 : q.type == "noul" ? 2 : 0;
        json input = {{"id",q.id}, {"type",q.type}, {"instructions",q.instructions},
                      {"state",state}, {"options",q.options}, {"images",json::array()}};
        // Structured values are serialized here, as the original Laya does, so the
        // template takes its string path; null descriptions stay null (no description).
        auto as_text = [](json &value) { if (!value.is_string() && !value.is_null()) value = python_json(value); };
        as_text(input["state"]);
        as_text(input["instructions"]);
        for (auto &option : input["options"]) as_text(option["description"]);
        sanitize(input, marker_text);
        jinja::context jc(source);
        jinja::global_from_json(jc, common_json::parse(input.dump()), false);
        jinja::runtime runtime(jc);
        auto rendered = jinja::runtime::gather_string_parts(runtime.execute(program))->as_string().str();
        require(rendered.size() <= max_request * 2, "Rendered prompt is too large");
        require(q.max_tokens <= capacity, "max_tokens exceeds this engine's context_tokens (" + std::to_string(capacity) + ")");
        const size_t limit = q.max_tokens ? q.max_tokens : capacity;
        int n = llama_tokenize(vocab, rendered.data(), static_cast<int32_t>(rendered.size()), nullptr, 0, false, true);
        require(n < 0 && static_cast<size_t>(-n) <= limit, q.id + ": prompt exceeds the " + std::to_string(limit) + "-token limit");
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
    json run(const question &q, const execution *control) {
        if (control) control->check();
        if (q.options.size() == 1) return single_option_answer(q.options);
#ifndef DTL_BROWSER_WEBGPU
        struct reset_abort {
            llama_context *ctx;
            ~reset_abort() { llama_set_abort_callback(ctx, nullptr, nullptr); }
        } reset{ctx.get()};
        llama_set_abort_callback(ctx.get(), [](void *p) {
            return static_cast<const execution *>(p)->stopped();
        }, const_cast<execution *>(control));
        if (!control || !control->enabled()) llama_set_abort_callback(ctx.get(), nullptr, nullptr);
#endif
        const auto &prepared = *q.prepared;
        if (auto memory = llama_get_memory(ctx.get())) llama_memory_clear(memory, true);
        // Own the batch arrays: llama_batch_init does not check its malloc results.
        const auto n = prepared.tokens.size();
        std::vector<llama_token> tokens(prepared.tokens.begin(), prepared.tokens.end());
        std::vector<llama_pos> positions(n);
        std::vector<int32_t> seq_counts(n, 1);
        llama_seq_id sequence = 0;
        std::vector<llama_seq_id *> seq_ids(n, &sequence);
        std::vector<int8_t> outputs(n, 1);
        for (size_t i = 0; i < n; ++i) positions[i] = static_cast<llama_pos>(i);
        llama_batch batch{static_cast<int32_t>(n), tokens.data(), nullptr, positions.data(), seq_counts.data(),
                          seq_ids.data(), outputs.data()};
        int status = llama_encode(ctx.get(), batch);
        if (control) control->check();
        if (status == -2) throw std::bad_alloc(); // llama_encode: allocation failed
        if (status != 0) throw failure(DTL_INFERENCE_ERROR, "Laya forward pass failed: " + std::to_string(status));
        std::vector<float> scores;
        for (size_t index : prepared.markers) {
            const float *v = llama_get_embeddings_ith(ctx.get(), index);
            if (!v || !std::isfinite(v[prepared.column])) throw failure(DTL_INFERENCE_ERROR, "Missing/nonfinite decision output");
            scores.push_back(v[prepared.column]);
        }
        if (!q.temperatures) return answer_from_scores(q.type, q.options, scores, temperature_for(q.type, scores.size(), temperatures));
        auto merged = temperatures;
        for (const auto &[key, t] : *q.temperatures) merged[key] = t;
        return answer_from_scores(q.type, q.options, scores, temperature_for(q.type, scores.size(), merged));
    }
};
backend::backend(const char *path, const engine_config &config) : impl_(std::make_unique<impl>(path, config)) {}
backend::~backend() = default;
json backend::info() const {
    return {{"architecture", meta(impl_->model.get(), "general.architecture")},
            {"name", meta(impl_->model.get(), "general.name")}, {"decision_type", "laya"},
            {"max_head_tokens", impl_->max_head}, {"context_tokens", impl_->capacity},
            {"trained_context_tokens", impl_->trained},
            {"temperatures", impl_->temperatures}};
}
void backend::prepare(question &q, const json &state) { impl_->prepare(q, state); }
json backend::run(const question &q, const execution *control) { return impl_->run(q, control); }
} // namespace dtl
