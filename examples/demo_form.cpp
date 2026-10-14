#include "demo_form.h"
#include <nlohmann/json.hpp>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <stdexcept>
using json = nlohmann::ordered_json;
static std::string trim(const std::string &s) {
    auto a = s.find_first_not_of(" \t\r\n"), b = s.find_last_not_of(" \t\r\n");
    return a == std::string::npos ? "" : s.substr(a, b - a + 1);
}
static std::string ask(const char *label, const char *fallback) {
    std::cout << label << " [" << fallback << "]: " << std::flush;
    std::string answer;
    if (!std::getline(std::cin, answer)) throw std::runtime_error("Input ended before the form was complete.");
    answer = trim(answer);
    return answer.empty() ? fallback : answer;
}
extern "C" char *demo_form(void) {
    try {
        std::cout << "Press Enter to use the example. Separate answer choices with |.\n";
        auto state = ask("Text to classify", "I was charged twice. Please refund me.");
        auto question = ask("Question", "Which department should handle this message?");
        auto choices = ask("Possible answers", "billing | technical | sales");
        json criteria = json::object();
        size_t start = 0;
        do {
            const auto end = choices.find('|', start);
            auto label = trim(choices.substr(start, end == std::string::npos ? end : end - start));
            if (label.empty() || criteria.contains(label)) throw std::runtime_error("Answer choices must be nonempty and unique.");
            criteria[label] = label;
            if (end == std::string::npos) break;
            start = end + 1;
        } while (true);
        if (criteria.size() < 2 || criteria.size() > 8) throw std::runtime_error("Enter 2 to 8 possible answers.");
        auto text = json({{"state", state}, {"questions", {{"decision", {
            {"type", "choice"}, {"instructions", question}, {"criteria", criteria}
        }}}}}).dump();
        auto *out = static_cast<char *>(std::malloc(text.size() + 1));
        if (!out) throw std::bad_alloc();
        std::memcpy(out, text.c_str(), text.size() + 1);
        return out;
    } catch (const std::exception &e) { std::cerr << "Error: " << e.what() << '\n'; return nullptr; }
}
extern "C" int demo_print(const char *result) {
    try {
        auto answer = json::parse(result).at("answers").at("decision");
        std::cout << "\nBest answer: " << answer.at("choice").get<std::string>() << "\nProbabilities:\n";
        for (auto &[label, probability] : answer.at("probabilities").items())
            std::cout << "  " << std::fixed << std::setprecision(2) << probability.get<double>() * 100
                      << "%  " << label << '\n';
        return 0;
    } catch (const std::exception &e) { std::cerr << "Error: " << e.what() << '\n'; return 1; }
}
