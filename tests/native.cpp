#include "ducktape_laya.hpp"
#include <filesystem>
#include <future>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <utility>
static void expect(bool ok, const char *message) {
    if (!ok) throw std::runtime_error(message);
}
// A hard link of a file under a non-ASCII name in the working directory; the path is
// UTF-8 with forward slashes, ready for JSON. Across file systems a small file is
// copied; a model (hundreds of MB, left behind if the test were killed) is not, and
// the path is then empty: the caller skips its checks and says so.
struct unicode_link {
    std::filesystem::path dir;
    std::string path;
    explicit unicode_link(const std::filesystem::path &source) {
        namespace fs = std::filesystem;
        dir = fs::current_path() / fs::u8path("ducktape-laya-\u00fcn\u00efc\u00f8d\u00e9-" + std::to_string(std::random_device{}()));
        fs::create_directories(dir);
        // Latin-1, CJK and a character outside the BMP (a surrogate pair in UTF-16).
        const auto target = dir / fs::u8path("m\u00f6del-\u6a21\u578b-\U0001F986" + source.extension().u8string());
        std::error_code ec;
        fs::create_hard_link(fs::canonical(source), target, ec);
        if (ec && fs::file_size(source) > (64u << 20)) {
            std::cout << "SKIPPED: " << source.filename().u8string() << " under a Unicode path (cannot hard-link it into the working directory: "
                      << ec.message() << ")\n";
            remove();
            return;
        }
        if (ec) fs::copy_file(fs::canonical(source), target);
        path = target.generic_u8string();
    }
    void remove() { std::error_code ec; std::filesystem::remove_all(dir, ec); }
    ~unicode_link() { remove(); }
};
int main(int argc, char **argv) {
    // Forward slashes: the path goes into JSON, and Windows accepts them.
    const std::string self_path = std::filesystem::canonical(argv[0]).generic_string();
    // A regular file that is not a model (lazy routes accept it until they load).
    auto self = [&](std::string text) {
        for (size_t at; (at = text.find("SELF")) != std::string::npos;) text.replace(at, 4, self_path);
        return text;
    };
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
        expect(dtl_open_router(self("{\"models\":{\"a\":\"SELF\"}}").c_str(), "{\"bogus\":1}", &raw, &error) == DTL_INVALID_ARGUMENT && !raw, "router unknown config key"); dtl_free(error);
        expect(dtl_open_router(self("{\"models\":{\"a\":{\"path\":\"SELF\",\"config\":{\"bogus\":1}}}}").c_str(), nullptr, &raw, &error) == DTL_INVALID_ARGUMENT && !raw, "route unknown config key"); dtl_free(error);
        {
            // A router reports runtime info only, without a model section.
            auto router = ducktape::engine::open_router(self(R"({"models":{"a":"SELF"}})").c_str());
            const auto info = router.info();
            expect(info.find("\"abi_version\":2") != std::string::npos && info.find("\"model\"") == std::string::npos, "router info has no model");
            // A moved-from engine must not silently answer with runtime info.
            auto moved = std::move(router);
            bool refused = false;
            try { router.info(); } catch (const ducktape::error &e) { refused = e.status == DTL_INVALID_ARGUMENT; }
            expect(refused, "info() on a moved-from engine throws");
            expect(moved.info().find("\"abi_version\":2") != std::string::npos, "the moved-to engine still works");
        }
        {
            // Paths are UTF-8 in the C ABI on every platform, including outside the
            // Windows ANSI code page and the Basic Multilingual Plane.
            unicode_link alias(argv[0]);
            auto router = ducktape::engine::open_router(R"({"models":{"a":")" + alias.path + R"("}})");
            dtl_engine *missing = nullptr;
            expect(dtl_open_router((R"({"models":{"a":")" + alias.path + R"(.missing"}})").c_str(), nullptr, &missing, &error) == DTL_MODEL_ERROR && !missing,
                   "missing non-ASCII route"); dtl_free(error);
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
            unicode_link model(argv[1]);
            if (!model.path.empty())
                expect(ducktape::engine::open(model.path, R"({"threads":2})").execute(request) == first, "non-ASCII model path");
        }
        std::cout << "passed\n";
        return 0;
    } catch (const std::exception &e) { std::cerr << e.what() << '\n'; return 1; }
}
