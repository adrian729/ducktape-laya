#include "platform.hpp"
#include "errors.hpp"
#include "build_config.hpp"
#include "threads.hpp"
#include <thread>
#if defined(__linux__) && !defined(__EMSCRIPTEN__)
#include <sched.h>
#include <fstream>
#include <iterator>
#elif defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#endif
#if defined(__aarch64__) && defined(__linux__)
#include <asm/hwcap.h>
#include <sys/auxv.h>
#elif defined(__aarch64__) && defined(__APPLE__)
#include <sys/sysctl.h>
#elif defined(_WIN32) && defined(__x86_64__)
#include <intrin.h>
#include <cstring>
#endif
namespace dtl {
namespace {
// detected is false where this build cannot check the feature: the guard then fails
// closed instead of assuming the CPU has it.
struct feature { const char *name; bool supported; bool detected = true; };
#if defined(_WIN32) && defined(__x86_64__)
// __builtin_cpu_supports needs compiler-rt, which clang-cl does not link by
// default; read the same CPUID bits (and the OS's AVX state for AVX users).
__attribute__((target("xsave"))) bool x86_supports(const char *name) {
    int leaf0[4], leaf1[4], leaf7[4] = {0, 0, 0, 0};
    __cpuid(leaf0, 0);
    __cpuid(leaf1, 1);
    if (leaf0[0] >= 7) __cpuidex(leaf7, 7, 0); // leaf 7 exists only when the maximum leaf reaches it
    const bool avx_state = (leaf1[2] & (1 << 27)) && (_xgetbv(0) & 6) == 6;
    if (!std::strcmp(name, "avx")) return (leaf1[2] & (1 << 28)) && avx_state;
    if (!std::strcmp(name, "avx2")) return (leaf7[1] & (1 << 5)) && avx_state;
    if (!std::strcmp(name, "fma")) return (leaf1[2] & (1 << 12)) && avx_state;
    if (!std::strcmp(name, "f16c")) return leaf1[2] & (1 << 29);
    if (!std::strcmp(name, "bmi2")) return leaf7[1] & (1 << 8);
    if (!std::strcmp(name, "sse4.2")) return leaf1[2] & (1 << 20);
    return false;
}
#endif
std::vector<feature> required_features() {
    std::vector<feature> out;
#if defined(__x86_64__) && !defined(__EMSCRIPTEN__)
#ifdef _WIN32
#define DTL_FEATURE(name) out.push_back({name, x86_supports(name)})
#else
    __builtin_cpu_init();
#define DTL_FEATURE(name) out.push_back({name, bool(__builtin_cpu_supports(name))})
#endif
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
#elif defined(__aarch64__) && defined(__APPLE__)
    auto has = [](const char *name) {
        int value = 0;
        size_t size = sizeof(value);
        return sysctlbyname(name, &value, &size, nullptr, 0) == 0 && value != 0;
    };
#ifdef DTL_NEED_DOTPROD
    out.push_back({"asimddp", has("hw.optional.arm.FEAT_DotProd")});
#endif
#ifdef DTL_NEED_FP16
    out.push_back({"fphp", has("hw.optional.arm.FEAT_FP16")});
#endif
#elif defined(__aarch64__) || defined(_M_ARM64)
    // Other 64-bit Arm systems (Windows on Arm): no feature check is implemented, so
    // a build that requires these features refuses to run instead of passing silently.
#ifdef DTL_NEED_DOTPROD
    out.push_back({"asimddp", false, false});
#endif
#ifdef DTL_NEED_FP16
    out.push_back({"fphp", false, false});
    out.push_back({"asimdhp", false, false});
#endif
#endif
    return out;
}
}
unsigned default_threads() {
    unsigned affinity = 0, quota = 0;
#if defined(__linux__) && !defined(__EMSCRIPTEN__)
    // CPUs in this thread's affinity mask (inherited from the process unless the host changed
    // it; glibc's hardware_concurrency counts online CPUs instead). A mask over 1024 CPUs is
    // not read and leaves the limit unknown.
    cpu_set_t set;
    CPU_ZERO(&set);
    if (sched_getaffinity(0, sizeof(set), &set) == 0) affinity = static_cast<unsigned>(CPU_COUNT(&set));
    // The CPU quota of the process's own cgroup v2 group; missing files mean no known limit.
    std::ifstream groups("/proc/self/cgroup");
    const std::string path = groups ? cgroup_v2_path(std::string(std::istreambuf_iterator<char>(groups), {})) : std::string();
    if (!path.empty()) {
        std::ifstream cpu_max("/sys/fs/cgroup" + path + "/cpu.max");
        std::string line;
        if (cpu_max && std::getline(cpu_max, line)) quota = cgroup_quota_cpus(line);
    }
#elif defined(_WIN32)
    DWORD_PTR process_mask = 0, system_mask = 0;
    if (GetProcessAffinityMask(GetCurrentProcess(), &process_mask, &system_mask))
        for (; process_mask; process_mask &= process_mask - 1) ++affinity;
#endif
    return automatic_threads(affinity, quota, std::thread::hardware_concurrency());
}
void ensure_cpu_supported() {
    for (const auto &feature : required_features()) {
        if (!feature.detected)
            throw failure(DTL_UNSUPPORTED_PLATFORM, std::string("This build cannot check the required CPU feature ") + feature.name +
                          " on this operating system");
        if (!feature.supported) throw failure(DTL_UNSUPPORTED_PLATFORM, std::string("CPU lacks required feature: ") + feature.name);
    }
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
