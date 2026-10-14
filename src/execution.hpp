#pragma once
#include "errors.hpp"
#include <atomic>
#include <chrono>
#include <mutex>
#include <thread>
namespace dtl {
// Borrowed per-call control. No global cancellation state or handle-wide races.
class execution {
    dtl_call_options options_{};
    mutable std::atomic<dtl_status> stopped_{DTL_OK};
    std::chrono::steady_clock::time_point deadline_{};
public:
    explicit execution(const dtl_call_options *options) {
        if (options) {
            require(options->struct_size == sizeof(dtl_call_options), "Incompatible call options size");
            options_ = *options;
            require(options_.reserved == 0 && options_.timeout_ms <= 86400000, "Invalid call options");
        }
        if (options_.timeout_ms) deadline_ = std::chrono::steady_clock::now() + std::chrono::milliseconds(options_.timeout_ms);
    }
    bool enabled() const noexcept { return options_.should_cancel || options_.timeout_ms; }
    dtl_status stop_status() const noexcept {
        auto status = stopped_.load(std::memory_order_relaxed);
        if (status) return status;
        uint32_t reason = 0;
        if (options_.should_cancel) {
            try { reason = options_.should_cancel(options_.user_data); }
            catch (...) { reason = DTL_CANCELLED; }
        }
        if (reason) status = reason == DTL_DEADLINE_EXCEEDED ? DTL_DEADLINE_EXCEEDED : DTL_CANCELLED;
        else if (options_.timeout_ms && std::chrono::steady_clock::now() >= deadline_) status = DTL_DEADLINE_EXCEEDED;
        if (status) {
            dtl_status expected = DTL_OK;
            stopped_.compare_exchange_strong(expected, status, std::memory_order_relaxed);
        }
        return stopped_.load(std::memory_order_relaxed);
    }
    bool stopped() const noexcept { return stop_status() != DTL_OK; }
    void check() const {
        auto status = stop_status();
        if (status == DTL_CANCELLED) throw failure(status, "Request cancelled");
        if (status == DTL_DEADLINE_EXCEEDED) throw failure(status, "Request deadline exceeded");
    }
    std::unique_lock<std::mutex> lock(std::mutex &mutex) const {
        std::unique_lock<std::mutex> held(mutex, std::defer_lock);
        if (!options_.should_cancel && !options_.timeout_ms) held.lock();
        else while (!held.try_lock()) { check(); std::this_thread::sleep_for(std::chrono::milliseconds(1)); }
        check();
        return held;
    }
};
} // namespace dtl
