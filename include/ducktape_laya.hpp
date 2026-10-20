#pragma once
#include "ducktape_laya.h"
#include <cstdint>
#include <functional>
#include <memory>
#include <stdexcept>
#include <string>
namespace ducktape {
class error : public std::runtime_error {
public:
    dtl_status status;
    error(dtl_status s, const std::string &message) : std::runtime_error(message), status(s) {}
};
inline void check(dtl_status status, char *message) {
    std::unique_ptr<char, decltype(&dtl_free)> owned(message, dtl_free);
    if (status != DTL_OK) throw error(status, message ? message : "Laya operation failed");
}
inline void check_text(const std::string &text, const char *what) {
    if (text.find('\0') != std::string::npos) throw error(DTL_INVALID_ARGUMENT, std::string(what) + " contains a NUL byte");
}
// Runtime identity, limits and CPU compatibility, without loading a model.
inline std::string info() {
    char *result = nullptr, *message = nullptr;
    auto status = dtl_info(nullptr, &result, &message);
    std::unique_ptr<char, decltype(&dtl_free)> owned(result, dtl_free);
    check(status, message);
    return result;
}
// Per-call controls. on_event runs synchronously on the calling thread; exceptions
// it throws are contained. should_cancel follows dtl_call_options (dtl_cancel_flag
// is a ready-made one).
struct call_options {
    uint64_t request_id = 0;
    uint64_t timeout_ms = 0;
    std::function<void(const std::string &)> on_event;
    dtl_cancel_callback should_cancel = nullptr;
    void *cancel_user_data = nullptr;
};
// A single model or a router. Calls on one engine serialize; move-only.
class engine {
    std::unique_ptr<dtl_engine, decltype(&dtl_close)> engine_{nullptr, dtl_close};
    explicit engine(dtl_engine *raw) { engine_.reset(raw); }
    static const char *optional(const std::string &config) { return config.empty() ? nullptr : config.c_str(); }
public:
    // config_json: empty, or {"threads", "context_tokens", "temperatures"}; see ducktape_laya.h.
    static engine open(const std::string &model_path, const std::string &config_json = "") {
        check_text(model_path, "Path"); check_text(config_json, "Config");
        dtl_engine *raw = nullptr;
        char *message = nullptr;
        check(dtl_open(model_path.c_str(), optional(config_json), &raw, &message), message);
        return engine(raw);
    }
    // router_json: {"models": {"id": "path.gguf" | {"path", "config"}}, "max_loaded": 1}.
    static engine open_router(const std::string &router_json, const std::string &config_json = "") {
        check_text(router_json, "Router config"); check_text(config_json, "Config");
        dtl_engine *raw = nullptr;
        char *message = nullptr;
        check(dtl_open_router(router_json.c_str(), optional(config_json), &raw, &message), message);
        return engine(raw);
    }
    engine(engine &&) noexcept = default;
    engine &operator=(engine &&) noexcept = default;
    engine(const engine &) = delete;
    engine &operator=(const engine &) = delete;
    // Model metadata for a single model; runtime capabilities for a router.
    std::string info() const {
        char *result = nullptr, *message = nullptr;
        auto status = dtl_info(engine_.get(), &result, &message);
        std::unique_ptr<char, decltype(&dtl_free)> owned(result, dtl_free);
        check(status, message);
        return result;
    }
    // One request or an array of requests; see docs/API.md.
    std::string execute(const std::string &request, const call_options &options = {}) const {
        check_text(request, "Request");
        auto native = dtl_default_call_options();
        native.request_id = options.request_id;
        native.timeout_ms = options.timeout_ms;
        native.should_cancel = options.should_cancel;
        native.cancel_user_data = options.cancel_user_data;
        if (options.on_event) {
            native.on_event = [](const char *event, void *data) {
                try { (*static_cast<const std::function<void(const std::string &)> *>(data))(event); } catch (...) {}
            };
            native.event_user_data = const_cast<std::function<void(const std::string &)> *>(&options.on_event);
        }
        char *result = nullptr, *message = nullptr;
        auto status = dtl_execute(engine_.get(), request.c_str(), &native, &result, &message);
        std::unique_ptr<char, decltype(&dtl_free)> owned(result, dtl_free);
        check(status, message);
        return result;
    }
};
} // namespace ducktape
