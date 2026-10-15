// Sustained public-ABI lifecycle/concurrency check; no model downloads.
#include "ducktape_laya.hpp"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <thread>
#include <vector>
#include <sys/resource.h>
#include <unistd.h>
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
    if (argc < 2 || argc > 4) return 2;
    try {
        int rounds = 40, seconds = 0;
        const auto number = [](const char *text) {
            size_t end = 0;
            const auto value = std::stoi(text, &end);
            if (text[end] != '\0') throw std::runtime_error("Invalid numeric argument");
            return value;
        };
        if (argc == 4) {
            if (std::string(argv[2]) != "--seconds") return 2;
            seconds = number(argv[3]); rounds = 10;
            if (seconds < 1 || seconds > 86400) return 2;
        } else if (argc == 3) rounds = number(argv[2]);
        if (rounds < 10 || rounds > 10000) return 2;
        const auto start = std::chrono::steady_clock::now();
        auto elapsed_seconds = [&] { return std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count(); };
        auto settings = dtl_default_options(); settings.threads = 2;
        std::string expected;
        { ducktape::laya warm(argv[1], settings); expected = warm.execute(request); }
        // Distinct canonical paths (hard links, same filesystem as the model) force real
        // router loads and evictions with one loaded model.
        const auto links = std::filesystem::current_path() / ("stress-router-" + std::to_string(::getpid()));
        std::filesystem::create_directory(links);
        struct cleanup { std::filesystem::path path; ~cleanup() { std::error_code e; std::filesystem::remove_all(path, e); } } remove_links{links};
        for (const char *name : {"a.gguf", "b.gguf"}) std::filesystem::create_hard_link(std::filesystem::canonical(argv[1]), links / name);
        const auto router_config = "{\"models\":{\"a\":\"" + (links / "a.gguf").string() + "\",\"b\":\"" +
                                   (links / "b.gguf").string() + "\"},\"max_loaded\":1}";
        const auto routed = [&](const char *id) { return request.substr(0, request.size() - 1) + ",\"model\":\"" + id + "\"}"; };
        const auto routed_expected = [&](const char *id) { return expected.substr(0, expected.size() - 1) + ",\"model\":\"" + id + "\"}"; };
        const auto baseline_rss = rss_kib(), baseline_fds = (long) entries("/proc/self/fd"), baseline_threads = (long) entries("/proc/self/task");
        if (baseline_rss < 0) throw std::runtime_error("Could not measure baseline RSS");
        unsigned completed = 0;
        long maximum_closed_rss = baseline_rss;
        std::vector<long> closed_rss;
        double last_progress = 0;
        for (; completed < unsigned(rounds) || elapsed_seconds() < seconds; ++completed) {
          {
            ducktape::laya engine(argv[1], settings);
            if (engine.execute(request) != expected) throw std::runtime_error("Open/use/close result changed");
            try { engine.execute("{bad json"); throw std::runtime_error("Invalid request accepted"); }
            catch (const ducktape::error &error) { if (error.status != DTL_INVALID_ARGUMENT) throw; }
            auto options = dtl_default_call_options(); options.timeout_ms = 1;
            try { engine.execute(request, nullptr, &options); throw std::runtime_error("Deadline ignored"); }
            catch (const ducktape::error &error) { if (error.status != DTL_DEADLINE_EXCEEDED) throw; }
            if (engine.execute(request) != expected) throw std::runtime_error("Post-deadline result changed");
            // Concurrent callers share the handle while the main thread issues mixed traffic.
            std::atomic<unsigned> mismatches{0};
            std::vector<std::thread> callers;
            for (int i = 0; i < 3; ++i) callers.emplace_back([&] {
                try { if (engine.execute(request) != expected) ++mismatches; } catch (...) { ++mismatches; }
            });
            if (completed % 10 == 0) {
                const auto batch = engine.execute("[" + request + "," + request + "]");
                if (batch != "[" + expected + "," + expected + "]") ++mismatches;
            }
            if (completed % 5 == 2) {
                // An independent engine runs alongside the shared one.
                ducktape::laya second(argv[1], settings);
                if (second.execute(request) != expected) ++mismatches;
            }
            for (auto &thread : callers) thread.join();
            if (mismatches) throw std::runtime_error("Concurrent or batch result changed");
            if (completed % 5 == 4) {
                // Cold loads and evictions: each switch unloads the other model.
                ducktape::router router(router_config, settings);
                for (const char *id : {"a", "b", "a"})
                    if (router.execute(routed(id)) != routed_expected(id)) throw std::runtime_error("Routed result changed");
                if (router.execute("[" + routed("b") + "," + routed("a") + "]") != "[" + routed_expected("b") + "," + routed_expected("a") + "]")
                    throw std::runtime_error("Routed batch result changed");
            }
          }
          // Measure after destruction so a temporarily resident model is not a leak.
          const long current_rss = rss_kib();
          if (current_rss < 0 || current_rss > baseline_rss + 128 * 1024 ||
              (long) entries("/proc/self/fd") != baseline_fds || (long) entries("/proc/self/task") != baseline_threads)
              throw std::runtime_error("Post-close resource growth exceeded the declared bound");
          maximum_closed_rss = std::max(maximum_closed_rss, current_rss);
          closed_rss.push_back(current_rss);
          if (elapsed_seconds() - last_progress >= 60) {
              last_progress = elapsed_seconds();
              std::cerr << "{\"completed_rounds\":" << completed + 1 << ",\"elapsed_seconds\":" << last_progress
                        << ",\"closed_rss_kib\":" << current_rss << "}\n";
          }
        }
        // Concurrent callers share one handle and serialize safely. Each thread
        // owns its buffers; close waits until all threads have joined.
        std::atomic<unsigned> failures{0};
        {
            ducktape::laya engine(argv[1], settings); std::vector<std::thread> callers;
            for (int i = 0; i < 4; ++i) callers.emplace_back([&] {
                for (int j = 0; j < 3; ++j) { try { if (engine.execute(request) != expected) ++failures; } catch (...) { ++failures; } }
            });
            for (auto &thread : callers) thread.join();
        }
        if (failures) throw std::runtime_error("Concurrent result mismatch");
        // Slow leaks can hide inside the per-close allowance. After warm-up (first
        // quarter), the last quarter's median closed RSS must not exceed the second
        // quarter's by more than 32 MiB; medians absorb allocator arena swings.
        const auto median = [&](size_t begin, size_t end) {
            std::vector<long> part(closed_rss.begin() + begin, closed_rss.begin() + end);
            std::nth_element(part.begin(), part.begin() + part.size() / 2, part.end());
            return part[part.size() / 2];
        };
        const size_t quarter = closed_rss.size() / 4;
        const long warm_median = quarter ? median(quarter, 2 * quarter) : baseline_rss;
        const long late_median = quarter ? median(closed_rss.size() - quarter, closed_rss.size()) : baseline_rss;
        if (closed_rss.size() >= 40 && late_median > warm_median + 32 * 1024)
            throw std::runtime_error("Sustained post-warmup closed RSS growth exceeded 32 MiB");
        const auto final_rss = rss_kib(), final_fds = (long) entries("/proc/self/fd"), final_threads = (long) entries("/proc/self/task");
        struct rusage usage{}; getrusage(RUSAGE_SELF, &usage);
        const double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
        // Allocators can retain arenas: this is a bounded-growth regression gate,
        // not proof that every byte was returned to the OS. Sanitizers are separate.
        if (final_fds != baseline_fds || final_threads != baseline_threads || final_rss > baseline_rss + 128 * 1024)
            throw std::runtime_error("Resource growth exceeded predeclared limits (FD/thread exact, RSS +128MiB)");
        std::cout << "{\"rounds\":" << completed << ",\"requested_seconds\":" << seconds
                  << ",\"inference_threads\":2,\"max_closed_rss_kib\":" << maximum_closed_rss
                  << ",\"concurrent_calls\":12,\"baseline_rss_kib\":" << baseline_rss
                  << ",\"warm_median_closed_rss_kib\":" << warm_median << ",\"late_median_closed_rss_kib\":" << late_median
                  << ",\"final_rss_kib\":" << final_rss << ",\"peak_rss_kib\":" << usage.ru_maxrss
                  << ",\"fds\":" << final_fds << ",\"threads\":" << final_threads << ",\"elapsed_seconds\":" << elapsed << "}\n";
    } catch (const std::exception &error) { std::cerr << error.what() << '\n'; return 1; }
}
