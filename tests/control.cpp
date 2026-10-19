#include "ducktape_laya.hpp"
#include <atomic>
#include <chrono>
#include <iostream>
#include <thread>
#include <vector>
#define CHECK(v) do { if (!(v)) { std::cerr << "Failed: " #v << " at " << __LINE__ << '\n'; return 1; } } while (0)
static const std::string request = R"({"state":"Please refund me.","questions":{"refund":{"type":"noul","instructions":"Is a refund requested?"}}})";
int main(int argc, char **argv) {
    auto options = dtl_default_call_options();
    CHECK(options.struct_size == sizeof(options) && !options.timeout_ms && !options.should_cancel);
    char *result = reinterpret_cast<char *>(1), *error = reinterpret_cast<char *>(1);
    options.reserved = 1;
    CHECK(dtl_execute_with_options(nullptr, "{}", nullptr, &options, &result, &error) == DTL_INVALID_ARGUMENT);
    CHECK(!result); dtl_free(error);
    uint32_t flag = 0;
    CHECK(dtl_cancel_flag(nullptr) == 0 && dtl_cancel_flag(&flag) == 0);
    flag = DTL_DEADLINE_EXCEEDED;
    CHECK(dtl_cancel_flag(&flag) == DTL_DEADLINE_EXCEEDED);
    if (argc != 2) return 0;
    ducktape::laya engine(argv[1]);
    const auto baseline = engine.execute(request);
    // The ready-made flag callback cancels like a host callback.
    flag = DTL_CANCELLED;
    options = dtl_default_call_options();
    options.should_cancel = dtl_cancel_flag;
    options.user_data = &flag;
    try { engine.execute(request, nullptr, &options); CHECK(false); }
    catch (const ducktape::error &e) { CHECK(e.status == DTL_CANCELLED); }
    flag = 0;
    CHECK(engine.execute(request, nullptr, &options) == baseline);
    std::atomic<bool> cancel{true};
    options = dtl_default_call_options();
    options.should_cancel = [](void *data) -> uint32_t { return static_cast<std::atomic<bool> *>(data)->load() ? 5 : 0; };
    options.user_data = &cancel;
    try { engine.execute(request, nullptr, &options); CHECK(false); }
    catch (const ducktape::error &e) { CHECK(e.status == DTL_CANCELLED); }
    cancel = false;
    options.timeout_ms = 1;
    try { engine.execute(request, nullptr, &options); CHECK(false); }
    catch (const ducktape::error &e) { CHECK(e.status == DTL_DEADLINE_EXCEEDED); }
    CHECK(engine.execute(request) == baseline); // aborted CPU graph must be reusable
    options.timeout_ms = 0;
    std::thread stopper([&] { std::this_thread::sleep_for(std::chrono::milliseconds(25)); cancel = true; });
    dtl_status status = DTL_OK;
    try { engine.execute(request, nullptr, &options); }
    catch (const ducktape::error &e) { status = e.status; }
    stopper.join();
    CHECK(status == DTL_CANCELLED);
    CHECK(engine.execute(request) == baseline);
    // A one-shot cancellation callback must remain latched after backend abort.
    std::atomic<unsigned> calls{0};
    options.user_data = &calls;
    options.should_cancel = [](void *p) -> uint32_t { return ++*static_cast<std::atomic<unsigned> *>(p) == 20 ? 1 : 0; };
    try { engine.execute(request, nullptr, &options); CHECK(false); }
    catch (const ducktape::error &e) { CHECK(e.status == DTL_CANCELLED); }
    CHECK(engine.execute(request) == baseline);
    std::cout << "Cancellation, deadline, latch and post-abort recovery passed\n";
}
