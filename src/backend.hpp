#pragma once
// Private boundary: no llama/ggml/Jinja types in shared orchestration or bindings.
#include <nlohmann/json.hpp>
#ifndef DTL_NLOHMANN_NOTHROW_DESTROY
#error "Build must use the patched nlohmann/json.hpp (patches/llama.cpp/nlohmann-nothrow-destroy.patch)"
#endif
#include <cstdint>
#include <map>
#include <memory>
#include <string>
#include <vector>
namespace dtl {
using json = nlohmann::ordered_json;
class execution;
struct prepared_question;
using temperature_map = std::map<std::string, float>;
// Engine settings from the open config (dtl_open, router routes).
struct engine_config {
    uint32_t threads = 0;
    size_t context_tokens = 0;      // 0: 512, capped by the trained length
    std::string overflow = "reject";// default overflow policy for requests
    temperature_map temperatures;   // overrides the GGUF calibration, key by key
    size_t max_windows_per_call = 256; // forward passes one call may plan (engine-wide; not a route setting)
};
struct question {
    std::string id, type;
    json instructions, options;
    size_t max_tokens = 0;          // per-request prompt limit; 0: the engine capacity
    std::shared_ptr<const temperature_map> temperatures; // per-request overrides
    // Input longer than the limit: "reject", "truncate_end", "truncate_start" or "scan";
    // empty takes the engine's default.
    std::string overflow;
    size_t max_windows = 32;        // scan: windows allowed per question
    bool aggregate_all = false;     // scan, noul: minimum over windows instead of maximum
    question(std::string id, std::string type, json instructions, json options);
    ~question();
    question(question &&) noexcept;
    question &operator=(question &&) noexcept;
    size_t token_count() const;
    // What overflow handling did: {"state_tokens", "state_tokens_dropped",
    // "head_shortened", "windows"}; empty when nothing was prepared.
    json overflow_report() const;
private:
    std::unique_ptr<prepared_question> prepared;
    friend class backend;
};
struct request_state; // request_state.hpp
// Calls are serialized by the shared engine/router. All prepared inputs are
// consumed by the same backend that prepared them, before that backend closes.
class backend {
public:
    backend(const char *path, const engine_config &config);
    ~backend();
    json info() const;
    // control stops a long tokenization (cancellation, deadline); nullptr: not stoppable.
    void prepare(question &q, request_state &state, const execution *control = nullptr);
    json run(const question &q, const execution *control = nullptr);
private:
    struct impl;
    std::unique_ptr<impl> impl_;
};
} // namespace dtl
