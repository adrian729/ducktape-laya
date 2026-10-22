#include "ducktape_laya.hpp"
#include <filesystem>
#include <atomic>
#include <chrono>
#include <iostream>
#include <thread>
#include <vector>
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
    cancel = false;
    options.timeout_ms = 1;
    try { engine.execute(request, options); CHECK(false); }
    catch (const ducktape::error &e) { CHECK(e.status == DTL_DEADLINE_EXCEEDED); }
    CHECK(engine.execute(request) == baseline); // aborted CPU graph must be reusable
    options.timeout_ms = 0;
    {
        // Cross-thread cancellation that must land mid-request. A timed sleep races the request on a
        // fast host. Instead the callback, polled from inside the evaluation, reports that work has
        // started and holds that poll (bounded) until another thread has set the cancel flag, so the
        // request cannot finish before the cancel is visible and must end DTL_CANCELLED.
        struct gate { std::atomic<bool> *cancel; std::atomic<unsigned> polls{0}; std::atomic<bool> started{false}, finished{false}; };
        gate g{&cancel};
        const auto until = [](auto &&done) {
            const auto limit = std::chrono::steady_clock::now() + std::chrono::seconds(60);
            while (!done() && std::chrono::steady_clock::now() < limit) std::this_thread::sleep_for(std::chrono::milliseconds(1));
        };
        options.cancel_user_data = &g;
        options.should_cancel = [](void *p) -> uint32_t {
            auto *x = static_cast<gate *>(p);
            if (++x->polls == 5) {
                x->started = true;
                const auto limit = std::chrono::steady_clock::now() + std::chrono::seconds(60);
                while (!x->cancel->load() && std::chrono::steady_clock::now() < limit) std::this_thread::sleep_for(std::chrono::milliseconds(1));
            }
            return x->cancel->load() ? 5 : 0;
        };
        std::thread stopper([&] { until([&] { return g.started.load() || g.finished.load(); }); cancel = true; });
        dtl_status status = DTL_OK;
        try { engine.execute(request, options); }
        catch (const ducktape::error &e) { status = e.status; }
        g.finished = true;
        stopper.join();
        CHECK(g.started.load()); // work was in flight when the cancel was issued
        CHECK(status == DTL_CANCELLED);
    }
    CHECK(engine.execute(request) == baseline);
    // A one-shot cancellation callback must remain latched after backend abort.
    std::atomic<unsigned> calls{0};
    options.cancel_user_data = &calls;
    options.should_cancel = [](void *p) -> uint32_t { return ++*static_cast<std::atomic<unsigned> *>(p) == 20 ? 1 : 0; };
    try { engine.execute(request, options); CHECK(false); }
    catch (const ducktape::error &e) { CHECK(e.status == DTL_CANCELLED); }
    CHECK(engine.execute(request) == baseline);
    std::cout << "Cancellation, deadline, latch and post-abort recovery passed\n";
}
