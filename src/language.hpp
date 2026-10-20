#pragma once
#include <nlohmann/json.hpp>
#include <string>
#include <string_view>

namespace dtl {
using json = nlohmann::ordered_json;
struct language_detection {
    bool english;
    std::string reason;   // english_text, no_letters, non_latin_script, too_short,
                          // other_language, non_english_letters or no_english_words
    std::string script;   // dominant non-Latin script, for non_latin_script
    std::string language; // "en", or another language the word lists name
};
// Whether a request's state is confidently English. Only string values count.
language_detection detect_language(const json &state);
// Whether a language tag (en, EN, en-US, eng, English) means English.
bool english_tag(std::string_view tag);
}
