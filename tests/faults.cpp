// Real-model fault injection: damaged model files and allocation failure.
// Damaged copies live in a temporary directory beside the working directory; the
// supplied model is only read. Allocation failure runs in forked children so an
// address-space limit cannot affect the parent or the other checks.
#include "ducktape_laya.hpp"
#include <fcntl.h>
#include <signal.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <unistd.h>
#include <atomic>
#include <cerrno>
#include <cmath>
#include <cstdlib>
#include <new>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>
namespace fs = std::filesystem;
#ifndef __has_feature
#define __has_feature(x) 0
#endif
#if defined(__SANITIZE_ADDRESS__) || defined(__SANITIZE_THREAD__) || __has_feature(address_sanitizer) || __has_feature(thread_sanitizer)
#define DTL_SANITIZED 1 // Sanitizer runtimes own operator new and reserve shadow address space.
#else
// Allocation-failure injection, armed only around a single call. C++ allocations
// (fail_after) throw; C allocations (c_fail_after: malloc and friends, which ggml
// and llama.cpp use directly) return NULL. Each counter fails its Nth allocation.
// operator new takes glibc's allocator directly so the two are counted separately.
extern "C" void *__libc_malloc(size_t);
extern "C" void *__libc_calloc(size_t, size_t);
extern "C" void *__libc_realloc(void *, size_t);
extern "C" void *__libc_memalign(size_t, size_t);
static std::atomic<long> fail_after{-1}, failed{0}, c_fail_after{-1}, c_failed{0}, c_counted{0};
static std::atomic<bool> c_counting{false};
void *operator new(std::size_t size) {
    if (fail_after.load(std::memory_order_relaxed) >= 0 && fail_after.fetch_sub(1) == 0) { ++failed; throw std::bad_alloc(); }
    if (void *p = __libc_malloc(size ? size : 1)) return p;
    throw std::bad_alloc();
}
void operator delete(void *p) noexcept { std::free(p); }
void operator delete(void *p, std::size_t) noexcept { std::free(p); }
static bool c_inject() {
    if (c_counting.load(std::memory_order_relaxed)) ++c_counted;
    if (c_fail_after.load(std::memory_order_relaxed) >= 0 && c_fail_after.fetch_sub(1) == 0) { ++c_failed; errno = ENOMEM; return true; }
    return false;
}
extern "C" {
void *malloc(size_t n) { return c_inject() ? nullptr : __libc_malloc(n); }
void *calloc(size_t n, size_t size) { return c_inject() ? nullptr : __libc_calloc(n, size); }
void *realloc(void *p, size_t n) { return n && c_inject() ? nullptr : __libc_realloc(p, n); }
void *memalign(size_t alignment, size_t n) { return c_inject() ? nullptr : __libc_memalign(alignment, n); }
void *aligned_alloc(size_t alignment, size_t n) { return c_inject() ? nullptr : __libc_memalign(alignment, n); }
int posix_memalign(void **out, size_t alignment, size_t n) {
    if (c_inject()) return ENOMEM;
    void *p = __libc_memalign(alignment, n);
    if (!p) return ENOMEM;
    *out = p;
    return 0;
}
}
#endif
static void expect(bool ok, const std::string &message) { if (!ok) throw std::runtime_error(message); }

struct gguf_layout {
    uint64_t n_tensors_at = 8, n_kv_at = 16;
    std::map<std::string, std::pair<uint32_t, uint64_t>> values; // key -> (type, value offset)
};
static gguf_layout parse(const std::string &path) {
    std::ifstream in(path, std::ios::binary);
    std::vector<char> head(16 << 20);
    in.read(head.data(), head.size());
    head.resize(in.gcount());
    size_t at = 0;
    auto take = [&](size_t n) { expect(at + n <= head.size(), "GGUF metadata exceeds parser window"); size_t was = at; at += n; return was; };
    auto u32 = [&] { uint32_t v; std::memcpy(&v, &head[take(4)], 4); return v; };
    auto u64 = [&] { uint64_t v; std::memcpy(&v, &head[take(8)], 8); return v; };
    auto str = [&] { auto n = u64(); return std::string(&head[take(n)], n); };
    std::function<void(uint32_t)> skip = [&](uint32_t type) {
        static const size_t sizes[] = {1, 1, 2, 2, 4, 4, 4, 1, 0, 0, 8, 8, 8};
        expect(type <= 12, "unknown GGUF type");
        if (type == 8) str();
        else if (type == 9) { auto inner = u32(); auto n = u64(); for (uint64_t i = 0; i < n; ++i) skip(inner); }
        else take(sizes[type]);
    };
    expect(head.size() > 24 && std::memcmp(head.data(), "GGUF", 4) == 0, "model is not GGUF");
    take(8); u64();
    gguf_layout layout;
    for (auto n = u64(); n; --n) {
        auto key = str();
        auto type = u32();
        layout.values[key] = {type, at};
        skip(type);
    }
    return layout;
}
static std::string read_at(const std::string &path, uint64_t offset, size_t n) {
    std::ifstream in(path, std::ios::binary);
    in.seekg(offset);
    std::string bytes(n, '\0');
    in.read(bytes.data(), n);
    expect(static_cast<size_t>(in.gcount()) == n, "short read");
    return bytes;
}
static void write_at(const std::string &path, uint64_t offset, const std::string &bytes) {
    std::fstream out(path, std::ios::binary | std::ios::in | std::ios::out);
    out.seekp(offset);
    out.write(bytes.data(), bytes.size());
    expect(static_cast<bool>(out), "patch write failed");
}
static void prefix_copy(const std::string &from, const std::string &to, uint64_t n) {
    std::ifstream in(from, std::ios::binary);
    std::ofstream out(to, std::ios::binary | std::ios::trunc);
    std::vector<char> buffer(1 << 20);
    while (n) {
        auto chunk = std::min<uint64_t>(n, buffer.size());
        in.read(buffer.data(), chunk);
        out.write(buffer.data(), chunk);
        n -= chunk;
    }
    expect(static_cast<bool>(in) && static_cast<bool>(out), "prefix copy failed");
}
template<class T> static std::string bytes_of(T value) { return std::string(reinterpret_cast<const char *>(&value), sizeof value); }
static size_t open_descriptors() {
    size_t n = 0;
    for ([[maybe_unused]] auto &entry : fs::directory_iterator("/proc/self/fd")) ++n;
    return n;
}
static uint64_t virtual_bytes() {
    std::ifstream in("/proc/self/status");
    for (std::string line; std::getline(in, line);)
        if (line.rfind("VmSize:", 0) == 0) return std::stoull(line.substr(7)) * 1024;
    throw std::runtime_error("VmSize unavailable");
}
static void limit_address_space(uint64_t bytes) {
    rlimit limit{};
    expect(getrlimit(RLIMIT_AS, &limit) == 0, "getrlimit");
    limit.rlim_cur = bytes;
    expect(setrlimit(RLIMIT_AS, &limit) == 0, "setrlimit");
}
static void unlimit_address_space() {
    rlimit limit{};
    expect(getrlimit(RLIMIT_AS, &limit) == 0, "getrlimit");
    limit.rlim_cur = limit.rlim_max;
    expect(setrlimit(RLIMIT_AS, &limit) == 0, "setrlimit");
}
// Runs fn in a child; a signal, abort or unexpected exit fails the parent check.
static void in_child(const char *name, const std::function<void()> &fn) {
    std::cout.flush(); std::cerr.flush();
    pid_t pid = fork();
    expect(pid >= 0, "fork");
    if (!pid) {
        try { fn(); std::_Exit(0); }
        catch (const std::exception &e) { std::cerr << name << ": " << e.what() << '\n'; std::_Exit(2); }
    }
    int status = 0;
    expect(waitpid(pid, &status, 0) == pid, "waitpid");
    expect(WIFEXITED(status) && WEXITSTATUS(status) == 0,
           std::string(name) + (WIFSIGNALED(status) ? ": child killed by signal " + std::to_string(WTERMSIG(status)) : ": child failed"));
    std::cerr << name << ": passed\n";
}

#ifndef DTL_SANITIZED
// Fail each C allocation of one dtl_execute and one dtl_open in its own child
// (four at a time). ggml aborts by design on most small allocation failures,
// so a point may end in an exact result, DTL_OUT_OF_MEMORY or SIGABRT from
// ggml; never another signal (an unchecked NULL). The thread pool's failure
// must reach the caller as DTL_OUT_OF_MEMORY and leave the engine usable.
static void c_allocation_sweep(const std::string &model, const std::string &batch) {
    setenv("GGML_NO_BACKTRACE", "1", 1); // ggml_abort would otherwise attach gdb
    dtl_engine *engine = nullptr; char *result = nullptr, *error = nullptr;
    auto options = dtl_default_options(); options.threads = 2;
    expect(dtl_open(model.c_str(), &options, &engine, &error) == DTL_OK, "open");
    expect(dtl_execute(engine, batch.c_str(), nullptr, &result, &error) == DTL_OK, "baseline");
    const std::string expected = result; dtl_free(result);
    auto count = [&](const std::function<void()> &call) { c_counted = 0; c_counting = true; call(); c_counting = false; return long(c_counted); };
    const long in_execute = count([&] { dtl_execute(engine, batch.c_str(), nullptr, &result, &error); dtl_free(result); });
    const long in_open = count([&] { dtl_engine *e = nullptr; dtl_open(model.c_str(), &options, &e, &error); dtl_close(e); });
    enum outcome { exact = 0, out_of_memory = 3, aborted = 4, model_error = 6 };
    auto sweep = [&](const char *label, long points, const std::function<int()> &child) {
        std::map<int, long> outcomes;
        std::map<pid_t, long> running;
        auto reap = [&] {
            int status = 0;
            pid_t pid = wait(&status);
            expect(pid > 0, "wait");
            const long point = running[pid]; running.erase(pid);
            if (WIFSIGNALED(status) && WTERMSIG(status) == SIGABRT) ++outcomes[aborted];
            else {
                expect(WIFEXITED(status) && (WEXITSTATUS(status) == exact || WEXITSTATUS(status) == out_of_memory || WEXITSTATUS(status) == model_error),
                       std::string(label) + " C allocation " + std::to_string(point) + ": " +
                       (WIFSIGNALED(status) ? "killed by signal " + std::to_string(WTERMSIG(status)) : "exit " + std::to_string(WEXITSTATUS(status))));
                ++outcomes[WEXITSTATUS(status)];
            }
        };
        for (long n = 0; n < points; ++n) {
            if (running.size() == 4) reap();
            std::cout.flush(); std::cerr.flush();
            pid_t pid = fork();
            expect(pid >= 0, "fork");
            if (!pid) {
                if (int null = ::open("/dev/null", O_WRONLY); null >= 0) dup2(null, 2);
                prctl(PR_SET_DUMPABLE, 0); // expected aborts skip the system crash handler
                c_fail_after = n;
                std::_Exit(child());
            }
            running[pid] = n;
        }
        while (!running.empty()) reap();
        std::cerr << label << " C allocation failures: " << points << " points, " << outcomes[exact] << " exact, "
                  << outcomes[out_of_memory] << " DTL_OUT_OF_MEMORY, " << outcomes[model_error] << " DTL_MODEL_ERROR, "
                  << outcomes[aborted] << " ggml abort\n";
        return outcomes;
    };
    auto executed = sweep("execute", in_execute, [&] {
        auto status = dtl_execute(engine, batch.c_str(), nullptr, &result, &error);
        c_fail_after = -1;
        if (status == DTL_OK && result != expected) return 1;
        if (status != DTL_OK && status != DTL_OUT_OF_MEMORY) return 2;
        // The engine must keep returning exact results after a rejected call.
        char *again = nullptr;
        if (dtl_execute(engine, batch.c_str(), nullptr, &again, &error) != DTL_OK || again != expected) return 5;
        return status == DTL_OK ? int(exact) : int(out_of_memory);
    });
    expect(executed[out_of_memory] > 0, "no C allocation failure reached the caller as DTL_OUT_OF_MEMORY");
    sweep("open", in_open, [&] {
        dtl_engine *e = nullptr;
        auto status = dtl_open(model.c_str(), &options, &e, &error);
        c_fail_after = -1;
        if (status == DTL_OK) { dtl_close(e); return int(exact); }
        if (e) return 2;
        return status == DTL_OUT_OF_MEMORY ? int(out_of_memory) : status == DTL_MODEL_ERROR ? int(model_error) : 2;
    });
    dtl_close(engine);
}
#endif

int main(int argc, char **argv) {
    // --c-allocations runs only the C allocation sweep (registered as its own test).
    if (argc < 2 || argc > 3 || (argc == 3 && std::string(argv[2]) != "--c-allocations")) {
        std::cerr << "usage: laya-fault-tests MODEL [--c-allocations]\n"; return 2;
    }
    const std::string model = fs::canonical(argv[1]).string();
    const auto work = fs::current_path() / ("fault-test-" + std::to_string(getpid()));
    struct cleanup { fs::path dir; ~cleanup() { std::error_code ec; fs::permissions(dir, fs::perms::owner_all, ec); fs::remove_all(dir, ec); } } guard{work};
    try {
        const std::string request = R"({"state":"Please refund the duplicate payment.","questions":{"refund":{"type":"noul","instructions":"Does the customer ask for a refund?"},"team":{"type":"choice","instructions":"Which team handles this?","criteria":{"billing":"Payments","technical":"Bugs","sales":"Purchases"}}}})";
        std::string batch = "[";
        for (int i = 0; i < 2; ++i) batch += (i ? "," : "") + request;
        batch += "]";

#ifndef DTL_SANITIZED
        if (argc == 3) {
            c_allocation_sweep(model, batch);
            std::cout << "C allocation fault tests passed\n";
            return 0;
        }
        // Allocation failure, before this process creates any runtime threads.
        in_child("open under address-space limit", [&] {
            limit_address_space(virtual_bytes() + (32ull << 20));
            dtl_engine *engine = nullptr; char *error = nullptr;
            auto options = dtl_default_options(); options.threads = 2;
            auto status = dtl_open(model.c_str(), &options, &engine, &error);
            expect(!engine, "open succeeded inside a 32 MiB address-space margin");
            expect(status == DTL_MODEL_ERROR || status == DTL_OUT_OF_MEMORY, "unexpected status " + std::to_string(status));
            dtl_free(error);
        });
        // Fail one C++ allocation at a time: early points exhaustively, later points
        // sampled. Every call must report allocation failure (or succeed when the
        // failure is absorbed) and the engine must then return exact results again.
        {
            dtl_engine *engine = nullptr; char *result = nullptr, *error = nullptr;
            auto options = dtl_default_options(); options.threads = 2;
            expect(dtl_open(model.c_str(), &options, &engine, &error) == DTL_OK, "open");
            expect(dtl_execute(engine, batch.c_str(), nullptr, &result, &error) == DTL_OK, "baseline");
            const std::string expected = result; dtl_free(result);
            auto points = [](long dense, long stride) { std::vector<long> n; for (long i = 0; i < dense; ++i) n.push_back(i); for (long i = dense; ; i += stride) { n.push_back(i); if (i > (1l << 30)) break; } return n; };
            long injected = 0, absorbed = 0;
            for (long n : points(384, 97)) {
                fail_after = n; failed = 0;
                auto status = dtl_execute(engine, batch.c_str(), nullptr, &result, &error);
                fail_after = -1;
                if (status == DTL_OK) {
                    expect(result == expected, "result changed after absorbed allocation failure");
                    dtl_free(result);
                    if (!failed) break;
                    ++absorbed;
                } else {
                    expect(status == DTL_OUT_OF_MEMORY && failed, "allocation failure " + std::to_string(n) + " returned status " +
                           std::to_string(status) + ": " + (error ? error : ""));
                    dtl_free(error); ++injected;
                }
                if (injected % 32 == 0) {
                    expect(dtl_execute(engine, batch.c_str(), nullptr, &result, &error) == DTL_OK && result == expected, "engine did not recover");
                    dtl_free(result);
                }
            }
            expect(injected > 384, "too few injected request allocation failures");
            expect(dtl_execute(engine, batch.c_str(), nullptr, &result, &error) == DTL_OK && result == expected, "engine did not recover");
            dtl_free(result);
            dtl_close(engine);
            std::cerr << "request allocation failures: " << injected << " rejected, " << absorbed << " absorbed\n";
            long opened = 0;
            for (long n = 0; ; n = n < 32 ? n + 1 : n * 3 / 2) {
                fail_after = n; failed = 0;
                auto status = dtl_open(model.c_str(), &options, &engine, &error);
                fail_after = -1;
                if (status == DTL_OK) { dtl_close(engine); if (!failed) break; continue; }
                expect(!engine && failed && (status == DTL_OUT_OF_MEMORY || status == DTL_MODEL_ERROR),
                       "open allocation failure " + std::to_string(n) + " returned status " + std::to_string(status) + ": " + (error ? error : ""));
                dtl_free(error); ++opened;
            }
            expect(opened > 32, "too few injected open allocation failures");
            std::cerr << "open allocation failures: " << opened << " rejected\n";
        }

#else
        if (argc == 3) { std::cerr << "C allocation injection requires an uninstrumented build\n"; return 1; }
        std::cerr << "allocation-failure injection skipped under a sanitizer runtime\n";
#endif
        fs::create_directory(work);
        const auto before = open_descriptors();
        auto rejected = [&](const std::string &label, const std::string &path, const char *message = nullptr) {
            dtl_engine *engine = nullptr; char *error = nullptr;
            auto options = dtl_default_options(); options.threads = 2;
            auto status = dtl_open(path.c_str(), &options, &engine, &error);
            const std::string text = error ? error : "";
            dtl_free(error);
            if (engine) dtl_close(engine);
            expect(status == DTL_MODEL_ERROR && !engine, label + ": expected model error, got " + std::to_string(status));
            expect(!text.empty(), label + ": missing error message");
            expect(!message || text.find(message) != std::string::npos, label + ": unexpected message: " + text);
            std::cerr << label << ": rejected\n";
        };
        const auto size = fs::file_size(model);
        const auto empty = (work / "empty.gguf").string();
        std::ofstream(empty).close();
        rejected("empty file", empty);
        const auto text = (work / "text.gguf").string();
        std::ofstream(text) << "not a model";
        rejected("non-GGUF bytes", text);
        rejected("directory", work.string());
        const auto prefix = (work / "prefix.gguf").string();
        for (uint64_t n : {uint64_t(24), uint64_t(4096), size / 2, size - 1}) {
            prefix_copy(model, prefix, n);
            rejected("truncated to " + std::to_string(n) + " bytes", prefix);
        }
        fs::remove(prefix);
        if (geteuid() != 0) {
            const auto unreadable = (work / "unreadable.gguf").string();
            prefix_copy(model, unreadable, 4096);
            fs::permissions(unreadable, fs::perms::none);
            rejected("unreadable file", unreadable);
        }

        // Each patch edits one copy in place and restores it before the next.
        const auto copy = (work / "copy.gguf").string();
        fs::copy_file(model, copy);
        const auto layout = parse(copy);
        auto patched = [&](const std::string &label, uint64_t offset, const std::string &bytes, const char *message = nullptr) {
            const auto original = read_at(copy, offset, bytes.size());
            write_at(copy, offset, bytes);
            rejected(label, copy, message);
            write_at(copy, offset, original);
        };
        auto value = [&](const std::string &suffix, uint32_t type) {
            for (auto &[key, entry] : layout.values)
                if (key.size() >= suffix.size() && key.compare(key.size() - suffix.size(), suffix.size(), suffix) == 0) {
                    expect(entry.first == type, "unexpected GGUF type for " + key);
                    return entry.second;
                }
            throw std::runtime_error("missing GGUF key " + suffix);
        };
        patched("corrupt magic", 0, "GGUX");
        patched("unsupported version", 4, bytes_of<uint32_t>(99));
        patched("huge tensor count", layout.n_tensors_at, bytes_of<uint64_t>(1ull << 62));
        patched("huge metadata count", layout.n_kv_at, bytes_of<uint64_t>(1ull << 62));
        // A string value starts with its u64 length, followed by its bytes.
        patched("other decision type", value(".decision.type", 8) + 8, "x", "Only Laya decision models");
        patched("oversized string length", value(".decision.type", 8), bytes_of<uint64_t>(1ull << 62));
        for (auto &[key, entry] : layout.values)
            if (key.find(".decision.temperature.") != std::string::npos) {
                expect(entry.first == 6, "temperature is not f32");
                patched("negative temperature", entry.second, bytes_of<float>(-1.0f), "Invalid temperature");
                patched("NaN temperature", entry.second, bytes_of<float>(std::nanf("")), "Invalid temperature");
                break;
            }
        for (auto &[key, entry] : layout.values)
            if (key.size() > 16 && key.compare(key.size() - 16, 16, ".max_head_tokens") == 0) {
                const auto zero = entry.first == 8 ? std::string() : std::string(entry.first == 10 || entry.first == 11 ? 8 : 4, '\0');
                if (!zero.empty()) patched("zero head budget", entry.second, zero, "Invalid head token budget");
            }
        expect(open_descriptors() == before, "descriptor leak after rejected models");

        // The restored copy and the original model still load and agree exactly.
        auto options = dtl_default_options(); options.threads = 2;
        ducktape::laya restored(copy.c_str(), options), original(model.c_str(), options);
        expect(restored.decide(request) == original.decide(request), "restored copy differs");
        std::cout << "fault tests passed\n";
        return 0;
    } catch (const std::exception &e) { std::cerr << e.what() << '\n'; return 1; }
}
