#include "ducktape_laya.hpp"
#include <algorithm>
#include <filesystem>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <thread>
#include <vector>
#ifdef __linux__
#include <cerrno>
#include <cstring>
#include <sched.h>
#endif
#include "thread-cpu-time.hpp"
#define CHECK(v) do { if (!(v)) { std::cerr << "Failed: " #v << " at " << __LINE__ << '\n'; return 1; } } while (0)
static const std::string request = R"({"state":"Please refund me.","questions":{"refund":{"type":"noul","instructions":"Is a refund requested?"}}})";
int main(int argc, char **argv) {
    // Forward slashes: the path goes into JSON, and Windows accepts them.
    const std::string self_path = std::filesystem::canonical(argv[0]).generic_string();
    // A regular file that is not a model (lazy routes accept it until they load).
    auto self = [&](std::string text) {
        for (size_t at; (at = text.find("SELF")) != std::string::npos;) text.replace(at, 4, self_path);
        return text;
    };
    auto native = dtl_default_call_options();
    CHECK(native.struct_size == sizeof(native) && !native.request_id && !native.timeout_ms && !native.on_event && !native.should_cancel);
    char *result = reinterpret_cast<char *>(1), *error = reinterpret_cast<char *>(1);
    native.reserved = 1;
    CHECK(dtl_execute(nullptr, "{}", &native, &result, &error) == DTL_INVALID_ARGUMENT);
    CHECK(!result); dtl_free(error);
    uint32_t flag = 0;
    CHECK(dtl_cancel_flag(nullptr) == 0 && dtl_cancel_flag(&flag) == 0);
    flag = DTL_DEADLINE_EXCEEDED;
    CHECK(dtl_cancel_flag(&flag) == DTL_DEADLINE_EXCEEDED);
    {
        // Request and config validation needs no model: a router checks every request
        // before loading, and dtl_open checks its config before reading the file.
        auto status = [](auto &&f) -> dtl_status { try { f(); return DTL_OK; } catch (const ducktape::error &e) { return e.status; } };
        auto router = ducktape::engine::open_router(self(R"({"models":{"a":"SELF"},"auto":{"english":"a","default":"a"}})"));
        for (const char *field : {R"("overflow":"clip")", R"("max_windows":0)", R"("max_windows":1025)", R"("max_tokens":0)",
                                  R"("temperatures":{"noul.2":0})", R"("temperatures":{"noul.2":1e-50})", R"("temperatures":{"noul.4":1})",
                                  R"("lang":"en")", R"("unknown":1)"})
            CHECK(status([&] {
                router.execute(std::string(R"({"model":"a","state":"x","questions":{"q":{"type":"noul","instructions":"Is it urgent?"}})") + "," + field + "}"); }) == DTL_INVALID_ARGUMENT);
        CHECK(status([&] { router.execute(R"({"model":"auto","lang":"","state":"x","questions":{"q":{"type":"noul","instructions":"Is it urgent?"}}})"); }) == DTL_INVALID_ARGUMENT);
        CHECK(status([&] { router.execute(R"({"model":"a","state":"x","questions":{"q":{"type":"score","instructions":"How urgent?","aggregate":"all","criteria":["a","b"]}}})"); }) == DTL_INVALID_ARGUMENT);
        // A valid request reaches the (non-GGUF) model and fails there, not in validation.
        CHECK(status([&] { router.execute(R"({"model":"a","state":"x","questions":{"q":{"type":"noul","instructions":"Is it urgent?"}}})"); }) == DTL_MODEL_ERROR);
        CHECK(status([&] { router.execute(R"({"model":"auto","lang":"fr","overflow":"scan","state":"x","questions":{"q":{"type":"noul","instructions":"Is it urgent?"}}})"); }) == DTL_MODEL_ERROR);
        auto manual = ducktape::engine::open_router(self(R"({"models":{"a":"SELF"}})"));
        CHECK(status([&] { manual.execute(R"({"model":"auto","state":"x","questions":{"q":{"type":"noul","instructions":"Is it urgent?"}}})"); }) == DTL_INVALID_ARGUMENT);
        for (const std::string &config : {self(R"({"models":{"auto":"SELF"}})"), self(R"({"models":{"a":"SELF"},"auto":{"english":"a"}})"),
                                   self(R"({"models":{"a":"SELF"},"auto":{"english":"a","default":"b"}})")})
            CHECK(status([&] { ducktape::engine::open_router(config); }) == DTL_INVALID_ARGUMENT);
        for (const char *config : {R"({"threads":257})", R"({"context_tokens":31})", R"({"overflow":"clip"})",
                                   R"({"temperatures":{"noul.2":1e39}})", R"({"bogus":1})"})
            CHECK(status([&] { ducktape::engine::open(self("SELF"), config); }) == DTL_INVALID_ARGUMENT);
        CHECK(status([&] { ducktape::engine::open(self("SELF"), R"({"overflow":"scan","context_tokens":256})"); }) == DTL_MODEL_ERROR);
        // Runtime info advertises the features and limits the core enforces.
        const auto info = ducktape::info();
        for (const char *needle : {"\"language_routing\"", "\"overflow_policies\":[\"reject\",\"truncate_end\",\"truncate_start\",\"scan\"]",
                                   "\"minimum_context_tokens\":32", "\"scan_windows\":1024", "\"default_context_tokens\":512"})
            CHECK(info.find(needle) != std::string::npos);
    }
    if (argc != 2) return 0;
    bool skipped = false; // a check the host does not allow (the test then exits 77)
    auto engine = ducktape::engine::open(argv[1], R"({"threads":2})");
    const auto baseline = engine.execute(request);
    {
        // Nonzero reserved or an out-of-range timeout is rejected on a live engine too; nothing runs.
        dtl_engine *raw = nullptr; char *e = nullptr, *r = nullptr;
        CHECK(dtl_open(argv[1], "{\"threads\":2}", &raw, &e) == DTL_OK);
        auto bad = dtl_default_call_options(); bad.reserved = 1;
        CHECK(dtl_execute(raw, request.c_str(), &bad, &r, &e) == DTL_INVALID_ARGUMENT && !r); dtl_free(e);
        bad = dtl_default_call_options(); bad.timeout_ms = 86400001;
        CHECK(dtl_execute(raw, request.c_str(), &bad, &r, &e) == DTL_INVALID_ARGUMENT && !r); dtl_free(e);
        dtl_close(raw);
    }
    // The ready-made flag callback cancels like a host callback.
    flag = DTL_CANCELLED;
    ducktape::call_options options;
    options.should_cancel = dtl_cancel_flag;
    options.cancel_user_data = &flag;
    try { engine.execute(request, options); CHECK(false); }
    catch (const ducktape::error &e) { CHECK(e.status == DTL_CANCELLED); }
    flag = 0;
    CHECK(engine.execute(request, options) == baseline);
    std::atomic<bool> cancel{true};
    options = {};
    options.should_cancel = [](void *data) -> uint32_t { return static_cast<std::atomic<bool> *>(data)->load() ? 5 : 0; };
    options.cancel_user_data = &cancel;
    try { engine.execute(request, options); CHECK(false); }
    catch (const ducktape::error &e) { CHECK(e.status == DTL_CANCELLED); }
    // A deadline that has passed by the first check (1 ms) stops the call wherever it is then.
    cancel = false;
    options.timeout_ms = 1;
    try { engine.execute(request, options); CHECK(false); }
    catch (const ducktape::error &e) { CHECK(e.status == DTL_DEADLINE_EXCEEDED); }
    CHECK(engine.execute(request) == baseline);
    options.timeout_ms = 0;
    // Stops inside the forward pass. The library polls should_cancel at its own checks, in
    // tokenization (tokenizer-stop-check.patch) and, through ggml's abort callback, after every
    // node of the forward pass's graph; once a stop is latched it polls no more. The polls of a
    // call are counted, not assumed: a state needing several windows under "scan" is tokenized
    // exactly like the one-window request (the state's tokens are cached; max_tokens does not
    // change the prompt), so the difference between the two counts is the polls of the extra
    // windows, and the last polls of the one-window call are its forward pass. A stop at a
    // chosen poll among those lands in the graph, which ggml then abandons (llama_encode
    // returns 2), and the same context must then give unchanged results.
    using clock = std::chrono::steady_clock;
    struct counter {
        std::atomic<unsigned> polls{0};
        unsigned mark = 0;                 // the poll whose time is recorded
        clock::time_point marked{};
        double marked_cpu = 0;             // the calling thread's CPU time then (polls run on it)
    };
    const auto counting = [](void *p) -> uint32_t {
        auto *c = static_cast<counter *>(p);
        if (++c->polls == c->mark) { c->marked = clock::now(); c->marked_cpu = dtl_test::thread_cpu_seconds(); }
        return 0;
    };
    const auto count_polls = [&](const std::string &body, std::string &out, counter &c) {
        ducktape::call_options call;
        call.should_cancel = counting;
        call.cancel_user_data = &c;
        out = engine.execute(body, call);
        return c.polls.load();
    };
    const auto windows_of = [](const std::string &result) {
        const auto at = result.find("\"windows\":");
        return at == std::string::npos ? 0ul : std::stoul(result.substr(at + 10));
    };
    std::string words;
    for (int i = 0; i < 4; ++i) words += "Order " + std::to_string(1000 + i) + " was charged twice and the customer asks for the money back. ";
    const auto graph_request = [&](unsigned max_tokens) {
        return R"({"state":")" + words + R"(","overflow":"scan","max_tokens":)" + std::to_string(max_tokens) +
               R"(,"questions":{"refund":{"type":"noul","instructions":"Is a refund requested?"}}})";
    };
    const std::string single = graph_request(512), scanned = graph_request(48);
    const std::string single_result = engine.execute(single); // also caches the state's tokens
    std::string out;
    counter c1, c2, cw;
    const unsigned polls = count_polls(single, out, c1);
    CHECK(out == single_result && windows_of(out) == 1);
    CHECK(count_polls(single, out, c2) == polls); // the count is deterministic
    const unsigned scanned_polls = count_polls(scanned, out, cw);
    const unsigned windows = static_cast<unsigned>(windows_of(out));
    CHECK(windows >= 2 && scanned_polls > polls && (scanned_polls - polls) % (windows - 1) == 0);
    const unsigned per_window = (scanned_polls - polls) / (windows - 1);
    std::cerr << "polls: " << polls << " for one window, " << per_window << " per forward pass\n";
    // The abort callback polls after each graph node: dozens per pass (a check before and after
    // the pass make 2). Without it, a stop could only land between passes.
    CHECK(per_window >= 32 && polls > per_window);
    const unsigned first_graph_poll = polls - per_window + 2, mid_graph_poll = polls - per_window / 2;
    {
        // Cross-thread cancellation that lands mid-pass. The callback holds the poll in the middle
        // of the graph (bounded) until another thread has set the cancel flag, so the request
        // cannot finish first; the stop must be latched there and polled no more.
        struct gate { std::atomic<bool> cancel{false}; std::atomic<unsigned> polls{0}; unsigned at = 0; std::atomic<bool> started{false}, finished{false}; };
        gate g;
        g.at = mid_graph_poll;
        const auto until = [](auto &&done) {
            const auto limit = clock::now() + std::chrono::seconds(60);
            while (!done() && clock::now() < limit) std::this_thread::sleep_for(std::chrono::milliseconds(1));
        };
        ducktape::call_options gated;
        gated.cancel_user_data = &g;
        gated.should_cancel = [](void *p) -> uint32_t {
            auto *x = static_cast<gate *>(p);
            if (++x->polls == x->at) {
                x->started = true;
                const auto limit = clock::now() + std::chrono::seconds(60);
                while (!x->cancel.load() && clock::now() < limit) std::this_thread::sleep_for(std::chrono::milliseconds(1));
            }
            return x->cancel.load() ? 5 : 0;
        };
        std::thread stopper([&] { until([&] { return g.started.load() || g.finished.load(); }); g.cancel = true; });
        dtl_status status = DTL_OK;
        try { engine.execute(single, gated); }
        catch (const ducktape::error &e) { status = e.status; }
        g.finished = true;
        stopper.join();
        CHECK(g.started.load()); // the pass was running when the cancel was issued
        CHECK(status == DTL_CANCELLED);
        CHECK(g.polls.load() == mid_graph_poll); // latched at that poll, inside the graph
        CHECK(engine.execute(single) == single_result); // the aborted graph's context is reusable
    }
    {
        // A one-shot callback (nonzero at one poll only) must stay latched after the backend
        // abort: the check after the pass would otherwise see 0 and let the call succeed. The
        // stop at the graph's first poll must also end the pass early: the calling thread's CPU
        // time from that poll to the call's return is compared with the same span of a full call
        // (the full span is the whole graph, the stopped one a node and the unwinding). ggml polls
        // from its first compute thread, which is the calling thread. CPU time, which load does
        // not stretch, is summed over pairs of calls until the full spans reach 0.25 s.
        struct once { std::atomic<unsigned> polls{0}; unsigned at = 0; double marked = 0; std::thread::id thread{}; };
        double stopped_tail = 0, full_tail = 0;
        int samples = 0;
        for (; samples < 200 && (samples < 3 || full_tail < 0.25); ++samples) {
            once o;
            o.at = first_graph_poll;
            ducktape::call_options one_shot;
            one_shot.cancel_user_data = &o;
            one_shot.should_cancel = [](void *p) -> uint32_t {
                auto *x = static_cast<once *>(p);
                if (++x->polls != x->at) return 0;
                x->marked = dtl_test::thread_cpu_seconds();
                x->thread = std::this_thread::get_id();
                return 1;
            };
            dtl_status status = DTL_OK;
            try { engine.execute(single, one_shot); } catch (const ducktape::error &e) { status = e.status; }
            const double returned = dtl_test::thread_cpu_seconds();
            CHECK(status == DTL_CANCELLED && o.polls.load() == first_graph_poll);
            CHECK(o.thread == std::this_thread::get_id());
            stopped_tail += returned - o.marked;
            counter full;
            full.mark = first_graph_poll;
            CHECK(count_polls(single, out, full) == polls && out == single_result);
            full_tail += dtl_test::thread_cpu_seconds() - full.marked_cpu;
        }
        std::cerr << "from the graph's first poll, CPU time over " << samples << " calls each: " << full_tail
                  << " s to the end of full calls, " << stopped_tail << " s to the return of cancelled ones\n";
        CHECK(stopped_tail * 2 < full_tail);
    }
    {
        // A deadline that expires inside the pass. The callback, at the graph's middle poll, waits
        // until the deadline has passed and reports no cancellation; the library's own deadline
        // check in that same poll must then stop the call. The deadline is set well beyond the
        // time a call takes to reach that poll; a call that stopped earlier (polls below the mark,
        // a stalled host) is repeated with twice the deadline.
        struct waiter { std::atomic<unsigned> polls{0}; unsigned at = 0; clock::time_point until{}; };
        counter timing;
        timing.mark = mid_graph_poll;
        const auto started = clock::now();
        CHECK(count_polls(single, out, timing) == polls);
        auto deadline = std::max<long long>(250, 4 * std::chrono::duration_cast<std::chrono::milliseconds>(timing.marked - started).count());
        bool landed = false;
        for (int attempt = 0; attempt < 4 && !landed; ++attempt, deadline *= 2) {
            waiter w;
            w.at = mid_graph_poll;
            ducktape::call_options timed;
            timed.timeout_ms = static_cast<uint64_t>(deadline);
            timed.cancel_user_data = &w;
            timed.should_cancel = [](void *p) -> uint32_t {
                auto *x = static_cast<waiter *>(p);
                if (++x->polls == x->at) std::this_thread::sleep_until(x->until);
                return 0;
            };
            const auto call_started = clock::now();
            w.until = call_started + std::chrono::milliseconds(deadline + 20);
            dtl_status status = DTL_OK;
            try { engine.execute(single, timed); } catch (const ducktape::error &e) { status = e.status; }
            CHECK(status == DTL_DEADLINE_EXCEEDED);
            landed = w.polls.load() == mid_graph_poll;
            if (!landed) std::cerr << "a " << deadline << " ms deadline expired before the graph (poll " << w.polls.load() << "); repeating\n";
            CHECK(w.polls.load() <= mid_graph_poll);
            CHECK(engine.execute(single) == single_result);
        }
        CHECK(landed);
    }
    CHECK(engine.execute(request) == baseline);
#ifdef __linux__
    {
        // The automatic thread count (threads 0) is at most the CPUs in the affinity mask
        // (platform.cpp default_threads, read when the engine opens). ggml starts threads - 1
        // workers for each pass, so the process's thread count in the middle of the graph,
        // less its count at the call's first poll, is the engine's thread count - 1.
        struct census { std::atomic<unsigned> polls{0}; unsigned at = 0; long before = -1, during = -1; };
        const auto tasks = []() -> long {
            long n = 0;
            for (const auto &entry : std::filesystem::directory_iterator("/proc/self/task")) { (void)entry; ++n; }
            return n;
        };
        static long (*count_tasks)() = +tasks;
        // Returns -100 when the call does not match the layout measured above.
        const auto workers = [&](ducktape::engine &target) -> long {
            if (target.execute(single) != single_result) return -100; // the poll layout above holds once the state is cached
            census s;
            s.at = mid_graph_poll;
            ducktape::call_options call;
            call.cancel_user_data = &s;
            call.should_cancel = [](void *p) -> uint32_t {
                auto *x = static_cast<census *>(p);
                const auto n = ++x->polls;
                if (n == 1) x->before = count_tasks();
                if (n == x->at) x->during = count_tasks();
                return 0;
            };
            if (target.execute(single, call) != single_result || s.polls.load() != polls) return -100;
            return s.during - s.before;
        };
        CHECK(workers(engine) == 1); // "threads": 2; the probe sees ggml's workers
        cpu_set_t original, one;
        CHECK(sched_getaffinity(0, sizeof original, &original) == 0);
        int first = 0;
        while (first < CPU_SETSIZE && !CPU_ISSET(first, &original)) ++first;
        CHECK(first < CPU_SETSIZE);
        CPU_ZERO(&one);
        CPU_SET(first, &one);
        // A sandbox may forbid changing the affinity (seccomp, EPERM): the probe cannot run there,
        // and the test reports itself skipped (77) after its other checks, never passed.
        if (sched_setaffinity(0, sizeof one, &one) != 0) {
            std::cout << "SKIPPED: the automatic thread count probe: setting the CPU affinity failed (" << std::strerror(errno) << ")\n";
            skipped = true;
        } else {
            auto pinned = ducktape::engine::open(argv[1], R"({"threads":0})");
            CHECK(sched_setaffinity(0, sizeof original, &original) == 0);
            const long pinned_workers = workers(pinned);
            std::cerr << "automatic threads with one allowed CPU: " << pinned_workers + 1 << "\n";
            CHECK(pinned_workers == 0);
        }
    }
#else
    std::cerr << "SKIPPED: the automatic thread count probe reads the affinity mask and /proc (Linux only)\n";
#endif
    {
        // Tokenization honours deadlines and cancellation (tokenizer-stop-check.patch). A 1 MiB state
        // under "reject" is tokenized in full and then rejected with status 1 before any inference,
        // so that call times tokenization alone (the faster of two calls). Each call uses a different
        // state, so none reuses the tokens cached from the previous one. A deadline and a cancellation
        // at a tenth of that time must end the call with their own status within half of it (before
        // the patch, such calls ran tokenization to the end and returned status 1). The margin covers
        // the request parsing and the unchecked regex pre-split, which run before the first check
        // (together about an eighth of the full time). Times are the calling thread's CPU time, which
        // load does not stretch (tokenization runs on that thread), as in real-control-tokenize; the
        // deadline and the cancellation come by the wall clock, so load only makes them come earlier.
        // Long pseudo-random words: most of their tokenization is merging, which is checked; the
        // regex pre-split, which is not, takes a larger share of ordinary text (about a quarter).
        auto large = [](const char *first) {
            std::string text = first;
            uint32_t seed = 12345;
            while (text.size() < 1040000) {
                text += ' ';
                for (int i = 0; i < 160; ++i) { seed = seed * 1103515245u + 12345u; text += char('a' + (seed >> 16) % 26); }
            }
            const std::string body = R"({"state":")" + text + R"(","questions":{"refund":{"type":"noul","instructions":"Is a refund requested?"}}})";
            return body;
        };
        auto timed_status = [&](const std::string &body, const ducktape::call_options &call, double &elapsed) {
            const double started = dtl_test::thread_cpu_seconds();
            dtl_status status = DTL_OK;
            try { engine.execute(body, call); } catch (const ducktape::error &e) { status = e.status; }
            elapsed = dtl_test::thread_cpu_seconds() - started;
            return status;
        };
        const auto full_body = large("Full."), again_body = large("Again."), deadline_body = large("Deadline."), cancel_body = large("Cancel.");
        CHECK(full_body.size() <= 1048576 && again_body.size() <= 1048576 && deadline_body.size() <= 1048576 && cancel_body.size() <= 1048576);
        double full = 0, again = 0, stopped = 0;
        CHECK(timed_status(full_body, {}, full) == DTL_INVALID_ARGUMENT);
        CHECK(timed_status(again_body, {}, again) == DTL_INVALID_ARGUMENT);
        full = std::min(full, again);
        const auto tenth = std::max<long long>(1, static_cast<long long>(full * 100)); // milliseconds
        // Each stop is sampled three times and the fastest counts, as for the full time: a single
        // sample would compare one possibly preempted call with the better of two. A stopped call
        // caches no tokens, so the same state is tokenized afresh each time.
        ducktape::call_options deadline;
        deadline.timeout_ms = static_cast<uint32_t>(tenth);
        double fastest = 1e30;
        for (int sample = 0; sample < 3; ++sample) {
            CHECK(timed_status(deadline_body, deadline, stopped) == DTL_DEADLINE_EXCEEDED);
            fastest = std::min(fastest, stopped);
        }
        std::cerr << "1 MiB tokenization " << full << " s CPU; stopped by a " << tenth
                  << " ms deadline after " << fastest << " s CPU (fastest of 3)\n";
        CHECK(fastest * 2 < full);
        std::atomic<bool> stop{false};
        ducktape::call_options cancellable;
        cancellable.should_cancel = [](void *p) -> uint32_t { return static_cast<std::atomic<bool> *>(p)->load() ? 1 : 0; };
        cancellable.cancel_user_data = &stop;
        fastest = 1e30;
        for (int sample = 0; sample < 3; ++sample) {
            stop = false;
            std::thread canceller([&] { std::this_thread::sleep_for(std::chrono::milliseconds(tenth)); stop = true; });
            const auto cancel_status = timed_status(cancel_body, cancellable, stopped);
            canceller.join();
            CHECK(cancel_status == DTL_CANCELLED);
            fastest = std::min(fastest, stopped);
        }
        std::cerr << "cancelled after " << tenth << " ms, returned after " << fastest << " s CPU (fastest of 3)\n";
        CHECK(fastest * 2 < full);
        CHECK(engine.execute(request) == baseline);
    }
    if (skipped) {
        std::cout << "Cancellation, deadline, latch and post-abort recovery passed; a check was skipped\n";
        return 77;
    }
    std::cout << "Cancellation, deadline, latch and post-abort recovery passed\n";
}
