#include "platform.hpp"
#include "errors.hpp"
#include "build_config.hpp"
namespace dtl {
namespace {
struct feature { const char *name; bool supported; };
std::vector<feature> required_features() {
    std::vector<feature> out;
#if defined(__x86_64__) && !defined(__EMSCRIPTEN__)
    __builtin_cpu_init();
#define DTL_FEATURE(name) out.push_back({name, bool(__builtin_cpu_supports(name))})
#ifdef DTL_NEED_AVX
    DTL_FEATURE("avx");
#endif
#ifdef DTL_NEED_AVX2
    DTL_FEATURE("avx2");
#endif
#ifdef DTL_NEED_FMA
    DTL_FEATURE("fma");
#endif
#ifdef DTL_NEED_F16C
    DTL_FEATURE("f16c");
#endif
#ifdef DTL_NEED_BMI2
    DTL_FEATURE("bmi2");
#endif
#ifdef DTL_NEED_SSE42
    DTL_FEATURE("sse4.2");
#endif
#undef DTL_FEATURE
#endif
    return out;
}
}
void ensure_cpu_supported() {
    for (const auto &feature : required_features())
        if (!feature.supported) throw failure(DTL_UNSUPPORTED_PLATFORM, std::string("CPU lacks required feature: ") + feature.name);
}
json runtime_info() {
    json required = json::array(), missing = json::array();
    for (const auto &feature : required_features()) { required.push_back(feature.name); if (!feature.supported) missing.push_back(feature.name); }
#ifdef __EMSCRIPTEN__
#ifdef DTL_BROWSER_WEBGPU
    const char *backend = "webgpu";
#else
    const char *backend = "wasm-cpu";
#endif
#else
    const char *backend = "native-cpu";
#endif
    return {{"schema_version", 1}, {"package_version", DTL_PACKAGE_VERSION}, {"abi_version", DTL_ABI_VERSION},
        {"llama_cpp_commit", DTL_RUNTIME_COMMIT}, {"backend", backend}, {"compiler", DTL_COMPILER},
        {"required_cpu_features", required}, {"missing_cpu_features", missing}, {"cpu_supported", missing.empty()},
        {"decision_types", {"choice", "noul", "score"}},
        {"features", {"structured_decisions", "sequential_batches", "routing", "observers", "cooperative_cancellation", "deadlines"}},
        {"limits", {{"request_bytes", 1048576}, {"questions_per_request", 32}, {"requests_per_batch", 32},
                    {"questions_per_batch", 128}, {"tokens_per_question", 512}, {"routes", 16}, {"loaded_models", 4}}}};
}
}
