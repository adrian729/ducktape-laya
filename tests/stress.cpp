// Sustained public-ABI lifecycle/concurrency check; no model downloads.
#include "ducktape_laya.hpp"
#include "stress_rule.hpp"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <thread>
#include <vector>
#ifdef _WIN32
// Windows: committed private bytes for memory (the working set is trimmed by the OS and
// would hide or fake growth), the handle count for descriptors, threads from a snapshot.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <psapi.h>
#include <tlhelp32.h>
#pragma comment(lib, "psapi.lib")
static const char *const memory_metric = "PrivateUsage";
static long rss_kib() {
    PROCESS_MEMORY_COUNTERS_EX counters{};
    if (!GetProcessMemoryInfo(GetCurrentProcess(), reinterpret_cast<PROCESS_MEMORY_COUNTERS *>(&counters), sizeof counters)) return -1;
    return long(counters.PrivateUsage / 1024);
}
static long peak_kib() {
    PROCESS_MEMORY_COUNTERS counters{};
    return GetProcessMemoryInfo(GetCurrentProcess(), &counters, sizeof counters) ? long(counters.PeakPagefileUsage / 1024) : -1;
}
static long descriptor_count() {
    DWORD count = 0;
    return GetProcessHandleCount(GetCurrentProcess(), &count) ? long(count) : -1;
}
// Threads Windows starts for its own thread pools (the loader's workers, I/O and timer
// workers) begin in ntdll.dll and retire on the pool's schedule, up to minutes later; they
// are not counted. Every other thread (std::thread, ggml's workers, _beginthreadex) is.
// A pool our code created and leaked would still show up as leaked handles.
static const char *const thread_metric = "threads not started by ntdll (OS thread-pool workers excluded)";
typedef LONG (NTAPI *query_thread_t)(HANDLE, ULONG, PVOID, ULONG, PULONG);
static bool os_pool_thread(DWORD id) {
    static const auto query = reinterpret_cast<query_thread_t>(
        reinterpret_cast<void *>(GetProcAddress(GetModuleHandleW(L"ntdll.dll"), "NtQueryInformationThread")));
    static const HMODULE ntdll = GetModuleHandleW(L"ntdll.dll");
    const HANDLE thread = OpenThread(THREAD_QUERY_INFORMATION, FALSE, id);
    if (!thread || !query) { if (thread) CloseHandle(thread); return false; }
    void *start = nullptr;
    const bool known = query(thread, 9 /* ThreadQuerySetWin32StartAddress */, &start, sizeof start, nullptr) >= 0;
    CloseHandle(thread);
    HMODULE owner = nullptr;
    return known && GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                                       static_cast<LPCWSTR>(start), &owner) && owner == ntdll;
}
static long thread_count() {
    const HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    if (snapshot == INVALID_HANDLE_VALUE) return -1;
    THREADENTRY32 entry{}; entry.dwSize = sizeof entry;
    long count = 0;
    for (BOOL more = Thread32First(snapshot, &entry); more; more = Thread32Next(snapshot, &entry))
        if (entry.th32OwnerProcessID == GetCurrentProcessId() && !os_pool_thread(entry.th32ThreadID)) ++count;
    CloseHandle(snapshot);
    return count;
}
static unsigned long process_id() { return GetCurrentProcessId(); }
// Windows' own thread pools (the parallel loader, I/O completion) start and retire worker
// threads on their own schedule. Counts must return exactly to the baseline; they get up
// to 60 s to do so, and every wait is reported.
static unsigned settle_waits = 0;
static bool settled(long fds, long threads) {
    for (int i = 0; i < 600; ++i) {
        if (descriptor_count() == fds && thread_count() == threads) return true;
        if (i == 0) ++settle_waits;
        Sleep(100);
    }
    return false;
}
static void stable_baseline() {
    // The loader's worker threads (three after start-up) retire 30 s after its last work.
    // Wait (up to 180 s) until the thread and handle counts have held for 40 s.
    long threads = thread_count(), fds = descriptor_count();
    for (int stable = 0, i = 0; stable < 400 && i < 1800; ++i) {
        Sleep(100);
        const long t = thread_count(), f = descriptor_count();
        stable = (t == threads && f == fds) ? stable + 1 : 0;
        threads = t; fds = f;
    }
}
#else
#include <sys/resource.h>
#include <unistd.h>
static const char *const memory_metric = "VmRSS";
static const char *const thread_metric = "threads (/proc/self/task)";
static long rss_kib() {
    std::ifstream file("/proc/self/status"); std::string name;
    while (file >> name) { if (name == "VmRSS:") { long value; file >> value; return value; } std::getline(file, name); }
    return -1;
}
static long peak_kib() { struct rusage usage{}; getrusage(RUSAGE_SELF, &usage); return usage.ru_maxrss; }
static size_t entries(const char *path) {
    size_t n = 0; for (const auto &entry : std::filesystem::directory_iterator(path)) { (void) entry; ++n; } return n;
}
static long descriptor_count() { return (long) entries("/proc/self/fd"); }
static long thread_count() { return (long) entries("/proc/self/task"); }
static unsigned long process_id() { return (unsigned long) ::getpid(); }
static const unsigned settle_waits = 0;
static bool settled(long fds, long threads) { return descriptor_count() == fds && thread_count() == threads; }
static void stable_baseline() {}
#endif
static const std::string request = R"({"state":"Please refund me.","questions":{"refund":{"type":"noul","instructions":"Is a refund requested?"}}})";
// laya-stress MODEL [ROUNDS]                            ROUNDS rounds (default 40, 10..10000)
// laya-stress MODEL --seconds N [--min-rounds M]        at least N seconds and M rounds (default 10)
// laya-stress --check-series RESULT_JSON                 both late-growth rules on a result's series
// The result line (stdout) is written on success and on every failure after the first completed
// round (status "failed", the message in "failure"), so a failed soak keeps its series.
static int check_series(const char *path) {
    std::ifstream file(path, std::ios::binary);
    std::stringstream text;
    text << file.rdbuf();
    std::vector<long> series;
    if (!file || !stress_rule::read_series(text.str(), series)) {
        std::cerr << "No result line with closed_rss_kib_by_round in " << path << '\n';
        return 2;
    }
    const auto growth = stress_rule::evaluate(series);
    const auto failure = stress_rule::violation(growth);
    std::cout << "{\"rounds\":" << series.size() << ",\"status\":\"" << (failure.empty() ? "passed" : "failed")
              << "\",\"failure\":" << (failure.empty() ? std::string("null") : stress_rule::json_string(failure))
              << ",\"late_growth\":" << stress_rule::late_growth_json(growth) << "}\n";
    return failure.empty() ? 0 : 1;
}
int main(int argc, char **argv) {
    if (argc == 3 && std::string(argv[1]) == "--check-series") return check_series(argv[2]);
    if (argc < 2 || argc > 6) return 2;
    stress_rule::result report;
    report.memory_metric = memory_metric;
    report.thread_metric = thread_metric;
    auto &closed_rss = report.closed_rss_kib_by_round;
    const auto start = std::chrono::steady_clock::now();
    auto elapsed_seconds = [&] { return std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count(); };
    try {
        int rounds = 40, seconds = 0;
        const auto number = [](const char *text) {
            size_t end = 0;
            const auto value = std::stoi(text, &end);
            if (text[end] != '\0') throw std::runtime_error("Invalid numeric argument");
            return value;
        };
        if (argc == 3) {
            rounds = number(argv[2]);
            if (rounds < 10 || rounds > 10000) return 2;
        } else if (argc > 3) {
            int min_rounds = 10;
            bool have_seconds = false, have_min_rounds = false;
            for (int i = 2; i < argc; i += 2) {
                const std::string flag = argv[i];
                if (i + 1 >= argc) return 2;
                if (flag == "--seconds" && !have_seconds) { seconds = number(argv[i + 1]); have_seconds = true; }
                else if (flag == "--min-rounds" && !have_min_rounds) { min_rounds = number(argv[i + 1]); have_min_rounds = true; }
                else return 2;
            }
            if (!have_seconds || seconds < 1 || seconds > 86400 || min_rounds < 10 || min_rounds > 1000000) return 2;
            rounds = min_rounds;
        }
        report.requested_seconds = seconds;
        report.min_rounds = rounds;
        const std::string settings = R"({"threads":2})";
        std::string expected;
        // The should_cancel poll that falls in the middle of the request's forward pass (see
        // tests/control.cpp): every round cancels there once, so the graph abort and the reuse
        // of the aborted context are exercised, not only stops before inference.
        unsigned graph_poll = 0;
        {
            auto warm = ducktape::engine::open(argv[1], settings);
            expected = warm.execute(request);
            const auto count = [&](const std::string &body, std::string *out) {
                std::atomic<unsigned> polls{0};
                ducktape::call_options call;
                call.should_cancel = [](void *p) -> uint32_t { ++*static_cast<std::atomic<unsigned> *>(p); return 0; };
                call.cancel_user_data = &polls;
                const auto result = warm.execute(body, call);
                if (out) *out = result;
                return polls.load();
            };
            std::string words, out;
            for (int i = 0; i < 4; ++i) words += "Order " + std::to_string(1000 + i) + " was charged twice and the customer asks for the money back. ";
            const auto graph_request = [&](unsigned max_tokens) {
                return R"({"state":")" + words + R"(","overflow":"scan","max_tokens":)" + std::to_string(max_tokens) +
                       R"(,"questions":{"refund":{"type":"noul","instructions":"Is a refund requested?"}}})";
            };
            warm.execute(graph_request(512));
            const unsigned one = count(graph_request(512), nullptr), several = count(graph_request(48), &out);
            const auto at = out.find("\"windows\":");
            const unsigned windows = at == std::string::npos ? 0 : unsigned(std::stoul(out.substr(at + 10)));
            if (windows < 2 || several <= one || (several - one) % (windows - 1)) throw std::runtime_error("Could not measure the forward pass's polls");
            const unsigned per_window = (several - one) / (windows - 1);
            warm.execute(request);
            const unsigned polls = count(request, nullptr);
            if (per_window < 32 || polls <= per_window) throw std::runtime_error("The forward pass does not poll the cancellation callback");
            graph_poll = polls - per_window / 2;
        }
        // Distinct canonical paths (hard links, same filesystem as the model) force real
        // router loads and evictions with one loaded model.
        const auto links = std::filesystem::current_path() / ("stress-router-" + std::to_string(process_id()));
        std::filesystem::create_directory(links);
        struct cleanup { std::filesystem::path path; ~cleanup() { std::error_code e; std::filesystem::remove_all(path, e); } } remove_links{links};
        for (const char *name : {"a.gguf", "b.gguf"}) std::filesystem::create_hard_link(std::filesystem::canonical(argv[1]), links / name);
        // Forward slashes and UTF-8: a Windows path's backslashes would be JSON escapes.
        const auto router_config = "{\"models\":{\"a\":\"" + (links / "a.gguf").generic_u8string() + "\",\"b\":\"" +
                                   (links / "b.gguf").generic_u8string() + "\"},\"max_loaded\":1}";
        const auto routed = [&](const char *id) { return request.substr(0, request.size() - 1) + ",\"model\":\"" + id + "\"}"; };
        const auto routed_expected = [&](const char *id) { return expected.substr(0, expected.size() - 1) + ",\"model\":\"" + id + "\"}"; };
        stable_baseline();
        const auto baseline_rss = rss_kib(), baseline_fds = descriptor_count(), baseline_threads = thread_count();
        if (baseline_rss < 0 || baseline_fds < 0 || baseline_threads < 0) throw std::runtime_error("Could not measure baseline resources");
        report.baseline_rss_kib = baseline_rss;
        unsigned completed = 0;
        long maximum_closed_rss = baseline_rss;
        double last_progress = 0;
        for (; completed < unsigned(rounds) || elapsed_seconds() < seconds; ++completed) {
          {
            auto engine = ducktape::engine::open(argv[1], settings);
            if (engine.execute(request) != expected) throw std::runtime_error("Open/use/close result changed");
            try { engine.execute("{bad json"); throw std::runtime_error("Invalid request accepted"); }
            catch (const ducktape::error &error) { if (error.status != DTL_INVALID_ARGUMENT) throw; }
            ducktape::call_options options; options.timeout_ms = 1;
            try { engine.execute(request, options); throw std::runtime_error("Deadline ignored"); }
            catch (const ducktape::error &error) { if (error.status != DTL_DEADLINE_EXCEEDED) throw; }
            if (engine.execute(request) != expected) throw std::runtime_error("Post-deadline result changed");
            struct one_shot { std::atomic<unsigned> polls{0}; unsigned at; } stop{{0}, graph_poll};
            ducktape::call_options cancelled;
            cancelled.cancel_user_data = &stop;
            cancelled.should_cancel = [](void *p) -> uint32_t { auto *s = static_cast<one_shot *>(p); return ++s->polls == s->at ? 1 : 0; };
            try { engine.execute(request, cancelled); throw std::runtime_error("Cancellation in the forward pass ignored"); }
            catch (const ducktape::error &error) { if (error.status != DTL_CANCELLED) throw; }
            if (stop.polls != graph_poll) throw std::runtime_error("Cancellation in the forward pass was not latched at its poll");
            if (engine.execute(request) != expected) throw std::runtime_error("Result changed after a graph abort");
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
                auto second = ducktape::engine::open(argv[1], settings);
                if (second.execute(request) != expected) ++mismatches;
            }
            for (auto &thread : callers) thread.join();
            if (mismatches) throw std::runtime_error("Concurrent or batch result changed");
            if (completed % 5 == 4) {
                // Cold loads and evictions: each switch unloads the other model.
                auto router = ducktape::engine::open_router(router_config, settings);
                for (const char *id : {"a", "b", "a"})
                    if (router.execute(routed(id)) != routed_expected(id)) throw std::runtime_error("Routed result changed");
                if (router.execute("[" + routed("b") + "," + routed("a") + "]") != "[" + routed_expected("b") + "," + routed_expected("a") + "]")
                    throw std::runtime_error("Routed batch result changed");
            }
          }
          // Measure after destruction so a temporarily resident model is not a leak.
          const bool counts = settled(baseline_fds, baseline_threads);
          const long current_rss = rss_kib();
          if (current_rss < 0 || current_rss > baseline_rss + 128 * 1024 || !counts)
              throw std::runtime_error("Post-close resource growth exceeded the declared bound after round " + std::to_string(completed) +
                                       ": memory " + std::to_string(current_rss) + " KiB (baseline " + std::to_string(baseline_rss) +
                                       "), descriptors " + std::to_string(descriptor_count()) + " (" + std::to_string(baseline_fds) +
                                       "), threads " + std::to_string(thread_count()) + " (" + std::to_string(baseline_threads) + ")");
          maximum_closed_rss = std::max(maximum_closed_rss, current_rss);
          closed_rss.push_back(current_rss);
          report.rounds = completed + 1;
          report.max_closed_rss_kib = maximum_closed_rss;
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
            auto engine = ducktape::engine::open(argv[1], settings); std::vector<std::thread> callers;
            for (int i = 0; i < 4; ++i) callers.emplace_back([&] {
                for (int j = 0; j < 3; ++j) { try { if (engine.execute(request) != expected) ++failures; } catch (...) { ++failures; } }
            });
            for (auto &thread : callers) thread.join();
        }
        if (failures) throw std::runtime_error("Concurrent result mismatch");
        // Slow leaks can hide inside the per-close allowance. After warm-up (first quarter),
        // the last quarter's median closed RSS must not exceed the second quarter's by more than
        // 32 MiB (from 40 rounds); from 1,000 rounds also the soak rule (stress_rule.hpp).
        // Medians absorb allocator arena swings.
        const auto growth = stress_rule::evaluate(closed_rss);
        const long warm_median = growth.warm_median_kib.value_or(baseline_rss);
        const long late_median = growth.late_median_kib.value_or(baseline_rss);
        report.warm_median_closed_rss_kib = warm_median;
        report.late_median_closed_rss_kib = late_median;
        if (const auto broken = stress_rule::violation(growth); !broken.empty()) throw std::runtime_error(broken);
        settled(baseline_fds, baseline_threads);
        const auto final_rss = rss_kib(), final_fds = descriptor_count(), final_threads = thread_count();
        report.final_rss_kib = final_rss;
        report.fds = final_fds;
        report.threads = final_threads;
        // Allocators can retain arenas: this is a bounded-growth regression gate,
        // not proof that every byte was returned to the OS. Sanitizers are separate.
        if (final_fds != baseline_fds || final_threads != baseline_threads || final_rss > baseline_rss + 128 * 1024)
            throw std::runtime_error("Resource growth exceeded predeclared limits (FD/thread exact, RSS +128MiB)");
        report.peak_rss_kib = peak_kib();
        report.elapsed_seconds = elapsed_seconds();
        report.settle_waits = settle_waits;
        std::cout << stress_rule::result_line(report);
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        if (!closed_rss.empty()) {
            // After the first completed round a failure still reports the series so far.
            report.failure = error.what();
            if (!report.warm_median_closed_rss_kib) {
                const auto growth = stress_rule::evaluate(closed_rss);
                report.warm_median_closed_rss_kib = growth.warm_median_kib.value_or(*report.baseline_rss_kib);
                report.late_median_closed_rss_kib = growth.late_median_kib.value_or(*report.baseline_rss_kib);
            }
            report.peak_rss_kib = peak_kib();
            report.elapsed_seconds = elapsed_seconds();
            report.settle_waits = settle_waits;
            std::cout << stress_rule::result_line(report);
        }
        return 1;
    }
}
