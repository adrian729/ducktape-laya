// The engine lock (src/fair_mutex.hpp): waiters get it in arrival order, unlock hands it over
// without waiting for a poll, and a waiter that stops leaves the queue without holding up the
// others. Every ordering is established before it is checked: a waiter is known to be queued
// when its stop check runs (once on joining the queue), so no step sleeps hoping for an order.
#include "fair_mutex.hpp"
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace {
using clock_type = std::chrono::steady_clock;
constexpr auto forever = clock_type::duration::max();
[[noreturn]] void fail(const std::string &message) { std::cerr << "Failed: " << message << '\n'; std::exit(1); }
void expect(bool ok, const std::string &message) { if (!ok) fail(message); }

// Signals between threads; every wait is bounded, so a lost handoff fails instead of hanging.
struct events {
    std::mutex m;
    std::condition_variable changed;
    std::vector<std::string> order;      // who held the lock, in order
    void record(const std::string &name) { std::lock_guard<std::mutex> g(m); order.push_back(name); changed.notify_all(); }
    template<class F> void wait(F done, const std::string &what) {
        std::unique_lock<std::mutex> g(m);
        if (!changed.wait_for(g, std::chrono::seconds(30), done)) fail("timed out waiting for " + what);
    }
};

struct waiter {
    std::string name;
    std::atomic<bool> cancel{false};
    std::atomic<int> polls{0};
    bool queued = false, returned = false, acquired = false;
    std::thread thread;
};

// Starts a thread that locks `mutex` (stopping when w.cancel is set, polled every `period`),
// records its turn and unlocks; returns once the thread has joined the queue.
void start(dtl::fair_mutex &mutex, events &e, waiter &w, clock_type::duration period) {
    w.thread = std::thread([&mutex, &e, &w, period] {
        const bool got = mutex.lock([&] {
            ++w.polls;
            std::lock_guard<std::mutex> g(e.m);
            w.queued = true;
            e.changed.notify_all();
            return w.cancel.load();
        }, clock_type::time_point::max(), period);
        if (got) { e.record(w.name); mutex.unlock(); }
        std::lock_guard<std::mutex> g(e.m);
        w.acquired = got;
        w.returned = true;
        e.changed.notify_all();
    });
    e.wait([&] { return w.queued; }, w.name + " to join the queue");
}
} // namespace

int main() {
    {
        // Arrival order, handoff by unlock alone (the waiters never poll again: their period is
        // unbounded), and no barging: the holder that unlocks and locks again comes last.
        dtl::fair_mutex mutex;
        events e;
        mutex.lock();
        std::vector<std::unique_ptr<waiter>> waiters;
        for (const char *name : {"a", "b", "c", "d"}) {
            waiters.push_back(std::make_unique<waiter>());
            waiters.back()->name = name;
            start(mutex, e, *waiters.back(), forever);
        }
        mutex.unlock();
        mutex.lock();
        e.record("holder");
        mutex.unlock();
        for (auto &w : waiters) w->thread.join();
        expect((e.order == std::vector<std::string>{"a", "b", "c", "d", "holder"}), "the lock was not taken in arrival order");
        for (auto &w : waiters) expect(w->polls.load() == 1, w->name + " polled while waiting for a handoff");
    }
    {
        // A waiter that stops leaves the queue while the lock is still held, from the middle and
        // from the front; the others keep their order.
        dtl::fair_mutex mutex;
        events e;
        mutex.lock();
        std::vector<std::unique_ptr<waiter>> waiters;
        for (const char *name : {"a", "b", "c", "d"}) {
            waiters.push_back(std::make_unique<waiter>());
            waiters.back()->name = name;
            start(mutex, e, *waiters.back(), std::chrono::milliseconds(10));
        }
        for (size_t i : {1, 0}) {
            auto &w = *waiters[i];
            w.cancel = true;
            e.wait([&] { return w.returned; }, w.name + " to leave the queue");
            expect(!w.acquired, w.name + " acquired a lock that was held");
        }
        mutex.unlock();
        for (auto &w : waiters) w->thread.join();
        expect((e.order == std::vector<std::string>{"c", "d"}), "the waiters behind a stopped one lost their order");
        mutex.lock();  // free again: nothing is left queued
        mutex.unlock();
    }
    {
        // A deadline ends the wait when it expires, without polling in between: the stop check
        // runs on joining the queue and at the deadline.
        dtl::fair_mutex mutex;
        mutex.lock();
        bool got = true;
        int polls = 0;
        std::thread t([&] {
            const auto deadline = clock_type::now() + std::chrono::milliseconds(50);
            got = mutex.lock([&] { ++polls; return clock_type::now() >= deadline; }, deadline, forever);
        });
        t.join();
        mutex.unlock();
        expect(!got && polls == 2, "the deadline wait polled " + std::to_string(polls) + " times");
    }
    std::cout << "fair engine lock tests passed\n";
    return 0;
}
