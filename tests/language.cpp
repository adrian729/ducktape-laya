// Language detection for "model": "auto" against the original Laya's recorded
// detections and routing decisions (tests/original-reference/routing).
#include "language.hpp"
#include <fstream>
#include <iostream>

int main(int argc, char **argv) {
    if (argc != 2) { std::cerr << "Usage: " << argv[0] << " tests/original-reference/routing\n"; return 2; }
    const std::string dir = argv[1];
    auto load = [&](const char *name) { std::ifstream in(dir + "/" + name); return dtl::json::parse(in); };
    int failures = 0, cases = 0;
    // English means the original named the language "en"; everything else (another
    // language, undecided, no letters) goes to the default model.
    for (const auto &c : load("languages.json")) {
        ++cases;
        const auto got = dtl::detect_language(c["state"]);
        if (got.english != (c["language"] == "en")) {
            ++failures;
            std::cerr << c["label"].get<std::string>() << ": original " << c["language"] << ", ours " << got.reason << '\n';
        }
    }
    for (const auto &c : load("routes.json")) {
        ++cases;
        const bool english = c.contains("lang") ? dtl::english_tag(c["lang"].get<std::string>())
                                                : dtl::detect_language(c["state"]).english;
        if (english != (c["model"] == "english")) {
            ++failures;
            std::cerr << c["label"].get<std::string>() << ": original " << c["model"] << '\n';
        }
    }
    std::cout << cases - failures << "/" << cases << " cases agree with the original Laya\n";
    return failures ? 1 : 0;
}
