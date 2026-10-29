#include "runtime_info.hpp"
#include "ducktape_laya.h"
#include "build_config.hpp"
#include "errors.hpp"
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
        {"limits", {{"request_bytes", max_request}, {"object_members", max_object_members}, {"questions_per_request", max_request_questions},
                    {"requests_per_batch", max_batch_requests}, {"questions_per_batch", max_batch_questions},
                    {"default_context_tokens", default_context_tokens}, {"minimum_context_tokens", min_context_tokens},
                    {"scan_windows", max_scan_windows}, {"windows_per_call", max_call_windows},
                    {"default_windows_per_call", default_call_windows}, {"routes", max_routes}, {"loaded_models", max_loaded_models}}}};
}
} // namespace dtl
