#pragma once
#include <nlohmann/json.hpp>
#include <charconv>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <string>
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
    default: out += "null";  // binary/discarded never come from parsed requests
    }
}
template <class Json>
std::string python_json(const Json &value) {
    std::string out;
    python_json(value, out);
    return out;
}
} // namespace dtl
