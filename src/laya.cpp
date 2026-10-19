#include "ducktape_laya.h"
#include "backend.hpp"
#include "decision.hpp"
#include "errors.hpp"
#include "execution.hpp"
#include "platform.hpp"
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
#include <vector>
using dtl::json;
using dtl::question;
using dtl::failure;
using dtl::require;
using dtl::max_request;
namespace {
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
        question out{id, q["type"].get<std::string>(), q["instructions"], json::array()};
        json criteria = q.value("criteria", json());
        auto add = [&](const std::string &key, const json &description) {
            out.options.push_back({{"key", key}, {"description", description}});
        };
        if (out.type == "choice") {
            require((criteria.is_object() || criteria.is_array()) && !criteria.empty() && criteria.size() <= 255,
                    id + ": choice criteria must contain 1..255 options");
            if (criteria.is_object()) {
                for (auto &[key, description] : criteria.items()) add(key, description);
            } else {  // a plain list of labels without descriptions
                std::set<std::string> seen;
                for (auto &label : criteria) {
                    require(label.is_string() && !label.get<std::string>().empty() && seen.insert(label.get<std::string>()).second,
                            id + ": choice labels must be distinct non-empty strings");
                    add(label.get<std::string>(), json());
                }
            }
        } else if (out.type == "score") {
            require(criteria.is_array() && criteria.size() >= 2 && criteria.size() <= 10,
                    id + ": score criteria must be an array of 2..10 levels");
            for (size_t i = 0; i < criteria.size(); ++i) add(std::to_string(i), criteria[i]);
        } else if (out.type == "noul") {
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
void schema_metadata(const json &value, const std::string &where) {
    for (const char *name : {"title", "description"})
        require(!value.contains(name) || value[name].is_string(), where + ": " + name + " must be text");
}
struct planned {
    json body;
    std::vector<question> questions;
    json fields = json::object();
    double threshold = 0.5;
};
dtl::temperature_map temperatures(const json &value, const std::string &where) {
    require(value.is_object(), where + " must be an object");
    dtl::temperature_map out;
    for (auto &[key, t] : value.items()) {
        auto dot = key.find('.');
        auto type = key.substr(0, dot), bucket = dot == std::string::npos ? std::string() : key.substr(dot + 1);
        require((type == "choice" || type == "score" || type == "noul") &&
                (dot == std::string::npos || bucket == "2" || bucket == "3_5" || bucket == "6_10" || bucket == "11"),
                where + ": unsupported key " + key);
        require(t.is_number() && std::isfinite(t.get<double>()) && t.get<double>() > 0, where + "." + key + " must be > 0");
        out[key] = t.get<float>();
    }
    return out;
}
planned plan(json body, bool routed) {
    keys(body, {"state", "questions", "schema", "boolean_threshold", "model", "max_tokens", "temperatures"}, "request");
    require(routed || !body.contains("model"), "model is only supported by a router");
    require(body.contains("questions") != body.contains("schema"), "Supply exactly one of questions or schema");
    planned out;
    if (body.contains("schema")) {
        const auto &schema = body["schema"];
        keys(schema, {"type", "properties", "required", "additionalProperties", "description", "title"}, "schema");
        schema_metadata(schema, "schema");
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
            schema_metadata(prop, "property " + name);
            require(prop.contains("type") && prop["type"].is_string(), name + ": type is required");
            json q = {{"instructions", prop.value("description", json(name))}};
            const auto type = prop["type"].get<std::string>();
            if (type == "string") {
                require(!prop.contains("minimum") && !prop.contains("maximum"), name + ": bounds only apply to integers");
                require(prop.contains("enum") && prop["enum"].is_array() && !prop["enum"].empty() &&
                        prop["enum"].size() <= 255, name + ": string enum needs 1..255 values");
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
    size_t max_tokens = 0;
    if (body.contains("max_tokens")) {
        require(body["max_tokens"].is_number_unsigned() && body["max_tokens"] >= 1, "max_tokens must be a positive integer");
        max_tokens = body["max_tokens"].get<size_t>();
    }
    std::shared_ptr<const dtl::temperature_map> overrides;
    if (body.contains("temperatures"))
        overrides = std::make_shared<const dtl::temperature_map>(temperatures(body["temperatures"], "temperatures"));
    for (auto &q : out.questions) { q.max_tokens = max_tokens; q.temperatures = overrides; }
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
struct model_engine {
    dtl::backend runtime;
    std::mutex mutex;
    model_engine(const char *path, const dtl::engine_config &config) : runtime(path, config) {}
};
json evaluate(model_engine &engine, planned &p, const dtl::execution &control) {
    json answers = json::object();
    size_t tokens = 0;
    for (const auto &q : p.questions) { answers[q.id] = engine.runtime.run(q, &control); tokens += q.token_count(); }
    json result = {{"answers", answers}, {"usage", {{"input_tokens", tokens}, {"output_tokens", 0}}}};
    if (!p.fields.empty()) {
        json values = json::object();
        for (auto &[name, field] : p.fields.items()) {
            const auto &a = answers[name];
            if (field["type"] == "boolean") values[name] = a["noul"].get<double>() >= p.threshold;
            else if (field["type"] == "string") values[name] = a["choice"];
            else values[name] = field["minimum"].get<int64_t>() + static_cast<int64_t>(dtl::ordinal_index(a["probabilities"]));
        }
        result["values"] = std::move(values);
    }
    return result;
}
struct observer {
    uint64_t request_id = 0;
    dtl_event_callback callback = nullptr;
    void *user_data = nullptr;
};
void event(const observer &o, const char *kind, dtl_status status, double elapsed_ms = 0) noexcept {
    if (!o.callback) return;
    try {
        auto text = json({{"event", kind}, {"request_id", o.request_id},
                          {"status", status}, {"elapsed_ms", elapsed_ms}}).dump();
        o.callback(text.c_str(), o.user_data);
    } catch (...) {} // Passive telemetry cannot fail an inference call.
}
template<class F> dtl_status observed(const dtl_call_options *options, char **result, char **error, F fn) {
    // Copy before callbacks so reentry cannot replace the observer for this call.
    observer saved;
    if (options && options->struct_size == sizeof(dtl_call_options))
        saved = {options->request_id, options->on_event, options->event_user_data};
    const auto start = std::chrono::steady_clock::now();
    if (result) *result = nullptr;
    if (error) *error = nullptr;
    event(saved, "start", DTL_OK);
    const auto status = boundary(error, [&] {
        require(result, "result_json is required");
        *result = copy(fn().dump());
    });
    const double elapsed = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
    event(saved, status == DTL_OK ? "end" : "error", status, elapsed);
    return status;
}
dtl::engine_config engine_config(const json &value, dtl::engine_config base = {}) {
    if (value.is_null()) return base;
    keys(value, {"threads", "context_tokens", "temperatures"}, "config");
    if (value.contains("threads")) {
        require(value["threads"].is_number_unsigned() && value["threads"] <= 256, "threads must be 0..256");
        base.threads = value["threads"].get<uint32_t>();
    }
    if (value.contains("context_tokens")) {
        require(value["context_tokens"].is_number_unsigned() && value["context_tokens"] >= 1, "context_tokens must be a positive integer");
        base.context_tokens = value["context_tokens"].get<size_t>();
    }
    if (value.contains("temperatures"))
        for (auto &[key, t] : temperatures(value["temperatures"], "config.temperatures")) base.temperatures[key] = t;
    return base;
}
json optional_json(const char *text) { return text ? read_json(text) : json(); }
std::unique_ptr<model_engine> load_model(const std::string &path, const dtl::engine_config &config) {
    try { return std::make_unique<model_engine>(path.c_str(), config); }
    catch (const failure &) { throw; }
    catch (const std::bad_alloc &) { throw; }
    catch (const std::exception &e) { throw failure(DTL_MODEL_ERROR, e.what()); }
}
}
struct router_state {
    struct route { std::string path, cache_key; dtl::engine_config config; };
    std::map<std::string, route> routes;
    std::map<std::string, std::unique_ptr<model_engine>> loaded; // by canonical path and config
    std::list<std::string> lru;
    size_t max_loaded = 1;
    std::mutex mutex;
    model_engine &load(const route &r) {
        auto it = loaded.find(r.cache_key);
        if (it == loaded.end()) {
            if (loaded.size() == max_loaded) { loaded.erase(lru.front()); lru.pop_front(); }
            auto engine = load_model(r.path, r.config);
            // Allocate the LRU entry before publishing; retain consistency on OOM.
            lru.push_back(r.cache_key);
            try { it = loaded.emplace(r.cache_key, std::move(engine)).first; }
            catch (...) { lru.pop_back(); throw; }
        } else {
            lru.splice(lru.end(), lru, std::find(lru.begin(), lru.end(), r.cache_key));
        }
        return *it->second;
    }
};
struct dtl_engine {
    std::unique_ptr<model_engine> single;
    std::unique_ptr<router_state> router;
};
extern "C" {
uint32_t dtl_abi_version(void) { return DTL_ABI_VERSION; }
dtl_status dtl_open(const char *path, const char *config_json, dtl_engine **out, char **error) {
    if (out) *out = nullptr;
    return boundary(error, [&] {
        require(out && path && *path, "model_path and out are required");
        auto config = engine_config(optional_json(config_json));
        auto engine = std::make_unique<dtl_engine>();
        engine->single = load_model(path, config);
        *out = engine.release();
    });
}
dtl_status dtl_open_router(const char *router_json, const char *config_json, dtl_engine **out, char **error) {
    if (out) *out = nullptr;
    return boundary(error, [&] {
        require(out, "out is required");
        auto shared = engine_config(optional_json(config_json));
        auto body = read_json(router_json);
        keys(body, {"models", "max_loaded"}, "router config");
        require(body.contains("models") && body["models"].is_object() && !body["models"].empty() &&
                body["models"].size() <= 16, "models must contain 1..16 routes");
        auto r = std::make_unique<router_state>();
        if (body.contains("max_loaded")) {
            require(body["max_loaded"].is_number_integer() && body["max_loaded"] >= 1 && body["max_loaded"] <= 4,
                    "max_loaded must be 1..4");
            r->max_loaded = body["max_loaded"].get<size_t>();
        }
        for (auto &[id, entry] : body["models"].items()) {
            require(!id.empty() && id.size() <= 128 && id.find('\0') == std::string::npos, "Invalid model ID");
            json path = entry, config;
            if (entry.is_object()) {
                keys(entry, {"path", "config"}, "route " + id);
                path = entry.value("path", json());
                config = entry.value("config", json());
            }
            require(path.is_string(), "route " + id + ": path must be a string");
            auto text = path.get<std::string>();
            require(!text.empty() && text.find('\0') == std::string::npos, "route " + id + ": invalid model path");
            std::error_code ec;
            auto canonical = std::filesystem::canonical(text, ec);
            if (ec || !std::filesystem::is_regular_file(canonical, ec) || ec)
                throw failure(DTL_MODEL_ERROR, "Model path is not a local regular file: " + id);
            router_state::route route{canonical.string(), {}, engine_config(config, shared)};
            json key = {{"path", route.path}, {"threads", route.config.threads}, {"context_tokens", route.config.context_tokens},
                        {"temperatures", route.config.temperatures}};
            route.cache_key = key.dump();
            r->routes[id] = std::move(route);
        }
        auto engine = std::make_unique<dtl_engine>();
        engine->router = std::move(r);
        *out = engine.release();
    });
}
dtl_status dtl_info(dtl_engine *engine, char **result, char **error) {
    if (result) *result = nullptr;
    return boundary(error, [&] {
        require(result, "result_json is required");
        auto info = dtl::runtime_info();
        if (engine && engine->single) {
            std::lock_guard<std::mutex> lock(engine->single->mutex);
            info["model"] = engine->single->runtime.info();
        }
        *result = copy(info.dump());
    });
}
dtl_call_options dtl_default_call_options(void) {
    return {sizeof(dtl_call_options), 0, 0, 0, nullptr, nullptr, nullptr, nullptr};
}
uint32_t dtl_cancel_flag(void *flag) {
    return flag ? __atomic_load_n(static_cast<const uint32_t *>(flag), __ATOMIC_ACQUIRE) : 0;
}
dtl_status dtl_execute(dtl_engine *engine, const char *request, const dtl_call_options *options, char **result, char **error) {
    return observed(options, result, error, [&] {
        dtl::execution control(options); control.check();
        require(engine, "engine is required");
        auto body = read_json(request);
        const bool routed = engine->router != nullptr;
        auto work = plans(body, routed);
        json results = json::array();
        if (!routed) {
            auto &single = *engine->single;
            auto lock = control.lock(single.mutex);
            for (auto &p : work) for (auto &q : p.questions) { control.check(); single.runtime.prepare(q, p.body["state"]); }
            for (auto &p : work) results.push_back(evaluate(single, p, control));
        } else {
            auto &router = *engine->router;
            for (auto &p : work) {
                require(p.body.contains("model") && p.body["model"].is_string(), "Router requests require model ID");
                require(router.routes.count(p.body["model"].get<std::string>()), "Unknown model ID");
            }
            auto lock = control.lock(router.mutex);
            for (auto &p : work) {
                auto id = p.body["model"].get<std::string>();
                control.check();
                auto &model = router.load(router.routes.at(id));
                for (auto &q : p.questions) { control.check(); model.runtime.prepare(q, p.body["state"]); }
                auto answer = evaluate(model, p, control); answer["model"] = id;
                results.push_back(std::move(answer));
            }
        }
        return body.is_array() ? results : results[0];
    });
}
void dtl_close(dtl_engine *engine) { delete engine; }
void dtl_free(void *p) { std::free(p); }
}
