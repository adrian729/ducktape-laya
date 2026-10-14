// Coverage-guided public request/planner/router boundary fuzzing. No model needed.
#include "ducktape_laya.h"
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <string>

namespace {
void require(bool condition) { if (!condition) std::abort(); }
struct fixture {
    dtl_router *router = nullptr;
    fixture() {
        char *error = nullptr;
        // A real path satisfies lazy router configuration; it is never a GGUF.
        const auto status = dtl_router_open(R"({"models":{"fuzz":"/proc/self/exe"}})", nullptr, &router, &error);
        require(status == DTL_OK && router && !error);
        dtl_free(error);
    }
    ~fixture() { dtl_router_close(router); }
};
}
extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size > 1048577) return 0;
    static fixture context;
    const std::string input(reinterpret_cast<const char *>(data), size);
    char *result = nullptr, *error = nullptr;
    auto status = dtl_router_execute(context.router, input.c_str(), nullptr, &result, &error);
    require(status >= DTL_INVALID_ARGUMENT && status <= DTL_UNSUPPORTED_PLATFORM);
    require(!result && error);
    dtl_free(result); dtl_free(error);
    dtl_router *candidate = nullptr;
    error = nullptr;
    status = dtl_router_open(input.c_str(), nullptr, &candidate, &error);
    require(status >= DTL_OK && status <= DTL_UNSUPPORTED_PLATFORM);
    require((status == DTL_OK) == (candidate != nullptr));
    require((status == DTL_OK) == (error == nullptr));
    dtl_router_close(candidate); dtl_free(error);
    return 0;
}
