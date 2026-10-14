#pragma once
#include "ducktape_laya.h"
#include <cstddef>
#include <stdexcept>
#include <string>
namespace dtl {
inline constexpr size_t max_request = 1024 * 1024;
struct failure : std::runtime_error {
    dtl_status status;
    failure(dtl_status s, const std::string &m) : std::runtime_error(m), status(s) {}
};
inline void require(bool ok, const std::string &message) {
    if (!ok) throw failure(DTL_INVALID_ARGUMENT, message);
}
} // namespace dtl
