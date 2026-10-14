// Sustained public-ABI lifecycle/concurrency check; no model downloads.
#include "ducktape_laya.hpp"
#include <atomic>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <thread>
#include <vector>
#include <sys/resource.h>
static const std::string request = R"({"state":"Please refund me.","questions":{"refund":{"type":"noul","instructions":"Is a refund requested?"}}})";
static long rss_kib() {
    std::ifstream file("/proc/self/status"); std::string name;
    while (file >> name) { if (name == "VmRSS:") { long value; file >> value; return value; } std::getline(file, name); }
    return -1;
}
static size_t entries(const char *path) {
    size_t n = 0; for (const auto &entry : std::filesystem::directory_iterator(path)) { (void) entry; ++n; } return n;
}
int main(int argc, char **argv) {
    if (argc < 2 || argc > 3) return 2;
    const int rounds = argc == 3 ? std::stoi(argv[2]) : 40;
    if (rounds < 10 || rounds > 10000) return 2;
    try {
        const auto start = std::chrono::steady_clock::now();
        std::string expected;
        { ducktape::laya warm(argv[1]); expected = warm.execute(request); }
        const auto baseline_rss = rss_kib(), baseline_fds = (long) entries("/proc/self/fd"), baseline_threads = (long) entries("/proc/self/task");
        for (int i = 0; i < rounds; ++i) {
            ducktape::laya engine(argv[1]);
            if (engine.execute(request) != expected) throw std::runtime_error("Open/use/close result changed");
            try { engine.execute("{bad json"); throw std::runtime_error("Invalid request accepted"); }
            catch (const ducktape::error &error) { if (error.status != DTL_INVALID_ARGUMENT) throw; }
            auto options = dtl_default_call_options(); options.timeout_ms = 1;
            try { engine.execute(request, nullptr, &options); throw std::runtime_error("Deadline ignored"); }
            catch (const ducktape::error &error) { if (error.status != DTL_DEADLINE_EXCEEDED) throw; }
            if (engine.execute(request) != expected) throw std::runtime_error("Post-deadline result changed");
        }
        // Concurrent callers share one handle and serialize safely. Each thread
        // owns its buffers; close waits until all threads have joined.
        std::atomic<unsigned> failures{0};
        {
            ducktape::laya engine(argv[1]); std::vector<std::thread> callers;
            for (int i = 0; i < 4; ++i) callers.emplace_back([&] {
                for (int j = 0; j < 3; ++j) { try { if (engine.execute(request) != expected) ++failures; } catch (...) { ++failures; } }
            });
            for (auto &thread : callers) thread.join();
        }
        if (failures) throw std::runtime_error("Concurrent result mismatch");
        const auto final_rss = rss_kib(), final_fds = (long) entries("/proc/self/fd"), final_threads = (long) entries("/proc/self/task");
        struct rusage usage{}; getrusage(RUSAGE_SELF, &usage);
        const double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
        // Allocators can retain arenas: this is a bounded-growth regression gate,
        // not proof that every byte was returned to the OS. Sanitizers are separate.
        if (final_fds != baseline_fds || final_threads != baseline_threads || final_rss > baseline_rss + 128 * 1024)
            throw std::runtime_error("Resource growth exceeded predeclared limits (FD/thread exact, RSS +128MiB)");
        std::cout << "{\"rounds\":" << rounds << ",\"concurrent_calls\":12,\"baseline_rss_kib\":" << baseline_rss
                  << ",\"final_rss_kib\":" << final_rss << ",\"peak_rss_kib\":" << usage.ru_maxrss
                  << ",\"fds\":" << final_fds << ",\"threads\":" << final_threads << ",\"elapsed_seconds\":" << elapsed << "}\n";
    } catch (const std::exception &error) { std::cerr << error.what() << '\n'; return 1; }
}
