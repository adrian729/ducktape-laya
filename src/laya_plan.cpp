// Request planning (schemas to questions, per-request settings), loaded models and evaluation
// (see laya_internal.hpp).
#include "laya_internal.hpp"
#include "decision.hpp"
#include "errors.hpp"
#include <algorithm>
#include <cmath>
#include <set>
#include <stdexcept>
namespace dtl::detail {
// Bounds the forward passes one call plans: 128 questions with 1024 scan windows
// each would otherwise be accepted from a small request.
void count_windows(size_t &total, const question &q, size_t limit) {
    total += q.overflow_report().value("windows", size_t(0));
    require(total <= limit, "the call needs more than " + std::to_string(limit) + " forward passes" +
            (limit < max_call_windows ? " (max_windows_per_call)" : ""));
}
void schema_metadata(const json &value, const std::string &where) {
    for (const char *name : {"title", "description"})
        require(!value.contains(name) || value[name].is_string(), where + ": " + name + " must be text");
}
planned plan(json body, bool routed) {
    keys(body, {"state", "questions", "schema", "boolean_threshold", "model", "lang", "max_tokens", "temperatures",
                "overflow", "max_windows"}, "request");
    require(routed || !body.contains("model"), "model is only supported by a router");
    require(!body.contains("lang") || (body.value("model", json()) == "auto" && body["lang"].is_string() &&
                                       !body["lang"].get<std::string>().empty() && body["lang"].get<std::string>().size() <= 64),
            "lang must be a 1..64 character string and requires model \"auto\"");
    require(body.contains("questions") != body.contains("schema"), "Supply exactly one of questions or schema");
    planned out;
    if (body.contains("schema")) {
        const auto &schema = body["schema"];
        keys(schema, {"type", "properties", "required", "additionalProperties", "description", "title"}, "schema");
        schema_metadata(schema, "schema");
        require(schema.contains("type") && schema["type"] == "object", "schema.type must be object");
        require(schema.contains("properties") && schema["properties"].is_object() &&
                !schema["properties"].empty() && schema["properties"].size() <= max_request_questions,
                "schema needs 1.." + std::to_string(max_request_questions) + " properties");
        if (schema.contains("additionalProperties"))
            require(schema["additionalProperties"] == false, "additionalProperties must be false");
        if (schema.contains("required")) {
            require(schema["required"].is_array(), "required must be an array");
            std::set<std::string> seen;
            for (auto &name : schema["required"]) {
                require(name.is_string(), "required entries must be strings");
                const auto key = name.get<std::string>();
                require(schema["properties"].contains(key) && seen.insert(key).second, "Invalid/duplicate required property");
            }
        }
        if (body.contains("boolean_threshold")) {
            require(body["boolean_threshold"].is_number(), "boolean_threshold must be numeric");
            out.threshold = body["boolean_threshold"].get<double>();
            require(std::isfinite(out.threshold) && out.threshold >= 0 && out.threshold <= 1,
                    "boolean_threshold must be 0..1");
        }
        json questions = json::object();
        for (auto &[name, prop] : schema["properties"].items()) {
            keys(prop, {"type", "description", "title", "enum", "minimum", "maximum"}, "property " + name);
            schema_metadata(prop, "property " + name);
            require(prop.contains("type") && prop["type"].is_string(), name + ": type is required");
            json q = {{"instructions", prop.value("description", json(name))}};
            const auto type = prop["type"].get<std::string>();
            if (type == "string") {
                require(!prop.contains("minimum") && !prop.contains("maximum"), name + ": bounds only apply to integers");
                require(prop.contains("enum") && prop["enum"].is_array() && !prop["enum"].empty() &&
                        prop["enum"].size() <= 255, name + ": string enum needs 1..255 values");
                json criteria = json::object();
                for (const auto &value : prop["enum"]) {
                    require(value.is_string(), name + ": enum values must be strings");
                    auto label = value.get<std::string>();
                    require(!criteria.contains(label), name + ": duplicate enum value");
                    criteria[label] = label;
                }
                q["type"] = "choice"; q["criteria"] = criteria;
            } else if (type == "boolean") {
                require(!prop.contains("enum") && !prop.contains("minimum") && !prop.contains("maximum"),
                        name + ": boolean constraints are unsupported");
                q["type"] = "noul";
            } else if (type == "integer") {
                require(!prop.contains("enum") && prop.contains("minimum") && prop.contains("maximum") &&
                        prop["minimum"].is_number_integer() && prop["maximum"].is_number_integer(),
                        name + ": integer requires minimum and maximum");
                // Keep arithmetic and cross-language JSON values exact.
                const double lo = prop["minimum"].get<double>(), hi = prop["maximum"].get<double>();
                require(lo >= -2147483648.0 && hi <= 2147483647.0 && hi > lo && hi - lo <= 9,
                        name + ": integer range must have 2..10 int32 levels");
                q["type"] = "score"; q["criteria"] = json::array();
                for (int64_t v = prop["minimum"].get<int64_t>(); v <= prop["maximum"].get<int64_t>(); ++v)
                    q["criteria"].push_back(std::to_string(v));
            } else require(false, name + ": supported properties are string enum, boolean, bounded integer");
            questions[name] = std::move(q);
            out.fields[name] = prop;
        }
        body["questions"] = std::move(questions);
    } else require(!body.contains("boolean_threshold"), "boolean_threshold requires schema");
    out.questions = parse_questions(body);
    size_t max_tokens = 0;
    if (body.contains("max_tokens")) {
        require(unsigned_in(body["max_tokens"], 1, UINT32_MAX), "max_tokens must be a positive integer");
        max_tokens = body["max_tokens"].get<size_t>();
    }
    std::shared_ptr<const dtl::temperature_map> overrides;
    if (body.contains("temperatures"))
        overrides = std::make_shared<const dtl::temperature_map>(temperatures(body["temperatures"], "temperatures"));
    std::string overflow;
    if (body.contains("overflow")) overflow = overflow_policy(body["overflow"], "overflow");
    size_t max_windows = 32;
    if (body.contains("max_windows")) {
        require(unsigned_in(body["max_windows"], 1, max_scan_windows),
                "max_windows must be 1.." + std::to_string(max_scan_windows));
        max_windows = body["max_windows"].get<size_t>();
    }
    for (auto &q : out.questions) {
        q.max_tokens = max_tokens; q.temperatures = overrides; q.overflow = overflow; q.max_windows = max_windows;
    }
    out.body = std::move(body);
    return out;
}
std::vector<planned> plans(const json &body, bool routed) {
    const auto requests = body.is_array() ? body : json::array({body});
    require(!requests.empty() && requests.size() <= max_batch_requests,
            "Batch must contain 1.." + std::to_string(max_batch_requests) + " requests");
    std::vector<planned> result;
    size_t count = 0;
    for (auto &request : requests) {
        result.push_back(plan(request, routed));
        count += result.back().questions.size();
        require(count <= max_batch_questions, "Batch exceeds " + std::to_string(max_batch_questions) + " questions");
    }
    return result;
}
json evaluate(model_engine *engine, planned &p, const dtl::execution &control) {
    json answers = json::object();
    size_t tokens = 0;
    for (const auto &q : p.questions) {
        answers[q.id] = engine ? engine->runtime.run(q, &control) : dtl::single_option_answer(q.options);
        tokens += q.token_count();
    }
    json result = {{"answers", answers}, {"usage", {{"input_tokens", tokens}, {"output_tokens", 0}}}};
    // Callers who allowed overflow handling always learn what it did.
    if (!p.questions.empty() && p.questions.front().overflow != "reject") {
        size_t state_tokens = 0, dropped = 0, windows = 0;
        json shortened = json::array();
        for (const auto &q : p.questions) {
            auto report = q.overflow_report();
            if (report.empty()) continue;
            state_tokens = std::max(state_tokens, report["state_tokens"].get<size_t>());
            dropped = std::max(dropped, report["state_tokens_dropped"].get<size_t>());
            windows = std::max(windows, report["windows"].get<size_t>());
            if (report["state_tokens_dropped"].get<size_t>() > 0 || report["head_shortened"].get<bool>()) shortened.push_back(q.id);
        }
        result["usage"]["state_tokens"] = state_tokens;
        result["usage"]["state_tokens_dropped"] = dropped;
        result["usage"]["truncated_questions"] = std::move(shortened);
        result["usage"]["windows"] = windows; // the most state windows any question used (1 when it fitted)
    }
    if (!p.fields.empty()) {
        json values = json::object();
        for (auto &[name, field] : p.fields.items()) {
            const auto &a = answers[name];
            if (field["type"] == "boolean") values[name] = a["noul"].get<double>() >= p.threshold;
            else if (field["type"] == "string") values[name] = a["choice"];
            else values[name] = field["minimum"].get<int64_t>() + static_cast<int64_t>(dtl::ordinal_index(a["probabilities"]));
        }
        result["values"] = std::move(values);
    }
    return result;
}
std::unique_ptr<model_engine> load_model(const std::string &path, const dtl::engine_config &config) {
    try { return std::make_unique<model_engine>(path.c_str(), config); }
    catch (const failure &) { throw; }
    catch (const std::bad_alloc &) { throw; }
    catch (const std::exception &e) { throw failure(DTL_MODEL_ERROR, e.what()); }
}
} // namespace dtl::detail
