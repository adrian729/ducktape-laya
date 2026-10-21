#include "platform.hpp"
#include "errors.hpp"
#include "build_config.hpp"
#if defined(__aarch64__) && defined(__linux__)
#include <asm/hwcap.h>
#include <sys/auxv.h>
#endif
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
#elif defined(__aarch64__) && defined(__linux__)
    const unsigned long hwcap = getauxval(AT_HWCAP);
#ifdef DTL_NEED_DOTPROD
    out.push_back({"asimddp", (hwcap & HWCAP_ASIMDDP) != 0});
#endif
#ifdef DTL_NEED_FP16
    out.push_back({"fphp", (hwcap & HWCAP_FPHP) != 0});
    out.push_back({"asimdhp", (hwcap & HWCAP_ASIMDHP) != 0});
#endif
    (void)hwcap;
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
    std::string profile = backend;
    if (!required.empty()) {
        profile += ":";
        for (size_t i = 0; i < required.size(); ++i) {
            if (i) profile += "+";
            profile += required[i].get<std::string>();
        }
    }
    return {{"schema_version", 1}, {"package_version", DTL_PACKAGE_VERSION}, {"abi_version", DTL_ABI_VERSION},
        {"runtime_patches", json::parse(DTL_RUNTIME_PATCHES)}, {"llama_cpp_commit", DTL_RUNTIME_COMMIT}, {"backend", backend}, {"compiler", DTL_COMPILER},
        {"numerical_profile", {{"id", profile}, {"cross_profile_equivalence", false}}},
        {"required_cpu_features", required}, {"missing_cpu_features", missing}, {"cpu_supported", missing.empty()},
        {"decision_types", {"choice", "noul", "score"}},
        {"features", {"structured_decisions", "sequential_batches", "routing", "language_routing", "overflow_policies",
                      "observers", "cooperative_cancellation", "deadlines"}},
        {"overflow_policies", {"reject", "truncate_end", "truncate_start", "scan"}},
        {"limits", {{"request_bytes", 1048576}, {"object_members", 1024}, {"questions_per_request", 32}, {"requests_per_batch", 32},
                    {"questions_per_batch", 128}, {"default_context_tokens", 512}, {"minimum_context_tokens", 32},
                    {"scan_windows", 1024}, {"windows_per_call", 4096}, {"routes", 16}, {"loaded_models", 4}}}};
}
}
