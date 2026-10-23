// Model-free CPU backend fault tests (Linux): a failed worker-thread creation and a
// failed work-buffer allocation must be reported as GGML_STATUS_ALLOC_FAILED, never
// abort the process or leave freed memory in use, and the next computation must
// return the same bytes as before (ggml-thread-creation-failure.patch,
// ggml-cpu-work-buffer.patch). The library maps that status to DTL_OUT_OF_MEMORY.
#include "ggml.h"
#include "ggml-backend.h"
#include "ggml-cpu.h"
#include <dirent.h>
#include <dlfcn.h>
#include <pthread.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <unistd.h>
#include <atomic>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <iostream>
#include <new>
#include <stdexcept>
#include <string>
#include <vector>

#ifndef __has_feature
#define __has_feature(x) 0
#endif
#if defined(__SANITIZE_ADDRESS__) || defined(__SANITIZE_THREAD__) || __has_feature(address_sanitizer) || __has_feature(thread_sanitizer)
#define DTL_SANITIZED 1 // sanitizer runtimes own operator new and pthread_create
#else
// Fails the Nth pthread_create (counting from 0) with EAGAIN, as the kernel does at
// RLIMIT_NPROC or a cgroup pids limit; the threads created before it run normally.
static std::atomic<int> fail_create_at{-1}, creates{0};
extern "C" int pthread_create(pthread_t *thread, const pthread_attr_t *attr, void *(*start)(void *), void *arg) noexcept {
    using real_t = int (*)(pthread_t *, const pthread_attr_t *, void *(*)(void *), void *);
    static const real_t real = reinterpret_cast<real_t>(dlsym(RTLD_NEXT, "pthread_create"));
    if (fail_create_at.load() >= 0 && creates++ == fail_create_at.load()) return EAGAIN;
    return real(thread, attr, start, arg);
}
// Array allocations larger than any recorded while armed=1 fail while armed=2.
static std::atomic<int> armed{0};
static std::atomic<size_t> largest{0};
static std::atomic<long> array_failures{0};
void *operator new[](std::size_t size) {
    if (armed == 1 && size > largest) largest = size;
    if (armed == 2 && size > largest) { ++array_failures; throw std::bad_alloc(); }
    if (void *p = std::malloc(size ? size : 1)) return p;
    throw std::bad_alloc();
}
void operator delete[](void *p) noexcept { std::free(p); }
void operator delete[](void *p, std::size_t) noexcept { std::free(p); }
#endif

static void expect(bool ok, const std::string &message) { if (!ok) throw std::runtime_error(message); }

// c = a (F16, K x N) times b (F32, K x M): the CPU backend converts b to F16 in its
// work buffer, so the buffer grows with M.
struct matmul {
    ggml_context *ctx = nullptr;
    ggml_cgraph *graph = nullptr;
    ggml_tensor *out = nullptr;
    matmul(int k, int n, int m) {
        ggml_init_params params{size_t(8) << 20, nullptr, false};
        ctx = ggml_init(params);
        expect(ctx != nullptr, "ggml_init");
        ggml_tensor *a = ggml_new_tensor_2d(ctx, GGML_TYPE_F16, k, n), *b = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, k, m);
        auto *ad = static_cast<ggml_fp16_t *>(a->data);
        auto *bd = static_cast<float *>(b->data);
        for (int64_t i = 0; i < ggml_nelements(a); ++i) ad[i] = ggml_fp32_to_fp16(float((i * 7919) % 97) / 97.0f - 0.5f);
        for (int64_t i = 0; i < ggml_nelements(b); ++i) bd[i] = float((i * 104729) % 89) / 89.0f - 0.5f;
        out = ggml_mul_mat(ctx, a, b);
        graph = ggml_new_graph(ctx);
        ggml_build_forward_expand(graph, out);
    }
    ~matmul() { ggml_free(ctx); }
    std::vector<char> bytes() const { auto *p = static_cast<const char *>(out->data); return {p, p + ggml_nbytes(out)}; }
    ggml_status compute(int threads) {
        std::memset(out->data, 0, ggml_nbytes(out));
        ggml_cplan plan = ggml_graph_plan(graph, threads, nullptr);
        std::vector<uint8_t> work(plan.work_size);
        plan.work_data = work.data();
        return ggml_graph_compute(graph, &plan);
    }
};

static size_t live_threads() {
    size_t n = 0;
    DIR *dir = opendir("/proc/self/task");
    expect(dir != nullptr, "/proc/self/task");
    while (dirent *entry = readdir(dir)) if (entry->d_name[0] != '.') ++n;
    closedir(dir);
    return n;
}

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
    expect(WIFEXITED(status) && (WEXITSTATUS(status) == 0 || WEXITSTATUS(status) == 77),
           std::string(name) + (WIFSIGNALED(status) ? ": killed by signal " + std::to_string(WTERMSIG(status)) : ": failed"));
    std::cerr << name << (WEXITSTATUS(status) == 77 ? ": skipped (thread limits do not apply to this user)\n" : ": passed\n");
}

int main() {
    try {
        matmul mm(256, 64, 32);
        expect(mm.compute(4) == GGML_STATUS_SUCCESS, "baseline computation");
        const auto expected = mm.bytes();

        // The kernel refuses new threads at RLIMIT_NPROC: the computation reports
        // the failure, then succeeds with the same bytes once threads are available.
        in_child("thread creation refused by RLIMIT_NPROC", [&] {
            rlimit old{};
            expect(getrlimit(RLIMIT_NPROC, &old) == 0, "getrlimit");
            rlimit limit = old;
            limit.rlim_cur = 1;
            expect(setrlimit(RLIMIT_NPROC, &limit) == 0, "setrlimit");
            pthread_t probe;
            if (pthread_create(&probe, nullptr, [](void *) -> void * { return nullptr; }, nullptr) == 0) {
                pthread_join(probe, nullptr);
                std::_Exit(77); // privileged user: the limit is not enforced
            }
            expect(mm.compute(4) == GGML_STATUS_ALLOC_FAILED, "expected GGML_STATUS_ALLOC_FAILED under RLIMIT_NPROC");
            expect(setrlimit(RLIMIT_NPROC, &old) == 0, "restore limit");
            expect(mm.compute(4) == GGML_STATUS_SUCCESS && mm.bytes() == expected, "computation after the limit was lifted differs");
        });

#ifndef DTL_SANITIZED
        // Fail the first, second or third worker: the ones already started are joined.
        const size_t before = live_threads();
        for (int at = 0; at < 3; ++at) {
            creates = 0; fail_create_at = at;
            const auto status = mm.compute(4);
            fail_create_at = -1;
            expect(status == GGML_STATUS_ALLOC_FAILED, "thread " + std::to_string(at + 1) + ": expected GGML_STATUS_ALLOC_FAILED");
            // A joined thread can stay listed for a moment while the kernel releases it.
            for (int wait = 0; wait < 200 && live_threads() != before; ++wait) usleep(10000);
            expect(live_threads() == before, "thread " + std::to_string(at + 1) + ": started workers were not joined");
            expect(mm.compute(4) == GGML_STATUS_SUCCESS && mm.bytes() == expected, "computation after a failed thread differs");
        }
        std::cerr << "failed worker creation unwinds: passed\n";

        // The CPU backend keeps its work buffer between computations. A larger graph
        // must grow it; when that allocation fails, the backend must not keep the freed
        // buffer: smaller and larger graphs then compute the same bytes as a fresh backend.
        in_child("work buffer growth failure", [] {
            matmul small(256, 64, 2), large(256, 64, 512);
            ggml_backend_t reference = ggml_backend_cpu_init();
            ggml_backend_cpu_set_n_threads(reference, 1);
            expect(ggml_backend_graph_compute(reference, small.graph) == GGML_STATUS_SUCCESS, "reference small");
            const auto small_bytes = small.bytes();
            expect(ggml_backend_graph_compute(reference, large.graph) == GGML_STATUS_SUCCESS, "reference large");
            const auto large_bytes = large.bytes();
            ggml_backend_free(reference);

            ggml_backend_t backend = ggml_backend_cpu_init();
            ggml_backend_cpu_set_n_threads(backend, 1);
            armed = 1;
            expect(ggml_backend_graph_compute(backend, small.graph) == GGML_STATUS_SUCCESS, "small");
            armed = 2;
            ggml_status status;
            try { status = ggml_backend_graph_compute(backend, large.graph); }
            catch (const std::bad_alloc &) { armed = 0; throw std::runtime_error("work buffer allocation failure escaped as an exception"); }
            armed = 0;
            expect(array_failures > 0, "the larger graph did not grow the work buffer");
            expect(status == GGML_STATUS_ALLOC_FAILED, "expected GGML_STATUS_ALLOC_FAILED for the work buffer");
            std::memset(small.out->data, 0, ggml_nbytes(small.out));
            expect(ggml_backend_graph_compute(backend, small.graph) == GGML_STATUS_SUCCESS && small.bytes() == small_bytes, "small after failure differs");
            std::memset(large.out->data, 0, ggml_nbytes(large.out));
            expect(ggml_backend_graph_compute(backend, large.graph) == GGML_STATUS_SUCCESS && large.bytes() == large_bytes, "large after failure differs");
            ggml_backend_free(backend);
        });
#else
        std::cerr << "thread and allocation injection skipped under a sanitizer runtime\n";
#endif
        std::cout << "ggml fault tests passed\n";
        return 0;
    } catch (const std::exception &e) { std::cerr << e.what() << '\n'; return 1; }
}
