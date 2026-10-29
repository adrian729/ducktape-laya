#pragma once
// A request's state as its prompts carry it, prepared once per request and model.
#include "backend.hpp"
#include "python_json.hpp"
#include <optional>
#include <string>
namespace dtl {
// Replaces every occurrence of the model's marker (mask) text in string values with a space,
// so the marker appears in a prompt only where the template puts it.
inline void sanitize(json &value, const std::string &marker) {
    if (value.is_string()) {
        // One pass: replacing in place would shift the tail once per marker.
        const auto &s = value.get_ref<const std::string &>();
        if (s.find(marker) == std::string::npos) return;
        std::string out;
        out.reserve(s.size());
        for (size_t at = 0;;) {
            const size_t next = s.find(marker, at);
            out.append(s, at, next == std::string::npos ? std::string::npos : next - at);
            if (next == std::string::npos) break;
            out += ' ';
            at = next + marker.size();
        }
        value = std::move(out);
    } else if (value.is_structured()) {
        for (auto &child : value) sanitize(child, marker);
    }
}
// Every question of a request reads the same state, and all of them run on one backend. It is
// serialized (structured values as the original Laya writes them, see python_json.hpp) and
// sanitized on the first question that needs it, and the text is reused for the others.
struct request_state {
    explicit request_state(const json &state) : source(state) {}
    // The state as text, with the marker text `marker` replaced. The request requires a
    // non-null state, so this is always a string.
    const std::string &text(const std::string &marker) {
        if (!prepared) {
            json value = source.is_string() ? source : json(python_json(source));
            sanitize(value, marker);
            prepared = std::move(value);
            ++serializations;
        }
        return prepared->get_ref<const std::string &>();
    }
    const json &source;
    size_t serializations = 0; // how often the state was prepared (tests)
private:
    std::optional<json> prepared;
};
}
