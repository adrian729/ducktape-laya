// Laya prompt layout and result formulas adapted from llama.cpp b11540
// tools/server/server-decision.cpp (MIT). See THIRD_PARTY_NOTICES.md.
#include "ducktape_laya.h"
#include "llama.h"
#include "jinja/parser.h"
#include "json.h"
#include <nlohmann/json.hpp>
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <map>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <vector>
using json = nlohmann::ordered_json;
namespace {
constexpr size_t max_request = 1024 * 1024;
struct failure : std::runtime_error {
    dtl_status status;
    failure(dtl_status s, const std::string &m) : std::runtime_error(m), status(s) {}
};
void require(bool ok, const std::string &message) {
    if (!ok) throw failure(DTL_INVALID_ARGUMENT, message);
}
char *copy(const std::string &s) {
    auto *p = static_cast<char *>(std::malloc(s.size() + 1));
    if (!p) throw std::bad_alloc();
    std::memcpy(p, s.c_str(), s.size() + 1);
    return p;
}
template<class F> dtl_status boundary(char **error, F f) noexcept {
    if (error) *error = nullptr;
    dtl_status status;
    try { f(); return DTL_OK; }
    catch (const std::bad_alloc &) { return DTL_OUT_OF_MEMORY; }
    catch (const std::exception &e) {
        const auto *specific = dynamic_cast<const failure *>(&e);
        status = specific ? specific->status : DTL_INFERENCE_ERROR;
        if (error) { try { *error = copy(e.what()); } catch (...) {} }
    } catch (...) { status = DTL_INFERENCE_ERROR; }
    return status;
}
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
        while ((at = s.find(marker, at)) != std::string::npos) s.replace(at, marker.size(), " ");
        value = s;
    } else if (value.is_structured()) {
        for (auto &child : value) sanitize(child, marker);
    }
}
struct question {
    std::string id, type;
    int column;
    json instructions, options;
    std::vector<llama_token> tokens;
    std::vector<size_t> markers;
};
std::vector<question> parse(const json &body) {
    require(body.is_object(), "Request must be an object");
    require(body.contains("state") && !body["state"].is_null(), "state is required");
    require(!body.contains("images") && !body.contains("files"), "Media is unsupported");
    require(body.contains("questions") && body["questions"].is_object() &&
            !body["questions"].empty() && body["questions"].size() <= 32,
            "questions must be an object with 1..32 entries");
    std::vector<question> result;
    for (auto &[id, q] : body["questions"].items()) {
        require(q.is_object() && q.contains("instructions") && !q["instructions"].is_null(),
                id + ": instructions are required");
        require(q.contains("type") && q["type"].is_string(), id + ": type is required");
        question out{id, q["type"].get<std::string>(), 0, q["instructions"], json::array(), {}, {}};
        json criteria = q.value("criteria", json());
        auto add = [&](const std::string &key, const json &description) {
            out.options.push_back({{"key", key}, {"description", description}});
        };
        if (out.type == "choice") {
            require(criteria.is_object() && criteria.size() >= 2 && criteria.size() <= 255,
                    id + ": choice criteria must contain 2..255 options");
            for (auto &[key, description] : criteria.items()) add(key, description);
        } else if (out.type == "score") {
            out.column = 1;
            require(criteria.is_array() && criteria.size() >= 2 && criteria.size() <= 10,
                    id + ": score criteria must be an array of 2..10 levels");
            for (size_t i = 0; i < criteria.size(); ++i) add(std::to_string(i), criteria[i]);
        } else if (out.type == "noul") {
            out.column = 2;
            require(criteria.is_null() || criteria.is_object(), id + ": noul criteria must be an object");
            if (criteria.is_object()) for (auto &[key, ignored] : criteria.items())
                require(key == "false" || key == "true", id + ": noul keys must be false or true");
            for (const char *key : {"false", "true"})
                add(key, criteria.is_object() ? criteria.value(key, json()) : json());
        } else require(false, id + ": supported types are choice, score, noul");
        result.push_back(std::move(out));
    }
    return result;
}
}
struct dtl_engine {
    std::unique_ptr<llama_model, decltype(&llama_model_free)> model{nullptr, llama_model_free};
    std::unique_ptr<llama_context, decltype(&llama_free)> ctx{nullptr, llama_free};
    const llama_vocab *vocab = nullptr;
    std::string source, marker_text;
    jinja::program program;
    llama_token marker, sep;
    size_t max_head;
    std::map<std::string, float> temperatures;
    std::mutex mutex;

    dtl_engine(const char *path, uint32_t threads) {
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
        q.tokens.resize(-n);
        n = llama_tokenize(vocab, rendered.data(), static_cast<int32_t>(rendered.size()), q.tokens.data(), q.tokens.size(), false, true);
        if (n < 0) throw failure(DTL_INFERENCE_ERROR, "Tokenization failed");
        q.tokens.resize(n);
        for (size_t i = 0; i < q.tokens.size(); ++i) if (q.tokens[i] == marker) q.markers.push_back(i);
        require(q.markers.size() == q.options.size() && !q.markers.empty() && q.markers[0] >= 2 &&
                q.tokens[q.markers[0]-1] == sep && q.tokens.back() == sep, "Invalid decision prompt layout");
        size_t opts_end = std::find(q.tokens.begin() + q.markers.back(), q.tokens.end(), sep) - q.tokens.begin();
        require(opts_end + 1 < q.tokens.size(), "Invalid option separator");
        size_t total = opts_end - q.markers[0];
        size_t cap = total + 16 > max_head ? std::max(size_t(4), (max_head - std::min(max_head, size_t(16))) / q.options.size()) : 49;
        // Reject where upstream would truncate; otherwise the prompt is identical.
        for (size_t i = 0; i < q.markers.size(); ++i) {
            size_t end = i + 1 < q.markers.size() ? q.markers[i+1] : opts_end;
            require(end - q.markers[i] <= std::min(size_t(49), cap), q.id + ": option text exceeds the model's head budget");
        }
        size_t head_length = q.markers[0] - 2;
        require(head_length <= std::max(size_t(8), max_head - std::min(max_head, total)),
                q.id + ": instructions exceed the model's head budget");
    }
    json run(const question &q) {
        if (auto memory = llama_get_memory(ctx.get())) llama_memory_clear(memory, true);
        auto batch = llama_batch_init(q.tokens.size(), 0, 1);
        struct cleanup { llama_batch &b; ~cleanup() { llama_batch_free(b); } } guard{batch};
        if (!batch.token || !batch.pos || !batch.n_seq_id || !batch.seq_id || !batch.logits) throw std::bad_alloc();
        batch.n_tokens = q.tokens.size();
        for (int i = 0; i < batch.n_tokens; ++i) {
            batch.token[i] = q.tokens[i]; batch.pos[i] = i; batch.n_seq_id[i] = 1;
            batch.seq_id[i][0] = 0; batch.logits[i] = 1;
        }
        int status = llama_encode(ctx.get(), batch);
        if (status != 0) throw failure(DTL_INFERENCE_ERROR, "Laya forward pass failed: " + std::to_string(status));
        std::vector<float> scores;
        for (size_t index : q.markers) {
            const float *v = llama_get_embeddings_ith(ctx.get(), index);
            if (!v || !std::isfinite(v[q.column])) throw failure(DTL_INFERENCE_ERROR, "Missing/nonfinite decision output");
            scores.push_back(v[q.column]);
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
extern "C" {
uint32_t dtl_abi_version(void) { return DTL_ABI_VERSION; }
dtl_options dtl_default_options(void) { return {sizeof(dtl_options), DTL_ABI_VERSION, 0, 0}; }
dtl_status dtl_open(const char *path, const dtl_options *options, dtl_engine **out, char **error) {
    if (out) *out = nullptr;
    return boundary(error, [&] {
        require(out && path && *path, "model_path and out are required");
        require(!options || options->struct_size == sizeof(dtl_options), "Incompatible options size");
        auto o = options ? *options : dtl_default_options();
        require(o.struct_size == sizeof(dtl_options) && o.abi_version == DTL_ABI_VERSION && o.reserved == 0,
                "Incompatible options/ABI version");
        require(o.threads <= 256, "threads must be 0..256");
        try { *out = new dtl_engine(path, o.threads); }
        catch (const failure &) { throw; }
        catch (const std::bad_alloc &) { throw; }
        catch (const std::exception &e) { throw failure(DTL_MODEL_ERROR, e.what()); }
    });
}
dtl_status dtl_decide(dtl_engine *engine, const char *request, char **result, char **error) {
    if (result) *result = nullptr;
    return boundary(error, [&] {
        require(engine && request && result, "engine, request_json and result_json are required");
        size_t n = 0;
        while (n <= max_request && request[n]) ++n;
        require(n <= max_request, "Request exceeds 1 MiB");
        json body;
        try {
            body = json::parse(request, [](int depth, json::parse_event_t, json &) {
                require(depth <= 64, "JSON nesting exceeds 64 levels");
                return true;
            });
        }
        catch (const json::exception &e) { throw failure(DTL_INVALID_ARGUMENT, e.what()); }
        auto questions = parse(body);
        std::lock_guard<std::mutex> lock(engine->mutex);
        for (auto &q : questions) engine->prepare(q, body["state"]);
        json answers = json::object();
        size_t tokens = 0;
        for (const auto &q : questions) { answers[q.id] = engine->run(q); tokens += q.tokens.size(); }
        *result = copy(json({{"answers", answers}, {"usage", {{"input_tokens", tokens}, {"output_tokens", 0}}}}).dump());
    });
}
void dtl_close(dtl_engine *engine) { delete engine; }
void dtl_free(void *p) { std::free(p); }
}
