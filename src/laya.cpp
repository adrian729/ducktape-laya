// The C API: entry points, call boundary, observers and the router (see laya_internal.hpp for
// request validation, laya_request.cpp, and planning and evaluation, laya_plan.cpp).
#include "ducktape_laya.h"
#include "laya_internal.hpp"
#include "errors.hpp"
#include "language.hpp"
#include "python_json.hpp"
#include "runtime_info.hpp"
#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <list>
#include <map>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <vector>
using dtl::json;
using dtl::failure;
using dtl::require;
using namespace dtl::detail;
namespace {
char *copy(const std::string &s) {
    auto *p = static_cast<char *>(std::malloc(s.size() + 1));
    if (!p) throw std::bad_alloc();
    std::memcpy(p, s.c_str(), s.size() + 1);
    return p;
}
// Error messages are valid UTF-8 of at most 1 KiB: they can quote request text, paths, model
// metadata and nlohmann's last read bytes, and every binding decodes them as UTF-8.
std::string error_text(const char *message) {
    constexpr size_t limit = 1024;
    auto text = dtl::parse_json(dtl::dump_json(json(message), -1, ' ', false, json::error_handler_t::replace)).get<std::string>();
    if (text.size() <= limit) return text;
    size_t end = limit - 3;
    while (end > 0 && (static_cast<unsigned char>(text[end]) & 0xC0) == 0x80) --end;
    return text.substr(0, end) + "...";
}
// The canonical path of a regular file, or empty: a directory, FIFO or device is not a model (a
// FIFO would block the load). Paths are UTF-8 in the C ABI: u8path/u8string convert to and from
// wide strings on Windows (the narrow forms use the ANSI code page there) and keep the bytes
// unchanged on POSIX. llama.cpp opens with ggml_fopen (UTF-8).
std::string regular_file(const std::string &path) {
    try {
        std::error_code ec;
        auto canonical = std::filesystem::canonical(std::filesystem::u8path(path), ec);
        if (!ec && std::filesystem::is_regular_file(canonical, ec) && !ec) return canonical.u8string();
    } catch (const std::system_error &) {}  // not convertible on this platform
    return {};
}
template<class F> dtl_status boundary(char **error, F f) noexcept {
    if (error) *error = nullptr;
    dtl_status status;
    try { f(); return DTL_OK; }
    catch (const std::bad_alloc &) { return DTL_OUT_OF_MEMORY; }
    catch (const std::exception &e) {
        const auto *specific = dynamic_cast<const failure *>(&e);
        status = specific ? specific->status : DTL_INFERENCE_ERROR;
        if (error) { try { *error = copy(error_text(e.what())); } catch (...) {} }
    } catch (...) { status = DTL_INFERENCE_ERROR; }
    return status;
}
struct observer {
    uint64_t request_id = 0;
    dtl_event_callback callback = nullptr;
    void *user_data = nullptr;
};
void event(const observer &o, const char *kind, dtl_status status, double elapsed_ms = 0) noexcept {
    if (!o.callback) return;
    try {
        auto text = dtl::dump_json(json({{"event", kind}, {"request_id", o.request_id},
                                         {"status", status}, {"elapsed_ms", elapsed_ms}}));
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
        *result = copy(dtl::result_json(fn()));
    });
    const double elapsed = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
    event(saved, status == DTL_OK ? "end" : "error", status, elapsed);
    return status;
}
}
struct router_state {
    struct route { std::string path, cache_key; dtl::engine_config config; };
    std::map<std::string, route> routes;
    std::string auto_english, auto_default; // routes for "model": "auto"; empty when not configured
    std::map<std::string, std::unique_ptr<model_engine>> loaded; // by canonical path and config
    std::list<std::string> lru;
    // context_tokens of each model loaded so far, by cache key: with it, a request of
    // single-option choices is checked and answered without loading (or evicting) a model.
    std::map<std::string, size_t> capacities;
    size_t max_loaded = 1;
    dtl::fair_mutex mutex;
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
        capacities[r.cache_key] = it->second->runtime.capacity();
        return *it->second;
    }
};
struct dtl_engine {
    std::unique_ptr<model_engine> single;
    std::string overflow; // a single model's default policy
    size_t max_windows = dtl::default_call_windows; // forward passes one call may plan
    std::unique_ptr<router_state> router;
};
extern "C" {
uint32_t dtl_abi_version(void) { return DTL_ABI_VERSION; }
dtl_status dtl_open(const char *path, const char *config_json, dtl_engine **out, char **error) {
    if (out) *out = nullptr;
    return boundary(error, [&] {
        require(out && path && *path, "model_path and out are required");
        auto config = engine_config(optional_json(config_json));
        const auto resolved = regular_file(path);
        if (resolved.empty()) throw failure(DTL_MODEL_ERROR, std::string("Model path is not a local regular file: ") + path);
        auto engine = std::make_unique<dtl_engine>();
        engine->single = load_model(resolved, config);
        engine->overflow = config.overflow;
        engine->max_windows = config.max_windows_per_call;
        *out = engine.release();
    });
}
dtl_status dtl_open_router(const char *router_json, const char *config_json, dtl_engine **out, char **error) {
    if (out) *out = nullptr;
    return boundary(error, [&] {
        require(out, "out is required");
        auto shared = engine_config(optional_json(config_json));
        auto body = read_json(router_json);
        keys(body, {"models", "max_loaded", "auto"}, "router config");
        require(body.contains("models") && body["models"].is_object() && !body["models"].empty() &&
                body["models"].size() <= dtl::max_routes, "models must contain 1.." + std::to_string(dtl::max_routes) + " routes");
        auto r = std::make_unique<router_state>();
        if (body.contains("max_loaded")) {
            require(unsigned_in(body["max_loaded"], 1, dtl::max_loaded_models),
                    "max_loaded must be 1.." + std::to_string(dtl::max_loaded_models));
            r->max_loaded = body["max_loaded"].get<size_t>();
        }
        for (auto &[id, entry] : body["models"].items()) {
            require(!id.empty() && id.size() <= 128 && id.find('\0') == std::string::npos, "Invalid model ID");
            require(id != "auto", "\"auto\" is reserved and cannot be a model ID");
            json path = entry, config;
            if (entry.is_object()) {
                keys(entry, {"path", "config"}, "route " + id);
                path = entry.value("path", json());
                config = entry.value("config", json());
            }
            require(path.is_string(), "route " + id + ": path must be a string");
            auto text = path.get<std::string>();
            require(!text.empty() && text.find('\0') == std::string::npos, "route " + id + ": invalid model path");
            const auto resolved = regular_file(text);
            if (resolved.empty())
                throw failure(DTL_MODEL_ERROR, "Model path is not a local regular file: " + id);
            router_state::route route{resolved, {}, engine_config(config, shared, true)};
            // The path is raw bytes (not necessarily UTF-8), so it is not put into JSON.
            json key = {{"threads", route.config.threads}, {"context_tokens", route.config.context_tokens},
                        {"temperatures", route.config.temperatures}};  // overflow is applied per call, not per loaded model
            route.cache_key = route.path + '\0' + dtl::dump_json(key);
            r->routes[id] = std::move(route);
        }
        if (body.contains("auto")) {
            const auto &automatic = body["auto"];
            keys(automatic, {"english", "default"}, "auto");
            for (const char *key : {"english", "default"})
                require(automatic.contains(key) && automatic[key].is_string() && r->routes.count(automatic[key].get<std::string>()),
                        std::string("auto.") + key + " must name a route");
            r->auto_english = automatic["english"].get<std::string>();
            r->auto_default = automatic["default"].get<std::string>();
        }
        auto engine = std::make_unique<dtl_engine>();
        engine->router = std::move(r);
        engine->max_windows = shared.max_windows_per_call;
        *out = engine.release();
    });
}
dtl_status dtl_info(dtl_engine *engine, char **result, char **error) {
    if (result) *result = nullptr;
    return boundary(error, [&] {
        require(result, "result_json is required");
        auto info = dtl::runtime_info();
        if (engine) info["max_windows_per_call"] = engine->max_windows;
        if (engine && engine->single) {
            std::lock_guard<dtl::fair_mutex> lock(engine->single->mutex);
            info["model"] = engine->single->runtime.info();
        }
        // Model metadata strings are not guaranteed to be UTF-8.
        *result = copy(dtl::dump_json(info, -1, ' ', false, json::error_handler_t::replace));
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
        const bool batch = body.is_array(), routed = engine->router != nullptr;
        auto work = plans(std::move(body), routed);
        json results = json::array();
        if (!routed) {
            auto &single = *engine->single;
            auto lock = control.lock(single.mutex);
            size_t windows = 0;
            for (auto &p : work) {
                dtl::request_state state(p.body["state"]);
                for (auto &q : p.questions) {
                    control.check();
                    single.prepare(q, state, engine->overflow, control);
                    count_windows(windows, q, engine->max_windows);
                }
            }
            for (auto &p : work) results.push_back(evaluate(&single, p, control));
        } else {
            auto &router = *engine->router;
            std::vector<std::pair<std::string, json>> chosen; // route ID and, for "auto", why
            for (auto &p : work) {
                require(p.body.contains("model") && p.body["model"].is_string(), "Router requests require model ID");
                auto id = p.body["model"].get<std::string>();
                if (id != "auto") {
                    require(router.routes.count(id), "Unknown model ID");
                    chosen.emplace_back(id, json());
                    continue;
                }
                require(!router.auto_english.empty(), "model \"auto\" requires an \"auto\" router config");
                json routing;
                bool english;
                if (p.body.contains("lang")) {
                    english = dtl::english_tag(p.body["lang"].get<std::string>());
                    routing = {{"reason", "lang"}, {"lang", p.body["lang"]}};
                } else {
                    auto detected = dtl::detect_language(p.body["state"]);
                    english = detected.english;
                    routing = {{"reason", detected.reason}};
                    if (!detected.script.empty()) routing["script"] = detected.script;
                    if (!detected.language.empty()) routing["language"] = detected.language;
                }
                chosen.emplace_back(english ? router.auto_english : router.auto_default, std::move(routing));
            }
            auto lock = control.lock(router.mutex);
            size_t windows = 0;
            for (size_t i = 0; i < work.size(); ++i) {
                auto &p = work[i];
                const auto &id = chosen[i].first;
                control.check();
                const auto &route = router.routes.at(id);
                const auto known = router.capacities.find(route.cache_key);
                const bool without_model = known != router.capacities.end() &&
                    std::all_of(p.questions.begin(), p.questions.end(), answered_without_model);
                model_engine *model = without_model ? nullptr : &router.load(route);
                dtl::request_state state(p.body["state"]);
                for (auto &q : p.questions) {
                    control.check();
                    if (model) {
                        model->prepare(q, state, route.config.overflow, control);
                    } else {
                        if (q.overflow.empty()) q.overflow = route.config.overflow;
                        dtl::check_max_tokens(q, known->second);
                    }
                    count_windows(windows, q, engine->max_windows);
                }
                auto answer = evaluate(model, p, control); answer["model"] = id;
                if (!chosen[i].second.is_null()) answer["usage"]["routing"] = chosen[i].second;
                results.push_back(std::move(answer));
            }
        }
        return batch ? results : results[0];
    });
}
void dtl_close(dtl_engine *engine) { delete engine; }
void dtl_free(void *p) { std::free(p); }
}
