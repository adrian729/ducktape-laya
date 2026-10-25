#pragma once
// The one place that instantiates nlohmann's parser and serializer (src/json_io.cpp): every
// parse and dump of the library goes through these, so other translation units only build
// and read json values. Requires the patched header, like backend.hpp.
#include <nlohmann/json.hpp>
#ifndef DTL_NLOHMANN_NOTHROW_DESTROY
#error "Build must use the patched nlohmann/json.hpp (patches/llama.cpp/nlohmann-nothrow-destroy.patch)"
#endif
#include <string>
#include <string_view>
namespace dtl {
using json = nlohmann::ordered_json;
// json::parse(text); throws json::parse_error on invalid input.
json parse_json(std::string_view text);
// json::parse(text, callback): the callback sees every parse event, as nlohmann documents.
json parse_json(std::string_view text, const json::parser_callback_t &callback);
// value.dump(indent, indent_char, ensure_ascii, handler), with nlohmann's defaults.
std::string dump_json(const json &value, int indent = -1, char indent_char = ' ', bool ensure_ascii = false,
                      json::error_handler_t handler = json::error_handler_t::strict);
} // namespace dtl
