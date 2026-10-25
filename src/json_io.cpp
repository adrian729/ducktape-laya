#include "json_io.hpp"
namespace dtl {
json parse_json(std::string_view text) { return json::parse(text); }
json parse_json(std::string_view text, const json::parser_callback_t &callback) { return json::parse(text, callback); }
std::string dump_json(const json &value, int indent, char indent_char, bool ensure_ascii, json::error_handler_t handler) {
    return value.dump(indent, indent_char, ensure_ascii, handler);
}
} // namespace dtl
