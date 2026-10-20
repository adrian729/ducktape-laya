// Deterministic mutation smoke fuzzing of public JSON/config/schema boundaries.
// Run against the sanitizer build. This is not a coverage-guided campaign.
#include "ducktape_laya.h"
#include <fstream>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

int main(int argc, char **argv) {
    if (argc < 2) return 2;
    std::vector<std::string> seeds = {"{}", "[]", "null", "{\"models\":{\"fuzz\":\"/proc/self/exe\"}}"};
    for (int i = 1; i < argc; ++i) {
        std::ifstream stream(argv[i]);
        if (!stream) return 2;
        seeds.emplace_back(std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>());
    }
    seeds.push_back(std::string(66, '[') + "0" + std::string(66, ']'));
    seeds.push_back(std::string(1024 * 1024 + 1, ' '));
    dtl_engine *router = nullptr;
    char *error = nullptr;
    auto status = dtl_open_router("{\"models\":{\"fuzz\":\"/proc/self/exe\"},\"auto\":{\"english\":\"fuzz\",\"default\":\"fuzz\"}}", nullptr, &router, &error);
    dtl_free(error);
    if (status || !router) return 1;
    constexpr unsigned rounds = 20000;
    std::mt19937 random(0x1a7a2026);
    unsigned executions = 0, configurations = 0;
    const std::vector<std::string> inserts = {"\"schema\"", "\"properties\"", "\"integer\"", "\"minimum\"",
        "\"maximum\"", "\"questions\"", "\"model\":\"fuzz\",", "1e999", "-2147483649", "4294967296",
        "true", "false", "null", "{}", "[]", "\"\\u0000\"", std::string(1, '\0')};
    try {
        for (unsigned i = 0; i < rounds; ++i) {
            // Large/deep boundary cases are deliberate; normal mutations stay small.
            std::string input = seeds[i < seeds.size() ? i : random() % (seeds.size() - 2)];
            if (i >= seeds.size()) {
                for (unsigned n = 1 + random() % 4; n; --n) {
                    const auto at = random() % (input.size() + 1);
                    switch (random() % 4) {
                    case 0: input.insert(at, inserts[random() % inserts.size()]); break;
                    case 1: if (at < input.size()) input.erase(at, 1 + random() % (input.size() - at)); break;
                    case 2: if (at < input.size()) input[at] = static_cast<char>(random() % 256); break;
                    case 3: input.insert(at, 1 + random() % 16, "{}[],:\" "[random() % 8]); break;
                    }
                }
            }
            char *result = nullptr;
            error = nullptr;
            status = dtl_execute(router, input.c_str(), nullptr, &result, &error);
            // No fixture is a model. Parsing may succeed, inference must not.
            if (status == DTL_OK || result || status < DTL_INVALID_ARGUMENT || status > DTL_UNSUPPORTED_PLATFORM)
                throw std::runtime_error("Unexpected execution status/result");
            dtl_free(result); dtl_free(error); ++executions;
            dtl_engine *candidate = nullptr;
            error = nullptr;
            status = dtl_open_router(input.c_str(), nullptr, &candidate, &error);
            if ((status == DTL_OK) != (candidate != nullptr))
                throw std::runtime_error("Router output/status disagreement");
            dtl_close(candidate); dtl_free(error); ++configurations;
        }
        dtl_close(router);
    } catch (const std::exception &exception) {
        std::cerr << exception.what() << '\n';
        dtl_close(router);
        return 1;
    }
    std::cout << "{\"seed\":\"0x1a7a2026\",\"request_cases\":" << executions
              << ",\"config_cases\":" << configurations << ",\"status\":\"passed\"}\n";
}
