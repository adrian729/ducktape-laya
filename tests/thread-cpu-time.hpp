// The calling thread's CPU time, for the tests' timing checks. Load on the machine stretches wall
// time but not the CPU time of the work a thread does itself, so ratios of CPU times do not fail
// on a busy host. Windows counts it in clock ticks (about 15.6 ms): cpu_seconds_per_run repeats a
// short operation until the measurement spans many ticks.
#pragma once
#include <cstdint>
#include <stdexcept>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#else
#include <time.h>
#endif

namespace dtl_test {
inline double thread_cpu_seconds() {
#ifdef _WIN32
    FILETIME created, exited, kernel, user;
    if (!GetThreadTimes(GetCurrentThread(), &created, &exited, &kernel, &user)) throw std::runtime_error("GetThreadTimes");
    auto ticks = [](FILETIME t) { return (static_cast<uint64_t>(t.dwHighDateTime) << 32) | t.dwLowDateTime; };
    return static_cast<double>(ticks(kernel) + ticks(user)) / 1e7;
#else
    timespec t{};
    if (clock_gettime(CLOCK_THREAD_CPUTIME_ID, &t) != 0) throw std::runtime_error("clock_gettime");
    return static_cast<double>(t.tv_sec) + static_cast<double>(t.tv_nsec) / 1e9;
#endif
}

// Runs `run` until it has taken at least `min_seconds` of the calling thread's CPU time (and at
// least once); returns the CPU seconds per run.
template <class F> double cpu_seconds_per_run(F &&run, double min_seconds = 0.25) {
    const double started = thread_cpu_seconds();
    long runs = 0;
    double elapsed = 0;
    do {
        run();
        ++runs;
        elapsed = thread_cpu_seconds() - started;
    } while (elapsed < min_seconds);
    return elapsed / static_cast<double>(runs);
}
} // namespace dtl_test
