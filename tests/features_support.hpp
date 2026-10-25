// Shared helpers of laya-feature-tests (tests/features.cpp, features_overflow.cpp,
// features_structured.cpp). main() in features.cpp runs the parts in a fixed order; a part may
// read what an earlier part stored in the context.
#pragma once
#include "ducktape_laya.hpp"
#include <nlohmann/json.hpp>
#include <chrono>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <system_error>
#ifndef __has_feature
#define __has_feature(x) 0
#endif
#if defined(__SANITIZE_ADDRESS__) || defined(__SANITIZE_THREAD__) || __has_feature(address_sanitizer) || __has_feature(thread_sanitizer)
#define DTL_SANITIZED 1 // Sanitizer runtimes own operator new and keep freed memory in quarantine.
#endif
#if defined(__linux__) && !defined(DTL_SANITIZED)
// features.cpp counts every operator new of the process (allocation_count()).
#define DTL_COUNTS_ALLOCATIONS 1
#endif
namespace features {
using json = nlohmann::ordered_json;
#ifdef DTL_COUNTS_ALLOCATIONS
long allocation_count();
#endif
inline void expect(bool value, const char *message) { if (!value) throw std::runtime_error(message); }
template<class F> void invalid(F fn, int status = DTL_INVALID_ARGUMENT) {
    try { fn(); } catch (const ducktape::error &e) { expect(e.status == status, e.what()); return; }
    throw std::runtime_error("Expected rejection");
}
// Resident set in KiB (Linux), or -1.
long resident_kib();
json primitive(const std::string &state);
inline std::string repeated(const char *word, size_t count) { std::string s; while (count--) s += word; return s; }
inline json with(json request, const char *key, json value) { request[key] = std::move(value); return request; }

// State shared by the parts of one run with a model.
struct context {
    const char *model;                 // argv[1]
    ducktape::engine &engine;          // {"threads":2}, the default 512-token context
    std::chrono::steady_clock::time_point started;
    json requests;                     // three single-question primitive requests
    json singles;                      // their results on `engine`
    size_t base = 0;                   // input tokens of a one-word state, less that word
    json long_option;                  // a request whose billing option is too long for the head budget
    json hot, hotter, engine_hot;      // temperature overrides and `engine_hot`'s result for requests[0]
    struct work_directory {
        std::filesystem::path dir;
        ~work_directory() { if (!dir.empty()) { std::error_code ec; std::filesystem::remove_all(dir, ec); } }
    } work;                            // router test models (hard links), removed at exit

    context(const char *model_path, ducktape::engine &shared_engine, std::chrono::steady_clock::time_point start)
        : model(model_path), engine(shared_engine), started(start) {}

    void checkpoint(const char *phase) const {
        std::cerr << phase << ": " << std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count()
                  << " seconds\n";
    }
    // A one-question request whose state is `words` repeated words (one token each).
    std::string sized(size_t words) const {
        json r = {{"state", repeated("refund ", words)}, {"questions", {{"refund", requests[0]["questions"]["refund"]}}}};
        return r.dump();
    }
    // Whether `request` is rejected with status 1 and a message containing `reason` (false if it is answered).
    bool rejected(const std::string &request, const char *reason, const ducktape::engine *target = nullptr) const {
        try { (target ? *target : engine).execute(request); return false; }
        catch (const ducktape::error &e) {
            expect(e.status == DTL_INVALID_ARGUMENT && std::string(e.what()).find(reason) != std::string::npos, e.what());
            return true;
        }
    }
};

void overflow_policies(context &c);          // features_overflow.cpp
void structured_decisions(context &c);       // features_structured.cpp
void structured_state_shared(context &c);    // features_structured.cpp
} // namespace features
