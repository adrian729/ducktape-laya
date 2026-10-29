// Model-free decision rules: calibration buckets, probabilities, confidence and the
// documented tie rule (first option or level) for choices, scores and integer values.
#include "decision.hpp"
#include "errors.hpp"
#include "python_json.hpp"
#include "threads.hpp"
#include "request_state.hpp"
#include "thread-cpu-time.hpp"
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
using dtl::json;
static void expect(bool ok, const char *message) { if (!ok) throw std::runtime_error(message); }
static bool nearly(double a, double b) { return std::fabs(a - b) < 1e-12; }
static json options(std::initializer_list<const char *> keys) {
    json result = json::array();
    for (auto key : keys) result.push_back({{"key", key}, {"description", std::string("level ") + key}});
    return result;
}
// Request parsing time must grow linearly with the text: nlohmann's parser callbacks rescan each
// object's parent when the object ends, so 40,000 empty objects (117 KiB) once took 6 s.
static int parse_scaling() {
    auto seconds = [](size_t objects) {
        std::string text = "[";
        for (size_t i = 0; i < objects; ++i) text += i ? R"(,{"a":[1]})" : R"({"a":[1]})";
        text += "]";
        return dtl_test::cpu_seconds_per_run([&] { expect(dtl::parse_request_json(text, 64, 1024).size() == objects, "parsed"); });
    };
    const double half = seconds(20000), full = seconds(40000);
    std::cout << "request parse: 20000 objects " << half << " s, 40000 objects " << full << " s\n";
    // Linear growth doubles the time and quadratic growth quadruples it.
    if (full > 3 * half) { std::cerr << "request parsing grows faster than linearly\n"; return 1; }
    return 0;
}
int main(int argc, char **argv) {
    try {
        if (argc > 1 && std::string(argv[1]) == "--parse-scaling") return parse_scaling();
        // Structured values are serialized exactly as Python's json.dumps(v, ensure_ascii=False).
        const std::pair<double, const char *> floats[] = {{149.0, "149.0"}, {3.14159265358979, "3.14159265358979"},
            {1e21, "1e+21"}, {1e-07, "1e-07"}, {-0.125, "-0.125"}, {1.0 / 3, "0.3333333333333333"}, {-0.0, "-0.0"},
            {0.0, "0.0"}, {1e16, "1e+16"}, {9999999999999998.0, "9999999999999998.0"}, {0.0001, "0.0001"},
            {0.00001234, "1.234e-05"}, {12345678.9, "12345678.9"}, {2.5e-300, "2.5e-300"},
            {1.7976931348623157e308, "1.7976931348623157e+308"}, {5e-324, "5e-324"}, {100.0, "100.0"},
            {123456789012345678.0, "1.2345678901234568e+17"}, {0.1 + 0.2, "0.30000000000000004"}};
        for (auto [value, text] : floats) expect(dtl::python_float(value) == text, text);
        auto mixed = json::parse(R"({"s":"Tom \"x\" / \\ \n\t\b\f\r\u0001\u007f\u2028 na\u00efve","n":null,"t":true,)"
                                 R"("f":false,"l":[1,2.0,{}],"e":{},"i":-17,"u":18446744073709551615})");
        expect(dtl::python_json(mixed) == "{\"s\": \"Tom \\\"x\\\" / \\\\ \\n\\t\\b\\f\\r\\u0001\x7f\u2028 na\u00efve\", "
               "\"n\": null, \"t\": true, \"f\": false, \"l\": [1, 2.0, {}], \"e\": {}, \"i\": -17, "
               "\"u\": 18446744073709551615}", "python_json object, escaping and separators");
        // Integers outside int64/uint64 keep their digits, as Python's json does (it parses
        // them as exact ints); the expected text is Python's json.dumps output.
        auto parse_kept = [](const std::string &text) { return dtl::parse_request_json(text, 64, 1024); };
        const std::string big = R"({"a":100000000000000000000000,"b":-9223372036854775809,"c":18446744073709551616,)"
            R"("d":9223372036854775807,"e":-9223372036854775808,"f":18446744073709551615,"g":1e23,)"
            R"("h":100000000000000000000000.0,"i":-0,"j":0,"k":[123456789012345678901234567890,)"
            R"({"x":-123456789012345678901234567890}],"s":"q\"12345678901234567890123\\","t":12345678901234567890123E0,)"
            R"("u":1234567890123456789})";
        expect(dtl::python_json(parse_kept(big)) == "{\"a\": 100000000000000000000000, \"b\": -9223372036854775809, "
               "\"c\": 18446744073709551616, \"d\": 9223372036854775807, \"e\": -9223372036854775808, "
               "\"f\": 18446744073709551615, \"g\": 1e+23, \"h\": 1e+23, \"i\": 0, \"j\": 0, "
               "\"k\": [123456789012345678901234567890, {\"x\": -123456789012345678901234567890}], "
               "\"s\": \"q\\\"12345678901234567890123\\\\\", \"t\": 1.2345678901234568e+22, \"u\": 1234567890123456789}",
               "python_json keeps integers outside int64/uint64 exact");
        const std::string huge = "1" + std::string(308, '0'), negative = "-" + std::string(300, '9');
        expect(dtl::python_json(parse_kept(huge)) == huge && dtl::python_json(parse_kept(negative)) == negative &&
               dtl::python_json(parse_kept("[" + huge + "," + negative + "]")) == "[" + huge + ", " + negative + "]",
               "python_json keeps 300-digit integers exact");
        expect(dtl::is_big_integer(parse_kept(huge)) && !dtl::is_big_integer(parse_kept("1e23")) &&
               parse_kept("18446744073709551615").is_number_unsigned(), "only overflowing integer literals are kept as text");
        // Results echo a big integer (in a score legend, also nested in a structured description)
        // as the digits the request gave; otherwise they are nlohmann's compact dump.
        {
            json levels = json::array();
            levels.push_back({{"key", "0"}, {"description", parse_kept(huge)}});
            levels.push_back({{"key", "1"}, {"description", parse_kept(R"({"x":[-123456789012345678901234567890,1.5,"t\u00e9"]})")}});
            levels.push_back({{"key", "2"}, {"description", "plain"}});
            const auto answer = dtl::answer_from_scores("score", levels, {0, 1, 2}, 1);
            const auto text = dtl::result_json(json::array({answer}));
            expect(text.find("\"legend\":{\"0\":" + huge + ",\"1\":{\"x\":[-123456789012345678901234567890,1.5,\"t\u00e9\"]},\"2\":\"plain\"}") !=
                   std::string::npos, "a legend echoes big integers with their digits");
            expect(json::parse(text)[0]["legend"]["0"].is_number() && json::parse(text)[0]["score"] == answer["score"],
                   "a result with big integers is valid JSON");
            const auto plain = dtl::answer_from_scores("score", options({"0", "1", "2"}), {2, 0, 2}, 1);
            expect(dtl::result_json(mixed) == mixed.dump() && dtl::result_json(plain) == plain.dump() &&
                   dtl::result_json(json::array({plain, json::object(), json::array()})) == json::array({plain, json::object(), json::array()}).dump(),
                   "results without big integers are nlohmann's dump");
        }
        // Request limits, enforced while parsing: depth 64, 1,024 members per object, no repeated key.
        {
            auto rejects = [](const std::string &text, const char *message) {
                try { dtl::parse_request_json(text, 64, 1024); } catch (const dtl::failure &e) {
                    expect(e.status == DTL_INVALID_ARGUMENT && std::string(e.what()).find(message) != std::string::npos, message);
                    return true;
                }
                return false;
            };
            auto nested = [](size_t levels) { return std::string(levels, '[') + std::string(levels, ']'); };
            auto members = [](size_t count) {
                std::string text = "{";
                for (size_t i = 0; i < count; ++i) text += (i ? ",\"" : "\"") + std::to_string(i) + "\":0";
                return text + "}";
            };
            expect(dtl::parse_request_json(nested(64), 64, 1024).is_array() && rejects(nested(65), "nesting exceeds 64") &&
                   rejects("{\"a\":" + nested(64) + "}", "nesting exceeds 64"), "64 levels of nesting are accepted, 65 are not");
            expect(dtl::parse_request_json(members(1024), 64, 1024).size() == 1024 && rejects(members(1025), "limited to 1024") &&
                   rejects("[" + members(1024) + "," + members(1025) + "]", "limited to 1024"), "1,024 members per object are accepted, 1,025 are not");
            expect(rejects(R"({"a":1,"b":{"a":2,"a":3}})", "repeats the key \"a\"") &&
                   dtl::parse_request_json(R"({"a":{"a":1},"b":[{"a":2},{"a":3}]})", 64, 1024).size() == 2,
                   "a repeated key is rejected; the same key in different objects is not");
            bool invalid = false;
            try { dtl::parse_request_json("{\"a\":}", 64, 1024); } catch (const json::parse_error &) { invalid = true; }
            expect(invalid, "invalid JSON is nlohmann's parse_error");
        }
        const std::map<std::string, float> temperatures{{"choice.2", 2}, {"choice.3_5", 3}, {"choice.6_10", 6},
            {"choice.11", 11}, {"score", 4}, {"score.3_5", 5}, {"noul.2", 7}};
        expect(dtl::temperature_for("choice", 2, temperatures) == 2 && dtl::temperature_for("choice", 3, temperatures) == 3 &&
               dtl::temperature_for("choice", 5, temperatures) == 3 && dtl::temperature_for("choice", 6, temperatures) == 6 &&
               dtl::temperature_for("choice", 10, temperatures) == 6 && dtl::temperature_for("choice", 11, temperatures) == 11 &&
               dtl::temperature_for("choice", 255, temperatures) == 11, "choice calibration buckets");
        expect(dtl::temperature_for("score", 4, temperatures) == 5 && dtl::temperature_for("score", 2, temperatures) == 4 &&
               dtl::temperature_for("score", 7, temperatures) == 4, "score bucket and type default");
        expect(dtl::temperature_for("noul", 2, temperatures) == 7 && dtl::temperature_for("other", 2, temperatures) == 1,
               "noul bucket and uncalibrated default");

        // Equal scores: uniform probabilities, first option, zero concentration.
        auto tie = dtl::answer_from_scores("choice", options({"a", "b", "c"}), {0.5f, 0.5f, 0.5f}, 3);
        expect(tie["choice"] == "a" && nearly(tie["probabilities"]["b"].get<double>(), 1.0 / 3) &&
               nearly(tie["confidence"].get<double>(), 0) && nearly(tie["answer_confidence"].get<double>(), 1.0 / 3),
               "three-way choice tie chooses the first option");
        auto later = dtl::answer_from_scores("choice", options({"a", "b", "c"}), {1, 3, 3}, 1);
        expect(later["choice"] == "b", "tie between later options chooses the first of them");
        expect(nearly(later["probabilities"]["a"].get<double>(), 1 / (1 + 2 * std::exp(2.0))) &&
               later["probabilities"]["b"] == later["probabilities"]["c"], "softmax probabilities");
        auto scaled = dtl::answer_from_scores("choice", options({"a", "b"}), {0, 2}, 2);
        const double pb = 1 / (1 + std::exp(-1.0));
        const double entropy = -(pb * std::log(pb) + (1 - pb) * std::log(1 - pb));
        expect(nearly(scaled["probabilities"]["b"].get<double>(), pb) && nearly(scaled["answer_confidence"].get<double>(), pb) &&
               nearly(scaled["confidence"].get<double>(), 1 - entropy / std::log(2.0)), "temperature, answer_confidence and concentration");

        // Ordinal score: the expected level over the probabilities.
        auto score = dtl::answer_from_scores("score", options({"0", "1", "2"}), {2, 0, 2}, 1);
        const double edge = 1 / (2 + std::exp(-2.0)), middle = std::exp(-2.0) * edge;
        expect(nearly(score["score"].get<double>(), middle + 2 * edge), "expected level");
        const double score_entropy = -(2 * edge * std::log(edge) + middle * std::log(middle));
        expect(nearly(score["confidence"].get<double>(), 1 - score_entropy / std::log(3.0)) &&
               nearly(score["answer_confidence"].get<double>(), edge), "score concentration and answer_confidence");
        expect(score["legend"]["2"] == "level 2" && !score.contains("choice"), "score legend");

        auto noul = dtl::answer_from_scores("noul", json::array(), {0, 0}, 1);
        expect(noul.size() == 4 && noul["noul"].get<double>() == 0.5 && nearly(noul["confidence"].get<double>(), 0) &&
               noul["answer_confidence"].get<double>() == 0.5, "noul tie is one half");
        auto single = dtl::single_option_answer(options({"only"}));
        expect(single["choice"] == "only" && single["probabilities"]["only"] == 1.0 && single["confidence"] == 1.0 &&
               single["answer_confidence"] == 1.0, "a single option is the answer with certainty");

        // Structured integer values use the first most probable level.
        expect(dtl::ordinal_index(json{{"0", 0.4}, {"1", 0.4}, {"2", 0.2}}) == 0, "integer tie chooses the first level");
        expect(dtl::ordinal_index(json{{"0", 0.2}, {"1", 0.4}, {"2", 0.4}}) == 1, "integer tie chooses the first of the tied levels");
        expect(dtl::ordinal_index(json{{"0", 0.1}, {"1", 0.2}, {"2", 0.7}}) == 2, "integer maximum");
        // Automatic thread count: min(4, affinity CPUs, cgroup v2 quota rounded up, hardware threads); 0 = unknown.
        expect(dtl::cgroup_quota_cpus("max 100000\n") == 0 && dtl::cgroup_quota_cpus("max 100000") == 0, "cpu.max without a limit");
        expect(dtl::cgroup_quota_cpus("100000 100000\n") == 1 && dtl::cgroup_quota_cpus("150000 100000") == 2 &&
               dtl::cgroup_quota_cpus("50000 100000") == 1 && dtl::cgroup_quota_cpus("800000 100000\n") == 8 &&
               dtl::cgroup_quota_cpus("1000 1000000") == 1, "cpu.max quota rounds up");
        for (const char *bad : {"", "\n", "100000", "x 100000", "100000 x", "100000 0", "0 100000", "-1 100000", " 100000",
                                "100000  100000", "99999999999999999999 1"})
            expect(dtl::cgroup_quota_cpus(bad) == 0, bad);
        expect(dtl::cgroup_v2_path("0::/user.slice/user-1000.slice/session-2.scope\n") == "/user.slice/user-1000.slice/session-2.scope",
               "cgroup v2 path");
        expect(dtl::cgroup_v2_path("12:cpu,cpuacct:/docker/abc\n1:name=systemd:/x\n0::/docker/abc\n") == "/docker/abc", "hybrid hierarchy");
        expect(dtl::cgroup_v2_path("0::/") == "/", "root cgroup");
        for (const char *none : {"", "12:cpu,cpuacct:/docker/abc\n", "0::\n", "10::/x\n"})
            expect(dtl::cgroup_v2_path(none).empty(), "no cgroup v2 path");
        {
            // The smallest quota of the group and its ancestors, in a fake cgroup v2 mount.
            namespace fs = std::filesystem;
            const auto mount = fs::temp_directory_path() /
                ("laya-cgroup-test-" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
            struct cleanup { fs::path dir; ~cleanup() { std::error_code ec; fs::remove_all(dir, ec); } } guard{mount};
            fs::create_directories(mount / "a/b/c");
            auto write = [&](const char *group, const char *text) { std::ofstream(mount / group / "cpu.max") << text; };
            const auto root = mount.generic_string();
            expect(dtl::cgroup_tree_quota_cpus(root, "/a/b/c") == 0, "no cpu.max anywhere");
            write("a", "max 100000\n");
            write("a/b", "350000 100000\n");
            expect(dtl::cgroup_tree_quota_cpus(root, "/a/b/c") == 4, "a parent's quota limits the group");
            write(".", "200000 100000\n");
            expect(dtl::cgroup_tree_quota_cpus(root, "/a/b/c") == 2, "the mount's top group (a cgroup namespace) limits the group");
            write("a/b/c", "100000 100000\n");
            expect(dtl::cgroup_tree_quota_cpus(root, "/a/b/c") == 1 && dtl::cgroup_tree_quota_cpus(root, "/a/b") == 2 &&
                   dtl::cgroup_tree_quota_cpus(root, "/a") == 2 && dtl::cgroup_tree_quota_cpus(root, "/") == 2,
                   "the smallest quota of the group and its ancestors");
            write(".", "garbage\n");
            expect(dtl::cgroup_tree_quota_cpus(root, "/a/b") == 4 && dtl::cgroup_tree_quota_cpus(root, "/a") == 0 &&
                   dtl::cgroup_tree_quota_cpus(root, "/a/x/y") == 0 && dtl::cgroup_tree_quota_cpus(root, "") == 0 &&
                   dtl::cgroup_tree_quota_cpus(root + "/missing", "/a/b/c") == 0, "unreadable or missing files are no limit");
        }
        expect(dtl::automatic_threads(0, 0, 0) == 1 && dtl::automatic_threads(0, 0, 8) == 4 &&
               dtl::automatic_threads(0, 0, 2) == 2 && dtl::automatic_threads(1, 0, 8) == 1 &&
               dtl::automatic_threads(16, 3, 16) == 3 && dtl::automatic_threads(2, 3, 16) == 2 &&
               dtl::automatic_threads(8, 0, 0) == 1 && dtl::automatic_threads(0, 1, 0) == 1 &&
               dtl::automatic_threads(3, 0, 8) == 3 && dtl::automatic_threads(0, 2, 8) == 2, "automatic thread count");
        // A request's state is prepared (serialized, marker replaced) once per request.
        {
            json state = json::parse(R"({"note":"see [MASK] here","n":1e15,"list":[1,2.5,true,null]})");
            dtl::request_state request(state);
            const std::string &first = request.text("[MASK]");
            expect(first == "{\"note\": \"see   here\", \"n\": 1000000000000000.0, \"list\": [1, 2.5, true, null]}",
                   "state serialized as the original Laya does, marker replaced by a space");
            state["note"] = "changed";
            const std::string &again = request.text("[MASK]");
            expect(&again == &first && again.find("see") != std::string::npos && request.serializations == 1,
                   "later questions reuse the prepared state without serializing it again");
            json text = "plain [MASK] text";
            dtl::request_state plain(text);
            expect(plain.text("[MASK]") == "plain   text", "string state");
        }
        std::cout << "decision rule tests passed\n";
        return 0;
    } catch (const std::exception &e) { std::cerr << e.what() << '\n'; return 1; }
}
