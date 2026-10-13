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
#include <filesystem>
#include <list>
#include <chrono>
#include <set>
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

namespace {
json read_json(const char *text) {
    require(text, "JSON input is required");
    size_t n = 0;
    while (n <= max_request && text[n]) ++n;
    require(n <= max_request, "Request exceeds 1 MiB");
    try {
        return json::parse(text, [](int depth, json::parse_event_t, json &) {
            require(depth <= 64, "JSON nesting exceeds 64 levels");
            return true;
        });
    } catch (const json::exception &e) { throw failure(DTL_INVALID_ARGUMENT, e.what()); }
}
void keys(const json &value, std::initializer_list<const char *> allowed, const std::string &where) {
    require(value.is_object(), where + " must be an object");
    for (auto &[key, ignored] : value.items()) {
        bool found = false;
        for (auto name : allowed) if (key == name) found = true;
        require(found, where + ": unsupported key " + key);
    }
}
struct planned {
    json body;
    std::vector<question> questions;
    json fields = json::object();
    double threshold = 0.5;
};
planned plan(json body, bool routed) {
    keys(body, {"state", "questions", "schema", "boolean_threshold", "model"}, "request");
    require(routed || !body.contains("model"), "model is only supported by a router");
    require(body.contains("questions") != body.contains("schema"), "Supply exactly one of questions or schema");
    planned out;
    if (body.contains("schema")) {
        const auto &schema = body["schema"];
        keys(schema, {"type", "properties", "required", "additionalProperties", "description", "title"}, "schema");
        require(schema.contains("type") && schema["type"] == "object", "schema.type must be object");
        require(schema.contains("properties") && schema["properties"].is_object() &&
                !schema["properties"].empty() && schema["properties"].size() <= 32, "schema needs 1..32 properties");
        if (schema.contains("additionalProperties"))
            require(schema["additionalProperties"] == false, "additionalProperties must be false");
        if (schema.contains("required")) {
            require(schema["required"].is_array(), "required must be an array");
            std::set<std::string> seen;
            for (auto &name : schema["required"]) {
                require(name.is_string(), "required entries must be strings");
                const auto key = name.get<std::string>();
                require(schema["properties"].contains(key) && seen.insert(key).second, "Invalid/duplicate required property");
            }
        }
        if (body.contains("boolean_threshold")) {
            require(body["boolean_threshold"].is_number(), "boolean_threshold must be numeric");
            out.threshold = body["boolean_threshold"].get<double>();
            require(std::isfinite(out.threshold) && out.threshold >= 0 && out.threshold <= 1,
                    "boolean_threshold must be 0..1");
        }
        json questions = json::object();
        for (auto &[name, prop] : schema["properties"].items()) {
            keys(prop, {"type", "description", "title", "enum", "minimum", "maximum"}, "property " + name);
            require(prop.contains("type") && prop["type"].is_string(), name + ": type is required");
            require(!prop.contains("description") || prop["description"].is_string(), name + ": description must be text");
            json q = {{"instructions", prop.value("description", json(name))}};
            const auto type = prop["type"].get<std::string>();
            if (type == "string") {
                require(!prop.contains("minimum") && !prop.contains("maximum"), name + ": bounds only apply to integers");
                require(prop.contains("enum") && prop["enum"].is_array() && prop["enum"].size() >= 2 &&
                        prop["enum"].size() <= 255, name + ": string enum needs 2..255 values");
                json criteria = json::object();
                for (const auto &value : prop["enum"]) {
                    require(value.is_string(), name + ": enum values must be strings");
                    auto label = value.get<std::string>();
                    require(!criteria.contains(label), name + ": duplicate enum value");
                    criteria[label] = label;
                }
                q["type"] = "choice"; q["criteria"] = criteria;
            } else if (type == "boolean") {
                require(!prop.contains("enum") && !prop.contains("minimum") && !prop.contains("maximum"),
                        name + ": boolean constraints are unsupported");
                q["type"] = "noul";
            } else if (type == "integer") {
                require(!prop.contains("enum") && prop.contains("minimum") && prop.contains("maximum") &&
                        prop["minimum"].is_number_integer() && prop["maximum"].is_number_integer(),
                        name + ": integer requires minimum and maximum");
                // Keep arithmetic and cross-language JSON values exact.
                const double lo = prop["minimum"].get<double>(), hi = prop["maximum"].get<double>();
                require(lo >= -2147483648.0 && hi <= 2147483647.0 && hi > lo && hi - lo <= 9,
                        name + ": integer range must have 2..10 int32 levels");
                q["type"] = "score"; q["criteria"] = json::array();
                for (int64_t v = prop["minimum"].get<int64_t>(); v <= prop["maximum"].get<int64_t>(); ++v)
                    q["criteria"].push_back(std::to_string(v));
            } else require(false, name + ": supported properties are string enum, boolean, bounded integer");
            questions[name] = std::move(q);
            out.fields[name] = prop;
        }
        body["questions"] = std::move(questions);
    } else require(!body.contains("boolean_threshold"), "boolean_threshold requires schema");
    out.questions = parse(body);
    out.body = std::move(body);
    return out;
}
std::vector<planned> plans(const json &body, bool routed) {
    const auto requests = body.is_array() ? body : json::array({body});
    require(!requests.empty() && requests.size() <= 32, "Batch must contain 1..32 requests");
    std::vector<planned> result;
    size_t count = 0;
    for (auto &request : requests) {
        result.push_back(plan(request, routed));
        count += result.back().questions.size();
        require(count <= 128, "Batch exceeds 128 questions");
    }
    return result;
}
json evaluate(dtl_engine &engine, planned &p) {
    json answers = json::object();
    size_t tokens = 0;
    for (const auto &q : p.questions) { answers[q.id] = engine.run(q); tokens += q.tokens.size(); }
    json result = {{"answers", answers}, {"usage", {{"input_tokens", tokens}, {"output_tokens", 0}}}};
    if (!p.fields.empty()) {
        json values = json::object();
        for (auto &[name, field] : p.fields.items()) {
            const auto &a = answers[name];
            if (field["type"] == "boolean") values[name] = a["noul"].get<double>() >= p.threshold;
            else if (field["type"] == "string") values[name] = a["choice"];
            else {
                size_t best = 0, index = 0;
                double probability = -1;
                for (auto &v : a["probabilities"]) {
                    if (v.get<double>() > probability) { probability = v.get<double>(); best = index; }
                    ++index;
                }
                values[name] = field["minimum"].get<int64_t>() + static_cast<int64_t>(best);
            }
        }
        result["values"] = std::move(values);
    }
    return result;
}
void event(const dtl_observer *observer, const char *kind, dtl_status status,
           double elapsed_ms = 0) noexcept {
    if (!observer || !observer->callback) return;
    try {
        auto text = json({{"event", kind}, {"request_id", observer->request_id},
                          {"status", status}, {"elapsed_ms", elapsed_ms}}).dump();
        observer->callback(text.c_str(), observer->user_data);
    } catch (...) {} // Passive telemetry cannot fail an inference call.
}
template<class F> dtl_status observed(const dtl_observer *observer, char **result, char **error, F fn) {
    // Copy before callbacks so reentry cannot replace the observer for this call.
    dtl_observer saved = observer ? *observer : dtl_observer{};
    const auto start = std::chrono::steady_clock::now();
    if (result) *result = nullptr;
    event(&saved, "start", DTL_OK);
    const auto status = boundary(error, [&] {
        require(result, "result_json is required");
        *result = copy(fn().dump());
    });
    const double elapsed = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
    event(&saved, status == DTL_OK ? "end" : "error", status, elapsed);
    return status;
}
dtl_options validate_options(const dtl_options *options) {
    require(!options || options->struct_size == sizeof(dtl_options), "Incompatible options size");
    auto o = options ? *options : dtl_default_options();
    require(o.abi_version == DTL_ABI_VERSION && o.reserved == 0 && o.threads <= 256, "Invalid options");
    return o;
}
}
struct dtl_router {
    std::map<std::string, std::string> paths;
    std::map<std::string, std::unique_ptr<dtl_engine>> loaded;
    std::list<std::string> lru;
    size_t max_loaded = 1;
    uint32_t threads = 0;
    std::mutex mutex;
    dtl_engine &load(const std::string &path) {
        auto it = loaded.find(path);
        if (it == loaded.end()) {
            if (loaded.size() == max_loaded) { loaded.erase(lru.front()); lru.pop_front(); }
            std::unique_ptr<dtl_engine> engine;
            try { engine = std::make_unique<dtl_engine>(path.c_str(), threads); }
            catch (const failure &) { throw; }
            catch (const std::bad_alloc &) { throw; }
            catch (const std::exception &e) { throw failure(DTL_MODEL_ERROR, e.what()); }
            // Allocate the LRU entry before publishing; retain consistency on OOM.
            lru.push_back(path);
            try { it = loaded.emplace(path, std::move(engine)).first; }
            catch (...) { lru.pop_back(); throw; }
        } else {
            auto pos = std::find(lru.begin(), lru.end(), path);
            lru.splice(lru.end(), lru, pos);
        }
        return *it->second;
    }
};
extern "C" {
dtl_status dtl_execute(dtl_engine *engine, const char *request, const dtl_observer *observer,
                       char **result, char **error) {
    return observed(observer, result, error, [&] {
        require(engine, "engine is required");
        auto body = read_json(request);
        auto work = plans(body, false);
        json results = json::array();
        {
            std::lock_guard<std::mutex> lock(engine->mutex);
            for (auto &p : work) for (auto &q : p.questions) engine->prepare(q, p.body["state"]);
            for (auto &p : work) results.push_back(evaluate(*engine, p));
        }
        return body.is_array() ? results : results[0];
    });
}
dtl_status dtl_router_open(const char *config, const dtl_options *options, dtl_router **out, char **error) {
    if (out) *out = nullptr;
    return boundary(error, [&] {
        require(out, "out is required");
        auto o = validate_options(options);
        auto body = read_json(config);
        keys(body, {"models", "max_loaded"}, "router config");
        require(body.contains("models") && body["models"].is_object() && !body["models"].empty() &&
                body["models"].size() <= 16, "models must contain 1..16 routes");
        auto r = std::make_unique<dtl_router>(); r->threads = o.threads;
        if (body.contains("max_loaded")) {
            require(body["max_loaded"].is_number_integer() && body["max_loaded"] >= 1 && body["max_loaded"] <= 4,
                    "max_loaded must be 1..4");
            r->max_loaded = body["max_loaded"].get<size_t>();
        }
        for (auto &[id, path] : body["models"].items()) {
            require(!id.empty() && id.size() <= 128 && id.find('\0') == std::string::npos, "Invalid model ID");
            require(path.is_string(), "Model paths must be strings");
            auto text = path.get<std::string>();
            require(!text.empty() && text.find('\0') == std::string::npos, "Invalid model path");
            std::error_code ec;
            auto canonical = std::filesystem::canonical(text, ec);
            if (ec || !std::filesystem::is_regular_file(canonical, ec) || ec)
                throw failure(DTL_MODEL_ERROR, "Model path is not a local regular file: " + id);
            r->paths[id] = canonical.string();
        }
        *out = r.release();
    });
}
dtl_status dtl_router_execute(dtl_router *router, const char *request, const dtl_observer *observer,
                              char **result, char **error) {
    return observed(observer, result, error, [&] {
        require(router, "router is required");
        auto body = read_json(request);
        auto work = plans(body, true);
        for (auto &p : work) {
            require(p.body.contains("model") && p.body["model"].is_string(), "Router requests require model ID");
            require(router->paths.count(p.body["model"].get<std::string>()), "Unknown model ID");
        }
        json results = json::array();
        {
            std::lock_guard<std::mutex> lock(router->mutex);
            for (auto &p : work) {
                auto id = p.body["model"].get<std::string>();
                auto &engine = router->load(router->paths.at(id));
                for (auto &q : p.questions) engine.prepare(q, p.body["state"]);
                auto answer = evaluate(engine, p); answer["model"] = id;
                results.push_back(std::move(answer));
            }
        }
        return body.is_array() ? results : results[0];
    });
}
void dtl_router_close(dtl_router *router) { delete router; }
}
