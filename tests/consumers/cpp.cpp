// Public-API conformance consumer; no demo dependencies.
// Usage: laya-conformance-cpp model.gguf [config.json] [--requests FILE... [--fresh-engine]] < request.json
// With --requests: one engine for all files (one per file with --fresh-engine), one result line per file.
#include "ducktape_laya.hpp"
#include <fstream>
#include <iostream>
#include <iterator>
#include <memory>
#include <vector>
int main(int argc, char **argv) {
    std::vector<std::string> args(argv + 1, argv + argc), files;
    std::string config = R"({"threads":4})";
    bool requests = false, fresh = false, usage = args.empty();
    size_t i = 1;
    if (args.size() > 1 && args[1].rfind("--", 0) != 0) config = args[i++];
    for (; i < args.size(); ++i) {
        if (args[i] == "--fresh-engine") fresh = true;
        else if (args[i] == "--requests" && !requests) {
            requests = true;
            while (i + 1 < args.size() && args[i + 1].rfind("--", 0) != 0) files.push_back(args[++i]);
            usage = usage || files.empty();
        } else usage = true;
    }
    if (usage || (fresh && !requests)) {
        std::cerr << "Usage: laya-conformance-cpp model.gguf [config.json] [--requests FILE... [--fresh-engine]] < request.json\n";
        return 2;
    }
    try {
        if (!requests) {
            std::string request(std::istreambuf_iterator<char>(std::cin), {});
            auto engine = ducktape::engine::open(args[0], config);
            std::cout << engine.execute(request) << '\n';
            return 0;
        }
        // C++11 like the header: MSVC compiles C++14 by default, where std::optional does not exist.
        std::unique_ptr<ducktape::engine> engine;
        for (const auto &file : files) {
            std::ifstream in(file, std::ios::binary);
            if (!in) { std::cerr << file << ": unreadable\n"; return 2; }
            std::string request(std::istreambuf_iterator<char>(in), {});
            if (!engine) engine.reset(new ducktape::engine(ducktape::engine::open(args[0], config)));
            std::cout << engine->execute(request) << '\n' << std::flush;
            if (fresh) engine.reset();
        }
        return 0;
    } catch (const std::exception &e) { std::cerr << e.what() << '\n'; return 1; }
}
