#include "ducktape_laya.hpp"
#include <iostream>
#include <iterator>
int main(int argc, char **argv) {
    if (argc != 2) { std::cerr << "Usage: laya-decide-cpp model.gguf < request.json\n"; return 2; }
    try {
        std::string request(std::istreambuf_iterator<char>(std::cin), {});
        ducktape::laya engine(argv[1]);
        std::cout << engine.decide(request) << '\n';
        return 0;
    } catch (const std::exception &e) { std::cerr << e.what() << '\n'; return 1; }
}
