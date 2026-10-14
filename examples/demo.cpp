#include "ducktape_laya.hpp"
#include "demo_form.h"
#include <cstdlib>
#include <iostream>

int main(int argc, char **argv) {
    const char *model = argc > 1 ? argv[1] : std::getenv("LAYA_MODEL");
    if (!model) { std::cerr << "Use ./demo cpp or supply a model path.\n"; return 1; }
    std::unique_ptr<char, decltype(&std::free)> request(demo_form(), std::free);
    if (!request) return 1;
    try {
        std::cout << "Running Laya...\n";
        ducktape::laya engine(model);
        return demo_print(engine.decide(request.get()).c_str());
    } catch (const std::exception &e) { std::cerr << "Error: " << e.what() << '\n'; return 1; }
}
