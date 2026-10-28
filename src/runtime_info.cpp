#include "runtime_info.hpp"
#include "ducktape_laya.h"
#include "build_config.hpp"
#include "platform.hpp"
#include <string>
namespace dtl {
json runtime_info() {
    const auto cpu = current_cpu_profile();
    json required = json::array(), missing = json::array();
    for (const char *name : cpu.required) required.push_back(name);
    for (const char *name : cpu.missing) missing.push_back(name);
    std::string profile = cpu.backend;
    if (!required.empty()) {
        profile += ":";
        for (size_t i = 0; i < required.size(); ++i) {
            if (i) profile += "+";
            profile += required[i].get<std::string>();
        }
    }
    return {{"schema_version", 1}, {"package_version", DTL_PACKAGE_VERSION}, {"abi_version", DTL_ABI_VERSION},
        {"runtime_patches", parse_json(DTL_RUNTIME_PATCHES)}, {"llama_cpp_commit", DTL_RUNTIME_COMMIT}, {"backend", cpu.backend}, {"compiler", DTL_COMPILER},
        {"numerical_profile", {{"id", profile}, {"cross_profile_equivalence", false}}},
        {"required_cpu_features", required}, {"missing_cpu_features", missing}, {"cpu_supported", missing.empty()},
        {"decision_types", {"choice", "noul", "score"}},
        {"features", {"structured_decisions", "sequential_batches", "routing", "language_routing", "overflow_policies",
                      "observers", "cooperative_cancellation", "deadlines"}},
        {"overflow_policies", {"reject", "truncate_end", "truncate_start", "scan"}},
        {"limits", {{"request_bytes", 1048576}, {"object_members", 1024}, {"questions_per_request", 32}, {"requests_per_batch", 32},
                    {"questions_per_batch", 128}, {"default_context_tokens", 512}, {"minimum_context_tokens", 32},
                    {"scan_windows", 1024}, {"windows_per_call", 4096}, {"default_windows_per_call", 256}, {"routes", 16}, {"loaded_models", 4}}}};
}
} // namespace dtl
