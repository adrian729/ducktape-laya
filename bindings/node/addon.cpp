#include <node_api.h>
#include "ducktape_laya.h"
#include <memory>
#include <stdexcept>
#include <string>
struct holder { dtl_engine *engine = nullptr; dtl_router *router = nullptr;
    ~holder() { dtl_close(engine); dtl_router_close(router); } };
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
        size_t argc = 3; napi_value args[3]; ok(napi_get_cb_info(env, info, &argc, args, nullptr, nullptr));
        if (argc != 3) throw std::runtime_error("Expected path/config, threads and router flag");
        bool router; ok(napi_get_value_bool(env, args[2], &router));
        auto path = string_arg(env, args[0]); auto options = dtl_default_options();
        ok(napi_get_value_uint32(env, args[1], &options.threads));
        auto h = std::make_unique<holder>(); char *error = nullptr;
        auto s = router ? dtl_router_open(path.c_str(), &options, &h->router, &error)
                        : dtl_open(path.c_str(), &options, &h->engine, &error);
        check(s, error);
        napi_value value;
        ok(napi_create_external(env, h.get(), [](napi_env, void *p, void *) { delete static_cast<holder *>(p); }, nullptr, &value));
        h.release(); return value;
    });
}
static napi_value decide(napi_env env, napi_callback_info info) {
    return guarded(env, [&] {
        size_t argc = 5; napi_value args[5]; ok(napi_get_cb_info(env, info, &argc, args, nullptr, nullptr));
        if (argc != 5) throw std::runtime_error("Expected engine, request, request ID, callback and legacy flag");
        bool legacy; ok(napi_get_value_bool(env, args[4], &legacy));
        void *p; ok(napi_get_value_external(env, args[0], &p)); auto *h = static_cast<holder *>(p);
        if (!h->engine && !h->router) throw std::runtime_error("Engine is closed");
        auto request = string_arg(env, args[1]); char *result = nullptr, *error = nullptr;
        struct callback_data { napi_env env; napi_value fn; } data{env, args[3]};
        int64_t id; ok(napi_get_value_int64(env, args[2], &id));
        napi_valuetype type; ok(napi_typeof(env, args[3], &type));
        dtl_observer observer{static_cast<uint64_t>(id), nullptr, &data};
        if (type == napi_function) observer.callback = [](const char *text, void *user) {
            auto &d = *static_cast<callback_data *>(user);
            napi_value arg, receiver, ignored;
            if (napi_create_string_utf8(d.env, text, NAPI_AUTO_LENGTH, &arg) != napi_ok ||
                napi_get_undefined(d.env, &receiver) != napi_ok) return;
            auto status = napi_call_function(d.env, receiver, d.fn, 1, &arg, &ignored);
            if (status == napi_pending_exception) napi_get_and_clear_last_exception(d.env, &ignored);
        };
        auto s = h->router ? dtl_router_execute(h->router, request.c_str(), &observer, &result, &error)
                           : legacy ? dtl_decide_observed(h->engine, request.c_str(), &observer, &result, &error)
                                    : dtl_execute(h->engine, request.c_str(), &observer, &result, &error);
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
        dtl_router_close(h->router); h->router = nullptr;
        napi_value value; ok(napi_get_undefined(env, &value)); return value;
    });
}
static napi_value init(napi_env env, napi_value exports) {
    napi_property_descriptor props[] = {
        {"open", nullptr, open_engine, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"decide", nullptr, decide, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"close", nullptr, close_engine, nullptr, nullptr, nullptr, napi_default, nullptr}};
    napi_define_properties(env, exports, 3, props); return exports;
}
NAPI_MODULE(NODE_GYP_MODULE_NAME, init)
