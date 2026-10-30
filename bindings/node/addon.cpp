#include <node_api.h>
#include "ducktape_laya.h"
#include <atomic>
#include <cmath>
#include <memory>
#include <new>
#include <stdexcept>
#include <string>
#ifdef __APPLE__
#include <dlfcn.h>
#include <climits>
#include <cstdlib>
#endif
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
// A model path: a string, or the raw bytes of a Buffer or Uint8Array (a POSIX path need not be UTF-8).
static std::string path_arg(napi_env env, napi_value v) {
    bool typed; ok(napi_is_typedarray(env, v, &typed));
    if (!typed) return string_arg(env, v);
    napi_typedarray_type type; size_t length, offset; void *data; napi_value buffer;
    ok(napi_get_typedarray_info(env, v, &type, &length, &data, &buffer, &offset));
    if (type != napi_uint8_array) throw std::runtime_error("Input must be a string, Buffer or Uint8Array");
    if (length > 1048576) throw std::runtime_error("Input exceeds 1 MiB");
    std::string text(static_cast<const char *>(data), length);
    if (text.find('\0') != std::string::npos) throw std::runtime_error("Input contains a NUL byte");
    return text;
}
static void check(dtl_status s, char *error) {
    std::unique_ptr<char, decltype(&dtl_free)> owned(error, dtl_free);
    if (s) throw native_error(s, error ? error : "Laya operation failed");
}
#ifndef DTL_ADDON_VERSION
#error "DTL_ADDON_VERSION (the package version) must be defined"
#endif
#ifdef __APPLE__
static std::string real_path(const char *path) {
    char buffer[PATH_MAX];
    return path && realpath(path, buffer) ? std::string(buffer) : std::string();
}
static std::string parent_of(const std::string &path) {
    auto slash = path.rfind('/');
    return slash == std::string::npos ? std::string() : slash == 0 ? "/" : path.substr(0, slash);
}
// dyld searches DYLD_LIBRARY_PATH by leaf name before the addon's rpaths, so a library of the same name there
// would replace the shipped one. The addon's rpaths are @loader_path and @loader_path/../..: require the image
// that provides dtl_abi_version to live in the addon's directory or two levels above it (status 7 otherwise).
static void require_library_location() {
    Dl_info addon{}, library{};
    if (!dladdr(reinterpret_cast<void *>(&require_library_location), &addon) || !dladdr(reinterpret_cast<void *>(&dtl_abi_version), &library))
        throw native_error(DTL_UNSUPPORTED_PLATFORM, "Cannot determine which file provides the native ducktape-laya library");
    auto addon_dir = parent_of(real_path(addon.dli_fname)), library_dir = parent_of(real_path(library.dli_fname));
    if (addon_dir.empty() || library_dir.empty() || (library_dir != addon_dir && library_dir != parent_of(parent_of(addon_dir))))
        throw native_error(DTL_UNSUPPORTED_PLATFORM, ("The native ducktape-laya library was loaded from " + std::string(library.dli_fname ? library.dli_fname : "an unknown image") +
            ", not from the addon's directory " + addon_dir + " or its parent's parent; DYLD_LIBRARY_PATH may have replaced it. Unset DYLD_LIBRARY_PATH.").c_str());
}
#endif
// The addon and its library come from one build: refuse a library of another ABI or package version
// (for example one the dynamic loader found elsewhere), with status 7. Checked once; a failure repeats.
static void require_library() {
    static std::atomic<bool> checked{false};
    if (checked.load(std::memory_order_acquire)) return;
#ifdef __APPLE__
    require_library_location();
#endif
    if (dtl_abi_version() != 2) throw native_error(DTL_UNSUPPORTED_PLATFORM, ("Native ducktape-laya ABI " + std::to_string(dtl_abi_version()) + " is not supported (expected 2)").c_str());
    char *result = nullptr, *error = nullptr;
    auto status = dtl_info(nullptr, &result, &error);
    std::unique_ptr<char, decltype(&dtl_free)> owned(result, dtl_free);
    check(status, error);
    // dtl_info writes compact JSON: "package_version":"X.Y.Z".
    const std::string text(result), key = "\"package_version\":\"";
    auto start = text.find(key);
    auto end = start == std::string::npos ? std::string::npos : text.find('"', start + key.size());
    std::string version = end == std::string::npos ? "unknown" : text.substr(start + key.size(), end - start - key.size());
    if (version != DTL_ADDON_VERSION)
        throw native_error(DTL_UNSUPPORTED_PLATFORM, ("The native ducktape-laya library is version " + version + ", but this addon was built with " DTL_ADDON_VERSION
            "; another copy of the library was loaded instead of the one shipped with the addon").c_str());
    checked.store(true, std::memory_order_release);
}
static void throw_status(napi_env env, int32_t code, const char *text) {
    napi_value message, error, status;
    napi_create_string_utf8(env, text, NAPI_AUTO_LENGTH, &message);
    napi_create_error(env, nullptr, message, &error);
    napi_create_int32(env, code, &status);
    napi_set_named_property(env, error, "status", status);
    napi_throw(env, error);
}
// Every error carries a status (docs/API.md): the addon's own checks reject arguments (size, NUL, types).
template<class F> napi_value guarded(napi_env env, F f) {
    try { return f(); }
    catch (const native_error &e) { throw_status(env, e.status, e.what()); }
    catch (const std::bad_alloc &) { throw_status(env, DTL_OUT_OF_MEMORY, "Out of memory"); }
    catch (const std::exception &e) { throw_status(env, DTL_INVALID_ARGUMENT, e.what()); }
    catch (...) { throw_status(env, DTL_INFERENCE_ERROR, "Native addon failure"); }
    return nullptr;
}
static napi_value open_engine(napi_env env, napi_callback_info info) {
    return guarded(env, [&] {
        require_library();
        size_t argc = 3; napi_value args[3]; ok(napi_get_cb_info(env, info, &argc, args, nullptr, nullptr));
        if (argc != 3) throw std::runtime_error("Expected path/router JSON, config JSON and router flag");
        bool router; ok(napi_get_value_bool(env, args[2], &router));
        auto path = path_arg(env, args[0]);
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
        if (!std::isfinite(timeout) || timeout < 0 || timeout > 9007199254740991.0 || std::floor(timeout) != timeout)
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
        require_library();
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
