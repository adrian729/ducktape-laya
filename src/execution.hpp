#pragma once
#include "errors.hpp"
#include "fair_mutex.hpp"
#include <atomic>
#include <chrono>
#include <mutex>
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
            require(options_.reserved == 0, "Invalid call options");
        }
        if (options_.timeout_ms) {
            // Any length: a deadline beyond the clock's range (about 292 years) never expires.
            auto now = std::chrono::steady_clock::now();
            auto left = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::time_point::max() - now).count();
            deadline_ = options_.timeout_ms >= static_cast<uint64_t>(left) ? std::chrono::steady_clock::time_point::max()
                                                                           : now + std::chrono::milliseconds(options_.timeout_ms);
        }
    }
    bool enabled() const noexcept { return options_.should_cancel || options_.timeout_ms; }
    dtl_status stop_status() const noexcept {
        auto status = stopped_.load(std::memory_order_relaxed);
        if (status) return status;
        uint32_t reason = 0;
        if (options_.should_cancel) {
            try { reason = options_.should_cancel(options_.cancel_user_data); }
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
    // Waits for an engine's lock in arrival order, leaving the queue on a stop. The deadline wakes the
    // waiter when it expires; a host's cancellation flag notifies nothing, so with a callback the
    // waiter also polls it every 10 ms (a waiting call stops within about the time a running one
    // takes to reach its next check in tokenization), and unlock() hands the lock over at once.
    std::unique_lock<fair_mutex> lock(fair_mutex &mutex) const {
        const auto period = options_.should_cancel ? std::chrono::steady_clock::duration(std::chrono::milliseconds(10))
                                                   : std::chrono::steady_clock::duration::max();
        const auto until = options_.timeout_ms ? deadline_ : std::chrono::steady_clock::time_point::max();
        if (!mutex.lock([this] { return stopped(); }, until, period)) check();
        std::unique_lock<fair_mutex> held(mutex, std::adopt_lock);
        check();
        return held;
    }
};
} // namespace dtl
