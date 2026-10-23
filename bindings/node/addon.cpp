#include <node_api.h>
#include "ducktape_laya.h"
#include <cmath>
#include <memory>
#include <stdexcept>
#include <string>
struct holder { dtl_engine *engine = nullptr;
    ~holder() { dtl_close(engine); } };
struct native_error : std::runtime_error {
    int32_t status;
    native_error(int32_t s, const char *m) : std::runtime_error(m), status(s) {}
};
static void ok(napi_status s) { if (s != napi_ok) throw std::runtime_error("Invalid native addon argument"); }
static std::string string_arg(napi_env env, napi_value v) {
    size_t size; ok(napi_get_value_string_utf8(env, v, nullptr, 0, &size));
    if (size > 1048576) throw std::runtime_error("Input exceeds 1 MiB");
    std::string text(size + 1, '\0');
    ok(napi_get_value_string_utf8(env, v, text.data(), text.size(), &size));
    text.resize(size);
    if (text.find('\0') != std::string::npos) throw std::runtime_error("Input contains a NUL byte");
    return text;
}
static void check(dtl_status s, char *error) {
    std::unique_ptr<char, decltype(&dtl_free)> owned(error, dtl_free);
    if (s) throw native_error(s, error ? error : "Laya operation failed");
}
static void require_abi() {
    if (dtl_abi_version() != 2) throw native_error(DTL_UNSUPPORTED_PLATFORM, ("Native ducktape-laya ABI " + std::to_string(dtl_abi_version()) + " is not supported (expected 2)").c_str());
}
template<class F> napi_value guarded(napi_env env, F f) {
    try { return f(); }
    catch (const native_error &e) {
        napi_value message, error, status;
        napi_create_string_utf8(env, e.what(), NAPI_AUTO_LENGTH, &message);
        napi_create_error(env, nullptr, message, &error);
        napi_create_int32(env, e.status, &status);
        napi_set_named_property(env, error, "status", status);
        napi_throw(env, error);
    }
    catch (const std::exception &e) { napi_throw_error(env, nullptr, e.what()); }
    catch (...) { napi_throw_error(env, nullptr, "Native addon failure"); }
    return nullptr;
}
static napi_value open_engine(napi_env env, napi_callback_info info) {
    return guarded(env, [&] {
        require_abi();
        size_t argc = 3; napi_value args[3]; ok(napi_get_cb_info(env, info, &argc, args, nullptr, nullptr));
        if (argc != 3) throw std::runtime_error("Expected path/router JSON, config JSON and router flag");
        bool router; ok(napi_get_value_bool(env, args[2], &router));
        auto path = string_arg(env, args[0]);
        napi_valuetype type; ok(napi_typeof(env, args[1], &type));
        std::string config; if (type == napi_string) config = string_arg(env, args[1]);
        auto h = std::make_unique<holder>(); char *error = nullptr;
        const char *config_json = type == napi_string ? config.c_str() : nullptr;
        auto s = router ? dtl_open_router(path.c_str(), config_json, &h->engine, &error)
                        : dtl_open(path.c_str(), config_json, &h->engine, &error);
        check(s, error);
        napi_value value;
        ok(napi_create_external(env, h.get(), [](napi_env, void *p, void *) { delete static_cast<holder *>(p); }, nullptr, &value));
        h.release(); return value;
    });
}
static napi_value execute(napi_env env, napi_callback_info info) {
    return guarded(env, [&] {
        size_t argc = 6; napi_value args[6]; ok(napi_get_cb_info(env, info, &argc, args, nullptr, nullptr));
        if (argc != 6) throw std::runtime_error("Expected engine, request, ID, callback, cancellation and timeout");
        void *p; ok(napi_get_value_external(env, args[0], &p)); auto *h = static_cast<holder *>(p);
        if (!h->engine) throw std::runtime_error("Engine is closed");
        auto request = string_arg(env, args[1]); char *result = nullptr, *error = nullptr;
        struct callback_data { napi_env env; napi_value fn; } data{env, args[3]};
        double id; ok(napi_get_value_double(env, args[2], &id));
        if (!std::isfinite(id) || id < 0 || id > 9007199254740991.0 || std::floor(id) != id)
            throw std::runtime_error("Invalid request ID");
        napi_valuetype type; ok(napi_typeof(env, args[3], &type));
        auto options = dtl_default_call_options();
        options.request_id = static_cast<uint64_t>(id);
        if (type == napi_function) {
            options.event_user_data = &data;
            options.on_event = [](const char *text, void *user) {
                auto &d = *static_cast<callback_data *>(user);
                napi_value arg, receiver, ignored;
                if (napi_create_string_utf8(d.env, text, NAPI_AUTO_LENGTH, &arg) != napi_ok ||
                    napi_get_undefined(d.env, &receiver) != napi_ok) return;
                auto status = napi_call_function(d.env, receiver, d.fn, 1, &arg, &ignored);
                if (status == napi_pending_exception) napi_get_and_clear_last_exception(d.env, &ignored);
            };
        }
        napi_typedarray_type array_type; size_t length, offset; void *flag; napi_value buffer;
        ok(napi_get_typedarray_info(env, args[4], &array_type, &length, &flag, &buffer, &offset));
        if (array_type != napi_int32_array || length != 1) throw std::runtime_error("Invalid cancellation flag");
        options.cancel_user_data = flag;
        options.should_cancel = [](void *data) -> uint32_t {
            return static_cast<uint32_t>(__atomic_load_n(static_cast<int32_t *>(data), __ATOMIC_RELAXED));
        };
        double timeout; ok(napi_get_value_double(env, args[5], &timeout));
        if (!std::isfinite(timeout) || timeout < 0 || timeout > 86400000 || std::floor(timeout) != timeout)
            throw std::runtime_error("Invalid timeout");
        options.timeout_ms = static_cast<uint64_t>(timeout);
        auto s = dtl_execute(h->engine, request.c_str(), &options, &result, &error);
        std::unique_ptr<char, decltype(&dtl_free)> owned(result, dtl_free); check(s, error);
        napi_value value; ok(napi_create_string_utf8(env, result, NAPI_AUTO_LENGTH, &value)); return value;
    });
}
static napi_value close_engine(napi_env env, napi_callback_info info) {
    return guarded(env, [&] {
        size_t argc = 1; napi_value arg; ok(napi_get_cb_info(env, info, &argc, &arg, nullptr, nullptr));
        if (argc != 1) throw std::runtime_error("Expected engine");
        void *p; ok(napi_get_value_external(env, arg, &p)); auto *h = static_cast<holder *>(p);
        dtl_close(h->engine); h->engine = nullptr;
        napi_value value; ok(napi_get_undefined(env, &value)); return value;
    });
}
static napi_value info_engine(napi_env env, napi_callback_info info) {
    return guarded(env, [&] {
        require_abi();
        size_t argc = 1; napi_value arg; ok(napi_get_cb_info(env, info, &argc, &arg, nullptr, nullptr));
        dtl_engine *engine = nullptr;
        if (argc) {
            void *p; ok(napi_get_value_external(env, arg, &p)); auto *h = static_cast<holder *>(p);
            if (!h->engine) throw std::runtime_error("Engine is closed");
            engine = h->engine;
        }
        char *result = nullptr, *error = nullptr;
        auto status = dtl_info(engine, &result, &error);
        std::unique_ptr<char, decltype(&dtl_free)> owned(result, dtl_free); check(status, error);
        napi_value value; ok(napi_create_string_utf8(env, result, NAPI_AUTO_LENGTH, &value)); return value;
    });
}
static napi_value init(napi_env env, napi_value exports) {
    napi_property_descriptor props[] = {
        {"info", nullptr, info_engine, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"open", nullptr, open_engine, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"execute", nullptr, execute, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"close", nullptr, close_engine, nullptr, nullptr, nullptr, napi_default, nullptr}};
    napi_define_properties(env, exports, 4, props); return exports;
}
NAPI_MODULE(NODE_GYP_MODULE_NAME, init)
