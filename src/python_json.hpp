#pragma once
#include <nlohmann/json.hpp>
#include <charconv>
#include <cmath>
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <string>
#include <string_view>
#include <utility>
#include <vector>
namespace dtl {
// Structured state, instructions and criteria reach the model as the original
// Laya writes them: Python's json.dumps(value, ensure_ascii=False) with default
// separators. That is the format the model was trained on. Jinja's own JSON
// output rounds floats (149.0 -> 149, six significant digits) and must not
// see them.
inline std::string python_float(double value) {
    if (value == 0) return std::signbit(value) ? "-0.0" : "0.0";
    char buffer[64];
    auto end = std::to_chars(buffer, buffer + sizeof buffer, std::fabs(value), std::chars_format::scientific).ptr;
    std::string text(buffer, end), digits;  // shortest round-trip digits, e.g. "1.25e+02"
    size_t e = text.find('e');
    int exponent = std::atoi(text.c_str() + e + 1);
    for (size_t i = 0; i < e; ++i) if (text[i] != '.') digits += text[i];
    std::string out;
    if (exponent < -4 || exponent >= 16) {  // Python repr switches to exponent form here
        out = digits.substr(0, 1) + (digits.size() > 1 ? "." + digits.substr(1) : "");
        char suffix[8];
        std::snprintf(suffix, sizeof suffix, "e%c%02d", exponent < 0 ? '-' : '+', std::abs(exponent));
        out += suffix;
    } else if (exponent < 0) {
        out = "0." + std::string(-exponent - 1, '0') + digits;
    } else if (digits.size() <= static_cast<size_t>(exponent) + 1) {
        out = digits + std::string(exponent + 1 - digits.size(), '0') + ".0";
    } else {
        out = digits.substr(0, exponent + 1) + "." + digits.substr(exponent + 1);
    }
    return value < 0 ? "-" + out : out;
}
// Integers outside the int64/uint64 range: Python keeps them exact and writes their
// digits, while nlohmann would parse them as doubles (1e+23). read_json stores such a
// number as a binary value of this subtype holding its JSON text, which is exactly
// what Python writes (a JSON integer has no leading zeros, and these are nonzero).
inline constexpr std::uint64_t big_integer_subtype = 0x6c61796142494e54ull; // "layaBINT"
template <class Json>
bool is_big_integer(const Json &value) {
    return value.is_binary() && value.get_binary().has_subtype() && value.get_binary().subtype() == big_integer_subtype;
}
template <class Json>
std::string big_integer_text(const Json &value) {
    const auto &bytes = value.get_binary();
    return std::string(bytes.begin(), bytes.end());
}
// Keeps integer literals outside int64/uint64 as text while parsing: construct it
// from the whole JSON text and call keep(parsed) for every parse_event_t::value.
// Number values are visited in document order, so the Nth number value is the Nth
// number literal of the text; only long integer literals are recorded.
class integer_literals {
public:
    explicit integer_literals(std::string_view text) {
        size_t numbers = 0;
        for (size_t i = 0; i < text.size();) {
            const char c = text[i];
            if (c == '"') {
                for (++i; i < text.size() && text[i] != '"'; ++i) if (text[i] == '\\') ++i;
                ++i;
            } else if (c == '-' || (c >= '0' && c <= '9')) {
                const size_t start = i;
                bool integer = true;
                for (; i < text.size() && (text[i] == '+' || text[i] == '-' || text[i] == '.' || text[i] == 'e' ||
                                           text[i] == 'E' || (text[i] >= '0' && text[i] <= '9')); ++i)
                    if ((text[i] < '0' || text[i] > '9') && i != start) integer = false;
                // Shorter literals always fit int64; only longer ones can overflow.
                if (integer && i - start >= 19) long_.emplace_back(numbers, text.substr(start, i - start));
                ++numbers;
            } else ++i;
        }
    }
    template <class Json>
    void keep(Json &parsed) {
        if (!parsed.is_number()) return;
        if (next_ < long_.size() && long_[next_].first == number_) {
            const auto literal = long_[next_++].second;
            // nlohmann makes an integer literal a double only when it overflows.
            if (parsed.is_number_float())
                parsed = Json::binary(typename Json::binary_t::container_type(literal.begin(), literal.end()), big_integer_subtype);
        }
        ++number_;
    }
private:
    std::vector<std::pair<size_t, std::string_view>> long_;
    size_t next_ = 0, number_ = 0;
};
template <class Json>
void python_json(const Json &value, std::string &out) {
    switch (value.type()) {
    case nlohmann::json::value_t::null: out += "null"; break;
    case nlohmann::json::value_t::boolean: out += value.template get<bool>() ? "true" : "false"; break;
    case nlohmann::json::value_t::number_integer: out += std::to_string(value.template get<int64_t>()); break;
    case nlohmann::json::value_t::number_unsigned: out += std::to_string(value.template get<uint64_t>()); break;
    case nlohmann::json::value_t::number_float: out += python_float(value.template get<double>()); break;
    case nlohmann::json::value_t::string:
        out += '"';
        for (unsigned char c : value.template get_ref<const std::string &>()) {
            switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            case '\b': out += "\\b"; break;
            case '\f': out += "\\f"; break;
            default:
                if (c < 0x20) {
                    char escaped[8];
                    std::snprintf(escaped, sizeof escaped, "\\u%04x", c);
                    out += escaped;
                } else out += static_cast<char>(c);
            }
        }
        out += '"';
        break;
    case nlohmann::json::value_t::array: {
        out += '[';
        bool first = true;
        for (const auto &item : value) { if (!first) out += ", "; first = false; python_json(item, out); }
        out += ']';
        break;
    }
    case nlohmann::json::value_t::object: {
        out += '{';
        bool first = true;
        for (const auto &[key, item] : value.items()) {
            if (!first) out += ", ";
            first = false;
            python_json(Json(key), out);
            out += ": ";
            python_json(item, out);
        }
        out += '}';
        break;
    }
    case nlohmann::json::value_t::binary:
        if (is_big_integer(value)) { out += big_integer_text(value); break; }
        out += "null";  // other binary values never come from parsed requests
        break;
    default: out += "null";  // discarded never comes from parsed requests
    }
}
template <class Json>
std::string python_json(const Json &value) {
    std::string out;
    python_json(value, out);
    return out;
}
} // namespace dtl
