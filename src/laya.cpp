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
struct dtl_engine {
    dtl::backend runtime;
    std::mutex mutex;
    dtl_engine(const char *path, uint32_t threads) : runtime(path, threads) {}
};
extern "C" {
dtl_status dtl_info(dtl_engine *engine, char **result, char **error) {
    if (result) *result = nullptr;
    return boundary(error, [&] {
        require(result, "result_json is required");
        auto info = dtl::runtime_info();
        if (engine) { std::lock_guard<std::mutex> lock(engine->mutex); info["model"] = engine->runtime.info(); }
        *result = copy(info.dump());
    });
}
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
static dtl_status decide_controlled(dtl_engine *engine, const char *request, const dtl_call_options *options, char **result, char **error) {
    if (result) *result = nullptr;
    return boundary(error, [&] {
        dtl::execution control(options); control.check();
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
        auto lock = control.lock(engine->mutex);
        for (auto &q : questions) { control.check(); engine->runtime.prepare(q, body["state"]); }
        json answers = json::object();
        size_t tokens = 0;
        for (const auto &q : questions) { answers[q.id] = engine->runtime.run(q, &control); tokens += q.token_count(); }
        *result = copy(json({{"answers", answers}, {"usage", {{"input_tokens", tokens}, {"output_tokens", 0}}}}).dump());
    });
}
dtl_status dtl_decide(dtl_engine *engine, const char *request, char **result, char **error) {
    return decide_controlled(engine, request, nullptr, result, error);
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
planned plan(json body, bool routed) {
    keys(body, {"state", "questions", "schema", "boolean_threshold", "model"}, "request");
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
json evaluate(dtl_engine &engine, planned &p, const dtl::execution &control) {
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
void event(const dtl_observer *observer, const char *kind, dtl_status status,
           double elapsed_ms = 0) noexcept {
    if (!observer || !observer->callback) return;
    try {
        auto text = json({{"event", kind}, {"request_id", observer->request_id},
                          {"status", status}, {"elapsed_ms", elapsed_ms}}).dump();
        observer->callback(text.c_str(), observer->user_data);
    } catch (...) {} // Passive telemetry cannot fail an inference call.
}
template<class F> dtl_status observed_call(const dtl_observer *observer, char **result, char **error, F fn) {
    // Copy before callbacks so reentry cannot replace the observer for this call.
    dtl_observer saved = observer ? *observer : dtl_observer{};
    const auto start = std::chrono::steady_clock::now();
    if (result) *result = nullptr;
    if (error) *error = nullptr;
    event(&saved, "start", DTL_OK);
    const auto status = fn();
    const double elapsed = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
    event(&saved, status == DTL_OK ? "end" : "error", status, elapsed);
    return status;
}
template<class F> dtl_status observed(const dtl_observer *observer, char **result, char **error, F fn) {
    return observed_call(observer, result, error, [&] {
        return boundary(error, [&] {
            require(result, "result_json is required");
            *result = copy(fn().dump());
        });
    });
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
dtl_status dtl_decide_observed(dtl_engine *engine, const char *request, const dtl_observer *observer,
                              char **result, char **error) {
    return observed_call(observer, result, error, [&] { return dtl_decide(engine, request, result, error); });
}
dtl_status dtl_execute_with_options(dtl_engine *engine, const char *request, const dtl_observer *observer,
                       const dtl_call_options *options, char **result, char **error) {
    return observed(observer, result, error, [&] {
        dtl::execution control(options); control.check();
        require(engine, "engine is required");
        auto body = read_json(request);
        auto work = plans(body, false);
        json results = json::array();
        {
            auto lock = control.lock(engine->mutex);
            for (auto &p : work) for (auto &q : p.questions) { control.check(); engine->runtime.prepare(q, p.body["state"]); }
            for (auto &p : work) results.push_back(evaluate(*engine, p, control));
        }
        return body.is_array() ? results : results[0];
    });
}
dtl_call_options dtl_default_call_options(void) { return {sizeof(dtl_call_options), 0, 0, nullptr, nullptr}; }
uint32_t dtl_cancel_flag(void *flag) {
    return flag ? __atomic_load_n(static_cast<const uint32_t *>(flag), __ATOMIC_ACQUIRE) : 0;
}
dtl_status dtl_execute(dtl_engine *engine, const char *request, const dtl_observer *observer, char **result, char **error) {
    return dtl_execute_with_options(engine, request, observer, nullptr, result, error);
}
dtl_status dtl_decide_with_options(dtl_engine *engine, const char *request, const dtl_observer *observer,
    const dtl_call_options *options, char **result, char **error) {
    return observed_call(observer, result, error, [&] { return decide_controlled(engine, request, options, result, error); });
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
dtl_status dtl_router_execute_with_options(dtl_router *router, const char *request, const dtl_observer *observer,
                              const dtl_call_options *options, char **result, char **error) {
    return observed(observer, result, error, [&] {
        dtl::execution control(options); control.check();
        require(router, "router is required");
        auto body = read_json(request);
        auto work = plans(body, true);
        for (auto &p : work) {
            require(p.body.contains("model") && p.body["model"].is_string(), "Router requests require model ID");
            require(router->paths.count(p.body["model"].get<std::string>()), "Unknown model ID");
        }
        json results = json::array();
        {
            auto lock = control.lock(router->mutex);
            for (auto &p : work) {
                auto id = p.body["model"].get<std::string>();
                control.check();
                auto &engine = router->load(router->paths.at(id));
                for (auto &q : p.questions) { control.check(); engine.runtime.prepare(q, p.body["state"]); }
                auto answer = evaluate(engine, p, control); answer["model"] = id;
                results.push_back(std::move(answer));
            }
        }
        return body.is_array() ? results : results[0];
    });
}
dtl_status dtl_router_execute(dtl_router *router, const char *request, const dtl_observer *observer, char **result, char **error) {
    return dtl_router_execute_with_options(router, request, observer, nullptr, result, error);
}
void dtl_router_close(dtl_router *router) { delete router; }
}
