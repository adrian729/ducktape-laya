// Language detection for "model": "auto" routing.
//
// Deliberately small: text is sent to the English model only when it is
// confidently English, and to the router's default (multilingual) model
// otherwise, which also reads English. The word lists and thresholds come from
// the original Laya's laya/lang.py (Apache-2.0, copyright the Laya authors,
// https://github.com/NandhaKishorM/laya at ffc1420); its finer heuristics for
// naming Latin-script languages are not ported. Agreement with its routing
// cases is checked by tests/language.cpp (ctest language-routing).
#include "language.hpp"
#include <algorithm>
#include <array>
#include <iterator>
#include <map>
#include <set>
#include <string_view>
#include <utility>
#include <vector>

namespace dtl {
namespace {
using words = std::set<std::string, std::less<>>;
// Function words per language, as constant data (a brace-initialized table of sets compiled
// to one huge initializer function); the sets are built from it once, at first use.
constexpr std::string_view words_en[] = {
    "and", "are", "as", "at", "be", "but", "can", "for", "from", "has", "have", "i", "in", "is",
    "it", "not", "of", "on", "please", "that", "the", "their", "there", "this", "to", "was",
    "we", "were", "what", "which", "will", "with", "would", "you",
};
constexpr std::string_view words_fr[] = {
    "alors", "au", "aux", "avec", "bien", "bonjour", "ce", "ces", "cette", "comment", "dans",
    "des", "deux", "dois", "doit", "donc", "du", "elle", "elles", "est", "et", "fait", "fois",
    "il", "ils", "je", "jour", "jours", "la", "le", "les", "ma", "mais", "merci", "mes", "mois",
    "mon", "nous", "ont", "ou", "pas", "peut", "peux", "plus", "pour", "pourquoi", "quand",
    "que", "qui", "sa", "ses", "sont", "sur", "ta", "tes", "ton", "tous", "tout", "toute",
    "trois", "très", "tu", "une", "veut", "veux", "vous", "être",
};
constexpr std::string_view words_de[] = {
    "aber", "auch", "auf", "aus", "bei", "bitte", "das", "dem", "den", "der", "dich", "die",
    "diese", "diesen", "dieser", "dieses", "dir", "ein", "eine", "einem", "einen", "einer",
    "für", "gibt", "habe", "haben", "heute", "ich", "im", "in", "ist", "jetzt", "kann",
    "kannst", "mein", "meine", "meinem", "meinen", "meiner", "mich", "mir", "mit", "nach",
    "nicht", "noch", "oder", "sich", "sind", "und", "uns", "von", "wann", "was", "welche",
    "werden", "wie", "wir", "wird", "wo", "wurde", "zu", "zum", "zur",
};
constexpr std::string_view words_es[] = {
    "al", "algo", "aquí", "aunque", "como", "con", "cuando", "del", "donde", "dos", "el",
    "entre", "es", "esa", "ese", "eso", "esta", "este", "esto", "está", "fue", "fueron",
    "gracias", "han", "hay", "hemos", "hoy", "la", "las", "le", "les", "lo", "los", "mi", "muy",
    "más", "nada", "necesito", "ni", "nos", "para", "pero", "por", "porque", "puede", "pueden",
    "que", "quiero", "se", "ser", "sobre", "son", "su", "sus", "también", "tengo", "tiene",
    "tienen", "todo", "tres", "tu", "un", "una", "y", "ya",
};
constexpr std::string_view words_pt[] = {
    "agora", "ainda", "alguem", "alguém", "ali", "antes", "ao", "aos", "aqui", "as", "até",
    "boa", "cadê", "com", "como", "consigo", "da", "das", "depois", "deu", "do", "dois", "dos",
    "e", "em", "entao", "então", "era", "esta", "estamos", "estava", "este", "estou", "está",
    "eu", "ficou", "fiz", "foi", "gostaria", "hoje", "isso", "isto", "ja", "já", "mais", "mas",
    "meu", "meus", "minha", "minhas", "muito", "na", "nada", "nao", "nas", "nenhum", "nenhuma",
    "ninguem", "ninguém", "noite", "nos", "nossa", "nosso", "não", "o", "obrigada", "obrigado",
    "olá", "onde", "ontem", "os", "para", "pela", "pelo", "pode", "podem", "por", "porque",
    "pra", "preciso", "quando", "que", "quero", "sao", "se", "ser", "seu", "sou", "sua", "são",
    "tambem", "também", "tarde", "tem", "tenho", "três", "tudo", "tá", "um", "uma", "vc", "vcs",
    "voce", "voces", "você", "vocês", "é",
};
constexpr std::string_view words_it[] = {
    "abbiamo", "adesso", "agli", "alla", "alle", "anche", "ancora", "avete", "che", "ci",
    "ciao", "col", "come", "con", "da", "dagli", "dal", "dalla", "dallo", "degli", "dei", "del",
    "della", "delle", "dello", "deve", "devo", "devono", "di", "dove", "e", "ed", "era", "fra",
    "già", "gli", "grazie", "ha", "hai", "hanno", "ho", "ieri", "il", "la", "le", "lo", "mai",
    "mi", "mia", "mio", "molto", "ne", "negli", "nel", "nell", "nella", "non", "o", "oggi",
    "per", "perche", "più", "poco", "quando", "questa", "questo", "scusa", "sempre", "si",
    "sono", "stata", "stato", "su", "sua", "sul", "sulla", "sulle", "tra", "tuo", "un", "una",
    "uno", "voglio", "vorrei", "è",
};
constexpr std::string_view words_nl[] = {
    "aan", "dat", "deze", "door", "een", "het", "is", "maar", "met", "naar", "niet", "ook",
    "op", "te", "van", "voor", "worden", "wordt", "zijn",
};
constexpr std::string_view words_sv[] = {
    "appen", "aterbetala", "aterbetalning", "att", "behover", "behöver", "blivit", "debiterade",
    "dessa", "detta", "faktura", "fel", "från", "får", "ganger", "gång", "gånger", "gör",
    "göra", "hittar", "hjalp", "hjalpa", "hjälp", "hjälpa", "här", "installningarna",
    "installningen", "inställningarna", "inställningen", "inte", "jag", "kommer", "kraschar",
    "kvittot", "losenord", "lösenord", "mig", "min", "mitt", "måste", "nar", "när", "nästa",
    "och", "också", "om", "oppnar", "paket", "ska", "skicka", "skulle", "sparningen",
    "spårningen", "säger", "till", "tva", "två", "upp", "uppdaterats", "utan", "vi", "vill",
    "är", "återbetala", "återbetalning", "öppnar",
};
constexpr std::string_view words_ro[] = {
    "aceasta", "această", "acest", "acesta", "acum", "ale", "care", "dar", "din", "după",
    "este", "foarte", "fost", "fără", "lui", "mi", "nu", "pentru", "până", "sunt", "să",
    "trebuie", "vreau", "vă", "în", "și", "ți",
};
constexpr std::string_view words_bn[] = {
    "abar", "ajke", "akhon", "amader", "amake", "amar", "ami", "amra", "apnake", "apnar",
    "apnara", "apni", "ar", "asbe", "bhai", "bhalo", "bolte", "bolun", "chai", "chaina",
    "dhonnobad", "dilam", "dite", "diye", "diyechi", "dorkar", "duibar", "ei", "eita", "ekbar",
    "ekhon", "ekhono", "ekta", "ferot", "geche", "gese", "hobe", "hocche", "hoise", "hoye",
    "hoyeche", "hoyni", "jabe", "jodi", "jonno", "kalke", "keno", "keu", "kharap", "khub", "ki",
    "kibhabe", "kichu", "kintu", "kivabe", "kobe", "kokhon", "korbo", "korchi", "kore",
    "koreche", "korechi", "koren", "korlam", "korsi", "korte", "korun", "kothay", "koto",
    "lagbe", "moddhe", "nai", "niye", "oi", "oita", "onek", "ota", "pabo", "paini", "parben",
    "parbo", "parchi", "parchina", "peyechi", "sathe", "shathe", "shob", "shomossa", "somossa",
    "tader", "tahole", "taka", "theke", "tomake", "tomar", "tomra", "tumi", "valo",
};
constexpr std::string_view words_az[] = {
    "amma", "ancaq", "artiq", "artıq", "bir", "biz", "bu", "cox", "daha", "deyil", "də", "eger",
    "görə", "həm", "hər", "ile", "ilə", "isə", "kimi", "lakin", "mən", "nə", "olan", "olmasa",
    "olub", "onlar", "siz", "sonra", "sən", "ucun", "var", "ve", "və", "yalniz", "yalnız",
    "yox", "yoxdur", "çox", "üçün", "əgər",
};
struct word_list { std::string_view language; const std::string_view *begin, *end; };
#define DTL_WORDS(lang) word_list{#lang, std::begin(words_##lang), std::end(words_##lang)}
// In the original's order (it reports the first best).
constexpr word_list word_lists[] = {
    DTL_WORDS(en), DTL_WORDS(fr), DTL_WORDS(de), DTL_WORDS(es), DTL_WORDS(pt), DTL_WORDS(it), DTL_WORDS(nl), DTL_WORDS(sv),
    DTL_WORDS(ro), DTL_WORDS(bn), DTL_WORDS(az),
};
#undef DTL_WORDS
const std::vector<std::pair<std::string, words>> &stopwords() {
    static const std::vector<std::pair<std::string, words>> table = [] {
        std::vector<std::pair<std::string, words>> out;
        out.reserve(std::size(word_lists));
        for (const auto &list : word_lists) {
            words set;
            for (auto word = list.begin; word != list.end; ++word) set.emplace(*word);
            out.emplace_back(std::string(list.language), std::move(set));
        }
        return out;
    }();
    return table;
}
// Words that more than one list claims; they name no language by themselves.
const words &shared_words() {
    static const words shared = [] {
        std::map<std::string, int, std::less<>> seen;
        for (const auto &[lang, list] : stopwords()) for (const auto &w : list) ++seen[w];
        words out;
        for (const auto &[w, n] : seen) if (n > 1) out.insert(w);
        return out;
    }();
    return shared;
}
// Letters that ordinary English does not use, lowercase.
constexpr std::u32string_view non_english_letters =
    U"ßàáâãäåæçèéêëìíîïñòóôõöøùúûüýÿāăąćčďđēęěğģīıķļłńņňőœřśşšţťūůűźżžșțə";

struct script_range { const char *name; char32_t lo, hi; };
constexpr std::array<script_range, 39> scripts = {{
    {"greek", 0x0370, 0x03FF}, {"greek", 0x1F00, 0x1FFF},
    {"cyrillic", 0x0400, 0x052F}, {"cyrillic", 0x2DE0, 0x2DFF}, {"cyrillic", 0xA640, 0xA69F},
    {"armenian", 0x0530, 0x058F}, {"hebrew", 0x0590, 0x05FF},
    {"arabic", 0x0600, 0x06FF}, {"arabic", 0x0750, 0x077F}, {"arabic", 0x08A0, 0x08FF},
    {"arabic", 0xFB50, 0xFDFF}, {"arabic", 0xFE70, 0xFEFF},
    {"devanagari", 0x0900, 0x097F}, {"devanagari", 0xA8E0, 0xA8FF},
    {"bengali", 0x0980, 0x09FF}, {"gurmukhi", 0x0A00, 0x0A7F}, {"gujarati", 0x0A80, 0x0AFF},
    {"oriya", 0x0B00, 0x0B7F}, {"tamil", 0x0B80, 0x0BFF}, {"telugu", 0x0C00, 0x0C7F},
    {"kannada", 0x0C80, 0x0CFF}, {"malayalam", 0x0D00, 0x0D7F}, {"sinhala", 0x0D80, 0x0DFF},
    {"thai", 0x0E00, 0x0E7F}, {"lao", 0x0E80, 0x0EFF}, {"tibetan", 0x0F00, 0x0FFF},
    {"myanmar", 0x1000, 0x109F}, {"georgian", 0x10A0, 0x10FF}, {"ethiopic", 0x1200, 0x137F},
    {"khmer", 0x1780, 0x17FF},
    {"hangul", 0x1100, 0x11FF}, {"hangul", 0x3130, 0x318F}, {"hangul", 0xAC00, 0xD7AF},
    {"kana", 0x3040, 0x309F}, {"kana", 0x30A0, 0x30FF}, {"kana", 0x31F0, 0x31FF},
    {"han", 0x3400, 0x4DBF}, {"han", 0x4E00, 0x9FFF}, {"han", 0xF900, 0xFAFF},
}};

bool latin_letter(char32_t c) {
    return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || c == 0xAA || c == 0xB5 || c == 0xBA ||
           (c >= 0xC0 && c < 0x2B0 && c != 0xD7 && c != 0xF7) || (c >= 0x1E00 && c <= 0x1EFF) ||
           (c >= 0xFF21 && c <= 0xFF3A) || (c >= 0xFF41 && c <= 0xFF5A);
}
// Digits and punctuation inside the script blocks below are not letters.
bool script_non_letter(char32_t c) {
    for (char32_t digits : {0x0660u, 0x06F0u, 0x0966u, 0x09E6u, 0x0A66u, 0x0AE6u, 0x0B66u, 0x0BE6u, 0x0C66u,
                            0x0CE6u, 0x0D66u, 0x0DE6u, 0x0E50u, 0x0ED0u, 0x0F20u, 0x1040u, 0x17E0u})
        if (c >= digits && c < digits + 10) return true;
    switch (c) {
    case 0x037E: case 0x0387: case 0x055A: case 0x055B: case 0x055C: case 0x055D: case 0x055E: case 0x055F:
    case 0x0589: case 0x058A: case 0x05BE: case 0x05C0: case 0x05C3: case 0x05C6: case 0x05F3: case 0x05F4:
    case 0x060C: case 0x061B: case 0x061F: case 0x066A: case 0x066B: case 0x066C: case 0x066D: case 0x06D4:
    case 0x0964: case 0x0965: case 0x0E5A: case 0x0E5B: case 0x10FB: case 0x1360: case 0x1361: case 0x1362:
    case 0x30FB:
        return true;
    default:
        return false;
    }
}
// The named non-Latin script of a letter; "other" for letters of unlisted scripts;
// nullptr for Latin letters and for anything that is not a letter.
const char *non_latin_script(char32_t c) {
    if (c < 0x0370 || latin_letter(c) || script_non_letter(c)) return nullptr;
    for (const auto &s : scripts) if (c >= s.lo && c <= s.hi) return s.name;
    // Unlisted scripts count as non-Latin, the safe direction. Punctuation, symbols,
    // emoji and other non-letter blocks are skipped.
    if ((c >= 0x1380 && c < 0x2000) || (c >= 0x2C00 && c < 0x2E00) || (c >= 0x3040 && c < 0xA000) ||
        (c >= 0xA000 && c < 0xD800) || (c >= 0xF900 && c < 0xFB00) || (c >= 0x10000 && c < 0x1D000) ||
        (c >= 0x1E000 && c < 0x1F000) || (c >= 0x20000 && c < 0x40000))
        return "other";
    return nullptr;
}
bool word_char(char32_t c) {
    return latin_letter(c) || non_latin_script(c) || (c >= '0' && c <= '9') || c == '_';
}
char32_t lower(char32_t c) {
    if (c == 0x130) return 'i'; // İ: Python lowercases it to i plus a combining dot
    if (c == 0x178) return 0xFF;
    if (c == 0x18F) return 0x259; // Ə
    if ((c >= 'A' && c <= 'Z') || (c >= 0xC0 && c <= 0xDE && c != 0xD7)) return c + 0x20;
    if ((c >= 0x100 && c <= 0x137) || (c >= 0x14A && c <= 0x177) || (c >= 0x218 && c <= 0x21B))
        return c % 2 ? c : c + 1;
    if ((c >= 0x139 && c <= 0x148) || (c >= 0x179 && c <= 0x17E)) return c % 2 ? c + 1 : c;
    return c;
}
std::u32string decode(std::string_view s) {
    std::u32string out;
    for (size_t i = 0; i < s.size();) {
        unsigned char b = static_cast<unsigned char>(s[i]);
        size_t n = b < 0x80 ? 1 : b < 0xE0 ? 2 : b < 0xF0 ? 3 : 4;
        if (i + n > s.size()) break;
        char32_t c = n == 1 ? b : n == 2 ? b & 0x1F : n == 3 ? b & 0x0F : b & 0x07;
        for (size_t k = 1; k < n; ++k) c = (c << 6) | (static_cast<unsigned char>(s[i + k]) & 0x3F);
        out.push_back(c);
        i += n;
    }
    return out;
}
std::string encode(const std::u32string &s) {
    std::string out;
    for (char32_t c : s) {
        if (c < 0x80) out += static_cast<char>(c);
        else if (c < 0x800) { out += static_cast<char>(0xC0 | (c >> 6)); out += static_cast<char>(0x80 | (c & 0x3F)); }
        else if (c < 0x10000) {
            out += static_cast<char>(0xE0 | (c >> 12)); out += static_cast<char>(0x80 | ((c >> 6) & 0x3F));
            out += static_cast<char>(0x80 | (c & 0x3F));
        } else {
            out += static_cast<char>(0xF0 | (c >> 18)); out += static_cast<char>(0x80 | ((c >> 12) & 0x3F));
            out += static_cast<char>(0x80 | ((c >> 6) & 0x3F)); out += static_cast<char>(0x80 | (c & 0x3F));
        }
    }
    return out;
}
// String values of the state, keys ignored (they are usually English field names).
void leaves(const json &value, int depth, std::vector<const std::string *> &out) {
    if (depth > 6) return;
    if (value.is_string()) out.push_back(&value.get_ref<const std::string &>());
    else if (value.is_structured()) for (const auto &child : value) leaves(child, depth + 1, out);
}
// Blank out identifiers such as github.com, user@example.com or v1.2.3: split into
// words, their pieces collide with function words (`com`, `o`, `e`).
void blank_identifiers(std::u32string &text) {
    auto part = [](char32_t c) { return word_char(c) || c == '-'; };
    for (size_t i = 0; i < text.size();) {
        if (i > 0 && part(text[i - 1])) { ++i; continue; }
        size_t j = i, groups = 0;
        while (j < text.size() && part(text[j])) ++j;
        while (j + 1 < text.size() && (text[j] == '.' || text[j] == '@') && part(text[j + 1])) {
            ++j;
            while (j < text.size() && part(text[j])) ++j;
            ++groups;
        }
        if (groups) { std::fill(text.begin() + i, text.begin() + j, U' '); i = j; }
        else ++i;
    }
}
} // namespace

language_detection detect_language(const json &state) {
    std::vector<const std::string *> parts;
    leaves(state, 0, parts);
    std::u32string text;
    for (const auto *part : parts) {
        if (!text.empty()) text.push_back(U' ');
        text += decode(*part);
        if (text.size() >= 4000) break;
    }
    if (text.size() > 4000) text.resize(4000);

    size_t latin = 0, non_latin = 0;
    std::map<std::string, size_t> by_script;
    for (char32_t c : text) {
        if (latin_letter(c)) ++latin;
        else if (const char *s = non_latin_script(c)) { ++non_latin; ++by_script[s]; }
    }
    if (!latin && !non_latin) return {false, "no_letters", "", ""};
    const double fraction = double(non_latin) / double(latin + non_latin);
    if (fraction >= 0.2 || (fraction >= 0.1 && non_latin >= 10)) {
        auto top = std::max_element(by_script.begin(), by_script.end(),
                                    [](const auto &a, const auto &b) { return a.second < b.second; });
        return {false, "non_latin_script", top->first, ""};
    }

    std::u32string lowered;
    for (char32_t c : text) lowered.push_back(lower(c));
    size_t marked = 0;
    for (char32_t c : lowered) marked += non_english_letters.find(c) != std::u32string_view::npos;
    const double diacritic_rate = double(marked) / double(lowered.size());
    std::u32string scan = lowered;
    blank_identifiers(scan);
    std::vector<std::string> list;
    std::u32string current;
    for (size_t i = 0; i <= scan.size(); ++i) {
        if (i < scan.size() && (latin_letter(scan[i]) || non_latin_script(scan[i]))) { current.push_back(scan[i]); continue; }
        if (!current.empty()) list.push_back(encode(current));
        current.clear();
    }
    if (list.size() < 4) return {false, "too_short", "", ""};

    const auto &table = stopwords();
    const auto &shared = shared_words();
    const words distinct(list.begin(), list.end());
    size_t english = 0;
    for (const auto &w : list) english += table[0].second.count(w);
    size_t best = 0;
    std::string best_language;
    for (size_t i = 1; i < table.size(); ++i) {
        const auto &[lang, set] = table[i];
        bool own = false;
        size_t hits = 0;
        for (const auto &w : distinct) if (set.count(w) && !shared.count(w)) own = true;
        if (!own) continue;
        for (const auto &w : list) hits += set.count(w);
        if (hits > best) { best = hits; best_language = lang; }
    }
    if (!best_language.empty() && best >= std::max<size_t>(2, english + 2))
        return {false, "other_language", "", best_language};
    if (diacritic_rate >= 0.02) {
        // One accented loanword or name must not pull plain English away: two
        // English-only function words and at most one marked word rescue it.
        size_t english_only = 0, marked_words = 0;
        for (const auto &w : distinct) {
            english_only += table[0].second.count(w) && !shared.count(w);
            auto letters = decode(w);
            marked_words += std::any_of(letters.begin(), letters.end(), [](char32_t c) {
                return non_english_letters.find(c) != std::u32string_view::npos; });
        }
        if (!(diacritic_rate < 0.06 && english_only >= 2 && marked_words <= 1))
            return {false, "non_english_letters", "", best_language};
    }
    if (!english) return {false, "no_english_words", "", ""};
    return {true, "english_text", "", "en"};
}

bool english_tag(std::string_view tag) {
    std::string primary;
    for (char c : tag) {
        if (c == '-' || c == '_') break;
        primary += static_cast<char>(c >= 'A' && c <= 'Z' ? c + 32 : c);
    }
    return primary == "en" || primary == "eng" || primary == "english";
}
} // namespace dtl
