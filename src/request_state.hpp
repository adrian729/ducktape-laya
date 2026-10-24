#pragma once
// A request's state as its prompts carry it, prepared once per request and model.
#include "backend.hpp"
#include "python_json.hpp"
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
// Every question of a request reads the same state. It is serialized (structured values as
// the original Laya writes them, see python_json.hpp) and sanitized on the first question
// that needs it, and the text is reused for the request's other questions on the same
// backend; another backend (marker) prepares it again.
struct request_state {
    explicit request_state(const json &state) : source(state) {}
    // The state text for the backend `owner` with marker text `marker`: a string, or null.
    const json &text(const void *owner, const std::string &marker) {
        if (prepared_by != owner) {
            json value = source.is_string() || source.is_null() ? source : json(python_json(source));
            sanitize(value, marker);
            prepared = std::move(value);
            prepared_by = owner;
            ++serializations;
        }
        return prepared;
    }
    const json &source;
    size_t serializations = 0; // how often the state was prepared (tests)
private:
    const void *prepared_by = nullptr;
    json prepared;
};
}
