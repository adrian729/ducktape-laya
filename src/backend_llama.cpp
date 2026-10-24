// Laya prompt layout and result formulas adapted from llama.cpp b11540
// tools/server/server-decision.cpp (MIT). See THIRD_PARTY_NOTICES.md.
#include "backend.hpp"
#include "python_json.hpp"
#include "decision.hpp"
#include "errors.hpp"
#include "execution.hpp"
#include "platform.hpp"
#include "threads.hpp"
#include "llama.h"
#include "jinja/parser.h"
#include "json.h"
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <map>
#include <mutex>
#include <string_view>
#include <thread>

// Runtime patch laya-selected-decision-head.patch: the question type whose decision head
// the next graph built on this thread evaluates, or -1 for all three (library-internal).
extern "C" void dtl_laya_select_decision_type(int type);
// Runtime patch tokenizer-stop-check.patch: a stop check llama_tokenize calls periodically on
// this thread; when it returns true, tokenization throws std::runtime_error (library-internal).
extern "C" void dtl_llama_tokenize_set_stop(bool (*stop)(void *), void *data);
namespace dtl {
namespace {
std::string meta(llama_model *m, const std::string &key) {
    int n = llama_model_meta_val_str(m, key.c_str(), nullptr, 0);
    if (n < 0) throw failure(DTL_MODEL_ERROR, "Missing/invalid model metadata: " + key);
    std::string value(static_cast<size_t>(n) + 1, '\0');
    if (llama_model_meta_val_str(m, key.c_str(), value.data(), value.size()) != n)
        throw failure(DTL_MODEL_ERROR, "Missing/invalid model metadata: " + key);
    value.resize(static_cast<size_t>(n));
    return value;
}
void sanitize(json &value, const std::string &marker) {
    if (value.is_string()) {
        // One pass: replacing in place would shift the tail once per marker.
        const auto &s = value.get_ref<const std::string &>();
        if (s.find(marker) == std::string::npos) return;
        std::string out;
        out.reserve(s.size());
        for (size_t at = 0;;) {
            const size_t next = s.find(marker, at);
            out.append(s, at, next == std::string::npos ? std::string::npos : next - at);
            if (next == std::string::npos) break;
            out += ' ';
            at = next + marker.size();
        }
        value = std::move(out);
    } else if (value.is_structured()) {
        for (auto &child : value) sanitize(child, marker);
    }
}
}
struct prepared_window {
    std::vector<llama_token> tokens;
    std::vector<size_t> markers;
    size_t state_start = 0, state_end = 0; // span of the state tokens this window reads
};
struct prepared_question {
    int column = 0;
    std::vector<prepared_window> windows;
    size_t state_tokens = 0, state_dropped = 0;
    bool head_shortened = false;
};
question::question(std::string id_, std::string type_, json instructions_, json options_)
    : id(std::move(id_)), type(std::move(type_)), instructions(std::move(instructions_)),
      options(std::move(options_)) {}
question::~question() = default;
question::question(question &&) noexcept = default;
question &question::operator=(question &&) noexcept = default;
size_t question::token_count() const {
    size_t n = 0;
    if (prepared) for (const auto &w : prepared->windows) n += w.tokens.size();
    return n;
}
json question::overflow_report() const {
    if (!prepared) return json::object();
    return {{"state_tokens", prepared->state_tokens}, {"state_tokens_dropped", prepared->state_dropped},
            {"head_shortened", prepared->head_shortened}, {"windows", prepared->windows.size()}};
}

struct backend::impl {
    std::unique_ptr<llama_model, decltype(&llama_model_free)> model{nullptr, llama_model_free};
    std::unique_ptr<llama_context, decltype(&llama_free)> ctx{nullptr, llama_free};
    const llama_vocab *vocab = nullptr;
    std::string source, marker_text;
    jinja::program program;
    llama_token marker, sep;
    size_t max_head, capacity, trained;
    temperature_map temperatures, metadata_temperatures;

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
            // Fitted values are bounded to [0.5, 5], as in the original Laya: the shipped
            // choice.11 value (0.1006) sharpens about 10x and would report a 0.24 top
            // probability as 0.9999. Temperatures the app sets are used as given.
            metadata_temperatures[std::string(key).substr(prefix.size())] = t;
            temperatures[std::string(key).substr(prefix.size())] = std::clamp(t, 0.5f, 5.0f);
        }
        for (const auto &[key, t] : config.temperatures) temperatures[key] = t;
        auto cp = llama_context_default_params();
        cp.n_ctx = cp.n_batch = cp.n_ubatch = static_cast<uint32_t>(capacity); cp.n_seq_max = 1;
        const uint32_t threads = config.threads;
        cp.n_threads = cp.n_threads_batch = threads ? threads : default_threads();
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
    // The last state tokenized, reused while consecutive questions share it.
    std::string cached_state_text;
    std::vector<llama_token> cached_state_tokens;
    void tokenize_into(std::vector<llama_token> &out, std::string_view text, const execution *control) const {
        // Cancellation and deadlines stop the BPE merges and the special-token split; the
        // regex pre-split and symbol setup of a word are not checked.
        struct stop_hook {
            explicit stop_hook(const execution *control) {
                if (control && control->enabled())
                    dtl_llama_tokenize_set_stop([](void *p) { return static_cast<const execution *>(p)->stopped(); },
                                                const_cast<execution *>(control));
            }
            ~stop_hook() { dtl_llama_tokenize_set_stop(nullptr, nullptr); }
        } hook{control};
        try {
            tokenize_once(out, text);
        } catch (const std::runtime_error &) {
            if (control) control->check(); // the latched cancellation or deadline, if it stopped
            throw;
        }
    }
    void tokenize_once(std::vector<llama_token> &out, std::string_view text) const {
        // Each llama_tokenize call does the full work, so tokenize once into a buffer of
        // text.size() + 2 tokens (common_tokenize's estimate) and only retry with the exact
        // count when that was too small. Texts are at most 2 MiB, well within int32_t.
        const size_t at = out.size();
        const auto length = static_cast<int32_t>(text.size());
        out.resize(at + text.size() + 2);
        int n;
        try {
            n = llama_tokenize(vocab, text.data(), length, out.data() + at, static_cast<int32_t>(text.size() + 2), false, true);
            if (n < 0 && n != std::numeric_limits<int32_t>::min()) {
                out.resize(at + static_cast<size_t>(-static_cast<int64_t>(n)));
                n = llama_tokenize(vocab, text.data(), length, out.data() + at, -n, false, true);
            }
        } catch (...) { out.resize(at); throw; }
        if (n < 0) { out.resize(at); throw failure(DTL_INFERENCE_ERROR, "Tokenization failed"); }
        out.resize(at + static_cast<size_t>(n));
    }
    void prepare(question &q, const json &state, const execution *control) {
        if (q.overflow.empty()) q.overflow = "reject";
        require(q.max_tokens <= capacity, "max_tokens exceeds this engine's context_tokens (" + std::to_string(capacity) + ")");
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
        // The state sits between two separators, so its tokens do not depend on the
        // question: it is tokenized on its own, once for all questions sharing it.
        const bool separate_state = input["state"].is_string();
        std::string state_text;
        if (separate_state) { state_text = input["state"].get<std::string>(); input["state"] = ""; }
        // Serialization can expand a structured state (1e15 is written 1000000000000000.0),
        // so the text to tokenize is bounded like the rendered prompt, not only the request.
        require(state_text.size() <= max_request * 2, "Serialized state is too large");
        jinja::context jc(source);
        jinja::global_from_json(jc, common_json::parse(input.dump()), false);
        jinja::runtime runtime(jc);
        auto rendered = jinja::runtime::gather_string_parts(runtime.execute(program))->as_string().str();
        require(rendered.size() <= max_request * 2, "Rendered prompt is too large");
        const size_t limit = q.max_tokens ? q.max_tokens : capacity;
        // The text between mask tokens is tokenized on its own, as the model was
        // trained: some vocabularies (mmBERT) mark the mask as left-stripping, which
        // would drop whitespace ending an option. After sanitizing, the mask text
        // appears only where the template put it.
        std::vector<llama_token> tokens;
        auto append = [&](std::string_view text) { tokenize_into(tokens, text, control); };
        for (size_t at = 0;;) {
            const size_t next = rendered.find(marker_text, at);
            append(std::string_view(rendered).substr(at, next == std::string::npos ? std::string::npos : next - at));
            if (next == std::string::npos) break;
            tokens.push_back(marker);
            at = next + marker_text.size();
        }
        // Layout: [CLS] question [SEP] ([MASK] option)* [SEP] state [SEP]. Special
        // tokens split the text, so each piece is tokenized on its own and can be
        // cut at token level without re-tokenizing.
        std::vector<size_t> markers;
        for (size_t i = 0; i < tokens.size(); ++i) if (tokens[i] == marker) markers.push_back(i);
        require(markers.size() == q.options.size() && !markers.empty() && markers[0] >= 2 &&
                tokens[markers[0]-1] == sep && tokens.back() == sep, "Invalid decision prompt layout");
        const size_t head_end = markers[0] - 1;
        const size_t opts_end = std::find(tokens.begin() + markers.back(), tokens.end(), sep) - tokens.begin();
        require(opts_end + 1 < tokens.size(), "Invalid option separator");
        // The head (question and options) is cut to the model's head budget exactly as
        // llama.cpp's decision server does, the way the model was trained.
        std::vector<std::vector<llama_token>> options;
        size_t option_tokens = 0;
        auto cap = [&](size_t n_max) {
            option_tokens = 0;
            for (auto &o : options) { o.resize(std::min(o.size(), n_max)); option_tokens += o.size(); }
        };
        size_t original_option_tokens = 0;
        for (size_t i = 0; i < markers.size(); ++i) {
            const size_t end = i + 1 < markers.size() ? markers[i + 1] : opts_end;
            options.emplace_back(tokens.begin() + markers[i], tokens.begin() + end);
            original_option_tokens += end - markers[i];
        }
        cap(49);
        if (option_tokens + 16 > max_head) cap(std::max(size_t(4), (max_head - std::min(max_head, size_t(16))) / options.size()));
        const size_t question_max = std::max(size_t(8), max_head - std::min(max_head, option_tokens));
        const size_t question_tokens = head_end - 1, kept_question = std::min(question_tokens, question_max);
        const bool head_cut = option_tokens != original_option_tokens || kept_question != question_tokens;
        const bool reject = q.overflow == "reject";
        if (reject && option_tokens != original_option_tokens)
            throw failure(DTL_INVALID_ARGUMENT, q.id + ": option text exceeds the model's head budget");
        if (reject && kept_question != question_tokens)
            throw failure(DTL_INVALID_ARGUMENT, q.id + ": instructions exceed the model's head budget");
        std::vector<llama_token> head{tokens[0]};
        head.insert(head.end(), tokens.begin() + 1, tokens.begin() + 1 + kept_question);
        head.push_back(sep);
        std::vector<size_t> head_markers;
        for (const auto &o : options) { head_markers.push_back(head.size()); head.insert(head.end(), o.begin(), o.end()); }
        head.push_back(sep);
        require(!separate_state || opts_end + 2 == tokens.size(), "Invalid decision prompt layout");
        if (separate_state && state_text != cached_state_text) {
            // Replace the cache only once tokenization succeeded.
            std::vector<llama_token> fresh;
            tokenize_into(fresh, state_text, control);
            cached_state_tokens = std::move(fresh);
            cached_state_text = std::move(state_text);
        }
        const std::vector<llama_token> state_tokens = separate_state ? cached_state_tokens
            : std::vector<llama_token>(tokens.begin() + opts_end + 1, tokens.end() - 1);
        prepared.state_tokens = state_tokens.size();
        prepared.head_shortened = head_cut;
        // The question, options and final separator must fit on their own, even with an
        // empty state; otherwise a window would exceed the context.
        require(head.size() + 1 <= limit,
                q.id + ": the question and options need " + std::to_string(head.size() + 1) + " tokens, over the " +
                std::to_string(limit) + "-token limit");
        const size_t room = limit - head.size() - 1;
        if (reject) require(state_tokens.size() <= room, q.id + ": prompt exceeds the " + std::to_string(limit) + "-token limit");
        require(room > 0 || state_tokens.empty(),
                q.id + ": the question and options leave no room for the state within " + std::to_string(limit) + " tokens");
        auto add = [&](size_t start, size_t end) {
            prepared_window w{head, head_markers, start, end};
            w.tokens.insert(w.tokens.end(), state_tokens.begin() + start, state_tokens.begin() + end);
            w.tokens.push_back(sep);
            prepared.windows.push_back(std::move(w));
        };
        if (state_tokens.size() <= room) {
            add(0, state_tokens.size());
        } else if (q.overflow == "truncate_end") {
            add(0, room); prepared.state_dropped = state_tokens.size() - room;
        } else if (q.overflow == "truncate_start") {
            add(state_tokens.size() - room, state_tokens.size()); prepared.state_dropped = state_tokens.size() - room;
        } else {  // scan: windows of the full room, half overlapping; the last one ends at the state's end
            const size_t stride = std::max<size_t>(1, room / 2);
            const size_t count = 1 + (state_tokens.size() - room + stride - 1) / stride;
            require(count <= q.max_windows, q.id + ": the state needs " + std::to_string(count) +
                    " windows; max_windows is " + std::to_string(q.max_windows));
            for (size_t i = 0; i < count; ++i) {
                const size_t start = std::min(i * stride, state_tokens.size() - room);
                add(start, start + room);
            }
        }
    }
    // One forward pass over one window; the question type's column at each marker.
    std::vector<float> encode(const prepared_window &w, int column, const execution *control) {
        if (control) control->check();
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
        if (auto memory = llama_get_memory(ctx.get())) llama_memory_clear(memory, true);
        // Own the batch arrays: llama_batch_init does not check its malloc results.
        const auto n = w.tokens.size();
        std::vector<llama_token> tokens(w.tokens.begin(), w.tokens.end());
        std::vector<llama_pos> positions(n);
        std::vector<int32_t> seq_counts(n, 1);
        llama_seq_id sequence = 0;
        std::vector<llama_seq_id *> seq_ids(n, &sequence);
        std::vector<int8_t> outputs(n, 1);
        for (size_t i = 0; i < n; ++i) positions[i] = static_cast<llama_pos>(i);
        llama_batch batch{static_cast<int32_t>(n), tokens.data(), nullptr, positions.data(), seq_counts.data(),
                          seq_ids.data(), outputs.data()};
        // Evaluate only this question type's head (its scores fill all three columns); the
        // reserved graph at open evaluated every type, so compute buffers never grow.
        struct reset_selection {
            ~reset_selection() { dtl_laya_select_decision_type(-1); }
        } reset_type;
        dtl_laya_select_decision_type(column);
        int status = llama_encode(ctx.get(), batch);
        if (control) control->check();
        if (status == -2) throw std::bad_alloc(); // llama_encode: allocation failed
        if (status != 0) throw failure(DTL_INFERENCE_ERROR, "Laya forward pass failed: " + std::to_string(status));
        std::vector<float> scores;
        for (size_t index : w.markers) {
            const float *v = llama_get_embeddings_ith(ctx.get(), static_cast<int32_t>(index));
            if (!v || !std::isfinite(v[column])) throw failure(DTL_INFERENCE_ERROR, "Missing/nonfinite decision output");
            scores.push_back(v[column]);
        }
        return scores;
    }
    json answer(const question &q, const std::vector<float> &scores) const {
        if (!q.temperatures) return answer_from_scores(q.type, q.options, scores, temperature_for(q.type, scores.size(), temperatures));
        auto merged = temperatures;
        for (const auto &[key, t] : *q.temperatures) merged[key] = t;
        return answer_from_scores(q.type, q.options, scores, temperature_for(q.type, scores.size(), merged));
    }
    json run(const question &q, const execution *control) {
        if (control) control->check();
        if (q.options.size() == 1) return single_option_answer(q.options);
        const auto &prepared = *q.prepared;
        if (prepared.windows.size() == 1) return answer(q, encode(prepared.windows[0], prepared.column, control));
        // Scanned windows: noul takes the maximum (or, with aggregate "all", the
        // minimum) over windows; choice and score take the most confident window.
        json best;
        size_t best_index = 0;
        for (size_t i = 0; i < prepared.windows.size(); ++i) {
            auto a = answer(q, encode(prepared.windows[i], prepared.column, control));
            bool better = i == 0;
            if (!better && q.type == "noul") better = q.aggregate_all ? a["noul"] < best["noul"] : a["noul"] > best["noul"];
            else if (!better) better = a["answer_confidence"] > best["answer_confidence"];
            if (better) { best = std::move(a); best_index = i; }
        }
        const auto &w = prepared.windows[best_index];
        best["window"] = {{"index", best_index}, {"count", prepared.windows.size()},
                          {"token_start", w.state_start}, {"token_end", w.state_end}};
        return best;
    }
};
backend::backend(const char *path, const engine_config &config) : impl_(std::make_unique<impl>(path, config)) {}
backend::~backend() = default;
json backend::info() const {
    return {{"architecture", meta(impl_->model.get(), "general.architecture")},
            {"name", meta(impl_->model.get(), "general.name")}, {"decision_type", "laya"},
            {"max_head_tokens", impl_->max_head}, {"context_tokens", impl_->capacity},
            {"trained_context_tokens", impl_->trained},
            {"temperatures", impl_->temperatures}, {"metadata_temperatures", impl_->metadata_temperatures}};
}
void backend::prepare(question &q, const json &state, const execution *control) { impl_->prepare(q, state, control); }
json backend::run(const question &q, const execution *control) { return impl_->run(q, control); }
} // namespace dtl
