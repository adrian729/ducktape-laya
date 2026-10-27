#include "json_io.hpp"
#include "errors.hpp"
#include "python_json.hpp"
#include <unordered_set>
#include <vector>
namespace dtl {
json parse_json(std::string_view text) { return json::parse(text); }
namespace {
using input = decltype(nlohmann::detail::input_adapter(std::string_view().begin(), std::string_view().end()));
// nlohmann's own document builder, with the request limits checked on its events.
class request_reader {
public:
    using number_integer_t = json::number_integer_t;
    using number_unsigned_t = json::number_unsigned_t;
    using number_float_t = json::number_float_t;
    using string_t = json::string_t;
    using binary_t = json::binary_t;
    request_reader(json &root, size_t max_depth, size_t max_members) : dom_(root), max_depth_(max_depth), max_members_(max_members) {}
    bool null() { return dom_.null(); }
    bool boolean(bool value) { return dom_.boolean(value); }
    bool number_integer(number_integer_t value) { return dom_.number_integer(value); }
    bool number_unsigned(number_unsigned_t value) { return dom_.number_unsigned(value); }
    bool number_float(number_float_t value, const string_t &literal) {
        // nlohmann reads an integer literal as a double only when it overflows 64 bits.
        if (literal.find_first_not_of("-0123456789") != string_t::npos) return dom_.number_float(value, literal);
        binary_t digits(binary_t::container_type(literal.begin(), literal.end()), big_integer_subtype);
        return dom_.binary(digits);
    }
    bool string(string_t &value) { return dom_.string(value); }
    bool binary(binary_t &value) { return dom_.binary(value); }
    bool start_object(size_t size) {
        enter();
        keys_.emplace_back();
        return dom_.start_object(size);
    }
    bool key(string_t &value) {
        auto &keys = keys_.back();
        require(keys.size() < max_members_, "JSON objects are limited to " + std::to_string(max_members_) + " members");
        require(keys.insert(value).second, "JSON object repeats the key \"" + value + "\"");
        return dom_.key(value);
    }
    bool end_object() {
        keys_.pop_back();
        --depth_;
        return dom_.end_object();
    }
    bool start_array(size_t size) {
        enter();
        return dom_.start_array(size);
    }
    bool end_array() {
        --depth_;
        return dom_.end_array();
    }
    template <class Exception>
    bool parse_error(size_t position, const std::string &token, const Exception &e) { return dom_.parse_error(position, token, e); }
private:
    void enter() { require(++depth_ <= max_depth_, "JSON nesting exceeds " + std::to_string(max_depth_) + " levels"); }
    nlohmann::detail::json_sax_dom_parser<json, input> dom_;
    std::vector<std::unordered_set<string_t>> keys_;
    size_t depth_ = 0, max_depth_, max_members_;
};
} // namespace
json parse_request_json(std::string_view text, size_t max_depth, size_t max_members) {
    json root;
    request_reader reader(root, max_depth, max_members);
    json::sax_parse(text.begin(), text.end(), &reader);
    return root;
}
std::string dump_json(const json &value, int indent, char indent_char, bool ensure_ascii, json::error_handler_t handler) {
    return value.dump(indent, indent_char, ensure_ascii, handler);
}
} // namespace dtl
