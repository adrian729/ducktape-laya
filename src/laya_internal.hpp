#pragma once
// Shared by the C API (laya.cpp), request validation (laya_request.cpp) and planning and
// evaluation (laya_plan.cpp). A named namespace, not an anonymous one: these entities are used
// across the three translation units; exports.map and hidden visibility keep them out of the
// library's exports, and clang-cl names no anonymous namespace after a source path in their RTTI.
#include "backend.hpp"
#include "execution.hpp"
#include "json_io.hpp"
#include "request_state.hpp"
#include <cstdint>
#include <initializer_list>
#include <memory>
#include <mutex>
#include <string>
#include <vector>
#ifdef _WIN32
// clang-cl names an anonymous namespace after the source path on its command line, and the
// DLL keeps the RTTI name of read_json's parser callback: in a named namespace, the DLL does not
// depend on the build directory (scripts/check-reproducibility.py compares two).
namespace dtl_json {
dtl::json read_json(const char *text);
}
#endif
namespace dtl::detail {
// laya_request.cpp: request text, questions and open configs.
#ifdef _WIN32
using dtl_json::read_json;
#else
json read_json(const char *text);
#endif
json optional_json(const char *text);
bool unsigned_in(const json &value, uint64_t low, uint64_t high);
void keys(const json &value, std::initializer_list<const char *> allowed, const std::string &where);
std::vector<question> parse_questions(const json &body);
temperature_map temperatures(const json &value, const std::string &where);
std::string overflow_policy(const json &value, const std::string &where);
dtl::engine_config engine_config(const json &value, dtl::engine_config base = {}, bool route = false);
// laya_plan.cpp: planned requests, loaded models and evaluation.
struct planned {
    json body;
    std::vector<question> questions;
    json fields = json::object();
    double threshold = 0.5;
};
struct model_engine {
    dtl::backend runtime;
    std::mutex mutex;
    explicit model_engine(const char *path, const dtl::engine_config &config) : runtime(path, config) {}
    // default_overflow applies to questions whose request names no policy; it comes from
    // the engine's or the route's config, so routes sharing a loaded model keep their own.
    void prepare(question &q, dtl::request_state &state, const std::string &default_overflow, const dtl::execution &control) {
        if (q.overflow.empty()) q.overflow = default_overflow;
        runtime.prepare(q, state, &control);
    }
};
// A question whose answer needs no model: a choice with one option.
inline bool answered_without_model(const question &q) { return q.options.size() == 1; }
std::vector<planned> plans(const json &body, bool routed);
void count_windows(size_t &total, const question &q, size_t limit);
std::unique_ptr<model_engine> load_model(const std::string &path, const dtl::engine_config &config);
// engine is null only when every question is answered without a model.
json evaluate(model_engine *engine, planned &p, const dtl::execution &control);
} // namespace dtl::detail
