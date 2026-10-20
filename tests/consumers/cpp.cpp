// Public-API conformance consumer; no demo dependencies.
#include "ducktape_laya.hpp"
#include <iostream>
#include <iterator>
int main(int argc, char **argv) {
    if (argc != 2 && argc != 3) { std::cerr << "Usage: laya-conformance-cpp model.gguf [config.json] < request.json\n"; return 2; }
    try {
        std::string request(std::istreambuf_iterator<char>(std::cin), {});
        auto engine = ducktape::engine::open(argv[1], argc == 3 ? argv[2] : R"({"threads":4})");
        std::cout << engine.execute(request) << '\n';
        return 0;
    } catch (const std::exception &e) { std::cerr << e.what() << '\n'; return 1; }
}
