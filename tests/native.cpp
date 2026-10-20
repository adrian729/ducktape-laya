#include "ducktape_laya.hpp"
#include <future>
#include <iostream>
#include <stdexcept>
#include <string>
static void expect(bool ok, const char *message) {
    if (!ok) throw std::runtime_error(message);
}
int main(int argc, char **argv) {
    try {
        expect(dtl_abi_version() == 2, "ABI version");
        const auto runtime = ducktape::info();
        expect(runtime.find("\"abi_version\":2") != std::string::npos, "diagnostics ABI");
        expect(runtime.find("\"cpu_supported\":true") != std::string::npos, "host CPU supported");
        expect(runtime.find("\"numerical_profile\":{\"id\":\"native-cpu") != std::string::npos, "native numerical profile");
        expect(runtime.find("\"cross_profile_equivalence\":false") != std::string::npos, "profile equivalence contract");
        dtl_engine *raw = nullptr;
        char *error = nullptr, *result = nullptr;
        expect(dtl_open(nullptr, nullptr, &raw, &error) == DTL_INVALID_ARGUMENT, "null path");
        expect(!raw && error, "error ownership"); dtl_free(error);
        expect(dtl_open("missing.gguf", "{\"bogus\":1}", &raw, &error) == DTL_INVALID_ARGUMENT && !raw, "unknown config key"); dtl_free(error);
        expect(dtl_open("missing.gguf", "{\"threads\":257}", &raw, &error) == DTL_INVALID_ARGUMENT && !raw, "threads bound"); dtl_free(error);
        expect(dtl_open("missing.gguf", "{", &raw, &error) == DTL_INVALID_ARGUMENT && !raw, "malformed config"); dtl_free(error);
        expect(dtl_open_router("{\"models\":{\"a\":\"/proc/self/exe\"}}", "{\"bogus\":1}", &raw, &error) == DTL_INVALID_ARGUMENT && !raw, "router unknown config key"); dtl_free(error);
        expect(dtl_open_router("{\"models\":{\"a\":{\"path\":\"/proc/self/exe\",\"config\":{\"bogus\":1}}}}", nullptr, &raw, &error) == DTL_INVALID_ARGUMENT && !raw, "route unknown config key"); dtl_free(error);
        {
            // A router reports runtime info only, without a model section.
            auto router = ducktape::engine::open_router(R"({"models":{"a":"/proc/self/exe"}})");
            const auto info = router.info();
            expect(info.find("\"abi_version\":2") != std::string::npos && info.find("\"model\"") == std::string::npos, "router info has no model");
        }
        expect(dtl_open("/nonexistent/ducktape-laya.gguf", nullptr, &raw, &error) == DTL_MODEL_ERROR, "missing model"); dtl_free(error);
        expect(dtl_execute(nullptr, "{}", nullptr, &result, &error) == DTL_INVALID_ARGUMENT, "null engine"); dtl_free(error);
        dtl_free(nullptr); dtl_close(nullptr);
        if (argc > 1) {
            auto engine = ducktape::engine::open(argv[1], R"({"threads":2})");
            const auto info = engine.info();
            expect(info.find("\"context_tokens\":512") != std::string::npos, "default context tokens");
            expect(info.find("\"trained_context_tokens\":") != std::string::npos, "trained context tokens");
            expect(info.find("\"max_tokens\"") == std::string::npos, "legacy max_tokens removed from model info");
            expect(info.find("\"decision_type\":\"laya\"") != std::string::npos, "model diagnostics");
            expect(info.find("\"temperatures\":") != std::string::npos, "calibration diagnostics");
            auto invalid = [&](const std::string &request) {
                bool failed = false;
                try { engine.execute(request); }
                catch (const ducktape::error &e) { failed = e.status == DTL_INVALID_ARGUMENT; }
                expect(failed, "bad request must return invalid argument");
            };
            invalid("{"); invalid("{}"); invalid("[]");
            invalid(R"({"state":"x","questions":{"q":{"type":"chat","instructions":"hi"}}})");
            invalid(R"({"state":"x","questions":{"q":{"type":"choice","instructions":"hi","criteria":{}}}})");
            invalid(R"({"state":"x","questions":{"q":{"type":"choice","instructions":"hi","criteria":["a","a"]}}})");
            invalid(R"({"state":"x","questions":{"q":{"type":"choice","instructions":"hi","criteria":["a",""]}}})");
            invalid(R"({"state":"x","questions":{"q":{"type":"score","instructions":"hi","criteria":["one"]}}})");
            invalid(std::string(1048577, 'x'));
            invalid("{\"state\":" + std::string(70, '[') + "0" + std::string(70, ']') +
                    R"(,"questions":{"q":{"type":"noul","instructions":"test"}}})");
            std::string long_text;
            for (int i=0; i<1000; ++i) long_text += "many words ";
            invalid("{\"state\":\"" + long_text + R"(","questions":{"q":{"type":"noul","instructions":"test"}}})");
            const std::string request = R"({"state":"Please refund the duplicate payment.","questions":{"refund":{"type":"noul","instructions":"Does the customer ask for a refund?"}}})";
            auto first = engine.execute(request);
            expect(first.find("\"noul\":") != std::string::npos, "real output");
            auto a = std::async(std::launch::async, [&] { return engine.execute(request); });
            auto b = std::async(std::launch::async, [&] { return engine.execute(request); });
            expect(a.get() == first && b.get() == first, "serialized concurrent inference parity");
        }
        std::cout << "passed\n";
        return 0;
    } catch (const std::exception &e) { std::cerr << e.what() << '\n'; return 1; }
}
