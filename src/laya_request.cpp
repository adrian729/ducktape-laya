// Request text, question and open-config validation (see laya_internal.hpp).
#include "laya_internal.hpp"
#include "errors.hpp"
#include "python_json.hpp"
#include <cmath>
#include <cstdlib>
#include <set>
#include <string_view>
#ifdef _WIN32
namespace dtl_json {
using namespace dtl;
#else
namespace dtl::detail {
#endif
json read_json(const char *text) {
    require(text, "JSON input is required");
    size_t n = 0;
    while (n <= max_request && text[n]) ++n;
    require(n <= max_request, "Request exceeds 1 MiB");
    try {
        return parse_request_json(std::string_view(text, n), 64, max_object_members);
    } catch (const json::exception &e) { throw failure(DTL_INVALID_ARGUMENT, e.what()); }
}
}
namespace dtl::detail {
// An unsigned JSON integer in [low, high]. Compared as uint64: nlohmann compares an
// unsigned value above 2^63 with a signed literal as negative.
bool unsigned_in(const json &value, uint64_t low, uint64_t high) {
    return value.is_number_unsigned() && value.get<uint64_t>() >= low && value.get<uint64_t>() <= high;
}
void keys(const json &value, std::initializer_list<const char *> allowed, const std::string &where) {
    require(value.is_object(), where + " must be an object");
    for (auto &[key, ignored] : value.items()) {
        bool found = false;
        for (auto name : allowed) if (key == name) found = true;
        require(found, where + ": unsupported key " + key);
    }
}
std::vector<question> parse_questions(const json &body) {
    require(body.is_object(), "Request must be an object");
    require(body.contains("state") && !body["state"].is_null(), "state is required");
    require(!body.contains("images") && !body.contains("files"), "Media is unsupported");
    require(body.contains("questions") && body["questions"].is_object() &&
            !body["questions"].empty() && body["questions"].size() <= 32,
            "questions must be an object with 1..32 entries");
    std::vector<question> result;
    for (auto &[id, q] : body["questions"].items()) {
        require(q.is_object() && q.contains("instructions") && !q["instructions"].is_null(),
                id + ": instructions are required");
        for (const auto &[key, ignored] : q.items())
            require(key == "type" || key == "instructions" || key == "criteria" || key == "aggregate",
                    id + ": unsupported key " + key);
        require(q.contains("type") && q["type"].is_string(), id + ": type is required");
        question out{id, q["type"].get<std::string>(), q["instructions"], json::array()};
        json criteria = q.value("criteria", json());
        auto add = [&](const std::string &key, const json &description) {
            out.options.push_back({{"key", key}, {"description", description}});
        };
        if (out.type == "choice") {
            require((criteria.is_object() || criteria.is_array()) && !criteria.empty() && criteria.size() <= 255,
                    id + ": choice criteria must contain 1..255 options");
            if (criteria.is_object()) {
                for (auto &[key, description] : criteria.items()) add(key, description);
            } else {  // a plain list of labels without descriptions
                std::set<std::string> seen;
                for (auto &label : criteria) {
                    require(label.is_string() && !label.get<std::string>().empty() && seen.insert(label.get<std::string>()).second,
                            id + ": choice labels must be distinct non-empty strings");
                    add(label.get<std::string>(), json());
                }
            }
        } else if (out.type == "score") {
            require(criteria.is_array() && criteria.size() >= 2 && criteria.size() <= 10,
                    id + ": score criteria must be an array of 2..10 levels");
            for (size_t i = 0; i < criteria.size(); ++i) {
                require(!criteria[i].is_null(), id + ": score levels need a description");
                add(std::to_string(i), criteria[i]);
            }
        } else if (out.type == "noul") {
            require(criteria.is_null() || criteria.is_object(), id + ": noul criteria must be an object");
            if (criteria.is_object()) for (auto &[key, ignored] : criteria.items())
                require(key == "false" || key == "true", id + ": noul keys must be false or true");
            for (const char *key : {"false", "true"})
                add(key, criteria.is_object() ? criteria.value(key, json()) : json());
        } else require(false, id + ": supported types are choice, score, noul");
        if (q.contains("aggregate")) {
            require(out.type == "noul" && (q["aggregate"] == "any" || q["aggregate"] == "all"),
                    id + ": aggregate is any or all, for noul questions");
            out.aggregate_all = q["aggregate"] == "all";
        }
        result.push_back(std::move(out));
    }
    return result;
}
dtl::temperature_map temperatures(const json &value, const std::string &where) {
    require(value.is_object(), where + " must be an object");
    dtl::temperature_map out;
    for (auto &[key, t] : value.items()) {
        auto dot = key.find('.');
        auto type = key.substr(0, dot), bucket = dot == std::string::npos ? std::string() : key.substr(dot + 1);
        require((type == "choice" || type == "score" || type == "noul") &&
                (dot == std::string::npos || bucket == "2" || bucket == "3_5" || bucket == "6_10" || bucket == "11"),
                where + ": unsupported key " + key);
        // Checked after narrowing to the float the engine uses: 1e-50 or 1e39 would
        // otherwise pass as a double and become 0 or infinity.
        const float temperature = t.is_number() ? t.get<float>() :
            dtl::is_big_integer(t) ? std::strtof(dtl::big_integer_text(t).c_str(), nullptr) : 0.0f;
        require(std::isfinite(temperature) && temperature > 0, where + "." + key + " must be > 0 and fit a float");
        out[key] = temperature;
    }
    return out;
}
std::string overflow_policy(const json &value, const std::string &where) {
    require(value.is_string() && (value == "reject" || value == "truncate_end" || value == "truncate_start" || value == "scan"),
            where + " must be reject, truncate_end, truncate_start or scan");
    return value.get<std::string>();
}
dtl::engine_config engine_config(const json &value, dtl::engine_config base, bool route) {
    if (value.is_null()) return base;
    keys(value, {"threads", "context_tokens", "temperatures", "overflow", "max_windows_per_call"}, "config");
    if (value.contains("max_windows_per_call")) {
        require(!route, "max_windows_per_call applies to the whole engine");
        require(unsigned_in(value["max_windows_per_call"], 1, max_call_windows),
                "max_windows_per_call must be an integer of 1.." + std::to_string(max_call_windows));
        base.max_windows_per_call = value["max_windows_per_call"].get<size_t>();
    }
    if (value.contains("overflow")) base.overflow = overflow_policy(value["overflow"], "config.overflow");
    if (value.contains("threads")) {
        require(unsigned_in(value["threads"], 0, 256), "threads must be 0..256");
        base.threads = value["threads"].get<uint32_t>();
    }
    if (value.contains("context_tokens")) {
        // The model's trained length bounds it from above when the model loads.
        require(unsigned_in(value["context_tokens"], 32, UINT32_MAX), "context_tokens must be an integer of at least 32");
        base.context_tokens = value["context_tokens"].get<size_t>();
    }
    if (value.contains("temperatures"))
        for (auto &[key, t] : temperatures(value["temperatures"], "config.temperatures")) base.temperatures[key] = t;
    return base;
}
json optional_json(const char *text) { return text ? read_json(text) : json(); }
} // namespace dtl::detail
