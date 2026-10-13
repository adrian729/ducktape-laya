#pragma once
#include "ducktape_laya.h"
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
namespace ducktape {
class error : public std::runtime_error {
public:
    dtl_status status;
    error(dtl_status s, const std::string &message) : std::runtime_error(message), status(s) {}
};
inline void check(dtl_status status, char *message) {
    std::unique_ptr<char, decltype(&dtl_free)> owned(message, dtl_free);
    if (status != DTL_OK) throw error(status, message ? message : "Laya operation failed");
}
class laya {
    std::unique_ptr<dtl_engine, decltype(&dtl_close)> engine_{nullptr, dtl_close};
public:
    explicit laya(const std::string &path, dtl_options options = dtl_default_options()) {
        if (path.find('\0') != std::string::npos) throw error(DTL_INVALID_ARGUMENT, "Path contains a NUL byte");
        dtl_engine *raw = nullptr;
        char *message = nullptr;
        auto status = dtl_open(path.c_str(), &options, &raw, &message);
        check(status, message);
        engine_.reset(raw);
    }
    laya(laya &&) noexcept = default;
    laya &operator=(laya &&) noexcept = default;
    laya(const laya &) = delete;
    laya &operator=(const laya &) = delete;
    std::string decide(const std::string &request) const {
        if (request.find('\0') != std::string::npos) throw error(DTL_INVALID_ARGUMENT, "Request contains a NUL byte");
        char *result = nullptr, *message = nullptr;
        auto status = dtl_decide(engine_.get(), request.c_str(), &result, &message);
        std::unique_ptr<char, decltype(&dtl_free)> owned(result, dtl_free);
        check(status, message);
        return result;
    }
};
}
