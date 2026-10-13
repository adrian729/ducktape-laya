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
        expect(dtl_abi_version() == 1, "ABI version");
        dtl_engine *raw = nullptr;
        char *error = nullptr, *result = nullptr;
        expect(dtl_open(nullptr, nullptr, &raw, &error) == DTL_INVALID_ARGUMENT, "null path");
        expect(!raw && error, "error ownership"); dtl_free(error);
        auto options = dtl_default_options(); options.abi_version = 999;
        expect(dtl_open("missing.gguf", &options, &raw, &error) == DTL_INVALID_ARGUMENT, "wrong ABI"); dtl_free(error);
        expect(dtl_open("/nonexistent/ducktape-laya.gguf", nullptr, &raw, &error) == DTL_MODEL_ERROR, "missing model"); dtl_free(error);
        expect(dtl_decide(nullptr, "{}", &result, &error) == DTL_INVALID_ARGUMENT, "null engine"); dtl_free(error);
        dtl_free(nullptr); dtl_close(nullptr);
        if (argc > 1) {
            options = dtl_default_options(); options.threads = 2;
            ducktape::laya engine(argv[1], options);
            auto invalid = [&](const std::string &request) {
                bool failed = false;
                try { engine.decide(request); }
                catch (const ducktape::error &e) { failed = e.status == DTL_INVALID_ARGUMENT; }
                expect(failed, "bad request must return invalid argument");
            };
            invalid("{"); invalid("{}"); invalid("[]");
            invalid(R"({"state":"x","questions":{"q":{"type":"chat","instructions":"hi"}}})");
            invalid(R"({"state":"x","questions":{"q":{"type":"choice","instructions":"hi","criteria":{"a":"x"}}}})");
            invalid(R"({"state":"x","questions":{"q":{"type":"score","instructions":"hi","criteria":["one"]}}})");
            invalid(std::string(1048577, 'x'));
            invalid("{\"state\":" + std::string(70, '[') + "0" + std::string(70, ']') +
                    R"(,"questions":{"q":{"type":"noul","instructions":"test"}}})");
            std::string long_text;
            for (int i=0; i<1000; ++i) long_text += "many words ";
            invalid("{\"state\":\"" + long_text + R"(","questions":{"q":{"type":"noul","instructions":"test"}}})");
            const std::string request = R"({"state":"Please refund the duplicate payment.","questions":{"refund":{"type":"noul","instructions":"Does the customer ask for a refund?"}}})";
            auto first = engine.decide(request);
            expect(first.find("\"noul\":") != std::string::npos, "real output");
            auto a = std::async(std::launch::async, [&] { return engine.decide(request); });
            auto b = std::async(std::launch::async, [&] { return engine.decide(request); });
            expect(a.get() == first && b.get() == first, "serialized concurrent inference parity");
        }
        std::cout << "passed\n";
        return 0;
    } catch (const std::exception &e) { std::cerr << e.what() << '\n'; return 1; }
}
