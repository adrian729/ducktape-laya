// Special-token partition: llama.cpp splits text at every control, user-defined and
// unknown token before running the tokenizer. The runtime patch
// tokenizer-linear-partition.patch makes that split linear in the number of matches
// (it was quadratic). This test checks, on vocabularies loaded without weights, that
//   1. token sequences are unchanged: each case's count and hash must equal the
//      golden record, which was produced by the unpatched runtime (--record), and
//   2. tokenization time grows linearly: text that matches a short special token
//      every few bytes is timed at two sizes, and the larger one must not take
//      quadratically longer.
// Usage: laya-tokenizer-tests [--record] GOLDEN VOCAB.gguf...
// Without --record, every vocabulary must have golden entries (an unknown vocabulary
// fails), and every golden entry of a supplied vocabulary must be checked.
#include "llama.h"
#include "thread-cpu-time.hpp"
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
uint64_t fnv(uint64_t h, const void *data, size_t n) {
    for (size_t i = 0; i < n; ++i) { h ^= static_cast<const unsigned char *>(data)[i]; h *= 1099511628211ull; }
    return h;
}
constexpr uint64_t fnv_start = 14695981039346656037ull;
std::string hex(uint64_t v) { char b[17]; std::snprintf(b, sizeof b, "%016llx", static_cast<unsigned long long>(v)); return b; }

std::vector<llama_token> tokenize(const llama_vocab *vocab, const std::string &text, bool add_special, bool parse_special) {
    int n = llama_tokenize(vocab, text.data(), static_cast<int32_t>(text.size()), nullptr, 0, add_special, parse_special);
    if (n >= 0) {
        if (n == 0) return {};
        throw std::runtime_error("unexpected tokenize result");
    }
    std::vector<llama_token> out(static_cast<size_t>(-n));
    n = llama_tokenize(vocab, text.data(), static_cast<int32_t>(text.size()), out.data(), static_cast<int32_t>(out.size()), add_special, parse_special);
    if (n < 0) throw std::runtime_error("tokenize failed");
    out.resize(static_cast<size_t>(n));
    return out;
}

// Texts of the tokens the partition splits at (llama-vocab.cpp cache_special_tokens).
std::vector<std::string> special_texts(const llama_vocab *vocab) {
    std::vector<std::string> out;
    const int n = llama_vocab_n_tokens(vocab);
    for (llama_token t = 0; t < n; ++t) {
        const auto attr = llama_vocab_get_attr(vocab, t);
        if (!(attr & (LLAMA_TOKEN_ATTR_CONTROL | LLAMA_TOKEN_ATTR_USER_DEFINED | LLAMA_TOKEN_ATTR_UNKNOWN))) continue;
        const char *text = llama_vocab_get_text(vocab, t);
        if (text && *text) out.push_back(text);
    }
    return out;
}

uint64_t fingerprint(const llama_vocab *vocab) {
    uint64_t h = fnv_start;
    const int n = llama_vocab_n_tokens(vocab);
    for (llama_token t = 0; t < n; ++t) {
        const char *text = llama_vocab_get_text(vocab, t);
        const std::string s = text ? text : "";
        const uint64_t size = s.size();
        const int32_t attr = llama_vocab_get_attr(vocab, t);
        h = fnv(h, &size, sizeof size);
        h = fnv(h, s.data(), s.size());
        h = fnv(h, &attr, sizeof attr);
    }
    return h;
}

std::string repeat(const std::string &unit, size_t bytes) {
    std::string s;
    while (s.size() + unit.size() <= bytes) s += unit;
    return s;
}

struct rng {
    uint64_t state;
    uint32_t next() { state = state * 6364136223846793005ull + 1442695040888963407ull; return static_cast<uint32_t>(state >> 33); }
    size_t below(size_t n) { return next() % n; }
};

const char *const multilingual =
    "El pingüino comió ñoquis. Größere Straßen führen nach Köln. L'été dernier, ça m'a plu. "
    "Съешь же ещё этих мягких французских булок. 我们今天去北京。日本語のテキストです。한국어 문장입니다. "
    "مرحبا بالعالم. नमस्ते दुनिया। Ελληνικά γράμματα. e\xcc\x81 (combining) \xf0\x9f\x98\x80\xf0\x9f\x91\x8d\xf0\x9f\x8f\xbd ";

// Deterministic inputs: whitespace runs, newlines, multilingual text, and the
// vocabulary's own special tokens at every position (start, end, adjacent, spaced).
std::vector<std::pair<std::string, std::string>> cases(const std::vector<std::string> &specials) {
    std::vector<std::pair<std::string, std::string>> out;
    out.push_back({"empty", ""});
    out.push_back({"double-spaces", repeat("x  ", 8192)});
    out.push_back({"triple-spaces", repeat("x   ", 8192)});
    out.push_back({"single-spaces", repeat("word ", 8192)});
    {
        std::string s;
        for (int n = 1; n <= 64; ++n) s += "w" + std::to_string(n) + std::string(n, ' ');
        out.push_back({"space-runs", s + std::string(5000, ' ') + "end"});
    }
    out.push_back({"newline-lines", repeat("log line 42\n", 8192)});
    {
        std::string s;
        for (int n = 1; n <= 40; ++n) s += "line" + std::string(n, '\n') + (n % 3 ? "\r\n" : "\t \n ");
        out.push_back({"newline-runs", s});
    }
    out.push_back({"multilingual", repeat(std::string(multilingual) + "\n", 16384)});
    std::string all, spaced, adjacent, edges;
    for (size_t i = 0; i < specials.size(); ++i) {
        const auto &t = specials[i];
        all += "a" + t + " b " + t + t + "\n " + t + " ";
        if (i < 40) { spaced += "  " + t + "  x" + t + "\n\n"; adjacent += t + t + t; edges += t + "mid " + t + " "; }
    }
    out.push_back({"specials-all", all});
    out.push_back({"specials-spaced", spaced});
    out.push_back({"specials-adjacent", adjacent});
    out.push_back({"specials-edges", edges.empty() ? edges : edges.substr(0, edges.size() - 1)});
    std::string prefixes;
    for (size_t i = 0; i < specials.size() && i < 60; ++i)
        if (specials[i].size() > 1) prefixes += specials[i].substr(0, specials[i].size() - 1) + " " + specials[i].substr(1) + " ";
    out.push_back({"specials-partial", prefixes});
    const std::vector<std::string> pool = {"the", "refund", "Größe", "日本", "ñ", "ø", "123", ".", ",", "!", " ", "  ", "   ",
                                           "\n", "\n\n", "\t", "\r\n", "    ", "x", "(", ")", "{\"a\": 1}", "مرحبا"};
    for (uint64_t seed = 1; seed <= 4; ++seed) {
        rng r{seed};
        std::string s;
        while (s.size() < 8192) {
            const size_t pick = r.below(10);
            if (pick < 2 && !specials.empty()) s += specials[r.below(specials.size())];
            else if (pick == 2) s += std::string(1 + r.below(30), ' ');
            else s += pool[r.below(pool.size())];
        }
        out.push_back({"random-" + std::to_string(seed), s});
    }
    return out;
}

} // namespace

int main(int argc, char **argv) {
    std::vector<std::string> args(argv + 1, argv + argc);
    const bool record = !args.empty() && args[0] == "--record";
    if (record) args.erase(args.begin());
    if (args.size() < 2) { std::cerr << "usage: laya-tokenizer-tests [--record] GOLDEN VOCAB.gguf...\n"; return 2; }
    const std::string golden_path = args[0];
    llama_log_set([](ggml_log_level, const char *, void *) {}, nullptr);
    llama_backend_init();
    std::map<std::string, std::string> golden; // "fingerprint case flags" -> "count hash"
    if (!record) {
        std::ifstream in(golden_path);
        if (!in) { std::cerr << "cannot read " << golden_path << '\n'; return 2; }
        for (std::string line; std::getline(in, line);) {
            if (line.empty() || line[0] == '#') continue;
            std::istringstream fields(line);
            std::string fp, name, flags, count, hash;
            fields >> fp >> name >> flags >> count >> hash;
            golden[fp + " " + name + " " + flags] = count + " " + hash;
        }
    }
    std::ostringstream recorded;
    int failures = 0;
    try {
        for (size_t v = 1; v < args.size(); ++v) {
            auto mp = llama_model_default_params();
            mp.vocab_only = true;
            std::unique_ptr<llama_model, decltype(&llama_model_free)> model(llama_model_load_from_file(args[v].c_str(), mp), llama_model_free);
            if (!model) throw std::runtime_error("cannot load vocabulary " + args[v]);
            const llama_vocab *vocab = llama_model_get_vocab(model.get());
            const std::string fp = hex(fingerprint(vocab));
            const auto specials = special_texts(vocab);
            const std::string label = args[v].substr(args[v].find_last_of("/\\") + 1);
            size_t checked = 0;
            std::set<std::string> produced;
            for (const auto &[name, text] : cases(specials)) {
                for (int flags = 0; flags < 2; ++flags) { // 0: plain text, 1: parse_special (as the library calls it)
                    const auto tokens = tokenize(vocab, text, false, flags == 1);
                    const std::string key = fp + " " + name + " " + std::to_string(flags);
                    const std::string value = std::to_string(tokens.size()) + " " + hex(fnv(fnv_start, tokens.data(), tokens.size() * sizeof(llama_token)));
                    produced.insert(key);
                    if (record) { recorded << key << ' ' << value << " # " << label << '\n'; continue; }
                    auto it = golden.find(key);
                    if (it == golden.end()) { std::cerr << label << ": no golden entry for " << key << '\n'; ++failures; continue; }
                    if (it->second != value) { std::cerr << label << ": " << name << " flags " << flags << ": got " << value << ", expected " << it->second << '\n'; ++failures; }
                    ++checked;
                }
            }
            for (const auto &entry : golden)
                if (entry.first.compare(0, fp.size() + 1, fp + " ") == 0 && !produced.count(entry.first)) {
                    std::cerr << label << ": golden entry not produced: " << entry.first << '\n';
                    ++failures;
                }
            // Linear time: text matching the shortest special token every few bytes.
            std::string shortest;
            for (const auto &t : specials) if (shortest.empty() || t.size() < shortest.size()) shortest = t;
            if (!shortest.empty() && !record) {
                const std::string unit = "x" + shortest;
                const size_t matches = (128 << 10) / unit.size();
                // Every vocabulary is timed, none skipped. Every special token is searched in
                // every fragment, so the linear cost per match differs between vocabularies;
                // gpt-neox's is the highest of those checked (about 0.1 s for 4 KiB).
                // The sizes differ 32-fold: linear growth is x32, quadratic x1024. The
                // unpatched partition took about 31 s for 64 KiB of double spaces (about
                // 0.1 s at 4 KiB), so its growth here is close to x1000, ten times the x96
                // limit, while linear growth keeps a threefold margin below it. Times are the
                // thread's CPU time per call, over at least 0.2 s of calls: load on the
                // machine does not stretch them.
                const std::string small_text = repeat(unit, 4 << 10), large_text = repeat(unit, 128 << 10);
                const double small = dtl_test::cpu_seconds_per_run([&] { tokenize(vocab, small_text, false, true); }, 0.2);
                const double large = dtl_test::cpu_seconds_per_run([&] { tokenize(vocab, large_text, false, true); }, 0.2);
                std::fprintf(stderr, "%s: %zu golden cases match; %zu special tokens, %zu partition matches: 4 KiB %.5f s, 128 KiB %.5f s CPU (x%.1f; linear x32, quadratic x1024)\n",
                             label.c_str(), checked, specials.size(), matches, small, large, large / std::max(small, 1e-9));
                // The 50 ms floor absorbs measurement noise on the fastest vocabularies.
                if (large > 0.05 && large > 96 * small) {
                    std::cerr << label << ": tokenization time grows faster than linearly\n";
                    ++failures;
                }
            }
        }
    } catch (const std::exception &e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
    if (record) {
        std::ofstream out(golden_path, std::ios::app);
        out << recorded.str();
        std::cerr << "recorded to " << golden_path << '\n';
        return out ? 0 : 1;
    }
    if (failures) { std::cerr << failures << " failures\n"; return 1; }
    std::cout << "tokenizer partition tests passed\n";
    return 0;
}
