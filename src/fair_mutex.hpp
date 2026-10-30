#pragma once
#include <chrono>
#include <condition_variable>
#include <mutex>
namespace dtl {
// An engine's lock: waiters get it in arrival order, and unlock hands it to the first waiter
// directly, so a call that releases and locks again queues behind the others. A waiter can also
// leave the queue (a cancellation or deadline) without holding up the waiters behind it.
class fair_mutex {
    using clock = std::chrono::steady_clock;
    // Lives on the waiting thread's stack; linked into the queue while it waits.
    struct waiter {
        std::condition_variable ready;
        bool granted = false;
        waiter *prev = nullptr, *next = nullptr;
    };
    std::mutex state_;
    bool held_ = false;
    waiter *first_ = nullptr, *last_ = nullptr;
    void unlink(waiter &w) noexcept {
        (w.prev ? w.prev->next : first_) = w.next;
        (w.next ? w.next->prev : last_) = w.prev;
        w.prev = w.next = nullptr;
    }
public:
    fair_mutex() = default;
    fair_mutex(const fair_mutex &) = delete;
    fair_mutex &operator=(const fair_mutex &) = delete;
    void lock() { lock([] { return false; }, clock::time_point::max(), clock::duration::max()); }
    // Waits for the lock until stopped() returns true; returns whether it holds the lock.
    // stopped() runs on this thread, without the internal mutex held, once on joining the queue,
    // at `deadline` and every `period` (for stops nothing notifies, such as a host's flag);
    // unlock() wakes the next waiter at once. stopped() must not throw.
    template<class Stopped> bool lock(Stopped stopped, clock::time_point deadline, clock::duration period) {
        std::unique_lock<std::mutex> state(state_);
        if (!held_ && !first_) { held_ = true; return true; }
        waiter w;
        w.prev = last_;
        (last_ ? last_->next : first_) = &w;
        last_ = &w;
        for (;;) {
            state.unlock();
            const bool stop = stopped();
            state.lock();
            if (w.granted) return true;
            if (stop) { unlink(w); return false; }
            const auto now = clock::now();
            auto until = deadline;
            if (period < until - now) until = now + period;
            // Without a deadline or period, wait for unlock() alone: a time_point::max() wait can
            // overflow in the conversion to the system call's clock.
            if (until == clock::time_point::max()) w.ready.wait(state, [&] { return w.granted; });
            else w.ready.wait_until(state, until, [&] { return w.granted; });
            if (w.granted) return true;
        }
    }
    void unlock() noexcept {
        std::lock_guard<std::mutex> state(state_);
        if (!first_) { held_ = false; return; }
        // The lock passes to the first waiter, which stays held_. Notified with state_ held: the
        // waiter (and its condition variable) cannot return before this notification is done.
        waiter &next = *first_;
        unlink(next);
        next.granted = true;
        next.ready.notify_one();
    }
};
} // namespace dtl
