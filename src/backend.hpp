#pragma once
// Private boundary: no llama/ggml/Jinja types in shared orchestration or bindings.
#include <nlohmann/json.hpp>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>
namespace dtl {
using json = nlohmann::ordered_json;
struct prepared_question;
struct question {
    std::string id, type;
    json instructions, options;
    question(std::string id, std::string type, json instructions, json options);
    ~question();
    question(question &&) noexcept;
    question &operator=(question &&) noexcept;
    size_t token_count() const;
private:
    std::unique_ptr<prepared_question> prepared;
    friend class backend;
};
// Calls are serialized by the shared engine/router. All prepared inputs are
// consumed by the same backend that prepared them, before that backend closes.
class backend {
public:
    backend(const char *path, uint32_t threads);
    ~backend();
    void prepare(question &q, const json &state);
    json run(const question &q);
private:
    struct impl;
    std::unique_ptr<impl> impl_;
};
} // namespace dtl
