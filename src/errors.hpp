#pragma once
#include "ducktape_laya.h"
#include <cstddef>
#include <stdexcept>
#include <string>
namespace dtl {
inline constexpr size_t max_request = 1024 * 1024;
inline constexpr size_t max_object_members = 1024; // per JSON object; parsing is quadratic in it
inline constexpr size_t max_call_windows = 4096;   // forward passes one call may plan
inline constexpr size_t default_call_windows = 256;
inline constexpr size_t max_request_questions = 32;
inline constexpr size_t max_batch_requests = 32;
inline constexpr size_t max_batch_questions = 128;
inline constexpr size_t default_context_tokens = 512; // capped by the model's trained length
inline constexpr size_t min_context_tokens = 32;
inline constexpr size_t max_scan_windows = 1024;   // per question
inline constexpr size_t max_routes = 16;
inline constexpr size_t max_loaded_models = 4;
struct failure : std::runtime_error {
    dtl_status status;
    failure(dtl_status s, const std::string &m) : std::runtime_error(m), status(s) {}
};
inline void require(bool ok, const std::string &message) {
    if (!ok) throw failure(DTL_INVALID_ARGUMENT, message);
}
} // namespace dtl
