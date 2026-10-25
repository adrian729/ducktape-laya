#pragma once
// CPU guard and the numerical profile's features, without JSON (runtime_info.hpp builds the
// info document from them), so editing this file recompiles no JSON code.
#include <vector>
namespace dtl {
struct cpu_profile {
    const char *backend;                 // native-cpu, wasm-cpu or webgpu
    std::vector<const char *> required;  // CPU features this build requires, in check order
    std::vector<const char *> missing;   // the required features this CPU lacks
};
cpu_profile current_cpu_profile();
void ensure_cpu_supported();
}
